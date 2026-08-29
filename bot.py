"""
TICKET-BOT (VOLT)
==================
Basis-Funktion (unverändert): Bestell-Buttons in #bestellen -> Ticket ->
Fam-Größe wählen -> Preis anzeigen -> Ticket schließen mit Transcript-Log.

NEU in dieser Version:
  /setup           [Admin] Löscht ALLE Kanäle & Rollen (außer @everyone,
                    verwalteten/Bot-Rollen) und baut Kategorien, Kanäle und
                    Rollen passend zum VOLT-Logo (Rot/Schwarz/Weiß) neu auf.
                    Fragt vorher zur Sicherheit noch mal nach ("bist du sicher?").
  /ban             [Mod] Bannt ein Mitglied, schickt optional eine DM,
                    protokolliert in #mod-logs.
  Message-Logging  Jede bearbeitete oder gelöschte Nachricht wird mit
                    Vorher/Nachher-Inhalt, Autor, Kanal und Zeitstempel in
                    #message-logs gepostet.

Einrichtung:
  1. pip install -r requirements.txt
  2. .env.example -> .env kopieren und ausfüllen
  3. python bot.py
  4. Auf dem Server einmalig /setup ausführen (baut alles auf)
  5. In #bestellen wird das Bestell-Menü automatisch gepostet
"""

import os
import io
import logging
import difflib
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from products import PRODUCTS, HOSTING, TERMS

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("volt-bot")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)

# Rollennamen zentral halten, damit sie überall konsistent verwendet werden
ADMIN_ROLE_NAME = os.getenv("ADMIN_ROLE_NAME", "Admin")
MOD_ROLE_NAME = os.getenv("MOD_ROLE_NAME", "Moderator")
SUPPORT_ROLE_NAME = os.getenv("SUPPORT_ROLE_NAME", "Support")

# Log-Kanäle
TICKET_LOG_CHANNEL = "ticket-logs"
MOD_LOG_CHANNEL = "mod-logs"
MESSAGE_LOG_CHANNEL = "message-logs"
REVIEWS_CHANNEL = "⭐・bewertungen"

# Kategorien/Rollen, die /setup NIE anfasst (weder löschen noch bearbeiten).
# Trag hier die Namen der Kategorien und Rollen eures separaten Ticket-Bots ein,
# damit /setup dessen Kanäle und Rollen nicht mit wegräumt. Mehrere Werte mit
# Komma trennen, z.B. PROTECTED_CATEGORY_NAMES="Tickets,Support-Tickets"
_extra_protected_categories = {c.strip() for c in os.getenv("PROTECTED_CATEGORY_NAMES", "").split(",") if c.strip()}
_extra_protected_roles = {r.strip() for r in os.getenv("PROTECTED_ROLE_NAMES", "").split(",") if r.strip()}

# VOLT-Markenfarben (aus dem Logo: schwarzer Kreis, roter Blitz/Ring, weißer Blitz)
COLOR_RED = discord.Colour.from_str("#E30613")
COLOR_DARK = discord.Colour.from_str("#1A1A1D")   # fast schwarz (reines #000000 gilt bei Discord als "keine Farbe")
COLOR_WHITE = discord.Colour.from_str("#F2F2F2")
COLOR_RED_DARK = discord.Colour.from_str("#8C0B1E")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # nötig, damit gelöschte/editierte Nachrichten Inhalt haben


# ---------------------------------------------------------------------------
# Server-Struktur: hier zentral definiert, damit /setup reproduzierbar ist
# ---------------------------------------------------------------------------

# Reihenfolge = Rollen-Hierarchie, oben = höchste Rolle (außer @everyone/Bot)
ROLE_CONFIG = [
    {
        "name": ADMIN_ROLE_NAME,
        "colour": COLOR_RED,
        "gradient_to": COLOR_DARK,   # wird nur genutzt, falls Server-Boosts es zulassen
        "hoist": True,
        "mentionable": True,
        "permissions": discord.Permissions(administrator=True),
    },
    {
        "name": MOD_ROLE_NAME,
        "colour": COLOR_RED_DARK,
        "gradient_to": None,
        "hoist": True,
        "mentionable": True,
        "permissions": discord.Permissions(
            kick_members=True, ban_members=True, manage_messages=True,
            manage_channels=True, moderate_members=True, view_audit_log=True,
        ),
    },
    {
        "name": SUPPORT_ROLE_NAME,
        "colour": COLOR_WHITE,
        "gradient_to": None,
        "hoist": True,
        "mentionable": True,
        "permissions": discord.Permissions(manage_messages=True, manage_channels=False),
    },
    {
        "name": "Kunde",
        "colour": COLOR_DARK,
        "gradient_to": None,
        "hoist": False,
        "mentionable": False,
        "permissions": discord.Permissions(),
    },
]

