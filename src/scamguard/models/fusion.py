"""Late Fusion: kombiniert die Scores der Einzelmodelle zu einem Gesamturteil.

Erste Version: gewichteter Mittelwert (Gewichte in config.yaml).
Ausbaustufe (TODO Team): Gewichte per logistischer Regression auf dem
Validierungsset lernen (Stacking) und mit Platt-Scaling kalibrieren.
"""

from __future__ import annotations

from scamguard.schema import ModelResult, ScanResult


def verdict_for(score: float, cfg: dict) -> str:
    t = cfg["thresholds"]
    if score >= t["high_risk"]:
        return "hohes Risiko"
    if score >= t["suspicious"]:
        return "verdächtig"
    return "unauffällig"


def fuse(results: list[ModelResult], cfg: dict) -> ScanResult:
    weights = cfg["fusion"]["weights"]
    scored = [r for r in results if r.available and r.score is not None]

    total_w = sum(weights.get(r.name, 1.0) for r in scored)
    score = (sum(weights.get(r.name, 1.0) * r.score for r in scored) / total_w) if total_w else 0.0

    signals = sorted((s for r in results for s in r.signals), key=lambda s: s.weight, reverse=True)
    # Ein einzelnes hartes Indiz (z. B. Fake-PayPal-Domain) darf nicht „weggemittelt“ werden
    if any(s.hard for s in signals):
        score = max(score, cfg["fusion"].get("hard_signal_floor", 0.75))

    score = round(min(max(score, 0.0), 1.0), 4)
    return ScanResult(score=score, verdict=verdict_for(score, cfg), model_results=results,
                      signals=signals)
