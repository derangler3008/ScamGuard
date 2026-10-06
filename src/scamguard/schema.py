"""Einheitliches Datenschema.

Jeder Datensatz – egal ob von Hugging Face, aus einer CSV oder selbst gelabelt –
wird in ein `Listing` übersetzt. Alle Modelle arbeiten nur mit diesem Schema.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from enum import IntEnum
from typing import Any


class Label(IntEnum):
    LEGIT = 0
    SCAM = 1


# Kategorien, auf die wir uns konzentrieren. Unbekannte Werte → "sonstiges".
CATEGORIES = [
    "elektronik",
    "haushaltsgeraete",
    "auto",
    "moebel",
    "mode",
    "tiere",
    "immobilien",
    "tickets",
    "dienstleistungen",
    "sonstiges",
]
CATEGORY_NAMES = {
    "elektronik": "Elektronik", "haushaltsgeraete": "Haushaltsgeräte", "auto": "Auto & Fahrzeuge",
    "moebel": "Möbel", "mode": "Mode", "tiere": "Tiere", "immobilien": "Immobilien",
    "tickets": "Tickets", "dienstleistungen": "Dienstleistungen", "sonstiges": "Sonstiges",
}

# Abzeichen, die Kleinanzeigen beim Anbieter anzeigt (Schlüssel → Anzeigename)
SELLER_BADGES = {
    "zufriedenheit_top": "TOP Zufriedenheit", "zufriedenheit_ok": "OK Zufriedenheit",
    "zufriedenheit_naja": "NA JA Zufriedenheit", "sehr_freundlich": "Sehr freundlich",
    "freundlich": "Freundlich", "besonders_zuverlaessig": "Besonders zuverlässig", "zuverlaessig": "Zuverlässig",
}
SELLER_TYPES = ("privat", "gewerblich")
# Rechtsform am Ende eines Anbieter- bzw. Firmennamens („Muster Immobilien GmbH“). Gespeichert wird
# nur die Rechtsform, nie der Name (Namen von Privatpersonen sind personenbezogene Daten).
LEGAL_FORM = re.compile(
    r"(?<![\w.])(GmbH\s*&\s*Co\.?\s*KG|gGmbH|GmbH|UG(?:\s*\(haftungsbeschränkt\))?|AG|SE|KGaA|KG|OHG|GbR|"
    r"PartG(?:mbB)?|eG|e\.\s?K\.|e\.\s?Kfm\.|e\.\s?V\.|Ltd\.?|Inc\.?|LLC|S\.?à\s?r\.?l\.?)\s*$")


def legal_form_of(name: str | None) -> str | None:
    """Rechtsform am Namensende: „Deutsche Reihenhaus AG“ → „AG“; sonst None."""
    m = LEGAL_FORM.search(re.sub(r"\s+", " ", str(name or "")).strip()[:80])
    return re.sub(r"\s+", " ", m.group(1)) if m else None


def badge_key(text: str) -> str | None:
    """„TOP Zufriedenheit“ → zufriedenheit_top, „Besonders zuverlässig“ → besonders_zuverlaessig."""
    t = re.sub(r"\s+", " ", str(text)).strip().lower().replace("ä", "ae")
    if t in SELLER_BADGES:
        return t
    if "zufriedenheit" in t:
        level = "top" if "top" in t else "naja" if re.search(r"na ?ja", t) else "ok" if re.search(r"\bok\b", t) else None
        return f"zufriedenheit_{level}" if level else None
    for key in ("sehr_freundlich", "freundlich", "besonders_zuverlaessig", "zuverlaessig"):
        if t == key.replace("_", " "):
            return key
    return None


def seller_type_of(text: str | None) -> str | None:
    """„Gewerblicher Nutzer“ → gewerblich, „Privater Nutzer“ → privat."""
    t = str(text or "").strip().lower()
    return "gewerblich" if t.startswith("gewerb") else "privat" if t.startswith("privat") else None


@dataclass
class Listing:
    """Ein Kleinanzeigen-Inserat (plus optional Chatverlauf mit dem Anbieter)."""

    title: str = ""
    description: str = ""
    price: float | None = None
    category: str = "sonstiges"
    location: str | None = None
    seller_name: str | None = None
    seller_account_age_days: int | None = None
    seller_num_ratings: int | None = None
    seller_type: str | None = None    # "privat" | "gewerblich" (wie auf der Anzeige angegeben)
    seller_badges: list[str] = field(default_factory=list)  # Schlüssel aus SELLER_BADGES
    seller_num_ads: int | None = None  # Anzahl Anzeigen des Anbieters
    seller_legal_form: str | None = None  # Rechtsform aus dem Anbieternamen (GmbH, UG, GbR …), ohne Namen
    # Chat-Nachrichten (kopierter Chat oder Kleinanzeigen-Postfach) – hier stecken oft Links/Mails
    messages: list[str] = field(default_factory=list)
    image_paths: list[str] = field(default_factory=list)
    # Nur für Trainingsdaten gesetzt
    label: int | None = None
    scam_type: str | None = None  # z. B. "fake_paypal", "vorkasse", "dreieck", "phishing_link"
    source: str | None = None     # Herkunft des Datensatzes (für Auswertung pro Quelle)
    url: str | None = None        # Adresse des Inserats (z. B. beim Labeln aus der Extension)
    # Satzgenaue Warnsignale aus der Einstufung: [{"signal": "zeitdruck", "text": "<Satz>"}]
    warnsignale: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            self.category = "sonstiges"
        # Werte kommen auch von Clients (Extension) → nur Bekanntes übernehmen
        self.seller_type = seller_type_of(self.seller_type)
        badges = self.seller_badges if isinstance(self.seller_badges, (list, tuple)) else []
        self.seller_badges = list(dict.fromkeys(k for b in badges if (k := badge_key(b))))
        try:
            self.seller_num_ads = int(self.seller_num_ads) if self.seller_num_ads is not None else None
        except (TypeError, ValueError):
            self.seller_num_ads = None
        self.seller_legal_form = legal_form_of(self.seller_legal_form)

    @property
    def chat_only(self) -> bool:
        """Nur Nachrichten, kein Inserat (z. B. Scan im Kleinanzeigen-Postfach)."""
        return bool(self.messages) and not (self.title.strip() or self.description.strip())

    @property
    def full_text(self) -> str:
        """Titel + Beschreibung + Nachrichten als ein Text (Eingabe für Textmodelle)."""
        parts = [self.title, self.description, *self.messages]
        return "\n".join(p.strip() for p in parts if p and p.strip())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Listing:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Signal:
    """Ein einzelnes, für Menschen erklärbares Warnsignal."""

    source: str        # "rules", "text_model", "image_model", "llm"
    code: str          # maschinenlesbar, z. B. "EXTERNAL_PAYMENT"
    message: str       # deutsche Erklärung für das Frontend
    weight: float      # Schwere 0..1
    evidence: str | None = None  # gefundene Textstelle, URL o. Ä. (zur Anzeige, ggf. gekürzt)
    hard: bool = False  # sehr starkes Indiz (z. B. Fake-Bezahlseite)
    # Für die Browser-Extension: exakte Originaltexte zum Markieren auf der Seite …
    highlights: list[str] = field(default_factory=list)
    # … oder ein Seitenelement statt Text: "price", "seller", "image:<n>" (n = Index in image_paths)
    target: str | None = None
    # Nur Kontext (z. B. Anbieterprofil, gewerblicher Anbieter) – wird angezeigt, ist aber kein Warnsignal
    info: bool = False


@dataclass
class ModelResult:
    """Ergebnis eines Einzelmodells."""

    name: str
    score: float | None          # P(Betrug) in [0, 1]; None = kein Urteil
    signals: list[Signal] = field(default_factory=list)
    available: bool = True
    error: str | None = None


@dataclass
class ScanResult:
    """Gesamtergebnis eines Scans."""

    score: float
    verdict: str                 # "unauffällig" | "verdächtig" | "hohes Risiko"
    model_results: list[ModelResult]
    signals: list[Signal]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
