"""Annotation-Workspace: Kategoriensystem, Übereinstimmung, Sätze, Konsens, Export, Web-App, CLI."""

import json
from pathlib import Path

import pytest
import yaml
from streamlit.testing.v1 import AppTest

from scamguard.cli import main
from scamguard.config import load_config, resolve_path
from scamguard.data import agreement, annotation
from scamguard.data.annotation import (
    Einstufung,
    Entscheidung,
    Werkstatt,
    image_urteil,
    make_aufgabe,
    regel_signale,
    saetze,
    split_sentences,
)
from scamguard.data.build import listing_fingerprint
from scamguard.data.codebook import load_codebook
from scamguard.data.loaders import load_spec
from scamguard.data.redact import redact
from scamguard.data.registry import DatasetSpec
from scamguard.finetune import target_answer
from scamguard.models.image_model import STYLE_PROMPTS
from scamguard.models.llm_judge import LANGUAGE_QUALITY, SCAM_TYPES
from scamguard.schema import Listing

CFG = load_config()
CB = load_codebook()
APP = str(Path(__file__).resolve().parents[1] / "frontend" / "app.py")
SCAM_CHAT = ("Abholung ist leider nicht möglich, ich bin im Ausland. Überweisen Sie bitte vorab. "
             "Es gibt noch drei andere Interessenten.")
LEGIT_CHAT = "Hallo, ist das Fahrrad noch da? Ich könnte morgen Abend zum Abholen vorbeikommen."


@pytest.fixture(autouse=True)
def no_real_data(tmp_path, monkeypatch):
    """Kein Test schreibt in echte Daten: weder data/raw/ noch einen gemeinsamen Team-Ordner."""
    monkeypatch.setattr(annotation, "EXPORT_DIR", str(tmp_path / "export"))
    monkeypatch.delenv(annotation.FOLDER_ENV, raising=False)


@pytest.fixture
def ws(tmp_path) -> Werkstatt:
    return Werkstatt(tmp_path / "einstufung", doppelt_anteil=1.0)


def _rate(ws, task_id, person, urteil, masche=None, marks=(), **extra):
    task = ws.aufgaben()[task_id]
    sentences = {s.nr: s.text for s in saetze(task.listing)}
    ws.speichern(Einstufung(task_id, person, urteil, masche=masche,
                            saetze=[{"nr": nr, "text": sentences[nr], "signal": sig} for nr, sig in marks],
                            **extra))


def _task(ws, text=SCAM_CHAT, person="anna", **listing):
    item = Listing(messages=[text], **listing)
    ws.hinzufuegen([item], "test", person)
    return make_aufgabe(item, "test", person).id


# --------------------------------------------------------------------------- Kategoriensystem

def test_codebook_matches_llm_rules_and_image_model():
    lexicon = yaml.safe_load(resolve_path(CFG["paths"]["lexicon"]).read_text(encoding="utf-8"))["groups"]
    contact_codes = {"LOOKALIKE_DOMAIN", "URL_SHORTENER", "SUSPICIOUS_TLD", "IP_URL", "PUNYCODE_URL",
                     "EXTERNAL_LINK", "IMPERSONATION_EMAIL", "EMAIL_IN_TEXT", "FOREIGN_PHONE", "PHONE_IN_TEXT",
                     "IBAN_IN_TEXT", "FOREIGN_IBAN"}
    known = {g.upper() for g in lexicon} | contact_codes
    mapped = CB.signal_by_rule
    assert set(mapped) <= known, f"Unbekannte Regel-Codes im Codebuch: {set(mapped) - known}"
    # Jede Lexikon-Gruppe gehört zu einem Warnsignal – sonst fehlt sie im Vergleich „Regeln gegen Mensch“
    assert {g.upper() for g in lexicon} <= set(mapped), "Neue Lexikon-Gruppe in kategorien.yaml zuordnen"
    assert all(entry["llm"] in SCAM_TYPES for entry in CB.maschen.values())
    assert list(CB.sprache) == LANGUAGE_QUALITY
    assert {e.get("clip") for e in CB.bildarten.values()} - {None} <= set(STYLE_PROMPTS)
    for group in (CB.maschen, CB.signale, CB.bildarten):
        assert all(entry.get("name") and entry.get("definition") for entry in group.values())


