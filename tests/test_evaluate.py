"""Auswertung: Fortschritt, LLM-Ausfälle und gespeicherter Bericht."""

from __future__ import annotations

import json

from scamguard import evaluate as ev
from scamguard.schema import Listing, ModelResult, ScanResult


class FakeGuard:
    """Text urteilt immer, das LLM fällt beim zweiten Inserat aus (z. B. Zeitüberschreitung)."""

    def __init__(self):
        self.cfg = {"thresholds": {"suspicious": 0.4}}
        self.detectors = []
        self.calls = 0

    def scan(self, listing):
        self.calls += 1
        llm = (ModelResult("llm", None, error="Zeitüberschreitung") if self.calls == 2
               else ModelResult("llm", 0.9 if listing.label else 0.1))
        bild = ModelResult("image_model", None, error="Keine Bilder hochgeladen")  # keine Fotos: kein Ausfall
        return ScanResult(0.8 if listing.label else 0.2, "", [ModelResult("text_model", 0.7), llm, bild], [])


def test_evaluate_reports_progress_and_llm_failures(monkeypatch, tmp_path):
    listings = [Listing(title=f"Inserat {i}", description="", label=i % 2, source="test") for i in range(4)]
    monkeypatch.setattr(ev, "read_split", lambda split: listings)
    seen = []

    report = ev.evaluate("test", guard=FakeGuard(), progress=lambda i, n: seen.append((i, n)))

    assert seen == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert report["models"]["llm"]["n"] == 3           # Ausfall zählt nicht als Urteil
    assert report["models"]["text_model"]["n"] == 4
    assert report["failed"] == {"llm": 1}              # fehlende Fotos sind kein Fehler

    path = ev.save_report(report, tmp_path, llm_model="qwen-test")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == "auswertung_test_llm.json"
    assert saved["llm"] == "qwen-test" and saved["models"]["FUSION"]["n"] == 4
    assert ev.save_report(report, tmp_path).name == "auswertung_test.json"


def test_ctrl_c_keeps_finished_cases(monkeypatch, tmp_path):
    listings = [Listing(title=f"Inserat {i}", description="", label=i % 2, source="test") for i in range(4)]
    monkeypatch.setattr(ev, "read_split", lambda split: listings)
    guard = FakeGuard()
    scan = guard.scan

    def interrupted(listing):
        if guard.calls == 3:
            raise KeyboardInterrupt
        return scan(listing)

    guard.scan = interrupted
    report = ev.evaluate("test", guard=guard)
    assert report["complete"] is False
    assert report["models"]["FUSION"]["n"] == 3