# (Kategorie, [(Kanalname, ist_privat_für_staff), ...])
CHANNEL_STRUCTURE = [
    ("📢 INFO", [
        ("willkommen", False),
        ("regeln", False),
        ("ankuendigungen", False),
    ]),
    ("🛒 BESTELLUNG", [
        ("bestellen", False),
        (REVIEWS_CHANNEL, False),
    ]),
    ("💬 COMMUNITY", [
        ("allgemein", False),
    ]),
    ("🛡️ STAFF", [
        (MOD_LOG_CHANNEL, True),
        (MESSAGE_LOG_CHANNEL, True),
        (TICKET_LOG_CHANNEL, True),
    ]),
]


async def apply_role_colour(role: discord.Role, colour: discord.Colour, gradient_to: discord.Colour | None):
    """Setzt eine solide Farbe; versucht zusätzlich einen Farbverlauf, falls
    discord.py das unterstützt UND der Server genug Boosts (Enhanced Role
    Styles, ab 3 Boosts) dafür freigeschaltet hat. Schlägt das fehl, bleibt
    einfach die solide Farbe bestehen - kein Fehler für den Nutzer sichtbar."""
    try:
        await role.edit(colour=colour, reason="VOLT /setup")
    except discord.HTTPException as e:
        log.warning("Konnte Farbe für Rolle %s nicht setzen: %s", role.name, e)
        return

    if gradient_to is None:
        return

    RoleColours = getattr(discord, "RoleColours", None)
    if RoleColours is None:
        return  # installierte discord.py-Version unterstützt das (noch) nicht

    try:
        colours = RoleColours(primary_colour=colour, secondary_colour=gradient_to)
        await role.edit(colours=colours, reason="VOLT /setup (Gradient)")
    except discord.HTTPException:
        # Server hat vermutlich nicht genug Boosts für Enhanced Role Styles -> ignorieren
        pass


class ConfirmView(discord.ui.View):
    """Generische Ja/Nein-Bestätigung für gefährliche Aktionen."""

    def __init__(self, author_id: int):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.value: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Nur der Befehl-Ausführende kann bestätigen.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Ja, alles löschen & neu aufbauen", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content="⏳ Server wird zurückgesetzt und neu aufgebaut...", view=self)
        self.stop()

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content="❌ Abgebrochen, es wurde nichts geändert.", view=self)
        self.stop()


def is_admin():
    return app_commands.checks.has_permissions(administrator=True)


def product_embed(key: str) -> discord.Embed:
    p = PRODUCTS[key]
    embed = discord.Embed(title=f"{p['emoji']} {p['name']}", color=COLOR_RED)
    embed.add_field(name="Big Fam", value=f"{p['big']}€", inline=True)
    embed.add_field(name="Klein Fam", value=f"{p['klein']}€", inline=True)
    if key == "komplett":
        embed.add_field(
            name="+ Hosting / Monat",
            value=f"Big Fam: {HOSTING['big']}€ | Klein Fam: {HOSTING['klein']}€",
            inline=False,
        )
    embed.add_field(name="Konditionen", value=TERMS, inline=False)
    embed.set_footer(text="Wähle unten deine Fam-Größe, um die Bestellung zu bestätigen.")
    return embed


