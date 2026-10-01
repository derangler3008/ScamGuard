"""Zentrale Pipeline: Inserat rein → alle Detektoren → Fusion → ScanResult raus."""

from __future__ import annotations

from scamguard.config import load_config, with_overrides
from scamguard.models.base import Detector
from scamguard.models.fusion import fuse
from scamguard.models.image_model import ImageDetector
from scamguard.models.llm_judge import LLMJudgeDetector
from scamguard.models.rules import RuleDetector
from scamguard.models.text_classifier import TextBaselineDetector, TextTransformerDetector
from scamguard.schema import Listing, ModelResult, ScanResult


def build_detectors(cfg: dict) -> list[Detector]:
    detectors: list[Detector] = [RuleDetector(cfg)]
    if cfg["text_model"].get("enabled", True):
        # Feingetunter Transformer hat Vorrang, sonst die TF-IDF-Baseline
        transformer = TextTransformerDetector(cfg)
        detectors.append(transformer if transformer.available else TextBaselineDetector(cfg))
    if cfg["image_model"].get("enabled", True):
        detectors.append(ImageDetector(cfg))
    detectors.append(LLMJudgeDetector(cfg))  # meldet selbst, wenn deaktiviert
    return detectors


class ScamGuard:
    def __init__(self, cfg: dict | None = None, overrides: dict | None = None):
        cfg = cfg or load_config()
        self.cfg = with_overrides(cfg, overrides) if overrides else cfg
        self.detectors = build_detectors(self.cfg)

    def scan(self, listing: Listing) -> ScanResult:
        results: list[ModelResult] = []
        for det in self.detectors:
            if not det.available:
                results.append(ModelResult(det.name, None, available=False, error=det.unavailable_reason))
                continue
            try:
                results.append(det.predict(listing))
            except Exception as exc:  # noqa: BLE001 – ein defektes Modell darf den Scan nicht abbrechen
                results.append(ModelResult(det.name, None, error=f"{type(exc).__name__}: {exc}"))
        return fuse(results, self.cfg)