# --------------------------------------------------------------------------- Übereinstimmung

def test_krippendorff_alpha_matches_published_example():
    # Krippendorff (2011), „Computing Krippendorff's Alpha-Reliability“, nominales Beispiel: α = 0,743
    n = None
    coders = [[1, 2, 3, 3, 2, 1, 4, 1, 2, n, n, n], [1, 2, 3, 3, 2, 2, 4, 1, 2, 5, n, 3],
              [n, 3, 3, 3, 2, 3, 4, 2, 2, 5, 1, n], [1, 2, 3, 3, 2, 4, 4, 1, 2, 5, 1, n]]
    units = {i: [c[i] for c in coders if c[i] is not None] for i in range(12)}
    assert agreement.krippendorff_alpha(units) == pytest.approx(0.743, abs=5e-4)


def test_agreement_edge_cases():
    assert agreement.krippendorff_alpha({1: ["a", "a"], 2: ["b", "b"]}) == 1.0
    assert agreement.krippendorff_alpha({1: ["a", "b"], 2: ["b", "a"]}) < 0
    assert agreement.krippendorff_alpha({1: ["a", "a"], 2: ["a", "a"]}) is None   # nur eine Kategorie
    assert agreement.krippendorff_alpha({1: ["a"]}) is None                        # nichts doppelt
    assert agreement.krippendorff_alpha({0: [1, 1], 1: [1, 0], 2: [0, 0], 3: [0, 0]}) == pytest.approx(0.5333, abs=1e-4)
    assert agreement.percent_agreement({1: ["a", "a"], 2: ["a", "b"], 3: ["a"]}) == 0.5
    # Cohens Kappa wie in Lehrbüchern: p_o = 0,75, p_e = 0,53125 → κ ≈ 0,4667
    pairs = list(zip([1, 1, 0, 0, 1, 0, 1, 1], [1, 0, 0, 0, 1, 1, 1, 1], strict=True))
    assert agreement.cohen_kappa(pairs) == pytest.approx(0.46667, abs=1e-4)
    assert agreement.cohen_kappa([("a", "a"), ("a", "a")]) is None
    assert agreement.rating(0.85) == "verlässlich" and agreement.rating(0.7) == "vorläufig verwendbar"


# --------------------------------------------------------------------------- Sätze

@pytest.mark.parametrize("text, expected", [
    ("Ist der Artikel noch da? Ich zahle sofort. Schreib mir z. B. per Mail.",
     ["Ist der Artikel noch da?", "Ich zahle sofort.", "Schreib mir z. B. per Mail."]),
    ("Baujahr 2019. Abholung am 3. Oktober, ca. 20 Min. vom Bahnhof.",
     ["Baujahr 2019.", "Abholung am 3. Oktober, ca. 20 Min. vom Bahnhof."]),
    ("erste zeile\nZweite Zeile\n\nDritte.", ["erste zeile", "Zweite Zeile", "Dritte."]),
    ("Tel. 0111 1111111 bitte", ["Tel. 0111 1111111 bitte"]),
])
def test_split_sentences(text, expected):
    assert split_sentences(text) == expected


def test_saetze_number_across_parts_and_rules_map_to_codebook():
    listing = Listing(title="PS5", description="Neu. Nur Versand.", messages=["Zahlung bitte per Freunde und Familie."])
    result = saetze(listing)
    assert [(s.nr, s.teil) for s in result] == [(1, "Titel"), (2, "Beschreibung"), (3, "Beschreibung"),
                                               (4, "Nachricht")]
    assert regel_signale(result[3].text) == {"zahlung_ausserhalb"}
    assert regel_signale("Es gibt noch drei andere Interessenten, wer zuerst zahlt.") == {"zeitdruck"}
    assert regel_signale(LEGIT_CHAT) == set()


# --------------------------------------------------------------------------- Aufgaben

