"""Anbieter-Merkmale (privat/gewerblich, Abzeichen, Rechtsform), neues Seitenlayout, Ordner-Ablage
beim Einstufen, Chat-Screenshots in Ordnern, GBERT-Laden mit transformers 5, optionale Modelle."""

import json
from datetime import datetime, timedelta

import pytest

from scamguard.cli import main
from scamguard.config import load_config
from scamguard.data import labels
from scamguard.data.listing_import import (
    import_paths,
    listing_from_html,
    listing_items,
    parse_rows,
    seller_info,
)
from scamguard.data.loaders import load_spec, normalize_category
from scamguard.data.registry import DatasetSpec
from scamguard.features import extract_features
from scamguard.models.base import noisy_or
from scamguard.models.llm_judge import _format_listing
from scamguard.schema import Listing, legal_form_of

CFG = load_config()
SINCE = (datetime.now().astimezone().date() - timedelta(days=2000)).strftime("%d.%m.%Y")

# Neues Seitenlayout (Stand 10/2026), Inhalte erfunden
NEW_LAYOUT = f"""<html><head><link rel="canonical" href="https://www.kleinanzeigen.de/s-anzeige/raeder/9-223-1"></head>
<body>
<div id="vap-brdcrmb" itemscope itemtype="https://schema.org/BreadcrumbList" class="flex">
  <span itemprop="itemListElement"><a class="inline-flex" itemprop="item"><span itemprop="name">Kleinanzeigen</span></a></span>
  <span itemprop="itemListElement"><a class="inline-flex" itemprop="item"><span itemprop="name">Auto, Rad &amp; Boot</span></a></span>
  <span itemprop="itemListElement"><a class="inline-flex" itemprop="item"><span itemprop="name">Autoteile &amp; Reifen</span></a></span>
</div>
<h1 id="viewad-title">4x Winterräder 17 Zoll</h1><h2 id="viewad-price">399 €</h2>
<p id="viewad-description-text">Komplettradsatz.<br>Montage möglich.</p>
<div id="viewad-imprint-section"><h2>Rechtliche Angaben</h2></div>
<div id="viewad-profile-box">
  <div id="viewad-bizteaser"><span>Muster Autoteile GmbH</span><button>Folgen</button></div>
  <div id="viewad-contact"><a>Muster Autoteile</a><a>TOP Zufriedenheit</a><a>Besonders zuverlässig</a>
    <span>Gewerblicher Nutzer</span><span>Aktiv seit {SINCE}</span></div>
  <span>248</span><span>Anzeigen</span><span>online</span>
</div></body></html>"""


def _codes(listing: Listing) -> dict[str, object]:
    return {s.code: s for s in extract_features(listing, CFG).signals}


# --------------------------------------------------------------------------- Datenmodell

def test_listing_normalizes_seller_fields():
    listing = Listing(seller_type="Gewerblicher Nutzer", seller_badges=["TOP Zufriedenheit", "Sehr freundlich",
                      "TOP Zufriedenheit", "Unsinn"], seller_num_ads="248", seller_legal_form="Muster GmbH")
    assert listing.seller_type == "gewerblich" and listing.seller_num_ads == 248
    assert listing.seller_badges == ["zufriedenheit_top", "sehr_freundlich"] and listing.seller_legal_form == "GmbH"
    assert Listing(seller_type="Hacker", seller_num_ads="viele").seller_type is None
    assert Listing.from_dict(Listing(seller_type="privat", seller_badges=["zuverlaessig"]).to_dict()).seller_badges == [
        "zuverlaessig"]


@pytest.mark.parametrize("name, form", [
    ("Deutsche Reihenhaus AG", "AG"), ("Auto Schmidt GmbH & Co. KG", "GmbH & Co. KG"), ("Muster GbR", "GbR"),
    ("Bau UG (haftungsbeschränkt)", "UG (haftungsbeschränkt)"), ("Kfz Meier e.K.", "e.K."), ("Hans OHG", "OHG"),
    ("Max Mustermann", None), ("Jörg Agathe", None), ("Agentur", None), ("Volkswagen AG Golf 7 TDI", None),
])
def test_legal_form_only_at_name_end(name, form):
    assert legal_form_of(name) == form


