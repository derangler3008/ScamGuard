"""Auswertung auf einem Split: Kennzahlen pro Einzelmodell und für die Fusion.

Kennzahlen: ROC-AUC (schwellenunabhängig), Precision/Recall/F1 an der
„verdächtig“-Schwelle, und eine Aufschlüsselung nach Datenquelle.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from scamguard.data.build import read_split
from scamguard.pipeline import ScamGuard


def _metrics(y_true: list[int], y_score: list[float], threshold: float) -> dict[str, float]:
    from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score

    y_pred = [int(s >= threshold) for s in y_score]
    out = {
        "n": len(y_true),
        "precision": precision_score(y_true, y_pred, zero_division=0),
        "recall": recall_score(y_true, y_pred, zero_division=0),
        "f1": f1_score(y_true, y_pred, zero_division=0),
    }
    if len(set(y_true)) == 2:
        out["roc_auc"] = roc_auc_score(y_true, y_score)
    return out


def _hide_from_llm_examples(guard: ScamGuard, listings) -> None:
    """Mit `--llm`: Vergleichsfälle nie aus der ausgewerteten Menge (sonst sähe das LLM deren Labels)."""
    from scamguard.data.build import listing_fingerprint

    for det in guard.detectors:
        bank = getattr(getattr(det, "backend", None), "examples", None)
        if bank is not None:
            bank.exclude(listing_fingerprint(listing) for listing in listings)


def evaluate(split: str = "test", guard: ScamGuard | None = None, limit: int | None = None,
             progress: Callable[[int, int], None] | None = None) -> dict:
    guard = guard or ScamGuard(overrides={"llm": {"enabled": False}})  # LLM nur mit --llm (Sekunden je Beispiel)
    listings = read_split(split)[:limit]
    threshold = guard.cfg["thresholds"]["suspicious"]
    _hide_from_llm_examples(guard, listings)

    per_model: dict[str, tuple[list[int], list[float]]] = defaultdict(lambda: ([], []))
    per_source: dict[str, tuple[list[int], list[float]]] = defaultdict(lambda: ([], []))
    failed: dict[str, int] = defaultdict(int)  # Fehler ohne Score (z. B. LLM-Zeitüberschreitung) – zählt nicht mit
    done = 0
    for i, listing in enumerate(listings, 1):
        try:
            result = guard.scan(listing)
        except KeyboardInterrupt:  # Ctrl+C: mit den bis dahin fertigen Fällen auswerten
            break
        done = i
        for r in result.model_results:
            if r.score is None:
                nothing_to_judge = r.name == "image_model" and not listing.image_paths  # Inserat ohne Fotos
                failed[r.name] += bool(r.error) and not nothing_to_judge
            else:
                per_model[r.name][0].append(listing.label)
                per_model[r.name][1].append(r.score)
        per_model["FUSION"][0].append(listing.label)
        per_model["FUSION"][1].append(result.score)
        per_source[listing.source or "?"][0].append(listing.label)
        per_source[listing.source or "?"][1].append(result.score)
        if progress:
            progress(i, len(listings))

    return {
        "split": split,
        "threshold": threshold,
        "models": {name: _metrics(y, s, threshold) for name, (y, s) in per_model.items()},
        "sources": {name: _metrics(y, s, threshold) for name, (y, s) in per_source.items()},
        "failed": {name: k for name, k in failed.items() if k},
        "complete": done == len(listings),
    }


def save_report(report: dict, models_dir: Path, llm_model: str | None = None) -> Path:
    """Ergebnis als JSON neben die Modelle – nachvollziehbar, welcher Stand welche Zahlen geliefert hat."""
    from scamguard import __version__

    suffix = "_llm" if llm_model else ""
    path = models_dir / f"auswertung_{report['split']}{suffix}.json"
    meta = {"version": __version__, "llm": llm_model,
            "erstellt": datetime.now().astimezone().isoformat(timespec="seconds")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**meta, **report}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
