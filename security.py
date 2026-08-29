"""
Anti-Nuke Sicherheitssystem.

Überwacht kritische Server-Aktionen (Kanäle löschen, Rollen löschen, Bans,
Kicks). Werden innerhalb eines Zeitfensters zu viele solcher Aktionen von
derselben Person ausgeführt, wird diese Person automatisch entfernt
(Rollen entzogen + gekickt), außer sie steht auf der Owner-Whitelist.

Der Bot braucht dafür die Berechtigung "Audit-Log ansehen", um herauszufinden,
WER eine Aktion ausgeführt hat.
"""

import time
import logging
from collections import defaultdict, deque

import discord

log = logging.getLogger("antinuke")


class AntiNuke:
    def __init__(self, bot, owner_ids: set[int], threshold: int, window_seconds: int):
        self.bot = bot
        self.owner_ids = owner_ids
        self.threshold = threshold
        self.window_seconds = window_seconds
        # user_id -> deque[timestamps]
        self._actions: dict[int, deque] = defaultdict(deque)
        # User-IDs, die aktuell gesperrt werden (verhindert doppelte Aktionen)
        self._locking: set[int] = set()

    def _record_and_check(self, user_id: int) -> bool:
        """Trägt eine Aktion ein und gibt True zurück, wenn der Schwellwert
        überschritten wurde."""
        now = time.time()
        dq = self._actions[user_id]
        dq.append(now)

        # alte Einträge außerhalb des Zeitfensters entfernen
        while dq and now - dq[0] > self.window_seconds:
            dq.popleft()

        return len(dq) >= self.threshold

    async def _get_audit_actor(self, guild: discord.Guild, action: discord.AuditLogAction):
        """Findet heraus, wer die letzte passende Aktion im Audit-Log ausgeführt hat."""
        try:
            async for entry in guild.audit_logs(limit=5, action=action):
                # nur Einträge der letzten 10 Sekunden berücksichtigen
                if (discord.utils.utcnow() - entry.created_at).total_seconds() < 10:
                    return entry.user
        except discord.Forbidden:
            log.warning("Fehlende Berechtigung 'Audit-Log ansehen' in %s", guild.name)
        return None

    async def _punish(self, guild: discord.Guild, member: discord.Member, reason: str):
        if member.id in self.owner_ids or member.id == guild.owner_id:
            return  # Whitelist / Server-Owner nie anfassen
        if member.id == self.bot.user.id:
            return
        if member.id in self._locking:
            return
        self._locking.add(member.id)

        try:
            # Alle Rollen entziehen bevor rausgeworfen wird -> falls Kick fehlschlägt,
            # kann die Person trotzdem nichts mehr anrichten
            try:
                await member.edit(roles=[], reason=f"Anti-Nuke: {reason}")
            except discord.Forbidden:
                pass

            try:
                await member.kick(reason=f"Anti-Nuke: {reason}")
            except discord.Forbidden:
                log.warning("Konnte %s nicht kicken (fehlende Rechte)", member)

            await self._log(guild, member, reason)
        finally:
            self._locking.discard(member.id)

    async def _log(self, guild: discord.Guild, member: discord.Member, reason: str):
        channel = discord.utils.get(guild.text_channels, name="admin-logs")
        if channel is None:
            return
        embed = discord.Embed(
            title="🚨 Anti-Nuke ausgelöst",
            description=f"**{member}** (`{member.id}`) wurde automatisch entfernt.",
            color=discord.Color.red(),
        )
        embed.add_field(name="Grund", value=reason, inline=False)
        embed.timestamp = discord.utils.utcnow()
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            pass

    # ---- Event-Handler, die von bot.py aufgerufen werden ----

    async def on_channel_delete(self, channel: discord.abc.GuildChannel):
        actor = await self._get_audit_actor(channel.guild, discord.AuditLogAction.channel_delete)
        if actor is None or not isinstance(actor, discord.Member):
            return
        if self._record_and_check(actor.id):
            await self._punish(channel.guild, actor, "Zu viele Kanäle in kurzer Zeit gelöscht")

    async def on_role_delete(self, role: discord.Role):
        actor = await self._get_audit_actor(role.guild, discord.AuditLogAction.role_delete)
        if actor is None or not isinstance(actor, discord.Member):
            return
        if self._record_and_check(actor.id):
            await self._punish(role.guild, actor, "Zu viele Rollen in kurzer Zeit gelöscht")

    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        actor = await self._get_audit_actor(guild, discord.AuditLogAction.ban)
        if actor is None or not isinstance(actor, discord.Member):
            return
        if self._record_and_check(actor.id):
            await self._punish(guild, actor, "Zu viele Bans in kurzer Zeit ausgesprochen")

    async def on_member_remove_check(self, guild: discord.Guild, user: discord.User):
        """Wird bei Kicks aufgerufen (member_remove + passender Audit-Log-Eintrag)."""
        actor = await self._get_audit_actor(guild, discord.AuditLogAction.kick)
        if actor is None or not isinstance(actor, discord.Member):
            return
        if self._record_and_check(actor.id):
            await self._punish(guild, actor, "Zu viele Kicks in kurzer Zeit ausgesprochen")
