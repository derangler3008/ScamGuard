"""Auswertung auf einem Split: Kennzahlen pro Einzelmodell und für die Fusion.

Für den Projektbericht: ROC-AUC (schwellenunabhängig), Precision/Recall/F1 an der
„verdächtig“-Schwelle, und eine Aufschlüsselung nach Datenquelle.
"""

from __future__ import annotations

from collections import defaultdict

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


def evaluate(split: str = "test", guard: ScamGuard | None = None) -> dict:
    guard = guard or ScamGuard()
    listings = read_split(split)
    threshold = guard.cfg["thresholds"]["suspicious"]

    per_model: dict[str, tuple[list[int], list[float]]] = defaultdict(lambda: ([], []))
    per_source: dict[str, tuple[list[int], list[float]]] = defaultdict(lambda: ([], []))
    for listing in listings:
        result = guard.scan(listing)
        for r in result.model_results:
            if r.score is not None:
                per_model[r.name][0].append(listing.label)
                per_model[r.name][1].append(r.score)
        per_model["FUSION"][0].append(listing.label)
        per_model["FUSION"][1].append(result.score)
        per_source[listing.source or "?"][0].append(listing.label)
        per_source[listing.source or "?"][1].append(result.score)

    return {
        "split": split,
        "threshold": threshold,
        "models": {name: _metrics(y, s, threshold) for name, (y, s) in per_model.items()},
        "sources": {name: _metrics(y, s, threshold) for name, (y, s) in per_source.items()},
    }