def test_service_category_wins_over_subcategory():
    assert normalize_category("Kleinanzeigen > Dienstleistungen > Auto, Rad & Boot") == "dienstleistungen"
    assert normalize_category("Kleinanzeigen > Auto, Rad & Boot > Autos") == "auto"


# --------------------------------------------------------------------------- Auslesen

def test_seller_info_from_profile_texts():
    private = seller_info(["DC", "Dr. Clemens Beispiel", "TOP Zufriedenheit", "Zuverlässig", "Privater Nutzer",
                           "Aktiv seit 30.09.2026", "7 Anzeigen online", "Folgen"])
    assert private == {"seller_type": "privat", "seller_badges": ["zufriedenheit_top", "zuverlaessig"],
                       "seller_num_ads": 7}                        # Jahreszahl ist keine Anzeigenzahl
    business = seller_info(["Deutsche Reihenhaus AG", "Folgen", "Gewerblicher Nutzer", "248", "Anzeigen"])
    assert business == {"seller_type": "gewerblich", "seller_num_ads": 248, "seller_legal_form": "AG"}


def test_saved_page_in_new_layout():
    fields = listing_from_html(NEW_LAYOUT)
    assert fields["category"] == "auto" and fields["price"] == 399.0
    assert fields["seller_type"] == "gewerblich" and fields["seller_legal_form"] == "GmbH"
    assert fields["seller_badges"] == ["zufriedenheit_top", "besonders_zuverlaessig"] and fields["seller_num_ads"] == 248
    assert 1990 <= fields["seller_account_age_days"] <= 2010
    assert "seller_name" not in fields and "Muster Autoteile" not in json.dumps(fields)


def test_screenshot_rows_take_legal_form_only_from_the_name_row():
    rows = ["Kleinanzeigen › Auto, Rad & Boot › Autos", "VW Golf 7", "8.500 € VB", "Beschreibung",
            "Scheckheftgepflegt, Kaufvertrag der Volkswagen AG liegt bei.", "Autohaus Muster GmbH",
            "Privater Nutzer", f"Aktiv seit {SINCE}"]
    fields = parse_rows(rows)
    assert fields["seller_type"] == "privat" and fields["seller_legal_form"] == "GmbH"
    only_description = parse_rows(["VW Golf 7", "8.500 €", "Beschreibung", "Rechnung der Volkswagen AG",
                                   "Max Beispiel", "Privater Nutzer"])
    assert "seller_legal_form" not in only_description


# --------------------------------------------------------------------------- Bewertung

def test_commercial_seller_is_marked_and_business_contacts_count_less():
    base = {"title": "Winterräder", "description": "Rufen Sie uns an: 0621 1111111", "price": 399,
            "category": "auto"}
    private = _codes(Listing(**base, seller_type="privat"))
    business = _codes(Listing(**base, seller_type="gewerblich", seller_legal_form="Muster GmbH"))
    assert business["PHONE_IN_TEXT"].weight < private["PHONE_IN_TEXT"].weight
    assert "gewerblichen Anbietern üblich" in business["PHONE_IN_TEXT"].message
    assert business["COMMERCIAL_SELLER"].info and "SELLER_PROFILE" in business
    assert "Gewerblicher Anbieter (GmbH)" in business["SELLER_PROFILE"].message
    # Kontext ist kein Warnsignal: ändert den Regel-Score nicht
    signals = extract_features(Listing(**base, seller_type="gewerblich"), CFG).signals
    assert noisy_or([s.weight for s in signals if not s.info]) == noisy_or([s.weight for s in signals])


def test_seller_warnings():
    young = _codes(Listing(title="iPhone 15", description="Nur Versand.", price=900, seller_type="privat",
                           seller_account_age_days=4))
    assert {"NEW_ACCOUNT", "NO_RATINGS_HIGH_VALUE"} <= set(young)
    rated = _codes(Listing(title="iPhone 15", description="Nur Versand.", price=900, seller_type="privat",
                           seller_account_age_days=400, seller_badges=["TOP Zufriedenheit"]))
    assert "NO_RATINGS_HIGH_VALUE" not in rated and "NEW_ACCOUNT" not in rated
    disguised = _codes(Listing(title="Waschmaschine", description="gebraucht", seller_type="privat",
                               seller_legal_form="Muster Haushalt GmbH"))
    assert disguised["COMMERCIAL_AS_PRIVATE"].weight > 0
    assert "LOW_SATISFACTION" in _codes(Listing(title="x", seller_badges=["NA JA Zufriedenheit"]))
    service = _codes(Listing(title="Umzugshilfe", description="Preis nach Absprache", category="dienstleistungen"))
    assert service["SERVICE_OR_AD"].info


