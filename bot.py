"""
VOLT ADMIN
==========
Nur für Administratoren gedacht. Baut den kompletten Shop-Server auf
(Kategorien, Kanäle, Rollen), schützt den Server über ein
Anti-Nuke-Sicherheitssystem (siehe security.py) und stellt die typischen
Moderations-Befehle bereit (siehe moderation.py): Kick, Ban, Timeout, Warn,
Clear, Slowmode, Lock/Unlock, Verify-System, Message-Log.

"Server Protection. Full Control." - VOLT Discord Solutions

WICHTIG: Alle sensiblen Commands sind zusätzlich über
`@app_commands.checks.has_permissions(...)` abgesichert - zusätzlich zur
Owner-Whitelist im Anti-Nuke-System.

Einrichtung:
1. pip install -r requirements.txt
2. .env.example -> .env kopieren und ausfüllen
3. python bot.py
"""

import os
import logging

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from security import AntiNuke
from moderation import VerifyView
import moderation
import branding
from branding import VOLT_RED

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("volt-admin")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
OWNER_IDS = {int(x) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip()}
ANTINUKE_THRESHOLD = int(os.getenv("ANTINUKE_THRESHOLD", "5"))
ANTINUKE_WINDOW = int(os.getenv("ANTINUKE_WINDOW_SECONDS", "15"))

intents = discord.Intents.default()
intents.members = True
intents.message_content = True


class VoltAdmin(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!volt-", intents=intents)
        self.antinuke: AntiNuke | None = None

    async def setup_hook(self):
        self.antinuke = AntiNuke(self, OWNER_IDS, ANTINUKE_THRESHOLD, ANTINUKE_WINDOW)
        await moderation.setup(self)
        self.add_view(VerifyView())

        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild_obj)
            await self.tree.sync(guild=guild_obj)
        else:
            await self.tree.sync()


bot = VoltAdmin()


def is_admin():
    return app_commands.checks.has_permissions(administrator=True)


async def ensure_log_channel(guild: discord.Guild) -> discord.TextChannel:
    channel = discord.utils.get(guild.text_channels, name="admin-logs")
    if channel is None:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
        }
        channel = await guild.create_text_channel("admin-logs", overwrites=overwrites, reason="Setup: Log-Kanal")
    return channel


@bot.event
async def on_ready():
    log.info("VOLT ADMIN eingeloggt als %s", bot.user)
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="über den Server ⚡"))


# ---------- Sicherheits-Events an das Anti-Nuke-Modul weiterreichen ----------

@bot.event
async def on_guild_channel_delete(channel):
    if bot.antinuke:
        await bot.antinuke.on_channel_delete(channel)


@bot.event
async def on_guild_role_delete(role):
    if bot.antinuke:
        await bot.antinuke.on_role_delete(role)


@bot.event
async def on_member_ban(guild, user):
    if bot.antinuke:
        await bot.antinuke.on_member_ban(guild, user)


@bot.event
async def on_member_remove(member):
    if bot.antinuke:
        await bot.antinuke.on_member_remove_check(member.guild, member)


# ---------------------------- Setup-Commands ----------------------------
#
#   📌 INFOS          -> ankuendigungen, server-status, bewertungen, kosten
#   👋 WILLKOMMEN     -> willkommen, verify
#   🛒 SHOP           -> preisliste, bestellen
#   🎫 TICKETS        -> ticket-logs   (Ticket-Kanäle selbst legt VOLT TICKETS an)
#   🛠️ TEAM-INTERN    -> admin-logs, team-chat   (nur für Staff-Rollen sichtbar)

