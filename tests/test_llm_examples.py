"""Vergleichsfälle: gelabelte Inserate aus dem eigenen Datensatz fließen in den Prompt des lokalen LLM."""

import json

import pytest

from scamguard.config import load_config, with_overrides
from scamguard.models.llm_examples import ExampleBank, count_reviewed, load_labeled
from scamguard.models.llm_judge import LLMJudgeDetector
from scamguard.schema import Listing

CFG = load_config()
httpx = pytest.importorskip("httpx")

TICKETS_BETRUG = [("2 Tickets Konzert Köln", "Bezahlung nur per PayPal Freunde, Tickets schicke ich per Mail."),
                  ("Bahngutschein 10 € für 1 €", "Erst zahlen, dann schicke ich den Code per WhatsApp.")]
TICKETS_SERIOES = [("2 Tickets Konzert Mannheim", "Tickets für das Konzert, Abholung in Mannheim, Barzahlung."),
                   ("Konzert Tickets Stehplatz", "Tickets für das Konzert, persönliche Übergabe am Einlass.")]


def _folder(root, label, name, title, text, **extra):
    path = root / label / "tickets" / name
    path.mkdir(parents=True)
    data = {"label": label, "title": title, "description": text, "price": 50.0, "category": "tickets", **extra}
    (path / "inserat.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def dataset(tmp_path):
    listing_dir, label_file = tmp_path / "inserate", tmp_path / "eigene_labels.jsonl"
    for i, (title, text) in enumerate(TICKETS_BETRUG):
        _folder(listing_dir, "betrug", f"b{i}__k{i}", title, text)
    for i, (title, text) in enumerate(TICKETS_SERIOES):
        _folder(listing_dir, "serioes", f"s{i}__k{i}", title, text)
    _folder(listing_dir / "betrug", "synthetisch", "x__1", "Synthetisch", "zählt wie jeder andere Fall")
    _folder(listing_dir, "betrug", "_testartefakt/y__2", "Testartefakt", "Ordner mit _ werden ignoriert")
    label_file.write_text(json.dumps({"title": "Sofa grau", "description": "Ruf an: 0151 2345678", "label": 0,
                                      "category": "moebel"}, ensure_ascii=False) + "\n", encoding="utf-8")
    return label_file, listing_dir


def test_labeled_listings_are_loaded_deduplicated_and_anonymized(dataset):
    label_file, listing_dir = dataset
    found = load_labeled(label_file, listing_dir)
    titles = {l.title for l in found}
    assert len(found) == 6 and "Synthetisch" in titles and "Testartefakt" not in titles
    sofa = next(l for l in found if l.title == "Sofa grau")
    assert "2345678" not in sofa.description  # Telefonnummer anonymisiert
    assert {l.label for l in found} == {0, 1}


def test_count_reviewed_includes_team_folders_not_only_own_labels(dataset, monkeypatch):
    from scamguard.data import labels

    label_file, listing_dir = dataset
    monkeypatch.setattr(labels, "LABEL_FILE", str(label_file))
    monkeypatch.setattr(labels, "DATASET_DIR", str(listing_dir))
    # Web-App und Extension zeigen damit dieselbe Menge, aus der das LLM seine Vergleichsfälle zieht
    assert count_reviewed() == {"gesamt": 6, "betrug": len(TICKETS_BETRUG) + 1,
                                "serioes": len(TICKETS_SERIOES) + 1, "eigene": 1}


def test_examples_are_similar_balanced_and_never_the_listing_itself(dataset):
    label_file, listing_dir = dataset
    bank = ExampleBank(4, label_file=label_file, listing_dir=listing_dir)
    query = Listing(title="Tickets Konzert Hamburg", category="tickets",
                    description="Bezahlung per PayPal Freunde, die Tickets schicke ich per Mail oder WhatsApp.")
    picked = bank.select(query)
    assert len(picked) == 4 and sum(l.label == 1 for l in picked) == 2  # je Klasse gleich viele
    assert picked[0].title == "2 Tickets Konzert Köln"  # ähnlichster Fall zuerst

    # Ein bereits gelabeltes Inserat sieht sich nie selbst (sonst „weiß“ das LLM die Antwort)
    labeled = Listing(title="2 Tickets Konzert Köln", category="tickets",
                      description="Bezahlung nur per PayPal Freunde, Tickets schicke ich per Mail.")
    assert all(l.title != labeled.title for l in bank.select(labeled))
    block = bank.prompt_block(query)
    assert block.startswith("<vergleichsfaelle>") and "BETRUG" in block and "SERIÖS" in block


def test_unrelated_listings_get_no_examples(dataset):
    label_file, listing_dir = dataset
    bank = ExampleBank(4, label_file=label_file, listing_dir=listing_dir)
    # Unpassende Vergleichsfälle führen eher in die Irre als keine
    assert bank.select(Listing(title="Kinderwagen Bugaboo", description="Regenschutz dabei, kaum genutzt.")) == []


def test_one_shared_word_does_not_make_listings_similar(dataset):
    label_file, listing_dir = dataset
    bank = ExampleBank(4, label_file=label_file, listing_dir=listing_dir)
    # Schuhe kommen im Datensatz nicht vor: ihre Wörter zählen trotzdem, sonst bliebe nur „Mail“ übrig und
    # die Ticket-Fälle sähen wie perfekte Treffer aus. Standardfloskeln verbinden ohnehin nichts.
    shoes = Listing(title="Schwarze Lackschuhe", category="mode",
                    description="Lackschuhe Größe 43, einmal getragen. Privatverkauf unter Ausschluss jeglicher "
                                "Gewährleistung, Versand möglich. Fotos gerne per Mail.")
    assert bank.select(shoes) == []


def test_one_class_never_fills_all_slots(dataset):
    label_file, listing_dir = dataset
    bank = ExampleBank(4, label_file=label_file, listing_dir=listing_dir)
    # Ähnlich sind hier nur die Betrugsfälle – trotzdem höchstens die Hälfte der Plätze
    picked = bank.select(Listing(title="Bahngutschein", description="Erst zahlen, dann schicke ich den Code per WhatsApp."))
    assert 1 <= len(picked) <= 2 and all(l.label == 1 for l in picked)


def test_evaluated_listings_are_hidden_from_examples(dataset):
    from scamguard.data.build import listing_fingerprint

    label_file, listing_dir = dataset
    bank = ExampleBank(4, label_file=label_file, listing_dir=listing_dir)
    query = Listing(title="Tickets Konzert Hamburg", category="tickets", description="Tickets per PayPal Freunde.")
    hidden = bank.select(query)[0]
    bank.exclude([listing_fingerprint(hidden)])  # z. B. die Testmenge bei `scamguard evaluate --llm`
    assert all(l.title != hidden.title for l in bank.select(query))


def test_local_llm_gets_examples_in_prompt_and_reports_them(dataset, monkeypatch):
    from scamguard.data import labels

    label_file, listing_dir = dataset
    monkeypatch.setattr(labels, "LABEL_FILE", str(label_file))
    monkeypatch.setattr(labels, "DATASET_DIR", str(listing_dir))
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        reply = {"scam_probability": 0.8, "scam_type": "vorkasse", "language_quality": "gut",
                 "red_flags": [], "summary": "Vorkasse für Tickets"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)},
                                                      "finish_reason": "stop"}]})

    det = LLMJudgeDetector(with_overrides(CFG, {"llm": {"enabled": True, "provider": "local", "beispiele": 4}}))
    det.backend._transport = httpx.MockTransport(handler)
    result = det.predict(Listing(title="Tickets Konzert Stuttgart", category="tickets",
                                 description="Tickets für das Konzert, Bezahlung per PayPal Freunde, ich schicke "
                                             "sie per Mail oder WhatsApp."))
    user = sent[0]["messages"][1]["content"]
    assert "<vergleichsfaelle>" in user and user.index("<vergleichsfaelle>") < user.index("<inserat>")
    info = next(s for s in result.signals if s.code == "LLM_EXAMPLES")
    assert info.info and "4 ähnlichen" in info.message and "2 Betrug, 2 seriös" in info.message


