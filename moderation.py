"""
Moderation-Cog: die "typischen" Admin-Bot-Befehle.

Wird in bot.py per `await bot.add_cog(Moderation(bot))` geladen.
Alle Befehle brauchen mindestens die jeweils sinnvolle Discord-Berechtigung
(kick_members, ban_members, moderate_members, manage_messages, manage_channels),
zusätzlich greift ggf. das Anti-Nuke-System aus security.py.
"""

import logging
import os
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

import branding
from branding import VOLT_RED

log = logging.getLogger("moderation")

WARN_STORE: dict[int, list[dict]] = {}  # user_id -> [{"reason":..., "moderator":...}]


async def get_log_channel(guild: discord.Guild) -> discord.TextChannel | None:
    return discord.utils.get(guild.text_channels, name="admin-logs")


async def log_action(guild: discord.Guild, embed: discord.Embed):
    channel = await get_log_channel(guild)
    if channel:
        embed.set_footer(text=branding.ADMIN_FOOTER)
        file = branding.banner_file(branding.ICON)
        if file:
            embed.set_thumbnail(url=f"attachment://{os.path.basename(branding.ICON)}")
        try:
            await channel.send(embed=embed, file=file)
        except discord.Forbidden:
            pass


def base_embed(title: str, color: discord.Color) -> discord.Embed:
    embed = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
    return embed


