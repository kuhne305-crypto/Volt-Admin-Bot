"""
TICKET-BOT
==========
Zuständig für das Bestell-System. Postet Buttons in #bestellen -> Klick
öffnet ein privates Ticket, in dem Big/Klein Fam ausgewählt wird und der
passende Preis angezeigt wird. Danach kann das Ticket geschlossen werden
(Transcript wird in #ticket-logs gespeichert).

Einrichtung:
1. pip install -r requirements.txt
2. .env.example -> .env kopieren und ausfüllen
3. python bot.py
4. Im Kanal #bestellen einmalig /setup-tickets ausführen
"""

import os
import logging

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from products import PRODUCTS, HOSTING, TERMS

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("ticket-bot")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
SUPPORT_ROLE_NAME = os.getenv("SUPPORT_ROLE_NAME", "Support")

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!ticket-", intents=intents)


def is_admin():
    return app_commands.checks.has_permissions(administrator=True)


async def get_or_create_category(guild: discord.Guild, name: str) -> discord.CategoryChannel:
    cat = discord.utils.get(guild.categories, name=name)
    return cat or await guild.create_category(name)


async def get_or_create_channel(guild: discord.Guild, name: str, category=None, overwrites=None) -> discord.TextChannel:
    ch = discord.utils.get(guild.text_channels, name=name)
    if ch is None:
        ch = await guild.create_text_channel(name, category=category, overwrites=overwrites or {})
    return ch


def product_embed(key: str) -> discord.Embed:
    p = PRODUCTS[key]
    embed = discord.Embed(
        title=f"{p['emoji']} {p['name']}",
        color=discord.Color.gold(),
    )
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
STATUS_PREFIX = {
    "in_bearbeitung": "🟠",
    "pause": "🟡",
    "fertig": "🟢",
}


async def set_ticket_status(channel: discord.TextChannel, status: str):
    guild = channel.guild
    category_name = STATUS_CATEGORIES[status]
    category = discord.utils.get(guild.categories, name=category_name) or await guild.create_category(category_name)
    await channel.edit(category=category, sync_permissions=True)

    # bisheriges Status-Emoji am Anfang des Namens entfernen, neues voranstellen
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
    """Sterne-Bewertung, die der Kunde vor dem Schließen abgeben kann."""

    def __init__(self, requester_id: int):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.rating: int | None = None
        self.message: discord.Message | None = None

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

        # Bewertung abfragen, bevor das Ticket wirklich geschlossen wird
        rating = None
        requester_id = None
        if channel.topic and "|" in channel.topic:
            try:
                requester_id = int(channel.topic.split("Ticket für ")[1].split(" |")[0])
            except (IndexError, ValueError):
                requester_id = None

        if requester_id:
            rating_view = RatingView(requester_id)
            rating_msg = await channel.send(
                f"<@{requester_id}> Wie zufrieden warst du mit diesem Ticket?", view=rating_view
            )
            await rating_view.wait()
            rating = rating_view.rating

            if rating:
                reviews_channel = discord.utils.get(guild.text_channels, name="⭐・bewertungen") or discord.utils.get(guild.text_channels, name="bewertungen")
                if reviews_channel:
                    review_embed = discord.Embed(
                        title="Neue Kundenbewertung",
                        description=f"{'⭐' * rating}{'☆' * (5 - rating)}  ({rating}/5)",
                        color=discord.Color.from_str("#E30613"),
                    )
                    review_embed.add_field(name="Ticket", value=channel.name, inline=True)
                    review_embed.add_field(name="Kunde", value=f"<@{requester_id}>", inline=True)
                    await reviews_channel.send(embed=review_embed)

        # Transcript zusammenbauen
        lines = []
        async for msg in channel.history(limit=None, oldest_first=True):
            lines.append(f"[{msg.created_at:%Y-%m-%d %H:%M}] {msg.author}: {msg.content}")
        transcript = "\n".join(lines) or "(keine Nachrichten)"

        log_channel = discord.utils.get(guild.text_channels, name="ticket-logs")
        if log_channel:
            import io
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

        tickets_category = await get_or_create_category(guild, "🎫 TICKETS")
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


class TicketBot(commands.Bot):
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


bot = TicketBot(command_prefix="!ticket-", intents=intents)


@bot.event
async def on_ready():
    log.info("Ticket-Bot eingeloggt als %s", bot.user)


@bot.tree.command(name="setup-tickets", description="[Admin] Postet das Bestell-Menü in diesen Kanal")
@is_admin()
async def setup_tickets(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛒 Bestellung",
        description="Wähle unten das gewünschte Produkt aus, um ein Ticket zu eröffnen.",
        color=discord.Color.blurple(),
    )
    await interaction.channel.send(embed=embed, view=ProductMenuView())
    await interaction.response.send_message("✅ Bestell-Menü wurde gepostet.", ephemeral=True)


@bot.tree.command(name="post-preisliste", description="[Admin] Postet die vollständige Preisliste in diesen Kanal")
@is_admin()
async def post_preisliste(interaction: discord.Interaction):
    accent = discord.Color.from_str("#E30613")
    intro = discord.Embed(
        title="💰 VOLT – Preisliste",
        description="Übersicht aller Leistungen. Preise gelten pro Fam-Größe, siehe Angaben.",
        color=accent,
    )
    await interaction.channel.send(embed=intro)
    for key in PRODUCTS:
        await interaction.channel.send(embed=product_embed(key))
    await interaction.response.send_message("✅ Preisliste gepostet.", ephemeral=True)


@setup_tickets.error
async def on_ticket_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ Dieser Command ist nur für Administratoren.", ephemeral=True)
    else:
        log.exception("Fehler in Ticket-Command", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("❌ Es ist ein Fehler aufgetreten.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Es ist ein Fehler aufgetreten.", ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN fehlt in der .env Datei!")
    bot.run(TOKEN)
