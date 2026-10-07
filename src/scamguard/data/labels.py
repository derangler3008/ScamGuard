"""Eigene Labels: Inserate, die selbst als Betrug oder seriös eingestuft wurden.

Quellen: Buttons im Panel der Browser-Extension (POST /label) und die Einstufen-Buttons der
Web-App. Gespeichert wird in data/raw/eigene_labels.jsonl (Registry: „eigene_labels“), Bilder in
data/images/eigene_labels/. Wird ein Inserat erneut gelabelt (gleiche URL bzw. gleicher Text),
ersetzt das neue Label das alte – so lassen sich Fehlklicks korrigieren.

Zusätzlich landet jedes eingestufte Inserat als Ordner im Datensatz-Ordner, sortiert nach Label und
Kategorie (gewerbliche Anbieter getrennt):

  data/datensatz_fuellen_inserate/<betrug|serioes>/<kategorie>[-gewerblich]/<titel>__<schlüssel>/
      inserat.json   alle Felder (ohne Anbietername), Masche, Adresse der Anzeige
      bild_1.jpg …   die Fotos des Inserats

Der Ordner lässt sich durchsehen, über Git teilen und beim Training einlesen; Telefonnummern,
Mailadressen u. Ä. in Titel, Text und Chat sind darin anonymisiert (data/redact.py, die Form bleibt
für die Merkmale erhalten). Er wird bei jeder neuen Einstufung derselben Anzeige aktualisiert bzw.
verschoben. Beim Bauen der Splits werden die Einträge mit eigene_labels.jsonl dedupliziert – nichts
zählt doppelt.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import threading
from pathlib import Path

from scamguard.config import resolve_path
from scamguard.data.build import listing_fingerprint, read_jsonl, write_jsonl
from scamguard.data.discovery import LISTING_DIR
from scamguard.data.listing_import import LISTING_JSON
from scamguard.data.redact import redact
from scamguard.features.seller import is_commercial
from scamguard.schema import Listing

LABEL_FILE = "data/raw/eigene_labels.jsonl"
LABEL_IMAGE_DIR = "data/images/eigene_labels"
DATASET_DIR = LISTING_DIR  # Ablage nach Label und Kategorie (Tests leiten auf einen temporären Ordner um)
LABEL_FOLDERS = {1: "betrug", 0: "serioes"}
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
        try:
            save_to_dataset_folder(listing)
        except OSError as exc:  # Label ist gespeichert – die Ordner-Ablage darf das nicht kippen
            print(f"Ablage im Datensatz-Ordner fehlgeschlagen: {exc}", file=sys.stderr)
        return len(kept)


def _slug(text: str, length: int = 50) -> str:
    s = text.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:length].strip("-") or "inserat"


def folder_key(listing: Listing) -> str:
    """Gleiche Anzeige → gleicher Schlüssel (URL, sonst Text) – auch nach Label- oder Kategoriewechsel."""
    return hashlib.sha1((listing.url or listing_fingerprint(listing)).encode("utf-8")).hexdigest()[:10]


def dataset_folder(listing: Listing) -> Path:
    category = listing.category + ("-gewerblich" if is_commercial(listing) else "")
    name = f"{_slug(redact(listing.title or listing.full_text))}__{folder_key(listing)}"
    return resolve_path(DATASET_DIR) / LABEL_FOLDERS[listing.label] / category / name


def save_to_dataset_folder(listing: Listing) -> Path | None:
    """Eingestuftes Inserat als Ordner ablegen: inserat.json und Fotos (siehe Modul-Doku)."""
    if listing.label not in LABEL_FOLDERS:
        return None
    target = dataset_folder(listing)
    root, key = resolve_path(DATASET_DIR), folder_key(listing)
    # schon abgelegt (anderes Label, andere Kategorie oder geänderter Titel) → dorthin verschieben
    for old in root.glob(f"*/*/*__{key}"):
        if old == target or not old.is_dir():
            continue
        if target.exists():
            shutil.rmtree(old)  # ältere Fassung derselben Anzeige
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            old.replace(target)
    target.mkdir(parents=True, exist_ok=True)

    pictures = []
    for i, path in enumerate(listing.image_paths, 1):
        source = resolve_path(path)
        if source.is_file():
            destination = target / f"bild_{i}{source.suffix.lower() or '.jpg'}"
            shutil.copyfile(source, destination)
            pictures.append(destination.name)
    for stale in target.glob("bild_*"):
        if stale.name not in pictures:
            stale.unlink()

    data = {k: v for k, v in listing.to_dict().items() if k not in ("image_paths", "label", "source", "seller_name")}
    data.update(title=redact(listing.title), description=redact(listing.description),
                messages=[redact(m) for m in listing.messages])
    manifest = {"label": LABEL_FOLDERS[listing.label], **data, "bilder": pictures}
    tmp = target / f"{LISTING_JSON}.tmp"
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target / LISTING_JSON)
    return target


def sync_dataset_folders() -> int:
    """Alle bisherigen eigenen Labels in die Ordner-Ablage übernehmen (scamguard data ordner)."""
    path = resolve_path(LABEL_FILE)
    listings = read_jsonl(path) if path.exists() else []
    return sum(save_to_dataset_folder(l) is not None for l in listings)


def count_labels() -> dict[str, int]:
    path = resolve_path(LABEL_FILE)
    if not path.exists():
        return {"gesamt": 0, "betrug": 0, "serioes": 0}
    labels = [l.label for l in read_jsonl(path)]
    return {"gesamt": len(labels), "betrug": labels.count(1), "serioes": labels.count(0)}
