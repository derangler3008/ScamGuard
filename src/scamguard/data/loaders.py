"""Lädt Datensätze aus der Registry und übersetzt sie ins einheitliche `Listing`-Schema."""

from __future__ import annotations

import io
import json
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scamguard.config import resolve_path
from scamguard.data.registry import DatasetSpec
from scamguard.features.language import looks_german
from scamguard.schema import CATEGORIES, Listing

# Reihenfolge zählt (erster Treffer gewinnt):
# - Fahrräder stehen bei Kleinanzeigen unter „Auto, Rad & Boot“, sind aber kein Auto.
# - Haushaltsgeräte stehen unter „Elektronik“ → vor elektronik prüfen.
CATEGORY_KEYWORDS = {
    "sonstiges": ["fahrrad", "fahrräder"],
    "haushaltsgeraete": ["haushalt", "waschmaschine", "kühlschrank", "küche", "staubsauger",
                         "trockner", "spülmaschine", "geschirrspüler"],
    "elektronik": ["elektronik", "handy", "smartphone", "computer", "laptop", "notebook", "tablet",
                   "konsole", "tv", "audio", "foto", "kamera"],
    "auto": ["auto", "kfz", "fahrzeug", "pkw", "motorrad", "wohnmobil", "reifen"],
    "moebel": ["möbel", "moebel", "sofa", "schrank", "einrichtung", "wohnen", "wohnzimmer",
               "schlafzimmer", "esszimmer"],
    "mode": ["mode", "kleidung", "schuhe", "taschen", "beauty"],
    "tiere": ["tier", "hund", "katze", "pferd"],
    "immobilien": ["wohnung", "immobilie", "häuser", "miete", "wg", "grundstück"],
    "tickets": ["ticket", "eintrittskarte", "konzert"],
}


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class LoadReport:
    name: str
    listings: list[Listing] = field(default_factory=list)
    rows: int = 0
    skipped_no_label: int = 0
    skipped_not_german: int = 0
    skipped_empty: int = 0
    error: str | None = None
    note: str | None = None


# --------------------------------------------------------------------------- Normalisierung

def _clean(value: Any) -> Any:
    """NaN aus pandas → None."""
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def parse_price(value: Any) -> float | None:
    """'1.200 €', '1.200,50', '350 VB', 99 → float. 'VB'/'zu verschenken' → None bzw. 0."""
    value = _clean(value)
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).lower().strip()
    if "verschenken" in s:
        return 0.0
    m = re.search(r"\d[\d.\s]*(?:,\d{1,2})?", s)
    if not m:
        return None
    num = m.group(0).replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(num)
    except ValueError:
        return None


def normalize_category(value: Any) -> str:
    """Freitext oder Brotkrumenpfad ("Kleinanzeigen Mannheim > Elektronik > Haushaltsgeräte")
    → eine unserer Kategorien. Bei Pfaden zählt der spezifischste Teil (von hinten gelesen)."""
    value = _clean(value)
    if not value:
        return "sonstiges"
    segments = [s.strip().lower() for s in re.split(r"[>›»|]", str(value)) if s.strip()]
    for s in reversed(segments):
        ascii_s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
        if ascii_s.replace(" ", "") in CATEGORIES:
            return ascii_s.replace(" ", "")
        for cat, keywords in CATEGORY_KEYWORDS.items():
            if any(k in s for k in keywords):
                return cat
    return "sonstiges"


def _as_list(value: Any) -> list[str]:
    value = _clean(value)
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return [str(value)]


def _save_image(value: Any, out_dir: Path, idx: int, base_dir: str | None = None) -> str | None:
    """HF-Bildspalten können PIL-Bilder, {"bytes": ...}/{"path": ...}-Dicts oder Pfade sein.
    Relative Pfade gelten relativ zur Datensatzdatei (base_dir), sonst zum Projektordner."""
    from PIL import Image

    value = _clean(value)
    if value is None:
        return None
    if isinstance(value, str):
        candidates = [Path(base_dir) / value] if base_dir and not Path(value).is_absolute() else []
        candidates.append(resolve_path(value))
        return next((str(c) for c in candidates if c.exists()), None)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{idx:07d}.jpg"
    if isinstance(value, dict):
        if value.get("bytes"):
            value = Image.open(io.BytesIO(value["bytes"]))
        elif value.get("path") and Path(value["path"]).exists():
            return value["path"]
        else:
            return None
    if isinstance(value, Image.Image):
        value.convert("RGB").save(target, format="JPEG", quality=90)
        return str(target)
    return None


# --------------------------------------------------------------------------- Rohdaten lesen