def test_tasks_are_anonymized_with_stable_ids():
    a = Listing(messages=["Schreib mir an max.muster1987@gmail.com oder +44 7911 123456"], seller_name="Max",
                location="Mannheim", url="https://www.kleinanzeigen.de/s-anzeige/1", label=1)
    b = Listing(messages=["Schreib mir an erika.beispiel@gmail.com oder +44 7911 654321"])
    task_a, task_b = make_aufgabe(a, "q", "anna"), make_aufgabe(b, "q", "ben")
    text = task_a.listing.full_text
    assert "max.muster1987" not in text and "123456" not in text and "anonym@gmail.com" in text
    assert task_a.listing.seller_name is None and task_a.listing.location is None and task_a.listing.url is None
    assert task_a.listing.label is None and task_a.quelle_label == 1     # Label der Quelle nur getrennt
    assert task_a.id == task_b.id                                         # gleiche Vorlage → gleiche Aufgabe
    # Original und anonymisierte Fassung gelten beim Bauen der Splits als dasselbe Inserat (kein Leak)
    assert listing_fingerprint(a) == listing_fingerprint(task_a.listing)
    assert redact(redact(text)) == redact(text)


def test_queue_second_opinion_first_and_personal_order(ws):
    ids = [_task(ws, f"{SCAM_CHAT} Nummer {i}.") for i in range(6)]
    assert [t.id for t in ws.warteschlange("anna")] != [t.id for t in ws.warteschlange("ben")]
    _rate(ws, ids[0], "anna", "betrug", "vorkasse")
    assert ws.warteschlange("ben")[0].id == ids[0]                       # Zweitmeinung zuerst
    assert ids[0] not in [t.id for t in ws.warteschlange("anna")]
    _rate(ws, ids[0], "ben", "betrug", "vorkasse")
    assert ids[0] not in [t.id for t in ws.warteschlange("cem")]          # zwei Meinungen reichen
    single = Werkstatt(ws.folder, doppelt_anteil=0.0)
    assert {t.id for t in single.warteschlange("cem")} == set(ids[1:])    # ohne Doppel-Anteil: erledigt


def test_sampling_is_balanced_by_source_label(ws):
    listings = [Listing(messages=[f"Betrugstext Nummer {i} mit Vorkasse"], label=1) for i in range(40)]
    listings += [Listing(messages=[f"Normale Anfrage Nummer {i} zum Abholen"], label=0) for i in range(5)]
    new, existing = ws.hinzufuegen(listings, "q", "anna", max_neu=10)
    labels = [t.quelle_label for t in ws.aufgaben().values()]
    assert (new, existing) == (10, 0) and labels.count(0) == 5 and labels.count(1) == 5
    assert ws.hinzufuegen(listings[:3], "q", "anna")[1] <= 3               # bekannte nicht doppelt