class FamSelect(discord.ui.Select):
    def __init__(self, product_key: str):
        self.product_key = product_key
        options = [
            discord.SelectOption(label="Big Fam", value="big", emoji="🏰"),
            discord.SelectOption(label="Klein Fam", value="klein", emoji="🏠"),
        ]
        super().__init__(placeholder="Fam-Größe wählen...", options=options, custom_id=f"fam_select:{product_key}")

    async def callback(self, interaction: discord.Interaction):
        p = PRODUCTS[self.product_key]
        size = self.values[0]
        price = p[size]
        label = "Big Fam" if size == "big" else "Klein Fam"

        embed = discord.Embed(
            title="🧾 Bestellbestätigung",
            description=f"**{p['emoji']} {p['name']}**\nFam-Größe: **{label}**\nPreis: **{price}€**",
            color=discord.Color.green(),
        )
        if self.product_key == "komplett":
            embed.add_field(name="+ Hosting / Monat", value=f"{HOSTING[size]}€", inline=False)
        embed.add_field(name="Nächster Schritt", value="Ein Teammitglied meldet sich gleich für die Zahlungsabwicklung.", inline=False)

        self.disabled = True
        await interaction.response.edit_message(view=self.view)
        await interaction.followup.send(embed=embed)


class FamSelectView(discord.ui.View):
    def __init__(self, product_key: str):
        super().__init__(timeout=None)
        self.add_item(FamSelect(product_key))


STATUS_CATEGORIES = {
    "in_bearbeitung": "🟠 In Bearbeitung ──",
    "pause": "🟡 Pause ──",
    "fertig": "🟢 Fertig ──",
}
STATUS_PREFIX = {"in_bearbeitung": "🟠", "pause": "🟡", "fertig": "🟢"}


async def set_ticket_status(channel: discord.TextChannel, status: str):
    guild = channel.guild
    category_name = STATUS_CATEGORIES[status]
    category = discord.utils.get(guild.categories, name=category_name) or await guild.create_category(category_name)
    await channel.edit(category=category, sync_permissions=True)

    base_name = channel.name
    for emoji in STATUS_PREFIX.values():
        if base_name.startswith(emoji + "-") or base_name.startswith(emoji):
            base_name = base_name.lstrip(emoji).lstrip("-")
    new_name = f"{STATUS_PREFIX[status]}-{base_name}"[:90]
    await channel.edit(name=new_name)


class StatusView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="In Bearbeitung", style=discord.ButtonStyle.secondary, emoji="🟠", custom_id="status_bearbeitung")
    async def bearbeitung(self, interaction: discord.Interaction, button: discord.ui.Button):
        await set_ticket_status(interaction.channel, "in_bearbeitung")
        await interaction.response.send_message("🟠 Status: In Bearbeitung", ephemeral=False)

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary, emoji="🟡", custom_id="status_pause")
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        await set_ticket_status(interaction.channel, "pause")
        await interaction.response.send_message("🟡 Status: Pause", ephemeral=False)

    @discord.ui.button(label="Fertig", style=discord.ButtonStyle.success, emoji="🟢", custom_id="status_fertig")
    async def fertig(self, interaction: discord.Interaction, button: discord.ui.Button):
        await set_ticket_status(interaction.channel, "fertig")
        await interaction.response.send_message("🟢 Status: Fertig", ephemeral=False)


class RatingView(discord.ui.View):
    def __init__(self, requester_id: int):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.rating: int | None = None

    async def _handle(self, interaction: discord.Interaction, stars: int):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("❌ Nur der Ticket-Ersteller kann bewerten.", ephemeral=True)
            return
        self.rating = stars
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=f"Danke für deine Bewertung: {'⭐' * stars}", view=self)
        self.stop()

    @discord.ui.button(label="1", emoji="⭐", style=discord.ButtonStyle.secondary)
    async def r1(self, interaction, button):
        await self._handle(interaction, 1)

    @discord.ui.button(label="2", emoji="⭐", style=discord.ButtonStyle.secondary)
    async def r2(self, interaction, button):
        await self._handle(interaction, 2)

    @discord.ui.button(label="3", emoji="⭐", style=discord.ButtonStyle.secondary)
    async def r3(self, interaction, button):
        await self._handle(interaction, 3)

    @discord.ui.button(label="4", emoji="⭐", style=discord.ButtonStyle.secondary)
    async def r4(self, interaction, button):
        await self._handle(interaction, 4)

    @discord.ui.button(label="5", emoji="⭐", style=discord.ButtonStyle.success)
    async def r5(self, interaction, button):
        await self._handle(interaction, 5)


