"""Datensätze im Team: Export-ZIP einer Person → Import bei einer anderen (ohne Doppelte, sicher)."""

import json
import zipfile

import pytest

from scamguard.data import labels, team


@pytest.fixture
def projekt(tmp_path, monkeypatch):
    """Zwei getrennte „Rechner“: Ordner werden pro Aufruf über resolve_path umgeleitet."""
    state = {"root": tmp_path / "jannis"}
    monkeypatch.setattr(team, "resolve_path", lambda p: state["root"] / p)
    monkeypatch.setattr(team, "_annotation_dir", lambda: state["root"] / "data" / "einstufung")
    monkeypatch.setattr(labels, "sync_dataset_folders", lambda: 0)
    return state


def _write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_export_import_roundtrip(projekt, tmp_path):
    root = projekt["root"]
    inserate = root / "data" / "datensatz_fuellen_inserate"
    _write(inserate / "betrug" / "elektronik" / "ps5__abc123" / "inserat.json", '{"title": "PS5"}')
    _write(inserate / "betrug" / "elektronik" / "ps5__abc123" / "bild_1.jpg")
    _write(inserate / "serioes" / "moebel" / "sofa__def456" / "inserat.json", '{"title": "Sofa"}')
    _write(inserate / "serioes" / "screenshot.png")
    _write(inserate / "synthetisch" / "betrug" / "chat_x" / "inserat.txt")       # nicht exportieren
    _write(root / "data" / "datensatz_fuellen_text" / "gesammelt_foren.csv", "nachricht;betrug\nGeld zuerst;ja\n")
    _write(root / "data" / "datensatz_fuellen_text" / "_vorlage_inserate.csv")    # nicht exportieren
    _write(root / "data" / "datensatz_fuellen_bilder" / "betrug" / "fake.jpg")
    _write(root / "data" / "einstufung" / "einstufungen_jannis.jsonl", '{"id": 1}\n')

    zip_path, counts = team.export("Jannis", tmp_path / "ausgang")
    assert counts["inserate_betrug"] == 1 and counts["inserate_serioes"] == 1
    names = zipfile.ZipFile(zip_path).namelist()
    assert not any("synthetisch" in n or "_vorlage" in n for n in names)
    assert json.loads(zipfile.ZipFile(zip_path).read(team.MANIFEST))["person"] == "jannis"

    # Bei mir: ein Inserat habe ich schon (gleicher Ordner), das andere ist neu
    projekt["root"] = mine = tmp_path / "viet"
    _write(mine / "data" / "datensatz_fuellen_inserate" / "betrug" / "elektronik" / "ps5__abc123" / "inserat.json")
    results = team.import_inbox([tmp_path / "ausgang"])
    r = results[0]
    assert r["person"] == "Jannis"
    assert r["inserate_betrug_vorhanden"] == 1 and r["inserate_serioes_neu"] == 2   # Sofa + Screenshot
    target = mine / "data" / "datensatz_fuellen_inserate"
    assert (target / "serioes" / "moebel" / "sofa__def456" / "inserat.json").exists()
    assert (target / "serioes" / "team_jannis__screenshot.png").exists()
    assert (mine / "data" / "datensatz_fuellen_text" / "team_jannis__gesammelt_foren.csv").exists()
    assert (mine / "data" / "datensatz_fuellen_bilder" / "betrug" / "team_jannis" / "fake.jpg").exists()
    assert (mine / "data" / "einstufung" / "einstufungen_jannis.jsonl").exists()

    again = team.import_inbox([tmp_path / "ausgang"])                                # schon eingelesen
    assert again[0]["übersprungen"]


def test_import_rejects_unsafe_paths(projekt, tmp_path):
    bad = tmp_path / "eingang" / "boese.zip"
    bad.parent.mkdir()
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr(team.MANIFEST, json.dumps({"person": "x"}))
        zf.writestr("../../ausserhalb.txt", "nein")
        zf.writestr("datensatz_fuellen_inserate/betrug/ok__1/inserat.json", "{}")
    team.import_inbox([bad.parent])
    assert not (tmp_path / "ausserhalb.txt").exists()
    assert (projekt["root"] / "data" / "datensatz_fuellen_inserate" / "betrug" / "ok__1" / "inserat.json").exists()


def test_import_without_manifest_is_reported(projekt, tmp_path):
    other = tmp_path / "eingang" / "fremd.zip"
    other.parent.mkdir()
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("irgendwas.txt", "x")
    assert "kein ScamGuard-Export" in team.import_inbox([other.parent])[0]["fehler"]


def test_person_slug():
    assert team.person_slug("Jürgen Groß") == "juergengross"
    with pytest.raises(ValueError):
        team.person_slug("  ")
