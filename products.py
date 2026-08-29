"""
products.py
============
Produktkatalog für den Ticket-Bot. Diese Datei hat vorher gefehlt
(daher der ModuleNotFoundError) und wurde hier als Vorlage neu angelegt.

WICHTIG: Preise/Namen unten sind nur Platzhalter ("XX") – bitte mit den
echten VOLT-Preisen ersetzen, bevor der Bot live geht.

Struktur pro Produkt:
    "key": {
        "name": "Anzeigename",
        "emoji": "🔥",
        "big": <Preis Big Fam in €>,
        "klein": <Preis Klein Fam in €>,
    }
"""

PRODUCTS = {
    "komplett": {
        "name": "Komplett-Paket",
        "emoji": "🏆",
        "big": 0,      # TODO: echten Preis eintragen
        "klein": 0,    # TODO: echten Preis eintragen
    },
    "basic": {
        "name": "Basic-Paket",
        "emoji": "⚡",
        "big": 0,      # TODO: echten Preis eintragen
        "klein": 0,    # TODO: echten Preis eintragen
    },
}

# Monatliche Hosting-Kosten, nur relevant für "komplett" (siehe bot.py)
HOSTING = {
    "big": 0,      # TODO: echten Preis eintragen
    "klein": 0,    # TODO: echten Preis eintragen
}

TERMS = (
    "Zahlung im Voraus per [Zahlungsart eintragen]. "
    "Lieferzeit: ca. X Werktage. Bei Fragen einfach im Ticket melden."
)
