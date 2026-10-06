"""Preisplausibilität (Anbieter-Merkmale: features/seller.py)."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from scamguard.schema import Listing, Signal


@lru_cache(maxsize=4)
def _load_reference(path: str) -> list[tuple[re.Pattern, float, float]]:
    with Path(path).open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return [(re.compile(i["pattern"], re.IGNORECASE), float(i["min"]), float(i["max"]))
            for i in raw.get("items", [])]


def reference_range(listing: Listing, reference_path: str) -> tuple[float, float] | None:
    """Sucht im Titel (dann in der Beschreibung) nach einem bekannten Artikel."""
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
