"""Preisplausibilität (Anbieter-Merkmale: features/seller.py)."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from scamguard.schema import Listing, Signal

# Den Referenzpreis hat nur der Artikel selbst: Zubehör, Defektes, Such-, Tausch- und Mietanzeigen
# („PS5 Controller“, „Hülle für iPhone 15 Pro“, „Suche Thermomix“) bleiben beim Preisvergleich außen vor.
# „Spiele“ nur ohne „mit“, „und“, „inkl.“, „+“ oder Anzahl davor: „PS5 + 2 Spiele“ ist die Konsole,
# „PS5 Spiele Paket“ nicht.
NOT_THE_ITEM = re.compile(
    r"\b(hülle|case|cover|folie|schutzfolie|panzerglas|schutzglas|displayschutz|kabel|ladekabel|ladegerät|"
    r"netzteil|adapter|akku|controller|fernbedienung|halterung|ständer|tasche|karton|verpackung|zubehör|"
    r"ersatzteile?|defekt|kaputt|bastler|reparatur|mainboard|platine|skin|aufkleber|spiel|game|"
    r"suche|gesucht|tausche?|vermiete\w*|verleih\w*|deckrüde|deckkater|tierschutz|schutzgebühr)\b"
    r"|(?<!mit )(?<!und )(?<!inkl\. )(?<!\+ )(?<!\s\d )\bspiele\b", re.IGNORECASE)


@lru_cache(maxsize=4)
def _load_reference(path: str) -> list[tuple[re.Pattern, float, float]]:
    with Path(path).open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return [(re.compile(i["pattern"], re.IGNORECASE), float(i["min"]), float(i["max"]))
            for i in raw.get("items", [])]


def reference_range(listing: Listing, reference_path: str) -> tuple[float, float] | None:
    """Sucht im Titel (dann in der Beschreibung) nach einem bekannten Artikel."""
    if NOT_THE_ITEM.search(listing.title or ""):
        return None
    items = _load_reference(reference_path)
    for text in (listing.title, listing.description):
        for pattern, lo, hi in items:
            if pattern.search(text or ""):
                return lo, hi
    return None


def analyze_price(listing: Listing, reference_path: str) -> tuple[dict[str, float], list[Signal]]:
    signals: list[Signal] = []
    features = {"price": listing.price or 0.0, "price_ratio_to_min": 1.0, "price_known_item": 0.0}
    src = "rules"

    ref = reference_range(listing, reference_path)
    if listing.price is not None and ref:
        lo, _hi = ref
        ratio = listing.price / lo if lo else 1.0
        features["price_ratio_to_min"] = ratio
        features["price_known_item"] = 1.0
        if 0 < ratio < 0.4:
            signals.append(Signal(src, "PRICE_FAR_TOO_LOW",
                                  f"Preis liegt weit unter dem üblichen Gebrauchtpreis (ab ca. {lo:.0f} €)",
                                  0.55, evidence=f"{listing.price:.0f} €", target="price"))
        elif 0 < ratio < 0.65:
            signals.append(Signal(src, "PRICE_LOW",
                                  f"Preis deutlich unter dem üblichen Gebrauchtpreis (ab ca. {lo:.0f} €)",
                                  0.25, evidence=f"{listing.price:.0f} €", target="price"))

    return features, signals
