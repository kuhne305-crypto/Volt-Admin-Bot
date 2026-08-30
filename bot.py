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
import traceback

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from security import AntiNuke
from moderation import VerifyView
import moderation
import branding
from branding import VOLT_RED, VOLT_BLUE, VOLT_GREEN, VOLT_GOLD, VOLT_PURPLE, VOLT_GREY

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
        channel = await guild.create_text_channel("admin-logs", overwrites=overwrites, reason="VOLT Setup: Log-Kanal")
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


# ============================================================
#  RESET (alles löschen)
# ============================================================

async def wipe_guild(guild: discord.Guild, skip_channel_id: int | None = None):
    """Löscht alle Kanäle/Kategorien (außer skip_channel_id) und alle
    nicht-verwalteten Rollen unterhalb der Bot-Rolle."""
    for channel in list(guild.channels):
        if skip_channel_id and channel.id == skip_channel_id:
            continue
        try:
            await channel.delete(reason="VOLT Setup: Reset")
        except (discord.Forbidden, discord.HTTPException):
            log.warning("Konnte Kanal %s nicht löschen", channel.name)

    me = guild.me
    for role in list(guild.roles):
        if role.is_default() or role.managed:
            continue
        if role >= me.top_role:
            continue
        try:
            await role.delete(reason="VOLT Setup: Reset")
        except (discord.Forbidden, discord.HTTPException):
            log.warning("Konnte Rolle %s nicht löschen", role.name)


class ConfirmResetView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.value: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Nur die Person, die `/setup` gestartet hat, kann bestätigen.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Ja, Server zurücksetzen", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.stop()
        await interaction.response.edit_message(content="🔄 Server wird zurückgesetzt & neu aufgebaut...", embed=None, view=None)

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.stop()
        await interaction.response.edit_message(content="❌ Abgebrochen, es wurde nichts verändert.", embed=None, view=None)


# ============================================================
#  AUFBAU (kompakte, farbige Struktur)
# ============================================================
#
#   📌 INFOS          -> ankuendigungen, info (Status + Kosten + Bewertungen)
#   👋 WILLKOMMEN     -> willkommen, verify
#   🛒 SHOP           -> preisliste, bestellen
#   🎫 TICKETS        -> ticket-logs   (Ticket-Kanäle selbst legt VOLT TICKETS an)
#   🛠️ TEAM-INTERN    -> admin-logs, team-chat   (nur für Staff-Rollen sichtbar)

async def build_server_structure(guild: discord.Guild) -> discord.TextChannel:
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
        kwargs = {"category": category}
        if overwrites is not None:
            kwargs["overwrites"] = overwrites
        return await guild.create_text_channel(name, **kwargs)

    ankuendigungen = await ensure_channel("📢-ankuendigungen", info_cat)
    info_channel = await ensure_channel("ℹ️-info", info_cat)

    welcome_channel = await ensure_channel("👋-willkommen", welcome_cat)
    verify_channel = await ensure_channel("✅-verify", welcome_cat)

    await ensure_channel("🛍️-preisliste", shop_cat)
    bestellen_channel = await ensure_channel("📩-bestellen", shop_cat)

    await ensure_channel("📁-ticket-logs", ticket_cat, overwrites=staff_only_overwrites)
    await ensure_channel("💬-team-chat", team_cat, overwrites=staff_only_overwrites)

    # ---- Willkommens-Banner ----
    embed = discord.Embed(
        title="⚡ Willkommen bei VOLT",
        description="Discord Solutions - Server Protection & Full Control.\nSchau bei ✅ #verify vorbei, um freigeschaltet zu werden.",
        color=VOLT_GREEN,
    )
    embed, file = branding.with_main_banner(embed)
    await welcome_channel.send(embed=embed, file=file)

    # ---- Verify-Panel ----
    embed = discord.Embed(
        title="✅ Verifizierung",
        description="Klicke auf den Button, um dich zu verifizieren und Zugriff auf den Server zu erhalten.",
        color=VOLT_GREEN,
    )
    embed, file = branding.with_icon_thumbnail(embed)
    await verify_channel.send(embed=embed, file=file, view=VerifyView("Verified"))

    # ---- Ankündigungs-Startpost ----
    embed = discord.Embed(
        title="📢 Ankündigungen",
        description="Hier postet das Team Updates, Neuigkeiten & wichtige Infos rund um den Server.",
        color=VOLT_BLUE,
    )
    await ankuendigungen.send(embed=embed)

    # ---- Info-Channel (Status + Kosten + Bewertungen kompakt gebündelt) ----
    embed = discord.Embed(title="ℹ️ Server-Info", color=VOLT_BLUE)
    embed.add_field(name="📊 Status", value="Aktuell online & einsatzbereit.", inline=False)
    embed.add_field(name="💰 Kosten", value="Preise & Konditionen siehe 🛍️ #preisliste.", inline=False)
    embed.add_field(name="⭐ Bewertungen", value="Feedback bitte hier posten oder per Ticket schicken.", inline=False)
    embed, file = branding.with_icon_thumbnail(embed)
    await info_channel.send(embed=embed, file=file)

    # ---- Bestell-Hinweis ----
    embed = discord.Embed(
        title="📩 Bestellen",
        description="Öffne über **VOLT TICKETS** ein Ticket, um eine Bestellung aufzugeben.",
        color=VOLT_GOLD,
    )
    await bestellen_channel.send(embed=embed)

    # ---- Abschluss-Log ----
    embed = discord.Embed(
        title="✅ VOLT Server-Setup abgeschlossen",
        description=(
            "Kategorien, Kanäle und Rollen wurden frisch aufgebaut.\n"
            "Nutze jetzt **VOLT TICKETS** mit `/setup-tickets` im Kanal `#bestellen`, "
            "um das Bestell-Dropdown zu posten (FiveM Bots / Discord Server / Discord Custom Bots)."
        ),
        color=VOLT_PURPLE,
    )
    embed, file = branding.with_admin_banner(embed)
    await log_channel.send(embed=embed, file=file)

    return log_channel


