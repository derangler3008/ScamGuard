import json

import pytest

from scamguard.config import load_config, resolve_path, with_overrides
from scamguard.models.base import Detector
from scamguard.models.fusion import fuse
from scamguard.models.text_classifier import TextBaselineDetector, train_text_baseline
from scamguard.pipeline import ScamGuard
from scamguard.schema import Listing, ModelResult, Signal

CFG = load_config()


@pytest.fixture(scope="module")
def samples() -> list[Listing]:
    path = resolve_path("data/samples/sample_listings.jsonl")
    return [Listing.from_dict(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]


def test_scan_works_without_any_trained_model(samples):
    guard = ScamGuard(overrides={"text_model": {"baseline_path": "models/__nicht_vorhanden__.joblib",
                                                "path": "models/__nicht_vorhanden__"}})
    result = guard.scan(samples[0])
    assert 0.0 <= result.score <= 1.0
    status = {r.name: r for r in result.model_results}
    assert status["rules"].score is not None
    assert status["text_model"].available is False
    assert status["llm"].available is False  # standardmäßig aus


def test_rules_separate_scam_from_legit_on_samples(samples):
    guard = ScamGuard(overrides={"image_model": {"enabled": False}})
    scam = [guard.scan(l).score for l in samples if l.label == 1]
    legit = [guard.scan(l).score for l in samples if l.label == 0]
    assert sum(scam) / len(scam) > sum(legit) / len(legit) + 0.3
    assert sum(s >= CFG["thresholds"]["suspicious"] for s in legit) <= 1  # kaum Fehlalarme


def test_hard_signal_sets_score_floor():
    hard = Signal("rules", "LOOKALIKE_DOMAIN", "x", 0.9, hard=True)
    result = fuse([ModelResult("rules", 0.1, [hard]), ModelResult("text_model", 0.05)], CFG)
    assert result.score >= CFG["fusion"]["hard_signal_floor"]
    assert result.verdict == "hohes Risiko"


def test_unavailable_models_are_ignored_in_fusion():
    result = fuse([ModelResult("rules", 0.5), ModelResult("llm", None, available=False)], CFG)
    assert result.score == 0.5


def test_broken_detector_does_not_crash_scan():
    class Broken(Detector):
        name = "broken"

        def predict(self, listing):
            raise RuntimeError("kaputt")

    guard = ScamGuard(overrides={"image_model": {"enabled": False}})
    guard.detectors.append(Broken())
    result = guard.scan(Listing(title="Test"))
    broken = next(r for r in result.model_results if r.name == "broken")
    assert broken.score is None and "kaputt" in broken.error


def test_text_baseline_trains_and_explains(samples, tmp_path):
    cfg = with_overrides(CFG, {"text_model": {"baseline_path": str(tmp_path / "baseline.joblib")}})
    train_text_baseline(samples, cfg)
    det = TextBaselineDetector(cfg)
    assert det.available
    scam = next(l for l in samples if l.label == 1)
    result = det.predict(scam)
    assert 0.0 <= result.score <= 1.0
    assert all(s.code == "TEXT_PATTERN" for s in result.signals)
    # Erklärungen dürfen keine reinen Stoppwörter sein
    assert not {s.evidence for s in result.signals} & {"ich", "das", "die", "und"}


def test_api_health_and_scan():
    from fastapi.testclient import TestClient

    from scamguard.api import app

    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    payload = {"title": "PS5", "description": "Zahlung nur Freunde und Familie", "price": 50,
               "category": "elektronik", "label": 1}
    resp = client.post("/scan", data={"listing": json.dumps(payload)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] in {"unauffällig", "verdächtig", "hohes Risiko"}
    assert any(s["code"] == "OFFPLATFORM_PAYMENT" for s in body["signals"])


def test_api_rejects_invalid_json():
    from fastapi.testclient import TestClient

    from scamguard.api import app

    assert TestClient(app).post("/scan", data={"listing": "{kaputt"}).status_code == 422
