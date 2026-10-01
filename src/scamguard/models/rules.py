"""Regelbasierter Detektor – funktioniert ohne Training und dient als Baseline."""

from __future__ import annotations

from scamguard.features import extract_features
from scamguard.models.base import Detector, noisy_or
from scamguard.schema import Listing, ModelResult


class RuleDetector(Detector):
    name = "rules"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def predict(self, listing: Listing) -> ModelResult:
        feats = extract_features(listing, self.cfg)
        score = noisy_or([s.weight for s in feats.signals])
        return ModelResult(name=self.name, score=score, signals=feats.signals)
