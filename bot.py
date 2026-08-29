"""
ADMIN-BOT
=========
Nur für Administratoren gedacht. Baut den kompletten Bestell-Server auf
(Kategorien, Kanäle, Rollen) und schützt den Server über ein
Anti-Nuke-Sicherheitssystem (siehe security.py).

WICHTIG: Dieser Bot sollte NUR Personen mit Administrator-Rechten Zugriff
auf seine Commands geben. Das wird über den Discord-Berechtigungscheck
`@app_commands.checks.has_permissions(administrator=True)` erzwungen -
zusätzlich zur Owner-Whitelist im Anti-Nuke-System.

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

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("admin-bot")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
OWNER_IDS = {int(x) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip()}
ANTINUKE_THRESHOLD = int(os.getenv("ANTINUKE_THRESHOLD", "5"))
ANTINUKE_WINDOW = int(os.getenv("ANTINUKE_WINDOW_SECONDS", "15"))

intents = discord.Intents.default()
intents.members = True  # nötig, um Rollen zu entziehen / zu kicken


class AdminBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!admin-", intents=intents)
        self.antinuke: AntiNuke | None = None

    async def setup_hook(self):
        self.antinuke = AntiNuke(self, OWNER_IDS, ANTINUKE_THRESHOLD, ANTINUKE_WINDOW)
        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild_obj)
            await self.tree.sync(guild=guild_obj)
        else:
            await self.tree.sync()


bot = AdminBot()


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
    log.info("Admin-Bot eingeloggt als %s", bot.user)


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

@bot.tree.command(name="setup-server", description="[Admin] Erstellt die komplette Server-Struktur für den Shop")
@is_admin()
async def setup_server(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild

    # 1) Rollen anlegen (falls noch nicht vorhanden)
    role_names = ["Owner", "Support", "Kunde"]
    roles = {}
    for name in role_names:
        existing = discord.utils.get(guild.roles, name=name)
        roles[name] = existing or await guild.create_role(name=name, reason="Shop-Setup")

    # 2) Log-Kanal sicherstellen
    log_channel = await ensure_log_channel(guild)

    # 3) Kategorien + Kanäle
    info_cat = discord.utils.get(guild.categories, name="📋 INFO") or await guild.create_category("📋 INFO")
    order_cat = discord.utils.get(guild.categories, name="🛒 BESTELLUNG") or await guild.create_category("🛒 BESTELLUNG")
    ticket_cat = discord.utils.get(guild.categories, name="🎫 TICKETS") or await guild.create_category("🎫 TICKETS")

    async def ensure_channel(name, category):
        existing = discord.utils.get(guild.text_channels, name=name)
        return existing or await guild.create_text_channel(name, category=category)

    await ensure_channel("regeln", info_cat)
    await ensure_channel("preisliste", info_cat)
    await ensure_channel("reviews", info_cat)
    await ensure_channel("bestellen", order_cat)
    await ensure_channel("ticket-logs", ticket_cat)

    embed = discord.Embed(
        title="✅ Server-Setup abgeschlossen",
        description=(
            "Kategorien, Kanäle und Rollen wurden angelegt.\n"
            "Nutze jetzt den **Ticket-Bot** mit `/setup-tickets` im Kanal `#bestellen`, "
            "um die Bestell-Buttons zu posten."
        ),
        color=discord.Color.green(),
    )
    await log_channel.send(embed=embed)
    await interaction.followup.send("Server-Struktur wurde erstellt. Details im `#admin-logs` Kanal.", ephemeral=True)


@bot.tree.command(name="security-status", description="[Admin] Zeigt die aktuellen Anti-Nuke-Einstellungen")
@is_admin()
async def security_status(interaction: discord.Interaction):
    embed = discord.Embed(title="🛡️ Anti-Nuke Status", color=discord.Color.blurple())
    embed.add_field(name="Schwellwert", value=f"{ANTINUKE_THRESHOLD} Aktionen", inline=True)
    embed.add_field(name="Zeitfenster", value=f"{ANTINUKE_WINDOW} Sekunden", inline=True)
    embed.add_field(
        name="Whitelist (nie gesperrt)",
        value=", ".join(f"<@{uid}>" for uid in OWNER_IDS) or "— keine gesetzt —",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


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
