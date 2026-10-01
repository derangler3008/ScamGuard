"""Feature-Extraktion: bündelt alle regelbasierten Merkmale eines Inserats."""

from __future__ import annotations

from dataclasses import dataclass, field

from scamguard.config import resolve_path
from scamguard.features.contact import analyze_contacts
from scamguard.features.language import analyze_language
from scamguard.features.price import analyze_price
from scamguard.features.scam_phrases import match_phrases
from scamguard.schema import Listing, Signal


@dataclass
class ListingFeatures:
    vector: dict[str, float] = field(default_factory=dict)  # numerisch, z. B. für ein Tabular-Modell
    signals: list[Signal] = field(default_factory=list)      # erklärbare Warnsignale


def extract_features(listing: Listing, cfg: dict) -> ListingFeatures:
    text = listing.full_text
    lexicon = str(resolve_path(cfg["paths"]["lexicon"]))
    price_ref = str(resolve_path(cfg["paths"]["price_reference"]))

    contact = analyze_contacts(text)
    language = analyze_language(text)
    price_vec, price_signals = analyze_price(listing, price_ref)
    phrase_signals = match_phrases(text, lexicon)

    vector = {**contact.as_vector(), **language.as_vector(), **price_vec,
              "n_phrase_groups": float(len(phrase_signals)),
              "n_images": float(len(listing.image_paths))}
    signals = [*phrase_signals, *contact.signals, *price_signals, *language.signals]
    return ListingFeatures(vector=vector, signals=signals)