def test_all_datasets_join_as_one_pool(dataset, tmp_path):
    label_file, listing_dir = dataset
    processed = tmp_path / "processed"
    processed.mkdir()
    rows = [  # aus den Splits: KI-generiert, Warnmeldung – und ein Doppelter eines geprüften Inserats
        {"title": "Tickets Konzert Berlin", "description": "Zahlung per PayPal Freunde, Tickets per Mail.",
         "label": 1, "category": "tickets", "source": "hf:tanaos/synthetic"},
        {"description": "Betrüger verkaufen Tickets Konzert gegen PayPal Freunde und verschwinden.",
         "label": 1, "category": "tickets", "source": "datei:gesammelt_warnungen.csv"},
        {"title": "2 Tickets Konzert Köln", "description": "Bezahlung nur per PayPal Freunde, Tickets schicke "
         "ich per Mail.", "label": 1, "category": "tickets", "source": "hf:doppelt"},
    ]
    # U+2028 im Text: darf die JSONL-Zeile nicht zerteilen
    rows[1]["description"] += "\u2028Zweite Zeile."
    (processed / "train.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                                           encoding="utf-8")
    bank = ExampleBank(4, label_file=label_file, listing_dir=listing_dir, processed_dir=processed)
    bank._refresh()
    titles = [l.title or l.description[:20] for l in bank._listings]
    assert len(titles) == 8 and "Tickets Konzert Berlin" in titles  # 6 aus Ordnern/Labels + 2 neue
    assert titles.count("2 Tickets Konzert Köln") == 1  # Doppelter nur einmal


def test_examples_only_for_local_model():
    det = LLMJudgeDetector(with_overrides(CFG, {"llm": {"enabled": True, "provider": "local", "beispiele": 0}}))
    assert det.backend.examples is None


def test_evaluate_hides_the_evaluated_split_from_examples(dataset):
    from types import SimpleNamespace

    from scamguard.data.build import listing_fingerprint
    from scamguard.evaluate import _hide_from_llm_examples

    label_file, listing_dir = dataset
    bank = ExampleBank(4, label_file=label_file, listing_dir=listing_dir)
    evaluated = [Listing(title=t, description=d, category="tickets", label=1) for t, d in TICKETS_BETRUG]
    guard = SimpleNamespace(detectors=[SimpleNamespace(backend=SimpleNamespace(examples=bank)), SimpleNamespace()])
    _hide_from_llm_examples(guard, evaluated)
    assert bank._excluded == {listing_fingerprint(l) for l in evaluated}


def test_prompt_stays_small_even_for_huge_listings():
    from scamguard.models.llm_judge import _format_listing

    huge = Listing(title="x" * 1000, description="Wort " * 20000, messages=["alt " * 5000, "neu " * 5000])
    prompt = _format_listing(huge)
    assert len(prompt) < 12000  # sonst sprengt ein Inserat den Grafikspeicher des lokalen Modells
    assert "neu" in prompt.split("Chat-Nachrichten:")[1]  # die neuesten Nachrichten bleiben


def test_binary_listings_are_no_examples(dataset):
    label_file, listing_dir = dataset
    _folder(listing_dir, "betrug", "jfif__x", "", "", messages=["ÿØÿà\x00\x10JFIF\x00\x01\x01" * 200])
    assert all("JFIF" not in " ".join(l.messages) for l in load_labeled(label_file, listing_dir))
