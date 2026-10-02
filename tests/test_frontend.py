"""Streamlit-Frontend ohne Browser (AppTest): einfügen → einstufen → gespeichert."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from scamguard.data import labels

APP = str(Path(__file__).resolve().parents[1] / "frontend" / "app.py")
TEXT = ("Kleinanzeigen › Elektronik › Konsolen\nPS5 Disc neu\n350 €\nBeschreibung\n"
        "Nur Vorkasse per Überweisung, ich bin im Ausland.")


@pytest.fixture
def label_store(tmp_path, monkeypatch):
    monkeypatch.setattr(labels, "LABEL_FILE", str(tmp_path / "eigene_labels.jsonl"))
    monkeypatch.setattr(labels, "LABEL_IMAGE_DIR", str(tmp_path / "label_bilder"))
    return tmp_path / "eigene_labels.jsonl"


def _button(at: AppTest, label: str):
    return next(b for b in at.button if b.label == label)


def test_paste_listing_and_label_it(label_store):
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    assert any("Noch leer" in i.value for i in at.info)          # leerer Zustand erklärt, was zu tun ist

    at.text_area(key="paste_0").input(TEXT).run()
    assert not at.exception
    assert any("PS5 Disc neu" in m.value for m in at.markdown)   # Zusammenfassung zeigt das Erkannte

    _button(at, "⚠ Betrug").click().run()
    assert not at.exception
    stored = [json.loads(line) for line in label_store.read_text(encoding="utf-8").splitlines()]
    assert len(stored) == 1 and stored[0]["label"] == 1 and stored[0]["price"] == 350.0
    assert stored[0]["title"] == "PS5 Disc neu" and stored[0]["source"] == "eigene_labels"
    assert any("Gespeichert als **Betrug**" in s.value for s in at.success)
    assert at.text_area(key="paste_1").value == ""               # bereit fürs nächste Inserat


def test_manual_tab_label_buttons_survive_the_rerun(label_store):
    at = AppTest.from_file(APP, default_timeout=120).run()
    _button(at, "Beispiel: seriös").click().run()
    _button(at, "Inserat scannen").click().run()
    assert not at.exception
    buttons = [b for b in at.button if b.label == "✓ Seriös"]
    buttons[-1].click().run()                                    # Button im Tab „Felder selbst eingeben“
    stored = [json.loads(line) for line in label_store.read_text(encoding="utf-8").splitlines()]
    assert len(stored) == 1 and stored[0]["label"] == 0
