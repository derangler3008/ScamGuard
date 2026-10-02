"""Neu trainieren – gemeinsam genutzt von `scamguard retrain` und dem Button im Frontend."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scamguard.config import load_config

DEMO_DATASET = "demo_synthetisch"


def train_model(model: str, train, val, cfg) -> Path:
    """model: "text-baseline" (TF-IDF + LogReg), "text-transformer" (GBERT), "image" (CNN)."""
    if model == "text-baseline":
        from scamguard.models.text_classifier import train_text_baseline

        return train_text_baseline(train, cfg)
    if model == "text-transformer":
        from scamguard.models.text_classifier import train_text_transformer

        return train_text_transformer(train, val, cfg)
    if model == "image":
        from scamguard.models.image_model import train_image_model

        return train_image_model(train, val, cfg)
    raise ValueError(f"Unbekanntes Modell: {model}")


@dataclass
class RetrainResult:
    reports: list          # LoadReport je Datensatz
    stats: dict            # Umfang, Splits, Quellen
    saved: dict[str, Path]
    evaluation: dict       # Kennzahlen auf dem Testset

    @property
    def fusion(self) -> dict:
        return self.evaluation["models"]["FUSION"]


def retrain(transformer: bool = False, images: bool = False, include_demo: bool = True,
            log: Callable[[str], None] = print) -> RetrainResult:
    """Alle aktiven Datensätze + eigene Labels einlesen, Modelle trainieren, auf dem Testset auswerten.
    Ein laufender Server lädt das neue Textmodell beim nächsten Scan von selbst."""
    from scamguard.data.build import build_dataset, read_split
    from scamguard.data.registry import get_specs
    from scamguard.evaluate import evaluate

    names = None
    if not include_demo:
        names = [s.name for s in get_specs() if s.name != DEMO_DATASET]
        if not names:
            raise ValueError("Ohne die Demo-Inserate gibt es noch keine Daten – erst Inserate einstufen "
                             "oder einen Datensatz-Ordner füllen.")
    reports, stats = build_dataset(names)
    log(f"{stats['total']} Beispiele eingelesen ({stats['duplicates_removed']} Duplikate entfernt)")
    cfg = load_config()
    train, val = read_split("train"), read_split("val")
    saved = {}
    for model in ["text-baseline"] + ["text-transformer"] * transformer + ["image"] * images:
        log(f"Trainiere {model} …")
        saved[model] = train_model(model, train, val, cfg)
    return RetrainResult(reports, stats, saved, evaluate("test"))