def test_llm_sees_seller_context_but_no_name():
    text = _format_listing(Listing(title="Reihenhaus", description="x", seller_name="Anja Beispiel",
                                   seller_type="gewerblich", seller_badges=["TOP Zufriedenheit"], seller_num_ads=60))
    assert "Anbieter: gewerblich" in text and "TOP Zufriedenheit" in text and "Anzeigen des Anbieters: 60" in text
    assert "Anja" not in text


# --------------------------------------------------------------------------- Ordner-Ablage

def _picture(tmp_path, name="foto.jpg"):
    from PIL import Image

    path = tmp_path / name
    Image.new("RGB", (8, 8), "red").save(path)
    return str(path)


def test_labeling_files_listing_into_dataset_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(labels, "LABEL_FILE", str(tmp_path / "eigene_labels.jsonl"))
    url = "https://www.kleinanzeigen.de/s-anzeige/reihenhaus/1-403-2"
    listing = Listing(title="Reihenhaus Travemünde", description="Neubauprojekt mit 14 Häusern.", price=399990,
                      category="immobilien", seller_type="gewerblich", seller_name="Anja Beispiel", url=url,
                      image_paths=[_picture(tmp_path)], label=1, scam_type="vorkasse")
    labels.save_label(listing)
    root = tmp_path / "datensatz_fuellen_inserate"
    (manifest,) = root.glob("betrug/immobilien-gewerblich/*/inserat.json")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["label"] == "betrug" and data["url"] == url and data["bilder"] == ["bild_1.jpg"]
    assert "seller_name" not in data and (manifest.parent / "bild_1.jpg").exists()

    # Umentscheiden verschiebt den Ordner statt einen zweiten anzulegen
    labels.save_label(Listing.from_dict({**listing.to_dict(), "label": 0, "scam_type": None}))
    assert not list(root.glob("betrug/*/*")) and len(list(root.glob("serioes/immobilien-gewerblich/*"))) == 1

    # Beim Training wird der Ordner ohne Texterkennung eingelesen – mit Label aus dem Ordner
    spec = DatasetSpec(name="inserate:serioes", source="listingfolder", path=str(root / "serioes"),
                       modality="multimodal", fixed_label=0, german_only=False, enabled=True)
    (loaded,) = load_spec(spec).listings
    assert loaded.label == 0 and loaded.title == "Reihenhaus Travemünde" and loaded.seller_type == "gewerblich"
    assert len(loaded.image_paths) == 1 and loaded.url == url


def test_dataset_folder_is_anonymized_for_sharing(tmp_path, monkeypatch):
    monkeypatch.setattr(labels, "LABEL_FILE", str(tmp_path / "eigene_labels.jsonl"))
    listing = Listing(title="Stuhl, Tel. +44 7700 900456", label=0, category="moebel",
                      description="Bei Fragen: +44 7700 900456 oder jemand@example.org",
                      messages=["Schreib mir an jemand@example.org"])
    labels.save_label(listing)
    (manifest,) = (tmp_path / "datensatz_fuellen_inserate").glob("serioes/moebel/*/inserat.json")
    shared = manifest.parent.name + manifest.read_text(encoding="utf-8")
    assert "900456" not in shared and "jemand@example.org" not in shared
    assert "jemand@example.org" in (tmp_path / "eigene_labels.jsonl").read_text(encoding="utf-8")  # lokal unverändert
    loaded = Listing.from_dict(json.loads(manifest.read_text(encoding="utf-8")))
    assert {s.code for s in extract_features(loaded, CFG).signals} >= {"PHONE_IN_TEXT", "EMAIL_IN_TEXT"}


