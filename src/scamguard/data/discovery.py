"""Ablageordner für Datensätze – ohne Python-Code.

data/datensatz_fuellen_text/
  Dateien (*.csv, *.tsv, *.jsonl, *.parquet) werden automatisch als Datensatz erkannt. Spalten und
  Labels werden über übliche Namen und Werte zugeordnet (deutsch oder englisch); deutsche
  Excel-CSVs (Semikolon, Windows-Zeichensatz) gehen auch. Hugging-Face-Datensätze: huggingface.yaml.

data/datensatz_fuellen_inserate/
  Ganze Inserate: Screenshots, gespeicherte Seiten (.html), PDFs oder .txt – eine Datei = ein Inserat,
  ein Unterordner = ein Inserat aus mehreren Dateien. Unterordner betrug/ bzw. serioes/ = Label.

data/datensatz_fuellen_bilder/
  Nur Produktfotos (fürs Bildmodell). Unterordner geben das Label vor: betrug/ (auch scam/, fake/)
  und serioes/ (auch echt/, legit/).

Dateien und Ordner, die mit „_“ beginnen, werden ignoriert (Vorlagen, Entwürfe).
Für Sonderfälle (Texte zusammensetzen, filtern …) bleibt registry.py mit `transform`-Funktionen.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import yaml

from scamguard.config import resolve_path
from scamguard.data.listing_import import listing_items
from scamguard.data.loaders import IMAGE_SUFFIXES
from scamguard.data.registry import DatasetSpec

TEXT_DIR = "data/datensatz_fuellen_text"
IMAGE_DIR = "data/datensatz_fuellen_bilder"
LISTING_DIR = "data/datensatz_fuellen_inserate"
HF_FILE = "huggingface.yaml"
FILE_TYPES = {".csv": "csv", ".tsv": "csv", ".jsonl": "jsonl", ".parquet": "parquet"}

# Unser Feld → mögliche Spaltennamen (klein geschrieben, Leerzeichen → "_")
COLUMN_ALIASES = {
    "title": ["title", "titel", "überschrift", "ueberschrift", "headline"],
    "description": ["description", "beschreibung", "text", "inhalt", "anzeigentext", "body"],
    "price": ["price", "preis"],
    "category": ["category", "kategorie", "rubrik"],
    "messages": ["messages", "nachrichten", "nachricht", "chat", "message"],
    "scam_type": ["scam_type", "masche", "betrugsart"],
    "seller_account_age_days": ["seller_account_age_days", "kontoalter", "kontoalter_tage"],
    "image": ["image", "bild", "bildpfad", "image_path"],
}
LABEL_ALIASES = ["label", "betrug", "ist_betrug", "is_scam", "scam", "fake", "ist_fake", "klasse", "class"]

# Label-Wert → 1 (Betrug) / 0 (seriös). Zahlen und Wahrheitswerte decken 1/0 bzw. True/False ab.
LABEL_WORDS: dict[Any, int] = {
    1: 1, 0: 0, "1": 1, "0": 0, "true": 1, "false": 0, "wahr": 1, "falsch": 0,
    "ja": 1, "nein": 0, "yes": 1, "no": 0, "j": 1, "n": 0,
    "betrug": 1, "scam": 1, "fake": 1, "spam": 1, "phishing": 1, "verdächtig": 1,
    "seriös": 0, "serioes": 0, "legit": 0, "legitim": 0, "echt": 0, "ham": 0, "ok": 0,
    "kein betrug": 0, "unauffällig": 0,
}


def label_of(value: Any) -> int | None:
    """Label-Wert (Zahl, Wort, Ordnername) → 1/0, sonst None."""
    if isinstance(value, str):
        return LABEL_WORDS.get(value.strip().lower().replace("_", " "))
    return LABEL_WORDS.get(value)


def _norm(name: str) -> str:
    return str(name).strip().lower().replace(" ", "_").replace("-", "_")


def _csv_options(path: Path) -> dict[str, Any]:
    """Trennzeichen und Zeichensatz erkennen (Excel speichert deutsch gern als „;“ und cp1252)."""
    raw = path.read_bytes()[:16384]
    try:
        sample, encoding = raw.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        sample, encoding = raw.decode("cp1252", errors="replace"), "cp1252"
    if path.suffix.lower() == ".tsv":
        return {"sep": "\t", "encoding": encoding}
    try:
        sep = csv.Sniffer().sniff(sample.splitlines()[0] if sample else ",", delimiters=",;\t|").delimiter
    except csv.Error:
        sep = ","
    return {"sep": sep, "encoding": encoding}


def _columns(path: Path, kind: str, read_kwargs: dict[str, Any]) -> list[str]:
    if kind == "jsonl":
        with path.open(encoding="utf-8") as f:
            first = next((line for line in f if line.strip()), "{}")
        return list(json.loads(first).keys())
    if kind == "csv":
        import pandas as pd

        return list(pd.read_csv(path, nrows=0, **read_kwargs).columns)
    import pyarrow.parquet as pq  # nur das Schema lesen, nicht die ganze Datei

    return list(pq.read_schema(path).names)


def _spec_for_file(path: Path) -> DatasetSpec:
    kind = FILE_TYPES[path.suffix.lower()]
    read_kwargs = _csv_options(path) if kind == "csv" else {}
    name = f"datei:{path.name}"
    try:
        columns = _columns(path, kind, read_kwargs)
    except Exception as exc:  # noqa: BLE001 – kaputte Datei soll die anderen nicht blockieren
        return DatasetSpec(name=name, source=kind, path=str(path), enabled=False,
                           notes=f"Datei nicht lesbar: {exc}")
    by_norm = {_norm(c): c for c in columns}
    column_map = {}
    for field_name, aliases in COLUMN_ALIASES.items():
        match = next((by_norm[a] for a in aliases if a in by_norm), None)
        if match is not None:
            column_map[field_name] = match
    label_column = next((by_norm[a] for a in LABEL_ALIASES if a in by_norm), None)

    problems = []
    if label_column is None:
        problems.append("keine Label-Spalte (z. B. „betrug“ oder „label“)")
    if not {"title", "description", "messages", "image"} & column_map.keys():
        problems.append("keine Text- oder Bildspalte (z. B. „titel“, „beschreibung“, „text“)")
    mapped = ", ".join(f"{k}←{v}" for k, v in column_map.items())
    return DatasetSpec(
        name=name, source=kind, path=str(path),
        modality="multimodal" if "image" in column_map else "text",
        column_map=column_map, label_column=label_column, label_map=LABEL_WORDS,
        read_kwargs=read_kwargs, base_dir=str(path.parent),
        enabled=not problems, license="eigene Daten – Quelle dokumentieren",
        notes=("Nicht verwendbar: " + "; ".join(problems)) if problems
        else f"Automatisch erkannt: {mapped}, Label←{label_column}",
    )


def _label_map(raw: dict | None) -> dict[Any, int]:
    """labels-Eintrag aus huggingface.yaml: Wert → „betrug“/„seriös“ (oder 1/0)."""
    if not raw:
        return LABEL_WORDS
    mapping = {}
    for value, meaning in raw.items():
        target = label_of(meaning)
        if target is not None:
            mapping[value] = int(target)
            if isinstance(value, str):
                mapping[value.strip().lower()] = int(target)
    return mapping


def _specs_from_hf_file(path: Path) -> list[DatasetSpec]:
    with path.open(encoding="utf-8") as f:
        entries = (yaml.safe_load(f) or {}).get("datensaetze") or []
    specs = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        specs.append(DatasetSpec(
            name=f"hf:{entry['id']}", source="huggingface", path=str(entry["id"]),
            split=str(entry.get("split", "train")), subset=entry.get("subset"),
            column_map=dict(entry.get("spalten") or {}),
            label_column=entry.get("label_spalte"), label_map=_label_map(entry.get("labels")),
            fixed_label=label_of(entry["alles_ist"]) if "alles_ist" in entry else None,
            german_only=bool(entry.get("nur_deutsch", True)), max_rows=entry.get("max_zeilen"),
            enabled=bool(entry.get("aktiv", True)), license=str(entry.get("lizenz", "bitte prüfen")),
            notes=str(entry.get("notiz", "aus huggingface.yaml")),
        ))
    return specs


def _specs_from_label_dirs(folder: Path, kind: str) -> list[DatasetSpec]:
    """Ein Datensatz pro Label-Unterordner (betrug/, serioes/ …).
    kind "bilder": jedes Bild ist ein Beispiel; kind "inserate": jede Datei bzw. jeder Unterordner
    (mehrere Screenshots/Fotos eines Inserats) ist ein Beispiel."""
    specs = []
    for sub in sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))):
        label = label_of(sub.name)
        if kind == "bilder":
            count = sum(1 for f in sub.rglob("*") if f.suffix.lower() in IMAGE_SUFFIXES)
            unit, source, modality = "Bilder", "imagefolder", "image"
        else:
            count = len(listing_items(sub))
            unit, source, modality = "Inserate", "listingfolder", "multimodal"
        specs.append(DatasetSpec(
            name=f"{kind}:{sub.name}", source=source, path=str(sub), modality=modality,
            fixed_label=label, german_only=False, enabled=label is not None and count > 0,
            license=f"eigene {unit} – Quelle dokumentieren",
            notes=(f"{count} {unit}, Label {'Betrug' if label else 'seriös'}" if label is not None
                   else "Ordnername ist kein Label – „betrug“ oder „serioes“ verwenden"),
        ))
    return specs


def discover_specs() -> list[DatasetSpec]:
    specs: list[DatasetSpec] = []
    text_dir = resolve_path(TEXT_DIR)
    if text_dir.is_dir():
        for path in sorted(text_dir.iterdir()):
            if path.name.startswith(("_", ".")):
                continue
            if path.name == HF_FILE:
                specs.extend(_specs_from_hf_file(path))
            elif path.suffix.lower() in FILE_TYPES:
                specs.append(_spec_for_file(path))
    for folder, kind in ((LISTING_DIR, "inserate"), (IMAGE_DIR, "bilder")):
        path = resolve_path(folder)
        if path.is_dir():
            specs.extend(_specs_from_label_dirs(path, kind))
    return specs
