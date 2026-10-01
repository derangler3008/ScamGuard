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
    assert all(len(s.evidence) >= 4 for s in result.signals)  # keine Mini-Wörter wie „per“ markieren


CLIENT = {"X-ScamGuard-Client": "pytest"}


@pytest.fixture(scope="module")
def api():
    from fastapi.testclient import TestClient

    from scamguard.api import app

    return TestClient(app)


def test_api_health_and_scan(api):
    assert api.get("/health").json()["status"] == "ok"
    payload = {"title": "PS5", "description": "Zahlung nur Freunde und Familie", "price": 50,
               "category": "elektronik", "label": 1}
    resp = api.post("/scan", data={"listing": json.dumps(payload)}, headers=CLIENT)
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] in {"unauffällig", "verdächtig", "hohes Risiko"}
    flag = next(s for s in body["signals"] if s["code"] == "OFFPLATFORM_PAYMENT")
    assert flag["highlights"] == ["Freunde und Familie"]  # exakter Originaltext für die Extension


def test_api_normalizes_raw_page_values_and_sets_targets(api):
    # So schickt die Extension die Werte: Rohtext von der Seite
    payload = {"title": "iPhone 15 Pro 256GB", "description": "Wie neu.", "price": "120 € VB",
               "category": "Kleinanzeigen Mannheim > Elektronik > Handy & Telefon",
               "seller_account_age_days": 2}
    body = api.post("/scan", data={"listing": json.dumps(payload)}, headers=CLIENT).json()
    targets = {s["code"]: s["target"] for s in body["signals"]}
    assert targets["PRICE_FAR_TOO_LOW"] == "price"
    assert targets["NEW_ACCOUNT"] == "seller"


def test_api_rejects_requests_without_client_header(api):
    # Simuliert eine fremde Webseite, die ohne CORS-Freigabe an die lokale API postet
    resp = api.post("/scan", data={"listing": json.dumps({"title": "x"}), "use_llm": "true"})
    assert resp.status_code == 403


def test_api_rejects_foreign_origin_but_accepts_extension(api):
    data = {"listing": json.dumps({"title": "x"})}
    evil = api.post("/scan", data=data, headers={**CLIENT, "Origin": "https://evil.example"})
    lookalike = api.post("/scan", data=data, headers={**CLIENT, "Origin": "http://localhost.evil.example"})
    ext = api.post("/scan", data=data, headers={**CLIENT, "Origin": "chrome-extension://abcdefghijklmnop"})
    assert (evil.status_code, lookalike.status_code, ext.status_code) == (403, 403, 200)


def test_api_llm_auto_mode_only_uses_local_models(api, monkeypatch):
    from fastapi import HTTPException

    from scamguard import api as api_module

    monkeypatch.setattr(api_module, "_llm_provider", lambda: "local")
    assert api_module.resolve_llm_mode("auto") is True       # lokal: kostenlos, Daten bleiben hier
    monkeypatch.setattr(api_module, "_llm_provider", lambda: "anthropic")
    assert api_module.resolve_llm_mode("auto") is False      # Claude nur auf ausdrücklichen Wunsch
    assert api_module.resolve_llm_mode("true") is True
    with pytest.raises(HTTPException):
        api_module.resolve_llm_mode("vielleicht")
    llm = api.get("/health").json()["llm"]
    assert {"provider", "model", "auto"} <= set(llm)


def test_api_rejects_invalid_json(api):
    assert api.post("/scan", data={"listing": "{kaputt"}, headers=CLIENT).status_code == 422
    assert api.post("/scan", data={"listing": "[1, 2]"}, headers=CLIENT).status_code == 422


def test_llm_server_command_starts_offline_with_matching_model_id(monkeypatch):
    import huggingface_hub

    from scamguard import cli

    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *a, **kw: "/cache/qwen")
    command, env, _ = cli._llm_server_process_args()
    model = CFG["llm"]["local"]["model"]
    assert command[1:4] == ["-m", "mlx_lm", "server"]           # nicht der veraltete Aufruf
    assert command[command.index("--model") + 1] == model       # gleiche ID wie in den Anfragen
    assert env["HF_HUB_OFFLINE"] == "1"                         # Modell im Cache → keine Netzabfrage
    assert json.loads(command[command.index("--chat-template-args") + 1]) == {"enable_thinking": False}
    assert command[command.index("--allowed-origins") + 1] != "*"


def _start_args(**kw):
    import argparse

    return argparse.Namespace(**{"host": "127.0.0.1", "port": 8000, "ohne_llm": False, **kw})


def _no_popen(*a, **kw):
    raise AssertionError("Qwen darf nicht gestartet werden")


def test_start_when_scamguard_already_runs(monkeypatch, capsys):
    from scamguard import cli

    monkeypatch.setattr(cli, "_port_busy", lambda host, port: True)
    monkeypatch.setattr(cli, "_http_json", lambda url, headers=None: {"version": "9.9", "detectors": {}})
    monkeypatch.setattr(cli.subprocess, "Popen", _no_popen)
    assert cli._cmd_start(_start_args()) == 0
    assert "läuft bereits" in capsys.readouterr().out


def test_start_when_port_is_taken_by_other_program(monkeypatch, capsys):
    from scamguard import cli

    monkeypatch.setattr(cli, "_port_busy", lambda host, port: True)
    monkeypatch.setattr(cli, "_http_json", lambda url, headers=None: None)
    monkeypatch.setattr(cli.subprocess, "Popen", _no_popen)
    assert cli._cmd_start(_start_args()) == 1
    assert "--port 8001" in capsys.readouterr().out


def test_start_reuses_running_qwen(monkeypatch, capsys):
    from scamguard import cli

    monkeypatch.setattr(cli, "_port_busy", lambda host, port: False)
    monkeypatch.setattr(cli, "_http_json", lambda url, headers=None: {"data": [{"id": "qwen"}]})
    monkeypatch.setattr(cli.subprocess, "Popen", _no_popen)
    monkeypatch.setattr(cli, "_serve_api", lambda args: 0)
    assert cli._cmd_start(_start_args()) == 0
    assert "wird mitbenutzt" in capsys.readouterr().out


def test_port_probe_with_real_socket():
    import socket

    from scamguard import cli

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert cli._port_busy("127.0.0.1", port)
        assert cli._port_busy("0.0.0.0", port)  # wird auf 127.0.0.1 geprüft
    assert not cli._port_busy("127.0.0.1", port)
