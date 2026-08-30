"""
VOLT - zentrales Branding.

Alle Bots/Module importieren Farben & Assets von hier, damit überall
konsistent "VOLT" statt generischer Namen auftaucht und die Banner/Icons
aus assets/ überall gleich eingebunden werden.
"""

import os
import discord

VOLT_RED = discord.Color.from_rgb(224, 17, 17)   # Rot aus den Bannern
VOLT_BLACK = discord.Color.from_rgb(10, 10, 10)

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")

ICON = os.path.join(ASSETS_DIR, "volt_icon.png")
MAIN_BANNER = os.path.join(ASSETS_DIR, "volt_main_banner.png")          # "VOLT DISCORD SOLUTIONS"
ADMIN_BANNER = os.path.join(ASSETS_DIR, "volt_admin_banner.png")        # "VOLT ADMIN"
TICKETS_BANNER = os.path.join(ASSETS_DIR, "volt_tickets_banner.png")    # "VOLT TICKETS"

ADMIN_FOOTER = "VOLT ADMIN • Server Protection. Full Control."
TICKETS_FOOTER = "VOLT TICKETS • Tickets. Orders. Done right."
MAIN_FOOTER = "VOLT Discord Solutions"


def banner_file(path: str) -> discord.File:
    """Lädt ein Asset als discord.File - Dateiname bleibt erhalten, damit
    embed.set_image(url='attachment://<dateiname>') darauf zeigen kann."""
    return discord.File(path, filename=os.path.basename(path))


def with_admin_banner(embed: discord.Embed) -> tuple[discord.Embed, discord.File]:
    file = banner_file(ADMIN_BANNER)
    embed.set_image(url=f"attachment://{os.path.basename(ADMIN_BANNER)}")
    if not embed.footer:
        embed.set_footer(text=ADMIN_FOOTER)
    return embed, file


def with_tickets_banner(embed: discord.Embed) -> tuple[discord.Embed, discord.File]:
    file = banner_file(TICKETS_BANNER)
    embed.set_image(url=f"attachment://{os.path.basename(TICKETS_BANNER)}")
    if not embed.footer:
        embed.set_footer(text=TICKETS_FOOTER)
    return embed, file


def with_main_banner(embed: discord.Embed) -> tuple[discord.Embed, discord.File]:
    file = banner_file(MAIN_BANNER)
    embed.set_image(url=f"attachment://{os.path.basename(MAIN_BANNER)}")
    if not embed.footer:
        embed.set_footer(text=MAIN_FOOTER)
    return embed, file


def with_icon_thumbnail(embed: discord.Embed) -> tuple[discord.Embed, discord.File]:
    file = banner_file(ICON)
    embed.set_thumbnail(url=f"attachment://{os.path.basename(ICON)}")
    return embed, file