def test_validation_and_latest_rating_wins(ws):
    tid = _task(ws)
    with pytest.raises(ValueError, match="Masche"):
        _rate(ws, tid, "anna", "betrug")
    with pytest.raises(ValueError, match="Urteil"):
        _rate(ws, tid, "anna", "")
    _rate(ws, tid, "anna", "betrug", "vorkasse")
    _rate(ws, tid, "anna", "serioes", "vorkasse")                         # Korrektur
    latest = ws.einstufungen()[tid]["anna"]
    assert latest.urteil == "serioes" and latest.masche is None and latest.leitfaden == CB.version
    lines = (ws.folder / "einstufungen_anna.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2                                                # Verlauf bleibt erhalten


# --------------------------------------------------------------------------- Konsens und Qualität

def test_consensus_status_and_sentence_majority(ws):
    one, two, three, conflict = (_task(ws, f"{SCAM_CHAT} Fall {i}.") for i in range(4))
    _rate(ws, one, "anna", "betrug", "vorkasse", marks=[(2, "zahlung_ausserhalb")])
    for person in ("anna", "ben"):
        _rate(ws, two, person, "betrug", "vorkasse",
              marks=[(2, "zahlung_ausserhalb")] + ([(1, "versandgeschichte")] if person == "anna" else []))
    _rate(ws, three, "anna", "betrug", "mietbetrug")
    _rate(ws, three, "ben", "betrug", "vorkasse")
    _rate(ws, three, "cem", "serioes")
    _rate(ws, conflict, "anna", "betrug", "vorkasse")
    _rate(ws, conflict, "ben", "serioes")
    k = ws.konsens()
    assert (k[one].status, k[one].label, k[one].saetze) == ("einfach", 1, {2: "zahlung_ausserhalb"})
    assert k[two].status == "einig" and k[two].saetze == {2: "zahlung_ausserhalb"}  # nur 1 von 2 → nicht drin
    assert k[three].status == "mehrheit" and k[three].urteil == "betrug" and k[three].masche == "vorkasse"
    assert k[conflict].status == "konflikt" and k[conflict].label is None
    ws.entscheiden(Entscheidung(conflict, "cem", "serioes", "vorkasse", "Verkäufer nennt Abholung"))
    decided = ws.konsens()[conflict]
    assert decided.status == "entschieden" and decided.label == 0 and decided.masche is None


def test_report_agreement_sources_and_hints(ws):
    ids = [_task(ws, f"{SCAM_CHAT} Variante {i}.") for i in range(4)]
    for i, tid in enumerate(ids):
        _rate(ws, tid, "anna", "betrug", "vorkasse", marks=[(2, "zahlung_ausserhalb")])
        _rate(ws, tid, "ben", "betrug" if i < 3 else "serioes", "vorkasse" if i < 3 else None,
              marks=[(2, "zahlung_ausserhalb")], dauer_s=1.0)
    legit = _task(ws, LEGIT_CHAT)
    for person in ("anna", "ben"):
        _rate(ws, legit, person, "serioes")
    report = ws.bericht()
    levels = {r["ebene"]: r for r in report["uebereinstimmung"]}
    assert report["doppelt_eingestuft"] == 5 and report["konflikte"] == [ids[3]]
    assert levels["Urteil (Betrug/seriös)"]["einheiten"] == 5
    assert levels["Urteil (Betrug/seriös)"]["prozent"] == pytest.approx(0.8)
    assert levels["Satz: Warnsignal ja/nein"]["alpha"] == pytest.approx(1.0)
    assert report["kappa"][0]["paar"] == "anna – ben" and report["kappa"][0]["n"] == 5
    assert report["auffaelligkeiten"] == []                               # kurze Texte: 1 s ist in Ordnung


def test_hints_for_suspicious_ratings(ws):
    long_text = SCAM_CHAT + " " + "Das Gerät ist gepflegt und funktioniert einwandfrei. " * 4
    quick, unmarked, contradiction = (_task(ws, f"{long_text} Fall {i}.") for i in range(3))
    _rate(ws, quick, "anna", "betrug", "vorkasse", marks=[(2, "zahlung_ausserhalb")], dauer_s=1.0)
    _rate(ws, unmarked, "anna", "betrug", "vorkasse")
    _rate(ws, contradiction, "anna", "serioes", marks=[(1, "versandgeschichte"), (2, "zahlung_ausserhalb")])
    hints = {a["aufgabe"]: a["hinweis"] for a in ws.auffaelligkeiten()}
    assert "sehr schnell" in hints[quick]
    assert hints[unmarked].startswith("Betrug, aber kein Satz")
    assert hints[contradiction].startswith("seriös, aber 2 Warnsignale")


def test_source_label_contradictions_are_counted(ws):
    ws.hinzufuegen([Listing(messages=[LEGIT_CHAT], label=1)], "spam_datensatz", "anna")
    tid = next(iter(ws.aufgaben()))
    _rate(ws, tid, "anna", "serioes")
    assert ws.bericht()["quellen"] == [{"quelle": "spam_datensatz", "n": 1, "widerspruch": 1}]


def test_rules_against_humans(ws):
    tid = _task(ws, "Ich bin gerade auf Montage. Bitte nur Freunde und Familie. Der Hund ist sehr lieb. "
                    "Abholung in Mannheim.")
    _rate(ws, tid, "anna", "betrug", "vorkasse", marks=[(2, "zahlung_ausserhalb"), (3, "sonstiges")])
    check = ws.regeln_gegen_mensch()
    rows = {r["signal"]: r for r in check["je_signal"]}
    assert check["saetze"] == 4
    assert rows["zahlung_ausserhalb"]["tp"] == 1 and rows["zahlung_ausserhalb"]["precision"] == 1.0
    assert check["verpasst"] == [{"signal": "sonstiges", "text": "Der Hund ist sehr lieb."}]
    assert check["gesamt"]["tp"] == 1 and check["gesamt"]["fn"] == 1


# --------------------------------------------------------------------------- Bilder

def test_image_tasks(ws, tmp_path):
    from PIL import Image

    picture = tmp_path / "foto.png"
    Image.new("RGB", (8, 8), "red").save(picture)
    ws.hinzufuegen([Listing(image_paths=[str(picture)])], "bilder", "anna")
    task = next(iter(ws.aufgaben().values()))
    assert task.nur_bilder and task.art == "Bild" and len(task.bilder[0]) == 16
    with pytest.raises(ValueError, match="Bild"):
        ws.speichern(Einstufung(task.id, "anna", "unklar"))
    marks = [{"bild": task.bilder[0], "art": "stockfoto", "verdaechtig": "ja"}]
    assert image_urteil(marks) == "betrug" and image_urteil([{"verdaechtig": "nein"}]) == "serioes"
    ws.speichern(Einstufung(task.id, "anna", image_urteil(marks), bilder=marks))
    counts = ws.export(tmp_path / "export")
    assert counts == {"konsens": 0, "saetze": 0, "bilder": 1}             # Bilder nicht in den Text-Splits
    row = json.loads((tmp_path / "export" / "einstufung_bilder.jsonl").read_text(encoding="utf-8"))
    assert row["art"] == "stockfoto" and row["verdaechtig"] == "ja"


# --------------------------------------------------------------------------- Export und Weiterverwendung

def test_export_feeds_dataset_and_finetuning(ws, tmp_path):
    scam, legit, conflict = _task(ws), _task(ws, LEGIT_CHAT), _task(ws, f"{SCAM_CHAT} Streitfall.")
    _rate(ws, scam, "anna", "betrug", "mietbetrug", marks=[(2, "zahlung_ausserhalb"), (3, "zeitdruck")],
          sprache="muttersprachlich")
    _rate(ws, legit, "anna", "serioes")
    _rate(ws, conflict, "anna", "betrug", "vorkasse")
    _rate(ws, conflict, "ben", "serioes")
    out = tmp_path / "export"
    counts = ws.export(out)
    assert counts["konsens"] == 2                                         # Konflikt bleibt draußen
    spec = DatasetSpec(name="einstufungen", source="jsonl", path=str(out / "einstufung_konsens.jsonl"),
                       german_only=False, enabled=True)
    listings = {l.label: l for l in load_spec(spec).listings}
    assert listings[1].scam_type == "mietbetrug" and listings[1].source == "einstufungen"
    assert [w["signal"] for w in listings[1].warnsignale] == ["zahlung_ausserhalb", "zeitdruck"]
    answer = target_answer(listings[1], CFG)
    assert answer["scam_type"] == "vorkasse"                              # Mietbetrug → Qwen-Kategorie
    assert [f["code"] for f in answer["red_flags"]] == ["zahlung_ausserhalb", "zeitdruck"]
    assert answer["red_flags"][0]["evidence"].startswith("Überweisen Sie bitte vorab")
    assert target_answer(listings[0], CFG)["red_flags"] == []


def test_merging_team_files(ws, tmp_path):
    tid = _task(ws)
    other = Werkstatt(tmp_path / "jannis", doppelt_anteil=1.0)
    other.zusammenfuehren("aufgaben_anna.jsonl", (ws.folder / "aufgaben_anna.jsonl").read_bytes(), "jannis")
    _rate(other, tid, "jannis", "betrug", "vorkasse")
    content = (other.folder / "einstufungen_jannis.jsonl").read_bytes()
    assert ws.zusammenfuehren("einstufungen_jannis.jsonl", content, "anna") == 1
    assert ws.zusammenfuehren("einstufungen_jannis.jsonl", content, "anna") == 0   # nichts doppelt
    assert "jannis" in ws.einstufungen()[tid]
    with pytest.raises(ValueError, match="eigene"):
        ws.zusammenfuehren("einstufungen_anna.jsonl", content, "anna")
    odd = json.dumps({"aufgabe": tid, "person": "[X](http://x.example)", "urteil": "serioes", "zeit": "1"})
    ws.zusammenfuehren("einstufungen_x.jsonl", odd.encode(), "anna")
    assert "xhttpxexample" in ws.einstufungen()[tid]                       # Name nur noch a–z/0–9
    with pytest.raises(ValueError, match="keine Workspace-Datei"):
        ws.zusammenfuehren("labels.jsonl", content, "anna")


def test_folder_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(annotation.FOLDER_ENV, str(tmp_path / "team"))
    assert Werkstatt().folder == tmp_path / "team"
    assert Werkstatt(tmp_path / "eigen").folder == tmp_path / "eigen"     # ausdrücklicher Ordner gewinnt


def test_cli_report_and_export(ws, tmp_path, monkeypatch, capsys):
    tid = _task(ws)
    _rate(ws, tid, "anna", "betrug", "vorkasse", marks=[(2, "zahlung_ausserhalb")])
    monkeypatch.setattr(annotation, "Werkstatt", lambda: ws)
    assert main(["einstufen", "bericht", "--regeln"]) == 0
    out = capsys.readouterr().out
    assert "Aufgaben 1" in out and "Krippendorffs" in out and "Regeln gegen Mensch" in out
    assert main(["einstufen", "holen"]) == 1                              # ohne Person
    assert main(["einstufen", "holen", "--person", "Anna"]) == 1          # ohne Datensatz
    assert main(["einstufen", "export"]) == 0
    assert (tmp_path / "export" / "einstufung_konsens.jsonl").exists()


# --------------------------------------------------------------------------- Web-App

@pytest.fixture
def app_folder(tmp_path, monkeypatch):
    folder = tmp_path / "einstufung"
    monkeypatch.setattr(annotation, "load_config",
                        lambda: {**CFG, "einstufung": {"ordner": str(folder), "doppelt_anteil": 1.0}})
    return folder


def test_web_app_rating_flow(app_folder):
    ws = Werkstatt()
    tid = _task(ws)
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    at.text_input(key="ws_person").input("Gabriel").run()
    assert not at.exception
    k = f"ws_{tid}"
    assert at.selectbox(key=f"{k}_s2").value is None
    at.selectbox(key=f"{k}_s2").set_value("zahlung_ausserhalb")
    at.segmented_control(key=f"{k}_urteil").set_value("betrug")
    at.segmented_control(key=f"{k}_sicher").set_value("sicher")
    next(b for b in at.button if b.label == "Speichern und weiter").click().run()
    assert any("Bei Betrug bitte die Masche" in e.value for e in at.error)   # Pflichtfeld fehlt

    at.selectbox(key=f"{k}_masche").set_value("vorkasse")
    next(b for b in at.button if b.label == "Speichern und weiter").click().run()
    assert not at.exception
    saved = Werkstatt().einstufungen()[tid]["gabriel"]
    assert saved.urteil == "betrug" and saved.masche == "vorkasse"
    assert saved.signal_of() == {2: "zahlung_ausserhalb"} and saved.dauer_s is not None
    assert any("Gespeichert: Betrug (Vorkasse" in s.value for s in at.success)

    at.segmented_control(key="ws_view").set_value("Qualität und Konflikte").run()
    assert not at.exception
    next(b for b in at.button if b.label == "Exportieren").click().run()
    assert not at.exception and any("1 Texte" in s.value for s in at.success)


def test_web_app_own_text_needs_consent_and_keeps_input(app_folder):
    label = "Text eines Inserats oder Chats"
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.text_input(key="ws_person").input("Gabriel").run()
    at.segmented_control(key="ws_view").set_value("Aufgaben holen").run()
    next(t for t in at.text_area if t.label == label).input("Schreib mir auf WhatsApp unter 0151 23456789.")
    next(b for b in at.button if b.label == "Als Aufgabe hinzufügen").click().run()
    assert any("Zustimmung" in e.value for e in at.error)
    assert next(t for t in at.text_area if t.label == label).value.startswith("Schreib mir")  # Text bleibt stehen
    assert Werkstatt().aufgaben() == {}

    next(c for c in at.checkbox if c.label.startswith("Der Text stammt")).check()
    next(b for b in at.button if b.label == "Als Aufgabe hinzufügen").click().run()
    assert not at.exception
    (task,) = Werkstatt().aufgaben().values()
    assert task.quelle == "eigene_eingabe" and "23456789" not in task.listing.full_text
    assert next(t for t in at.text_area if t.label == label).value == ""                       # bereit für den nächsten
