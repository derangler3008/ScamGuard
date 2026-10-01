"""Baut aus allen aktiven Datensätzen die Splits train/val/test (data/processed/*.jsonl)."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from scamguard.config import resolve_path
from scamguard.data.loaders import LoadReport, load_spec
from scamguard.data.registry import get_specs
from scamguard.schema import Listing

PROCESSED_DIR = "data/processed"
SPLITS = ("train", "val", "test")


def write_jsonl(listings: list[Listing], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for listing in listings:
            f.write(json.dumps(listing.to_dict(), ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[Listing]:
    with path.open(encoding="utf-8") as f:
        return [Listing.from_dict(json.loads(line)) for line in f if line.strip()]


def read_split(split: str) -> list[Listing]:
    path = resolve_path(PROCESSED_DIR) / f"{split}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{path} fehlt → zuerst `scamguard data build` ausführen")
    return read_jsonl(path)


def listing_fingerprint(listing: Listing) -> str:
    """Gleiche Inserate (auch aus verschiedenen Quellen) nur einmal behalten → kein Train/Test-Leak."""
    norm = re.sub(r"\W+", " ", listing.full_text.lower()).strip()
    key = norm or "|".join(sorted(listing.image_paths))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _split(listings: list[Listing], val_size: float, test_size: float, seed: int):
    from sklearn.model_selection import train_test_split

    labels = [l.label for l in listings]
    counts = Counter(labels)
    # Stratifizieren nur möglich, wenn jede Klasse genug Beispiele hat
    can_stratify = len(counts) > 1 and min(counts.values()) >= 3
    strat = labels if can_stratify else None
    rest, test = train_test_split(listings, test_size=test_size, random_state=seed, stratify=strat)
    rel_val = val_size / (1.0 - test_size)
    strat_rest = [l.label for l in rest] if can_stratify else None
    train, val = train_test_split(rest, test_size=rel_val, random_state=seed, stratify=strat_rest)
    return train, val, test


def build_dataset(names: list[str] | None = None, val_size: float = 0.15, test_size: float = 0.15,
                  seed: int = 42) -> tuple[list[LoadReport], dict]:
    reports = [load_spec(spec) for spec in get_specs(names)]

    seen: set[str] = set()
    listings: list[Listing] = []
    duplicates = 0
    for report in reports:
        for listing in report.listings:
            fp = listing_fingerprint(listing)
            if fp in seen:
                duplicates += 1
                continue
            seen.add(fp)
            listings.append(listing)

    if len(listings) < 10:
        raise ValueError(f"Nur {len(listings)} verwertbare Beispiele – mindestens 10 nötig. "
                         "Datensätze in registry.py aktivieren/prüfen.")

    train, val, test = _split(listings, val_size, test_size, seed)
    out_dir = resolve_path(PROCESSED_DIR)
    for name, part in zip(SPLITS, (train, val, test)):
        write_jsonl(part, out_dir / f"{name}.jsonl")

    stats = {
        "total": len(listings),
        "duplicates_removed": duplicates,
        "splits": {name: {"n": len(part), "scam": sum(l.label == 1 for l in part)}
                   for name, part in zip(SPLITS, (train, val, test))},
        "by_source": dict(Counter(l.source for l in listings)),
        "by_category": dict(Counter(l.category for l in listings)),
        "with_images": sum(bool(l.image_paths) for l in listings),
    }
    (out_dir / "stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    return reports, stats