class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Ticket schließen", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        channel = interaction.channel
        member = interaction.user

        support_role = discord.utils.get(guild.roles, name=SUPPORT_ROLE_NAME)
        is_support = support_role in member.roles if support_role else False
        is_owner_of_ticket = channel.topic and str(member.id) in channel.topic

        if not (is_support or is_owner_of_ticket or member.guild_permissions.administrator):
            await interaction.response.send_message("❌ Nur Support oder der Ersteller können dieses Ticket schließen.", ephemeral=True)
            return

        await interaction.response.send_message("🔒 Ticket wird in 60 Sekunden archiviert. Bitte kurz bewerten:", ephemeral=False)

        rating = None
        requester_id = None
        if channel.topic and "|" in channel.topic:
            try:
                requester_id = int(channel.topic.split("Ticket für ")[1].split(" |")[0])
            except (IndexError, ValueError):
                requester_id = None

        if requester_id:
            rating_view = RatingView(requester_id)
            await channel.send(f"<@{requester_id}> Wie zufrieden warst du mit diesem Ticket?", view=rating_view)
            await rating_view.wait()
            rating = rating_view.rating

            if rating:
                reviews_channel = discord.utils.get(guild.text_channels, name=REVIEWS_CHANNEL) or discord.utils.get(guild.text_channels, name="bewertungen")
                if reviews_channel:
                    review_embed = discord.Embed(
                        title="Neue Kundenbewertung",
                        description=f"{'⭐' * rating}{'☆' * (5 - rating)}  ({rating}/5)",
                        color=COLOR_RED,
                    )
                    review_embed.add_field(name="Ticket", value=channel.name, inline=True)
                    review_embed.add_field(name="Kunde", value=f"<@{requester_id}>", inline=True)
                    await reviews_channel.send(embed=review_embed)

        lines = []
        async for msg in channel.history(limit=None, oldest_first=True):
            lines.append(f"[{msg.created_at:%Y-%m-%d %H:%M}] {msg.author}: {msg.content}")
        transcript = "\n".join(lines) or "(keine Nachrichten)"

        log_channel = discord.utils.get(guild.text_channels, name=TICKET_LOG_CHANNEL)
        if log_channel:
            file = discord.File(io.BytesIO(transcript.encode("utf-8")), filename=f"{channel.name}.txt")
            await log_channel.send(content=f"📁 Transcript von {channel.name} (geschlossen von {member})", file=file)

        await channel.delete(reason=f"Ticket geschlossen von {member}")


class ProductButton(discord.ui.Button):
    def __init__(self, key: str, product: dict):
        super().__init__(
            label=f"{product['name']} – ab {product['klein']}€",
            emoji=product["emoji"],
            style=discord.ButtonStyle.primary,
            custom_id=f"order_product:{key}",
        )
        self.product_key = key

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user

        tickets_category = discord.utils.get(guild.categories, name="🎫 TICKETS") or await guild.create_category("🎫 TICKETS")
        support_role = discord.utils.get(guild.roles, name=SUPPORT_ROLE_NAME)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        channel_name = f"ticket-{user.name}-{self.product_key}"[:90]
        ticket_channel = await guild.create_text_channel(
            channel_name,
            category=tickets_category,
            overwrites=overwrites,
            topic=f"Ticket für {user.id} | Produkt: {self.product_key}",
        )

        await interaction.response.send_message(f"✅ Dein Ticket wurde erstellt: {ticket_channel.mention}", ephemeral=True)

        await ticket_channel.send(
            content=f"{user.mention} willkommen! Bitte wähle unten deine Fam-Größe.",
            embed=product_embed(self.product_key),
            view=FamSelectView(self.product_key),
        )
        await ticket_channel.send("Status ändern:", view=StatusView())
        await ticket_channel.send(view=CloseTicketView())
        await set_ticket_status(ticket_channel, "in_bearbeitung")


class ProductMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for key, product in PRODUCTS.items():
            self.add_item(ProductButton(key, product))


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

class VoltBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!volt-", intents=intents)

    async def setup_hook(self):
        self.add_view(ProductMenuView())
        self.add_view(CloseTicketView())
        self.add_view(StatusView())
        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild_obj)
            await self.tree.sync(guild=guild_obj)
        else:
            await self.tree.sync()


bot = VoltBot()


@bot.event
async def on_ready():
    log.info("VOLT-Bot eingeloggt als %s", bot.user)


# ---------------------------------------------------------------------------
# /setup – alles löschen & neu aufbauen
# ---------------------------------------------------------------------------