class VerifyView(discord.ui.View):
    """Persistent View für den #verify Kanal."""

    def __init__(self, role_name: str = "Verified"):
        super().__init__(timeout=None)
        self.role_name = role_name

    @discord.ui.button(label="Verifizieren", style=discord.ButtonStyle.success, emoji="✅", custom_id="verify_button")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = discord.utils.get(interaction.guild.roles, name=self.role_name)
        if role is None:
            role = await interaction.guild.create_role(name=self.role_name, reason="Verify-System Setup")
        if role in interaction.user.roles:
            return await interaction.response.send_message("Du bist bereits verifiziert. ✅", ephemeral=True)
        await interaction.user.add_roles(role, reason="Verify-Button")
        await interaction.response.send_message("✅ Du wurdest erfolgreich verifiziert!", ephemeral=True)


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------------------------------------------------------------- kick
    @app_commands.command(name="kick", description="[Mod] Kickt ein Mitglied vom Server")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, grund: str = "Kein Grund angegeben"):
        await member.kick(reason=f"{interaction.user}: {grund}")
        embed = base_embed("👢 Mitglied gekickt", discord.Color.orange())
        embed.add_field(name="Mitglied", value=f"{member} (`{member.id}`)")
        embed.add_field(name="Moderator", value=interaction.user.mention)
        embed.add_field(name="Grund", value=grund, inline=False)
        await log_action(interaction.guild, embed)
        await interaction.response.send_message(f"👢 {member.mention} wurde gekickt. Grund: {grund}")

    # ----------------------------------------------------------------- ban
    @app_commands.command(name="ban", description="[Mod] Bannt ein Mitglied vom Server")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, member: discord.Member, grund: str = "Kein Grund angegeben", loesche_nachrichten_tage: int = 0):
        await member.ban(reason=f"{interaction.user}: {grund}", delete_message_days=max(0, min(7, loesche_nachrichten_tage)))
        embed = base_embed("🔨 Mitglied gebannt", discord.Color.red())
        embed.add_field(name="Mitglied", value=f"{member} (`{member.id}`)")
        embed.add_field(name="Moderator", value=interaction.user.mention)
        embed.add_field(name="Grund", value=grund, inline=False)
        await log_action(interaction.guild, embed)
        await interaction.response.send_message(f"🔨 {member.mention} wurde gebannt. Grund: {grund}")

    @app_commands.command(name="unban", description="[Mod] Entbannt eine Person per User-ID")
    @app_commands.checks.has_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str, grund: str = "Kein Grund angegeben"):
        user = discord.Object(id=int(user_id))
        await interaction.guild.unban(user, reason=f"{interaction.user}: {grund}")
        await interaction.response.send_message(f"✅ User-ID `{user_id}` wurde entbannt.")

    # ------------------------------------------------------------- timeout
    @app_commands.command(name="timeout", description="[Mod] Timeout (Stummschaltung) für X Minuten")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(self, interaction: discord.Interaction, member: discord.Member, minuten: int, grund: str = "Kein Grund angegeben"):
        await member.timeout(discord.utils.utcnow() + timedelta(minutes=minuten), reason=f"{interaction.user}: {grund}")
        embed = base_embed("🔇 Timeout gesetzt", discord.Color.orange())
        embed.add_field(name="Mitglied", value=f"{member} (`{member.id}`)")
        embed.add_field(name="Dauer", value=f"{minuten} Minuten")
        embed.add_field(name="Grund", value=grund, inline=False)
        await log_action(interaction.guild, embed)
        await interaction.response.send_message(f"🔇 {member.mention} hat für {minuten} Minuten einen Timeout erhalten.")

    @app_commands.command(name="timeout-entfernen", description="[Mod] Entfernt einen laufenden Timeout")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def remove_timeout(self, interaction: discord.Interaction, member: discord.Member):
        await member.timeout(None, reason=f"Timeout entfernt von {interaction.user}")
        await interaction.response.send_message(f"🔊 Timeout von {member.mention} wurde entfernt.")

    # ---------------------------------------------------------------- warn
    @app_commands.command(name="warn", description="[Mod] Verwarnt ein Mitglied (wird protokolliert)")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, grund: str):
        WARN_STORE.setdefault(member.id, []).append({"reason": grund, "moderator": str(interaction.user)})
        count = len(WARN_STORE[member.id])
        embed = base_embed("⚠️ Verwarnung", discord.Color.yellow())
        embed.add_field(name="Mitglied", value=f"{member} (`{member.id}`)")
        embed.add_field(name="Anzahl Verwarnungen", value=str(count))
        embed.add_field(name="Grund", value=grund, inline=False)
        await log_action(interaction.guild, embed)
        try:
            await member.send(f"⚠️ Du wurdest auf **{interaction.guild.name}** verwarnt. Grund: {grund}")
        except discord.Forbidden:
            pass
        await interaction.response.send_message(f"⚠️ {member.mention} wurde verwarnt ({count}. Verwarnung). Grund: {grund}")

    @app_commands.command(name="warnungen", description="[Mod] Zeigt alle Verwarnungen eines Mitglieds")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def list_warnings(self, interaction: discord.Interaction, member: discord.Member):
        warns = WARN_STORE.get(member.id, [])
        if not warns:
            return await interaction.response.send_message(f"{member.mention} hat keine Verwarnungen.", ephemeral=True)
        text = "\n".join(f"{i+1}. {w['reason']} (von {w['moderator']})" for i, w in enumerate(warns))
        await interaction.response.send_message(f"**Verwarnungen von {member}:**\n{text}", ephemeral=True)

    # --------------------------------------------------------------- clear
    @app_commands.command(name="clear", description="[Mod] Löscht die letzten X Nachrichten in diesem Kanal")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clear(self, interaction: discord.Interaction, anzahl: app_commands.Range[int, 1, 100]):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=anzahl)
        await interaction.followup.send(f"🧹 {len(deleted)} Nachrichten gelöscht.", ephemeral=True)

    # ------------------------------------------------------------ slowmode
    @app_commands.command(name="slowmode", description="[Mod] Setzt den Slowmode für diesen Kanal (Sekunden)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction, sekunden: app_commands.Range[int, 0, 21600]):
        await interaction.channel.edit(slowmode_delay=sekunden)
        if sekunden == 0:
            await interaction.response.send_message("🐇 Slowmode deaktiviert.")
        else:
            await interaction.response.send_message(f"🐌 Slowmode auf {sekunden} Sekunden gesetzt.")

    # ------------------------------------------------------------ lock/unlock
    @app_commands.command(name="lock", description="[Mod] Sperrt diesen Kanal für @everyone")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def lock(self, interaction: discord.Interaction):
        overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = False
        await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message("🔒 Kanal gesperrt.")

    @app_commands.command(name="unlock", description="[Mod] Entsperrt diesen Kanal wieder")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction):
        overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = None
        await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message("🔓 Kanal entsperrt.")

    # ------------------------------------------------------------- verify
    @app_commands.command(name="setup-verify", description="[Admin] Postet den Verify-Button in diesen Kanal")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_verify(self, interaction: discord.Interaction, verified_rolle: str = "Verified"):
        # Zuerst deferren, dann erst die (potenziell langsame) Datei senden -
        # sonst kann die Interaction ablaufen ("Es ist ein Fehler aufgetreten").
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed = discord.Embed(
            title="✅ Verifizierung",
            description="Klicke auf den Button, um dich zu verifizieren und Zugriff auf den Server zu erhalten.",
            color=VOLT_RED,
        )
        embed, file = branding.with_icon_thumbnail(embed)
        await interaction.channel.send(embed=embed, file=file, view=VerifyView(verified_rolle))
        await interaction.followup.send("✅ Verify-Panel gepostet.", ephemeral=True)

    # -------------------------------------------------------- error handler
    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            msg = "❌ Dir fehlt die nötige Berechtigung für diesen Befehl."
        else:
            log.exception("Fehler in Moderation-Command", exc_info=error)
            # das eigentliche Original (z.B. FileNotFoundError, Forbidden, ...)
            # steckt bei App-Command-Fehlern meist in error.original
            original = getattr(error, "original", error)
            short_error = f"{type(original).__name__}: {original}"[:1500]
            msg = f"❌ Es ist ein Fehler aufgetreten:\n```\n{short_error}\n```"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


class MessageLog(commands.Cog):
    """Protokolliert gelöschte/bearbeitete Nachrichten in #admin-logs."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        channel = await get_log_channel(message.guild)
        if not channel:
            return
        embed = base_embed("🗑️ Nachricht gelöscht", discord.Color.dark_grey())
        embed.add_field(name="Autor", value=message.author.mention, inline=True)
        embed.add_field(name="Kanal", value=message.channel.mention, inline=True)
        embed.add_field(name="Inhalt", value=(message.content or "*kein Text (evtl. Embed/Anhang)*")[:1024], inline=False)
        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or not before.guild or before.content == after.content:
            return
        channel = await get_log_channel(before.guild)
        if not channel:
            return
        embed = base_embed("✏️ Nachricht bearbeitet", discord.Color.dark_grey())
        embed.add_field(name="Autor", value=before.author.mention, inline=True)
        embed.add_field(name="Kanal", value=before.channel.mention, inline=True)
        embed.add_field(name="Vorher", value=(before.content or "-")[:512], inline=False)
        embed.add_field(name="Nachher", value=(after.content or "-")[:512], inline=False)
        await channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
    await bot.add_cog(MessageLog(bot))