def test_sync_command_files_existing_labels(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(labels, "LABEL_FILE", str(tmp_path / "eigene_labels.jsonl"))
    labels.save_label(Listing(title="Stuhl", description="Schaukelstuhl, Abholung", label=0, category="moebel"))
    for folder in (tmp_path / "datensatz_fuellen_inserate").glob("*/*/*"):
        (folder / "inserat.json").unlink()
    assert main(["data", "ordner"]) == 0 and "1 eingestufte Inserate" in capsys.readouterr().out
    assert list((tmp_path / "datensatz_fuellen_inserate").glob("serioes/moebel/*/inserat.json"))


def test_folder_items_with_category_level_and_chat_files(tmp_path):
    label_dir = tmp_path / "betrug"
    (label_dir / "screenshot_fall").mkdir(parents=True)
    (label_dir / "screenshot_fall" / "inserat.txt").write_text("PS5 neu\n300 €\nBeschreibung\nNur Versand.",
                                                                encoding="utf-8")
    (label_dir / "screenshot_fall" / "chat_1.txt").write_text("Bitte zuerst überweisen, dann schicke ich.",
                                                              encoding="utf-8")
    (label_dir / "einzeln.txt").write_text("iPhone\n100 €", encoding="utf-8")
    (label_dir / "elektronik" / "abgelegt__abc").mkdir(parents=True)
    (label_dir / "elektronik" / "abgelegt__abc" / "inserat.json").write_text(
        json.dumps({"title": "Laptop", "description": "neu", "label": "betrug"}), encoding="utf-8")
    items = listing_items(label_dir)
    assert len(items) == 3                                          # Datei, Unterordner, Kategorie-Ordner
    case = next(files for files in items if any(f.name == "chat_1.txt" for f in files))
    listing = import_paths(case).listing
    assert listing.messages == ["Bitte zuerst überweisen, dann schicke ich."]
    assert "überweisen" not in listing.description


# --------------------------------------------------------------------------- Training

def test_bert_models_without_model_type_fall_back_to_bert_classes(monkeypatch):
    transformers = pytest.importorskip("transformers")
    from scamguard.models import text_classifier

    def unrecognized(*args, **kwargs):
        raise ValueError("Unrecognized model in deepset/gbert-base. Should have a `model_type` key")

    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", unrecognized)
    configs = {"deepset/gbert-base": {"architectures": ["BertForMaskedLM"]},  # alt: ohne model_type
               "FacebookAI/xlm-roberta-base": {"model_type": "xlm-roberta", "architectures": ["XLMRobertaForMaskedLM"]}}
    monkeypatch.setattr(transformers.PretrainedConfig, "get_config_dict",
                        classmethod(lambda cls, name, **kw: (configs[name], {})))
    monkeypatch.setattr(transformers.BertTokenizer, "from_pretrained", lambda name, **kw: ("tokenizer", name))
    monkeypatch.setattr(transformers.BertForSequenceClassification, "from_pretrained",
                        lambda name, **kw: ("modell", kw.get("num_labels")))
    tokenizer, model = text_classifier.load_pretrained("deepset/gbert-base", num_labels=2)
    assert tokenizer == ("tokenizer", "deepset/gbert-base") and model == ("modell", 2)
    with pytest.raises(ValueError):
        text_classifier.load_pretrained("FacebookAI/xlm-roberta-base")   # kein BERT → Fehler bleibt sichtbar


def test_optional_models_are_skipped_instead_of_aborting(monkeypatch):
    from scamguard import training

    stats = {"total": 20, "duplicates_removed": 0, "splits": {"train": {"n": 14, "scam": 7}}}
    monkeypatch.setattr("scamguard.data.build.build_dataset", lambda names=None: ([], stats))
    monkeypatch.setattr("scamguard.data.build.read_split", lambda split: [])
    monkeypatch.setattr("scamguard.evaluate.evaluate", lambda split: {"models": {"FUSION": {"n": 0}}})

    def fake_train(model, train, val, cfg):
        if model == "image":
            raise ValueError("Fotos nur von einer Klasse")
        return f"models/{model}"

    monkeypatch.setattr(training, "train_model", fake_train)
    result = training.retrain(images=True, log=lambda message: None)
    assert result.saved == {"text-baseline": "models/text-baseline"}
    assert result.skipped == {"image": "Fotos nur von einer Klasse"}


def test_image_model_needs_both_classes(tmp_path):
    pytest.importorskip("torchvision")
    from scamguard.models.image_model import train_image_model

    scam = [Listing(title=f"x{i}", image_paths=[_picture(tmp_path, f"{i}.jpg")], label=1) for i in range(3)]
    with pytest.raises(ValueError, match="einer Klasse"):
        train_image_model(scam[:2], scam[2:], CFG)
