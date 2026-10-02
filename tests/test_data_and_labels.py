"""Ablageordner (datensatz_fuellen_*), eigene Labels und Optimierungen – alles in Temp-Ordnern."""

import json
import os

import pytest
from PIL import Image

from scamguard.config import load_config, with_overrides
from scamguard.data import discovery, labels
from scamguard.data.loaders import load_spec
from scamguard.schema import Listing

CFG = load_config()


@pytest.fixture
def folders(tmp_path, monkeypatch):
    text, images = tmp_path / "datensatz_fuellen_text", tmp_path / "datensatz_fuellen_bilder"
    text.mkdir()
    (images / "betrug").mkdir(parents=True)
    (images / "serioes").mkdir()
    monkeypatch.setattr(discovery, "TEXT_DIR", str(text))
    monkeypatch.setattr(discovery, "IMAGE_DIR", str(images))
    return text, images


@pytest.fixture
def label_store(tmp_path, monkeypatch):
    monkeypatch.setattr(labels, "LABEL_FILE", str(tmp_path / "eigene_labels.jsonl"))
    monkeypatch.setattr(labels, "LABEL_IMAGE_DIR", str(tmp_path / "label_bilder"))
    return tmp_path


def _specs():
    return {s.name: s for s in discovery.discover_specs()}


# --------------------------------------------------------------------------- Textordner

def test_german_excel_csv_is_detected_with_aliases(folders):
    text, _ = folders
    # Excel-Export: Semikolon, Windows-Zeichensatz, deutsche Spaltennamen und Label-Wörter
    (text / "inserate.csv").write_bytes(
        "Titel;Beschreibung;Preis;Betrug\n"
        "PS5;Zahlung nur per PayPal Freunde und Familie, ich bin im Ausland;120 €;ja\n"
        "Kühlschrank;Läuft super, Abholung in Mannheim;150;nein\n".encode("cp1252"))
    (text / "_vorlage.csv").write_text("titel;betrug\nx;ja\n", encoding="utf-8")  # wird ignoriert
    specs = _specs()
    assert {n for n in specs if n.startswith("datei:")} == {"datei:inserate.csv"}  # _vorlage ignoriert
    spec = specs["datei:inserate.csv"]
    assert spec.enabled and spec.read_kwargs == {"sep": ";", "encoding": "cp1252"}
    assert spec.column_map == {"title": "Titel", "description": "Beschreibung", "price": "Preis"}
    report = load_spec(spec)
    assert report.error is None
    assert [(l.title, l.label, l.price) for l in report.listings] == [("PS5", 1, 120.0), ("Kühlschrank", 0, 150.0)]


def test_file_without_label_column_is_listed_but_disabled(folders):
    text, _ = folders
    (text / "ohne_label.jsonl").write_text(json.dumps({"text": "Hallo"}) + "\n", encoding="utf-8")
    spec = _specs()["datei:ohne_label.jsonl"]
    assert not spec.enabled and "Label-Spalte" in spec.notes


def test_huggingface_yaml_entries(folders):
    text, _ = folders
    (text / "huggingface.yaml").write_text(
        "datensaetze:\n"
        "  - id: org/spam\n    spalten: {description: text}\n    label_spalte: label\n"
        "    labels: {spam: betrug, ham: seriös}\n"
        "  - id: org/phishing\n    alles_ist: betrug\n    aktiv: false\n", encoding="utf-8")
    specs = _specs()
    assert specs["hf:org/spam"].label_map == {"spam": 1, "ham": 0}
    assert specs["hf:org/phishing"].fixed_label == 1 and not specs["hf:org/phishing"].enabled


# --------------------------------------------------------------------------- Bilderordner

def test_image_folders_become_labeled_image_datasets(folders):
    _, images = folders
    for name in ("a.jpg", "b.png"):
        Image.new("RGB", (8, 8), "red").save(images / "betrug" / name)
    (images / "unklar").mkdir()
    Image.new("RGB", (8, 8)).save(images / "unklar" / "c.jpg")
    specs = _specs()
    assert specs["bilder:betrug"].enabled and specs["bilder:betrug"].fixed_label == 1
    assert not specs["bilder:serioes"].enabled  # leer
    assert not specs["bilder:unklar"].enabled and "Label" in specs["bilder:unklar"].notes
    report = load_spec(specs["bilder:betrug"])
    assert len(report.listings) == 2 and all(l.label == 1 and l.image_paths for l in report.listings)


# --------------------------------------------------------------------------- eigene Labels