@bot.tree.command(name="setup", description="[Admin] Löscht ALLE Kanäle & Rollen und baut den Server neu auf")
@is_admin()
async def setup_cmd(interaction: discord.Interaction):
    view = ConfirmView(interaction.user.id)
    await interaction.response.send_message(
        "⚠️ **Das löscht wirklich ALLE Kanäle und Rollen** (außer von Discord/Bots verwalteten) "
        "und baut sie neu auf. Das kann nicht rückgängig gemacht werden.\n\nBist du sicher?",
        view=view,
        ephemeral=True,
    )
    await view.wait()
    if not view.value:
        return

    guild = interaction.guild

    # 0) Geschützte Kategorien sammeln: dieser Bot's eigene Ticket-Kategorien
    #    (laufende Bestellungen) + alles, was in PROTECTED_CATEGORY_NAMES steht
    #    (z.B. euer separater Ticket-Bot). Diese werden NICHT angefasst.
    protected_category_names = {"🎫 TICKETS", *STATUS_CATEGORIES.values(), *_extra_protected_categories}
    protected_role_names = {ADMIN_ROLE_NAME, MOD_ROLE_NAME, SUPPORT_ROLE_NAME, "Kunde", *_extra_protected_roles}
    # ^ die eigenen Rollennamen stehen hier nicht, weil wir die bewusst neu
    #   erstellen wollen - siehe Schritt 2. Nur die EXTRA-Rollen (anderer Bot)
    #   werden komplett übersprungen.

    protected_channels = [
        ch for ch in guild.channels
        if (ch.category and ch.category.name in protected_category_names)
        or (isinstance(ch, discord.CategoryChannel) and ch.name in protected_category_names)
    ]
    protected_channel_ids = {ch.id for ch in protected_channels}
    skipped_channels = len(protected_channels)

    # 1) Kanäle löschen (außer geschützte)
    for channel in list(guild.channels):
        if channel.id in protected_channel_ids:
            continue
        try:
            await channel.delete(reason="VOLT /setup Rebuild")
        except discord.HTTPException as e:
            log.warning("Kanal %s konnte nicht gelöscht werden: %s", channel, e)

    # 2) Rollen löschen (außer @everyone, von Discord verwalteten Rollen und
    #    den explizit geschützten Rollen des anderen Bots)
    skipped_roles = 0
    for role in list(guild.roles):
        if role.is_default() or role.managed:
            continue
        if role >= guild.me.top_role:
            continue  # kann der Bot ohnehin nicht löschen
        if role.name in _extra_protected_roles:
            skipped_roles += 1
            continue
        try:
            await role.delete(reason="VOLT /setup Rebuild")
        except discord.HTTPException as e:
            log.warning("Rolle %s konnte nicht gelöscht werden: %s", role, e)

    # 3) Rollen neu anlegen (Reihenfolge = ROLE_CONFIG, oben = höchste Rolle)
    created_roles: dict[str, discord.Role] = {}
    for cfg in ROLE_CONFIG:
        role = await guild.create_role(
            name=cfg["name"],
            hoist=cfg["hoist"],
            mentionable=cfg["mentionable"],
            permissions=cfg["permissions"],
            reason="VOLT /setup Rebuild",
        )
        await apply_role_colour(role, cfg["colour"], cfg["gradient_to"])
        created_roles[cfg["name"]] = role

    admin_role = created_roles[ADMIN_ROLE_NAME]
    mod_role = created_roles[MOD_ROLE_NAME]
    support_role = created_roles[SUPPORT_ROLE_NAME]
    staff_roles = [admin_role, mod_role, support_role]

    # 3.5) Zugriff auf geschützte Ticket-Kanäle DIESES Bots (🎫 TICKETS,
    #      🟠/🟡/🟢-Status) für die neuen Staff-Rollen wiederherstellen.
    #      Der andere Ticket-Bot (PROTECTED_CATEGORY_NAMES) wird hier NICHT
    #      angefasst, damit dessen eigene Rollen/Overwrites unangetastet bleiben.
    own_ticket_category_names = {"🎫 TICKETS", *STATUS_CATEGORIES.values()}
    for ch in protected_channels:
        if isinstance(ch, discord.CategoryChannel):
            continue
        cat_name = ch.category.name if ch.category else None
        if cat_name not in own_ticket_category_names:
            continue
        try:
            overwrites = dict(ch.overwrites)
            for r in staff_roles:
                overwrites[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            await ch.edit(overwrites=overwrites, reason="VOLT /setup: Staff-Zugriff nach Rollen-Rebuild erneuern")
        except discord.HTTPException as e:
            log.warning("Konnte Overwrites für %s nicht erneuern: %s", ch, e)

    # 4) Kategorien & Kanäle neu anlegen
    bestellen_channel = None
    for cat_name, channels in CHANNEL_STRUCTURE:
        category = await guild.create_category(cat_name, reason="VOLT /setup Rebuild")
        for chan_name, staff_only in channels:
            overwrites = {}
            if staff_only:
                overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
                for r in staff_roles:
                    overwrites[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
            new_channel = await guild.create_text_channel(chan_name, category=category, overwrites=overwrites)
            if chan_name == "bestellen":
                bestellen_channel = new_channel

    # 5) Bestell-Menü automatisch in #bestellen posten
    if bestellen_channel:
        embed = discord.Embed(
            title="🛒 Bestellung",
            description="Wähle unten das gewünschte Produkt aus, um ein Ticket zu eröffnen.",
            color=COLOR_RED,
        )
        await bestellen_channel.send(embed=embed, view=ProductMenuView())

    skip_note = ""
    if skipped_channels or skipped_roles:
        skip_note = f"\nℹ️ Übersprungen (nicht angefasst): {skipped_channels} Kanäle/Kategorien, {skipped_roles} Rollen (Ticket-System / geschützte Rollen)."

    await interaction.followup.send(
        f"✅ Server wurde neu aufgebaut: {len(ROLE_CONFIG)} Rollen, "
        f"{sum(len(c) for _, c in CHANNEL_STRUCTURE)} Kanäle in {len(CHANNEL_STRUCTURE)} Kategorien."
        f"{skip_note}",
        ephemeral=True,
    )


@setup_cmd.error
async def on_setup_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ Dieser Command ist nur für Administratoren.", ephemeral=True)
    else:
        log.exception("Fehler in /setup", exc_info=error)
        target = interaction.followup if interaction.response.is_done() else interaction.response
        await target.send_message("❌ Es ist ein Fehler aufgetreten.", ephemeral=True) if not interaction.response.is_done() \
            else await interaction.followup.send("❌ Es ist ein Fehler aufgetreten.", ephemeral=True)


# ---------------------------------------------------------------------------
# /post-preisliste (unverändert, zusätzlicher Admin-Command)
# ---------------------------------------------------------------------------

@bot.tree.command(name="post-preisliste", description="[Admin] Postet die vollständige Preisliste in diesen Kanal")
@is_admin()
async def post_preisliste(interaction: discord.Interaction):
    intro = discord.Embed(
        title="💰 VOLT – Preisliste",
        description="Übersicht aller Leistungen. Preise gelten pro Fam-Größe, siehe Angaben.",
        color=COLOR_RED,
    )
    await interaction.channel.send(embed=intro)
    for key in PRODUCTS:
        await interaction.channel.send(embed=product_embed(key))
    await interaction.response.send_message("✅ Preisliste gepostet.", ephemeral=True)


# ---------------------------------------------------------------------------
# /ban
# ---------------------------------------------------------------------------

@bot.tree.command(name="ban", description="[Mod] Bannt ein Mitglied vom Server")
@app_commands.describe(
    mitglied="Das zu bannende Mitglied",
    grund="Grund für den Bann (wird dem Mitglied und im Log angezeigt)",
    nachrichten_loeschen_tage="Nachrichten der letzten X Tage mit löschen (0-7, Standard 0)",
)
@app_commands.checks.has_permissions(ban_members=True)
async def ban_cmd(
    interaction: discord.Interaction,
    mitglied: discord.Member,
    grund: str = "Kein Grund angegeben",
    nachrichten_loeschen_tage: app_commands.Range[int, 0, 7] = 0,
):
    guild = interaction.guild
    moderator = interaction.user

    if mitglied.id == moderator.id:
        await interaction.response.send_message("❌ Du kannst dich nicht selbst bannen.", ephemeral=True)
        return
    if mitglied.top_role >= moderator.top_role and moderator.id != guild.owner_id:
        await interaction.response.send_message("❌ Du kannst niemanden mit gleicher oder höherer Rolle bannen.", ephemeral=True)
        return
    if mitglied.top_role >= guild.me.top_role:
        await interaction.response.send_message("❌ Meine Rolle ist zu niedrig, um dieses Mitglied zu bannen.", ephemeral=True)
        return

    dm_sent = True
    try:
        dm_embed = discord.Embed(
            title=f"Du wurdest von {guild.name} gebannt",
            description=f"**Grund:** {grund}",
            color=COLOR_RED,
        )
        await mitglied.send(embed=dm_embed)
    except discord.HTTPException:
        dm_sent = False

    await guild.ban(mitglied, reason=f"{moderator}: {grund}", delete_message_days=nachrichten_loeschen_tage)

    result_embed = discord.Embed(
        title="🔨 Mitglied gebannt",
        color=COLOR_RED,
        timestamp=datetime.now(timezone.utc),
    )
    result_embed.add_field(name="Mitglied", value=f"{mitglied} (`{mitglied.id}`)", inline=False)
    result_embed.add_field(name="Moderator", value=str(moderator), inline=True)
    result_embed.add_field(name="Grund", value=grund, inline=True)
    result_embed.add_field(name="DM zugestellt", value="Ja" if dm_sent else "Nein (DMs deaktiviert)", inline=True)

    await interaction.response.send_message(embed=result_embed)

    mod_log = discord.utils.get(guild.text_channels, name=MOD_LOG_CHANNEL)
    if mod_log:
        await mod_log.send(embed=result_embed)


@ban_cmd.error
async def on_ban_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ Dieser Command ist nur für Moderatoren/Admins.", ephemeral=True)
    else:
        log.exception("Fehler in /ban", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("❌ Es ist ein Fehler aufgetreten.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Es ist ein Fehler aufgetreten.", ephemeral=True)


# ---------------------------------------------------------------------------
# Message-Logging: jede Bearbeitung/Löschung wird protokolliert
# ---------------------------------------------------------------------------

def _truncate(text: str, limit: int = 1000) -> str:
    text = text or "*(kein Text / nur Anhang oder Embed)*"
    return text if len(text) <= limit else text[: limit - 3] + "..."


async def _get_message_log_channel(guild: discord.Guild) -> discord.TextChannel | None:
    return discord.utils.get(guild.text_channels, name=MESSAGE_LOG_CHANNEL)


@bot.event
async def on_message_delete(message: discord.Message):
    if message.guild is None:
        return
    log_channel = await _get_message_log_channel(message.guild)
    if log_channel is None or message.channel.id == log_channel.id:
        return

    embed = discord.Embed(
        title="🗑️ Nachricht gelöscht",
        color=discord.Colour.dark_red(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Autor", value=f"{message.author} (`{message.author.id}`)" if message.author else "Unbekannt", inline=True)
    embed.add_field(name="Kanal", value=message.channel.mention, inline=True)
    embed.add_field(name="Inhalt", value=_truncate(message.content), inline=False)
    if message.attachments:
        embed.add_field(
            name="Anhänge",
            value="\n".join(a.filename for a in message.attachments)[:1000],
            inline=False,
        )
    embed.set_footer(text=f"Nachrichten-ID: {message.id}")

    try:
        await log_channel.send(embed=embed)
    except discord.HTTPException as e:
        log.warning("Konnte Delete-Log nicht senden: %s", e)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.guild is None or before.content == after.content:
        return  # Embeds/Link-Previews lösen ebenfalls edit aus, ohne echten Textwechsel
    log_channel = await _get_message_log_channel(before.guild)
    if log_channel is None or before.channel.id == log_channel.id:
        return

    embed = discord.Embed(
        title="✏️ Nachricht bearbeitet",
        color=discord.Colour.orange(),
        timestamp=datetime.now(timezone.utc),
        url=after.jump_url,
    )
    embed.add_field(name="Autor", value=f"{before.author} (`{before.author.id}`)", inline=True)
    embed.add_field(name="Kanal", value=before.channel.mention, inline=True)
    embed.add_field(name="Vorher", value=_truncate(before.content), inline=False)
    embed.add_field(name="Nachher", value=_truncate(after.content), inline=False)
    embed.set_footer(text=f"Nachrichten-ID: {before.id}")

    try:
        await log_channel.send(embed=embed)
    except discord.HTTPException as e:
        log.warning("Konnte Edit-Log nicht senden: %s", e)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN fehlt in der .env Datei!")
    bot.run(TOKEN)