# ---------------------------- Setup-Command ----------------------------

@bot.tree.command(name="setup", description="[Admin] Löscht den gesamten Server und baut ihn kompakt & farbenfroh neu auf")
@is_admin()
async def setup_cmd(interaction: discord.Interaction):
    guild = interaction.guild
    origin_channel_id = interaction.channel_id

    warn_embed = discord.Embed(
        title="⚠️ Server-Reset",
        description=(
            "Dieser Befehl **löscht ALLE Kanäle, Kategorien und Rollen** "
            "(außer @everyone und Bot-/Integrations-Rollen) und baut den "
            "kompletten Shop-Server danach frisch und kompakt neu auf.\n\n"
            "**Das kann nicht rückgängig gemacht werden!**\nBist du sicher?"
        ),
        color=VOLT_RED,
    )
    view = ConfirmResetView(interaction.user.id)
    await interaction.response.send_message(embed=warn_embed, view=view, ephemeral=True)

    timed_out = await view.wait()
    if timed_out or not view.value:
        return

    status = await interaction.followup.send("🧨 Lösche alte Struktur...", ephemeral=True)

    try:
        # eigenen Kanal beim Wipe aussparen, sonst bricht die Interaction ab
        await wipe_guild(guild, skip_channel_id=origin_channel_id)
        await status.edit(content="🏗️ Baue neue Struktur auf...")
        log_channel = await build_server_structure(guild)
        await status.edit(content=f"✅ Server-Reset abgeschlossen! Details in {log_channel.mention}.")

        # alten Ursprungskanal jetzt sicher nachträglich löschen (falls er nicht
        # zufällig selbst zum neuen admin-logs-Kanal geworden ist)
        origin_channel = guild.get_channel(origin_channel_id)
        if origin_channel and origin_channel.id != log_channel.id:
            try:
                await origin_channel.delete(reason="VOLT Setup: Reset (alter Ursprungskanal)")
            except (discord.Forbidden, discord.HTTPException):
                pass

    except Exception as e:
        # Vollen Traceback in die Konsole/Logs schreiben - so siehst du
        # beim nächsten Fehler SOFORT, woran es lag, statt "nichts passiert".
        log.exception("Fehler beim Server-Reset")
        short_error = f"{type(e).__name__}: {e}"[:1500]

        try:
            await status.edit(content=f"❌ Beim Neuaufbau ist ein Fehler aufgetreten:\n```\n{short_error}\n```")
        except discord.HTTPException:
            # Ursprungskanal/Interaction nicht mehr erreichbar -> per DM Bescheid geben
            try:
                await interaction.user.send(
                    f"❌ Beim Server-Reset ist ein Fehler aufgetreten:\n```\n{short_error}\n```"
                )
            except discord.Forbidden:
                pass


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


@setup_cmd.error
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
