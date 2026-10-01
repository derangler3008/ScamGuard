"""Einheitliches Datenschema.

Jeder Datensatz – egal ob von Hugging Face, aus einer CSV oder selbst gelabelt –
wird in ein `Listing` übersetzt. Alle Modelle arbeiten nur mit diesem Schema.
"""

from __future__ import annotations

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
    "sonstiges",
]


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
    # Nachrichten des Anbieters (z. B. kopierter Chat) – hier stecken oft Links/Mails
    messages: list[str] = field(default_factory=list)
    image_paths: list[str] = field(default_factory=list)
    # Nur für Trainingsdaten gesetzt
    label: int | None = None
    scam_type: str | None = None  # z. B. "fake_paypal", "vorkasse", "dreieck", "phishing_link"
    source: str | None = None     # Herkunft des Datensatzes (für Auswertung pro Quelle)
    url: str | None = None        # Adresse des Inserats (z. B. beim Labeln aus der Extension)

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            self.category = "sonstiges"

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
