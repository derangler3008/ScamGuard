"""Gemeinsame Schnittstelle aller Detektoren.

Neues Modell hinzufügen = Klasse von `Detector` ableiten, `predict` implementieren
und in `pipeline.build_detectors` registrieren.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from scamguard.schema import Listing, ModelResult


class Detector(ABC):
    #: Schlüssel für Fusion-Gewichte in config.yaml (fusion.weights.<name>)
    name: str = "base"

    @property
    def available(self) -> bool:
        """False, wenn das Modell nicht trainiert/installiert ist. Die Pipeline überspringt es dann."""
        return True

    @property
    def unavailable_reason(self) -> str | None:
        return None

    @abstractmethod
    def predict(self, listing: Listing) -> ModelResult:
        ...


def noisy_or(weights: list[float]) -> float:
    """Kombiniert unabhängige Indizien: P = 1 − Π(1 − w). Mehr Indizien → höher, aber nie > 1."""
    p = 1.0
    for w in weights:
        p *= 1.0 - max(0.0, min(1.0, w))
    return 1.0 - p
