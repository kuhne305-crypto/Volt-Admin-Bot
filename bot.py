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


class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Direkt verifizieren", style=discord.ButtonStyle.success, emoji="✅", custom_id="verify_button")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        role = discord.utils.get(guild.roles, name="Kunde")
        if role is None:
            await interaction.response.send_message("❌ Rolle 'Kunde' wurde noch nicht angelegt. Führe zuerst /setup-server aus.", ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.response.send_message("✅ Du bist bereits verifiziert.", ephemeral=True)
            return
        await interaction.user.add_roles(role, reason="Verify-Button")
        await interaction.response.send_message("✅ Verifiziert! Du hast jetzt Zugriff auf den Server.", ephemeral=True)


@bot.event
async def on_ready():
    bot.add_view(VerifyView())
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

    # 1) Rollen anlegen (mit Farbe -> das ist die einzige "echte" Farbe, die Discord
    #    zulässt: Rollenfarben zeigen sich am Nutzernamen in Chat & Mitgliederliste)
    role_defs = {
        "Owner": discord.Color.from_str("#E30613"),
        "Support": discord.Color.from_str("#FF8C00"),
        "Kunde": discord.Color.from_str("#B0B0B0"),
    }
    roles = {}
    for name, color in role_defs.items():
        existing = discord.utils.get(guild.roles, name=name)
        roles[name] = existing or await guild.create_role(name=name, color=color, reason="Shop-Setup")

    # 2) Log-Kanal sicherstellen
    log_channel = await ensure_log_channel(guild)

    async def ensure_category(name):
        return discord.utils.get(guild.categories, name=name) or await guild.create_category(name)

    async def ensure_text(name, category, overwrites=None):
        existing = discord.utils.get(guild.text_channels, name=name)
        return existing or await guild.create_text_channel(name, category=category, overwrites=overwrites or {})

    async def ensure_voice(name, category):
        existing = discord.utils.get(guild.voice_channels, name=name)
        return existing or await guild.create_voice_channel(name, category=category)

    # 3) Kategorien (Emojis übernehmen die Rolle von "Farbe", da Discord Kanäle nicht
    #    einfärben kann)
    welcome_cat = await ensure_category("📌 WILLKOMMEN")
    info_cat = await ensure_category("📋 INFO")
    order_cat = await ensure_category("🛒 BESTELLUNG")
    ticket_cat = await ensure_category("🎫 TICKETS")
    voice_cat = await ensure_category("🔊 VOICE")

    verify_overwrites = {guild.default_role: discord.PermissionOverwrite(send_messages=False)}

    welcome_ch = await ensure_text("👋・willkommen", welcome_cat)
    verify_ch = await ensure_text("✅・verify", welcome_cat, verify_overwrites)
    rules_ch = await ensure_text("📜・regeln", welcome_cat, verify_overwrites)

    ann_ch = await ensure_text("📢・ankündigungen", info_cat, verify_overwrites)
    price_ch = await ensure_text("💰・preisliste", info_cat, verify_overwrites)
    reviews_ch = await ensure_text("⭐・bewertungen", info_cat, verify_overwrites)
    status_ch = await ensure_text("🌐・status", info_cat, verify_overwrites)

    await ensure_text("🎫・bestellen", order_cat, verify_overwrites)
    await ensure_text("📁・ticket-logs", ticket_cat, {guild.default_role: discord.PermissionOverwrite(view_channel=False)})

    await ensure_voice("🔊・Allgemein", voice_cat)
    await ensure_voice("🎙️・Support-Voice", voice_cat)
    await ensure_voice("💤・AFK", voice_cat)

    # 4) Willkommens-/Info-Embeds posten (nur falls Kanal noch leer ist -> kein Spam
    #    bei mehrfachem Ausführen von /setup-server)
    async def post_if_empty(channel, **kwargs):
        async for _ in channel.history(limit=1):
            return  # schon Nachrichten drin -> nichts tun
        await channel.send(**kwargs)

    accent = discord.Color.from_str("#E30613")

    await post_if_empty(
        welcome_ch,
        embed=discord.Embed(
            title="Willkommen bei VOLT",
            description=(
                f"Schön, dass du da bist! Schau zuerst in <#{verify_ch.id}> vorbei, um Zugriff auf "
                f"den restlichen Server zu erhalten.\n\nDanach findest du in <#{price_ch.id}> unsere "
                f"Preise und kannst in <#{ann_ch.id}> aktuelle Neuigkeiten verfolgen."
            ),
            color=accent,
        ),
    )

    await post_if_empty(
        verify_ch,
        embed=discord.Embed(
            title="✅ Verifizierung",
            description="Klicke unten auf **Direkt verifizieren**, um sofort Zugriff auf den Server zu erhalten.",
            color=accent,
        ),
        view=VerifyView(),
    )

    await post_if_empty(
        rules_ch,
        embed=discord.Embed(
            title="📜 Serverregeln",
            description=(
                "1️⃣ Freundlicher Umgang miteinander\n"
                "2️⃣ Kein Spam, keine Werbung ohne Absprache\n"
                "3️⃣ Bestellungen ausschließlich über <#%d>\n"
                "4️⃣ Anweisungen vom Team sind zu befolgen"
                % order_cat.id
            ),
            color=accent,
        ),
    )

    await post_if_empty(
        status_ch,
        embed=discord.Embed(
            title="🌐 Systemstatus",
            description="🟢 Alle Systeme laufen normal.",
            color=discord.Color.green(),
        ),
    )

    embed = discord.Embed(
        title="✅ Server-Setup abgeschlossen",
        description=(
            "Kategorien, Kanäle, Voice-Channels, Rollen (mit Farbe) und Willkommens-Nachrichten wurden angelegt.\n"
            "Nutze jetzt den **Ticket-Bot** mit `/setup-tickets` im Kanal `#bestellen` und `/post-preisliste` "
            "im Kanal `#preisliste`."
        ),
        color=discord.Color.green(),
    )
    await log_channel.send(embed=embed)
    await interaction.followup.send("Server-Struktur wurde erstellt/aktualisiert. Details im `#admin-logs` Kanal.", ephemeral=True)


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