@bot.tree.command(name="setup-server", description="[Admin] Erstellt die komplette Server-Struktur für den Shop")
@is_admin()
async def setup_server(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild

    role_names = ["Admin", "Moderator", "Supporter", "Kunde", "Verified"]
    roles = {}
    for name in role_names:
        existing = discord.utils.get(guild.roles, name=name)
        roles[name] = existing or await guild.create_role(name=name, reason="VOLT Setup")

    staff_roles = [roles["Admin"], roles["Moderator"], roles["Supporter"]]
    staff_only_overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        **{r: discord.PermissionOverwrite(view_channel=True, send_messages=True) for r in staff_roles},
    }

    log_channel = await ensure_log_channel(guild)
    await log_channel.edit(overwrites=staff_only_overwrites)

    info_cat = discord.utils.get(guild.categories, name="📌 INFOS") or await guild.create_category("📌 INFOS")
    welcome_cat = discord.utils.get(guild.categories, name="👋 WILLKOMMEN") or await guild.create_category("👋 WILLKOMMEN")
    shop_cat = discord.utils.get(guild.categories, name="🛒 SHOP") or await guild.create_category("🛒 SHOP")
    ticket_cat = discord.utils.get(guild.categories, name="🎫 TICKETS") or await guild.create_category("🎫 TICKETS")
    team_cat = discord.utils.get(guild.categories, name="🛠️ TEAM-INTERN") or await guild.create_category("🛠️ TEAM-INTERN", overwrites=staff_only_overwrites)

    async def ensure_channel(name, category, overwrites=None):
        existing = discord.utils.get(guild.text_channels, name=name)
        if existing:
            return existing
        return await guild.create_text_channel(name, category=category, overwrites=overwrites)

    await ensure_channel("ankuendigungen", info_cat)
    await ensure_channel("server-status", info_cat)
    await ensure_channel("bewertungen", info_cat)
    await ensure_channel("kosten-übersicht", info_cat)

    welcome_channel = await ensure_channel("willkommen", welcome_cat)
    verify_channel = await ensure_channel("verify", welcome_cat)

    await ensure_channel("preisliste", shop_cat)
    await ensure_channel("bestellen", shop_cat)

    await ensure_channel("ticket-logs", ticket_cat, overwrites=staff_only_overwrites)
    await ensure_channel("team-chat", team_cat, overwrites=staff_only_overwrites)

    # Willkommens-Banner posten, falls Kanal noch leer ist
    if welcome_channel.last_message_id is None:
        embed = discord.Embed(
            title="⚡ Willkommen bei VOLT",
            description="Discord Solutions - Server Protection & Full Control.\nSchau bei `#verify` vorbei, um freigeschaltet zu werden.",
            color=VOLT_RED,
        )
        embed, file = branding.with_main_banner(embed)
        await welcome_channel.send(embed=embed, file=file)

    if verify_channel.last_message_id is None:
        embed = discord.Embed(
            title="✅ Verifizierung",
            description="Klicke auf den Button, um dich zu verifizieren und Zugriff auf den Server zu erhalten.",
            color=VOLT_RED,
        )
        embed, file = branding.with_icon_thumbnail(embed)
        await verify_channel.send(embed=embed, file=file, view=VerifyView("Verified"))

    embed = discord.Embed(
        title="✅ VOLT Server-Setup abgeschlossen",
        description=(
            "Kategorien, Kanäle und Rollen wurden angelegt.\n"
            "Nutze jetzt **VOLT TICKETS** mit `/setup-tickets` im Kanal `#bestellen`, "
            "um das Bestell-Dropdown zu posten (FiveM Bots / Discord Server / Discord Custom Bots)."
        ),
        color=VOLT_RED,
    )
    embed, file = branding.with_admin_banner(embed)
    await log_channel.send(embed=embed, file=file)
    await interaction.followup.send("Server-Struktur wurde erstellt. Details im `#admin-logs` Kanal.", ephemeral=True)


@bot.tree.command(name="security-status", description="[Admin] Zeigt die aktuellen Anti-Nuke-Einstellungen")
@is_admin()
async def security_status(interaction: discord.Interaction):
    embed = discord.Embed(title="🛡️ VOLT Anti-Nuke Status", color=VOLT_RED)
    embed.add_field(name="Schwellwert", value=f"{ANTINUKE_THRESHOLD} Aktionen", inline=True)
    embed.add_field(name="Zeitfenster", value=f"{ANTINUKE_WINDOW} Sekunden", inline=True)
    embed.add_field(
        name="Whitelist (nie gesperrt)",
        value=", ".join(f"<@{uid}>" for uid in OWNER_IDS) or "— keine gesetzt —",
        inline=False,
    )
    embed, file = branding.with_icon_thumbnail(embed)
    await interaction.response.send_message(embed=embed, file=file, ephemeral=True)


@setup_server.error
@security_status.error
async def on_admin_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ Dieser Command ist nur für Administratoren.", ephemeral=True
        )
    else:
        log.exception("Fehler in Admin-Command", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("❌ Es ist ein Fehler aufgetreten.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Es ist ein Fehler aufgetreten.", ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN fehlt in der .env Datei!")
    bot.run(TOKEN)