def _iter_rows(spec: DatasetSpec) -> Iterator[dict]:
    if spec.source == "huggingface":
        try:
            from datasets import load_dataset
        except ImportError as exc:
            raise ImportError('Hugging-Face-Datasets fehlt → pip install -e ".[data]"') from exc
        ds = load_dataset(spec.path, spec.subset, split=spec.split, **spec.hf_kwargs)
        if spec.max_rows:
            ds = ds.select(range(min(spec.max_rows, len(ds))))
        yield from ds
        return

    path = resolve_path(spec.path)
    if not path.exists():
        raise FileNotFoundError(f"Datei nicht gefunden: {path}")
    if spec.source == "imagefolder":  # Label kommt aus dem Ordnernamen (fixed_label)
        images = sorted(p for p in path.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
        for image in images[:spec.max_rows] if spec.max_rows else images:
            yield {"image_paths": [str(image)]}
        return
    if spec.source == "listingfolder":  # ganze Inserate: je Datei bzw. Unterordner eines
        from scamguard.data.listing_import import SUPPORTED_SUFFIXES, import_paths

        def supported(p: Path) -> bool:
            return p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES and not p.name.startswith(("_", "."))

        items = sorted(p for p in path.iterdir()
                       if supported(p) or (p.is_dir() and not p.name.startswith(("_", "."))))
        for item in items[:spec.max_rows] if spec.max_rows else items:
            files = sorted(f for f in item.rglob("*") if supported(f)) if item.is_dir() else [item]
            if files:
                yield import_paths(files).listing.to_dict()
        return
    if spec.source == "jsonl":
        with path.open(encoding="utf-8") as f:
            for i, line in enumerate(f):
                if spec.max_rows and i >= spec.max_rows:
                    break
                if line.strip():
                    yield json.loads(line)
        return

    import pandas as pd

    if spec.source == "csv":
        df = pd.read_csv(path, nrows=spec.max_rows, **spec.read_kwargs)
    elif spec.source == "parquet":
        df = pd.read_parquet(path)
        if spec.max_rows:
            df = df.head(spec.max_rows)
    else:
        raise ValueError(f"Unbekannte Quelle: {spec.source}")
    yield from df.to_dict("records")


def _resolve_label(row: dict, spec: DatasetSpec) -> int | None:
    if spec.fixed_label is not None:
        return int(spec.fixed_label)
    col = spec.label_column or "label"
    raw = _clean(row.get(col))
    if raw is None:
        return None
    if spec.label_map:
        mapped = spec.label_map.get(raw)
        if mapped is None and isinstance(raw, str):
            mapped = spec.label_map.get(raw.strip().lower())
        return None if mapped is None else int(mapped)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value in (0, 1) else None


def row_to_listing(row: dict, spec: DatasetSpec, idx: int) -> Listing | None:
    if spec.transform:
        row = spec.transform(dict(row))
        if row is None:
            return None

    # Ohne column_map: Datei ist bereits in unserem Schema (z. B. eigene JSONL-Dateien)
    data = {k: _clean(row.get(src)) for k, src in spec.column_map.items()} if spec.column_map else dict(row)
    # Felder, die eine transform-Funktion direkt im Zielformat liefert, ebenfalls übernehmen
    for key in ("title", "description", "messages", "label", "image_paths"):
        if key in row and key not in data:
            data[key] = row[key]

    # Relative Bildpfade (z. B. aus dem Labeling-Tab) gegen den Projektordner auflösen
    images = [str(resolve_path(p)) for p in _as_list(data.pop("image_paths", None))]
    if "image" in data:
        image_dir = resolve_path("data/images") / re.sub(r"[^\w.-]+", "_", spec.name)  # „hf:org/x“ → Ordnername
        saved = _save_image(data.pop("image"), image_dir, idx, spec.base_dir)
        if saved:
            images.append(saved)

    label_source = row if spec.fixed_label is None and spec.label_column else data
    label = _resolve_label(label_source, spec)

    return Listing(
        title=str(_clean(data.get("title")) or ""),
        description=str(_clean(data.get("description")) or ""),
        price=parse_price(data.get("price")),
        category=spec.category or normalize_category(data.get("category")),
        location=_clean(data.get("location")),
        seller_account_age_days=_clean(data.get("seller_account_age_days")),
        seller_num_ratings=_clean(data.get("seller_num_ratings")),
        messages=_as_list(data.get("messages")),
        image_paths=images,
        label=label,
        scam_type=_clean(data.get("scam_type")),
        source=spec.name,
    )


def load_spec(spec: DatasetSpec) -> LoadReport:
    report = LoadReport(spec.name)
    if spec.source != "huggingface" and not resolve_path(spec.path).exists():
        report.note = "Datei existiert noch nicht – übersprungen"
        return report
    try:
        for idx, row in enumerate(_iter_rows(spec)):
            report.rows += 1
            listing = row_to_listing(row, spec, idx)
            if listing is None or (not listing.full_text and not listing.image_paths):
                report.skipped_empty += 1
                continue
            if listing.label is None:
                report.skipped_no_label += 1
                continue
            if spec.german_only and spec.modality != "image" and not looks_german(listing.full_text):
                report.skipped_not_german += 1
                continue
            report.listings.append(listing)
    except Exception as exc:  # noqa: BLE001 – ein kaputter Datensatz soll den Build der anderen nicht stoppen
        report.error = f"{type(exc).__name__}: {exc}"
    return report
