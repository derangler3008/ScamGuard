"""Eigene Labels: Inserate, die ihr selbst als Betrug oder seriös eingestuft habt.

Quellen: Buttons im Panel der Browser-Extension (POST /label) und der Tab „Labeln“ im Streamlit-
Frontend. Gespeichert wird in data/raw/eigene_labels.jsonl (Registry: „eigene_labels“), Bilder in
data/images/eigene_labels/. Wird ein Inserat erneut gelabelt (gleiche URL bzw. gleicher Text),
ersetzt das neue Label das alte – so lassen sich Fehlklicks korrigieren.
"""

from __future__ import annotations

import hashlib
import threading

from scamguard.config import resolve_path
from scamguard.data.build import listing_fingerprint, read_jsonl, write_jsonl
from scamguard.schema import Listing

LABEL_FILE = "data/raw/eigene_labels.jsonl"
LABEL_IMAGE_DIR = "data/images/eigene_labels"
_lock = threading.Lock()  # Server bearbeitet Anfragen parallel


def save_label_images(images: list[tuple[str, bytes]]) -> list[str]:
    """Speichert Bilder unter ihrem Inhalts-Hash (gleiches Bild = gleiche Datei).
    Rückgabe: Pfade relativ zum Projektordner."""
    folder = resolve_path(LABEL_IMAGE_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for suffix, content in images:
        target = folder / f"{hashlib.sha1(content).hexdigest()}{suffix}"
        if not target.exists():
            target.write_bytes(content)
        paths.append(f"{LABEL_IMAGE_DIR}/{target.name}")
    return paths


def save_label(listing: Listing) -> int:
    """Speichert bzw. ersetzt das Label. Rückgabe: Anzahl eigener Labels."""
    path = resolve_path(LABEL_FILE)
    fingerprint = listing_fingerprint(listing)
    with _lock:
        existing = read_jsonl(path) if path.exists() else []
        kept = [l for l in existing
                if not ((listing.url and l.url == listing.url) or listing_fingerprint(l) == fingerprint)]
        kept.append(listing)
        tmp = path.with_suffix(".tmp")  # atomar ersetzen, nie eine halb geschriebene Datei
        write_jsonl(kept, tmp)
        tmp.replace(path)
        return len(kept)


def count_labels() -> dict[str, int]:
    path = resolve_path(LABEL_FILE)
    if not path.exists():
        return {"gesamt": 0, "betrug": 0, "serioes": 0}
    labels = [l.label for l in read_jsonl(path)]
    return {"gesamt": len(labels), "betrug": labels.count(1), "serioes": labels.count(0)}