def test_relabeling_replaces_previous_label(label_store):
    first = Listing(title="PS5", description="Freunde und Familie", label=1, url="https://x/s-anzeige/1")
    assert labels.save_label(first) == 1
    corrected = Listing(title="PS5", description="Freunde und Familie", label=0, url="https://x/s-anzeige/1")
    assert labels.save_label(corrected) == 1  # ersetzt statt doppelt
    labels.save_label(Listing(description="anderer Text", label=1))
    assert labels.count_labels() == {"gesamt": 2, "betrug": 1, "serioes": 1}


def test_label_images_are_stored_once_by_content(label_store):
    paths = labels.save_label_images([(".jpg", b"bild"), (".jpg", b"bild")])
    assert paths[0] == paths[1] and len(os.listdir(label_store / "label_bilder")) == 1


def test_api_label_endpoint(label_store):
    from fastapi.testclient import TestClient

    from scamguard.api import app

    client = TestClient(app)
    data = {"listing": json.dumps({"title": "PS5", "description": "Vorkasse bitte", "price": "120 €"}),
            "label": "betrug"}
    resp = client.post("/label", data=data, headers={"X-ScamGuard-Client": "pytest"},
                       files=[("images", ("bild_0.jpg", b"jpegdaten", "image/jpeg"))])
    assert resp.json() == {"ok": True, "label": "betrug", "count": 1}
    stored = json.loads((label_store / "eigene_labels.jsonl").read_text(encoding="utf-8"))
    assert stored["label"] == 1 and stored["price"] == 120.0 and len(stored["image_paths"]) == 1
    assert client.post("/label", data={**data, "label": "vielleicht"},
                       headers={"X-ScamGuard-Client": "pytest"}).status_code == 422
    assert client.post("/label", data=data).status_code == 403  # Cross-Site-Schutz auch hier


# --------------------------------------------------------------------------- Optimierungen

def test_second_stage_reuses_fast_results(monkeypatch):
    from fastapi.testclient import TestClient

    from scamguard import api

    seen = []
    real = api._guard

    class Spy:
        def __init__(self, guard):
            self.guard = guard

        def scan(self, listing, reuse=None):
            seen.append(sorted(reuse) if reuse else None)
            return self.guard.scan(listing, reuse=reuse)

    monkeypatch.setattr(api, "_guard", lambda use_llm: Spy(real(False)))
    client, headers = TestClient(api.app), {"X-ScamGuard-Client": "pytest"}
    data = {"listing": json.dumps({"title": "Cache-Test", "description": "Bitte Vorkasse"})}
    client.post("/scan", data={**data, "use_llm": "false"}, headers=headers)
    client.post("/scan", data={**data, "use_llm": "true"}, headers=headers)
    assert seen[0] is None and "rules" in seen[1] and "llm" not in seen[1]


def test_text_model_reloads_after_retraining(tmp_path):
    from scamguard.models.text_classifier import TextBaselineDetector, train_text_baseline

    cfg = with_overrides(CFG, {"text_model": {"baseline_path": str(tmp_path / "m.joblib")}})
    det = TextBaselineDetector(cfg)
    assert not det.available
    data = [Listing(description=f"Vorkasse Ausland {i}", label=1) for i in range(5)] + \
           [Listing(description=f"Abholung Mannheim {i}", label=0) for i in range(5)]
    train_text_baseline(data * 2, cfg)
    assert det.available  # ohne Neustart erkannt
    assert det.predict(Listing()).score is None  # kein Text → kein Text-Urteil


def test_clip_only_hints_do_not_lower_the_score(tmp_path, monkeypatch):
    from scamguard.models.image_model import ImageDetector

    img = tmp_path / "foto.jpg"
    Image.new("RGB", (32, 32), "white").save(img)
    det = ImageDetector(with_overrides(CFG, {"image_model": {"path": str(tmp_path / "kein.pt")}}))
    monkeypatch.setattr(det, "_ensure_clip", lambda: True)
    monkeypatch.setattr(det, "_clip_probs", lambda image: {
        "style": {"real": 0.1, "stock": 0.8, "screenshot": 0.05, "text": 0.05}, "category": {}})
    result = det.predict(Listing(title="x", image_paths=[str(img)]))
    assert result.score is None and "Nur Hinweise" in result.error
    assert any(s.code == "STOCK_PHOTO" and s.target == "image:0" for s in result.signals)


def test_retrain_warns_about_one_sided_data():
    from scamguard.training import balance_warning

    def stats(scam, total):
        return {"total": total, "splits": {"train": {"n": total, "scam": scam}}}

    assert balance_warning(stats(50, 100)) is None
    assert "seriöse" in balance_warning(stats(610, 640))
    assert "Betrugs-" in balance_warning(stats(3, 100))
