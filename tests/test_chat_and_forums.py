"""v0.7.0: Chat-Kriterien, Foren-Erfahrungen (ohne Netz), Anonymisierung, Phrasen-Suche, Feintuning-Daten."""

import csv
import json
from pathlib import Path

import pytest

from scamguard.config import load_config, resolve_path
from scamguard.data import collect_forums as cf
from scamguard.data.discovery import _spec_for_file
from scamguard.data.loaders import load_spec
from scamguard.data.phrases import candidates, log_odds
from scamguard.data.redact import redact
from scamguard.features.contact import analyze_contacts, find_ibans
from scamguard.features.scam_phrases import match_phrases
from scamguard.models.llm_judge import JSON_INSTRUCTIONS, SYSTEM_PROMPT, _format_listing
from scamguard.schema import Listing

CFG = load_config()
LEXICON = str(resolve_path(CFG["paths"]["lexicon"]))


def _codes(text):
    return {s.code for s in match_phrases(text, LEXICON)}


# --------------------------------------------------------------------------- Chat-Kriterien

@pytest.mark.parametrize("text, code", [
    ("Du musst dich sofort entscheiden, sonst ist es bald nicht mehr da.", "PRESSURE_URGENCY"),
    ("Zahl heute noch, ich habe schon 5 andere Interessenten.", "PRESSURE_URGENCY"),
    ("mein Hund ist gestorben, bitte schicke mir jetzt schnell das Geld", "PRESSURE_URGENCY"),
    ("Erst das Geld, dann die Ware.", "PAYMENT_FIRST"),
    ("Geld zuerst bitte, dann verschicke ich.", "PAYMENT_FIRST"),
    ("Ich reserviere es dir nur gegen Anzahlung von 50 €.", "PAYMENT_FIRST"),
    ("Abholung ist leider nicht möglich, ich bin beruflich unterwegs.", "PICKUP_REFUSED"),
    ("Die Transportkosten von 280 € musst du vorab überweisen.", "ANIMAL_TRANSPORT_FEE"),
    ("Ich lebe derzeit in London, die Wohnung zeigt ein Bekannter. Die Schlüssel kommen per Post.", "RENTAL_SCAM"),
    ("Hallo Mama, das ist meine neue Nummer. Mein Handy ist kaputt.", "NEW_NUMBER_FAMILY"),
    ("Überweisen Sie Ihr Guthaben sofort auf das Sicherungskonto.", "SAFE_ACCOUNT_SCAM"),
    ('Der Artikel wurde vom Käufer über die Funktion "Sicher bezahlen" bezahlt.', "PAYMENT_PENDING_STORY"),
    ("Bitte scannen Sie den QR-Code, um die Zahlung zu empfangen", "FAKE_PAYMENT_LINK"),
    ("Guten Morgen, ich benötige Ihre Mail Ad oder Handynummer, um einen Kauf abzuschließen.",
     "PAYMENT_CONTACT_REQUEST"),
    ("bitte Telenummer oder E-Mail ich möchte bezahlen", "PAYMENT_CONTACT_REQUEST"),
])
def test_chat_scam_phrases_are_recognized(text, code):
    assert code in _codes(text)


@pytest.mark.parametrize("text", [
    "Hallo, ist das Fahrrad noch da? Ich könnte heute noch vorbeikommen.",
    "Zahlung bar bei Abholung, gerne auch mit Besichtigung.",
    "Kann ich eine Anzahlung leisten, damit du es mir reservierst?",
    "Die Wohnung kann am Samstag besichtigt werden, Kaution wird bei Vertragsunterzeichnung fällig.",
    "Mama, kannst du mich abholen? Mein Akku ist fast leer.",
    "Versand nach Zahlungseingang per Überweisung, Abholung ebenfalls möglich.",
    "Kannst du mir deine Handynummer geben? Dann melde ich mich, wenn ich vor der Tür stehe.",
    "Ich würde es kaufen, meine Handynummer ist 0151 1234567, ruf gern an.",
    "Der Hund wird geimpft und gechippt übergeben, Besuch jederzeit möglich.",
])
def test_ordinary_chat_stays_clean(text):
    assert not _codes(text), text


# --------------------------------------------------------------------------- Anonymisieren

def test_redact_keeps_what_the_features_need_but_no_identifiers():
    text = ("Schreib an max.muster1987@gmail.com oder +44 7911 123456, IBAN LT60 1010 0123 4567 8901 oder "
            "DE89 3704 0044 0532 0130 00 bitte. Link: https://kleinanzeigen-sicher.top/pay/8f3a?id=42 @hans_99")
    clean = redact(text)
    for secret in ("max.muster1987", "7911", "1010 0123", "3704", "8f3a", "hans_99"):
        assert secret not in clean
    assert "anonym@gmail.com" in clean and "kleinanzeigen-sicher.top" in clean and " bitte." in clean
    before = {s.code for s in analyze_contacts(text).signals}
    assert {s.code for s in analyze_contacts(clean).signals} >= before - {"EXTERNAL_LINK"}
    assert {"FOREIGN_IBAN", "FOREIGN_PHONE", "LOOKALIKE_DOMAIN"} <= {s.code for s in analyze_contacts(clean).signals}


def test_redact_obfuscated_forum_mail_and_german_numbers():
    assert redact("nadine816 [at] gmail.com") == "anonym@gmail.com"
    clean = redact("Ruf an: 0151 23456789")
    assert "23456789" not in clean
    assert {s.code for s in analyze_contacts(clean).signals} == {"PHONE_IN_TEXT"}  # nicht plötzlich „Ausland“


def test_iban_search_does_not_swallow_following_words():
    text = "Konto LT60 1010 0123 4567 8901 oder DE89 3704 0044 0532 0130 00 bitte"
    found = [text[a:b] for a, b in find_ibans(text)]
    assert found == ["LT60 1010 0123 4567 8901", "DE89 3704 0044 0532 0130 00"]


# --------------------------------------------------------------------------- Foren (ohne Netz)

XENFORO = """<html><body><h1 class="p-title-value">Betrug bei Kleinanzeigen?</h1>
<article class="message"><time datetime="2026-09-01T10:00:00+0200"></time>
<div class="message-body"><div class="bbWrapper">Ich habe mein Rad inseriert und bekam folgende Nachricht:<br>
<blockquote class="bbCodeBlock bbCodeBlock--quote"><div class="bbCodeBlock-content">Hallo, ich benötige Ihre
E-Mail-Adresse, um über Sicher bezahlen zu zahlen. Schreib an kaeufer.echt99@gmail.com</div></blockquote>
Mir kam das komisch vor, ist das Betrug?</div></div></article>
<article class="message"><time datetime="2026-09-01T11:00:00+0200"></time>
<div class="message-body"><div class="bbWrapper">
<blockquote class="bbCodeBlock bbCodeBlock--quote" data-quote="Forenname"><div class="bbCodeBlock-title">Forenname
schrieb:</div><div class="bbCodeBlock-content">Hallo, ich benötige Ihre E-Mail-Adresse</div></blockquote>
Ja, klassischer Betrug. Laut Verbraucherzentrale gilt „Niemals die E-Mail-Adresse an Fremde herausgeben, wirklich niemals“.
</div></div></article>
<ul class="pageNav-main"><li class="pageNav-page"><a href="/threads/betrug.1/">1</a></li>
<li class="pageNav-page"><a href="/threads/betrug.1/page-12">12</a></li></ul></body></html>"""

WOLTLAB = """<html><body><h1 class="contentTitle">Betrugsversuche</h1>
<article class="wbbPost"><woltlab-core-date-time date="2025-12-24T10:00:00+01:00"></woltlab-core-date-time>
<div class="messageText"><blockquote class="quoteBox" cite="https://forum.test/post/1"><div class="quoteBoxTitle">Zitat von
X</div><div class="quoteBoxContent">Schick mir deine Handynummer für die Zahlung</div></blockquote>
<p>Bei mir war es genauso. Er schrieb mir: "Schick mir bitte deine Handynummer, damit ich über PayPal zahlen kann"
und ich habe natürlich nicht geantwortet, das ist doch Betrug.</p></div></article>
<woltlab-core-pagination count="3" page="1"></woltlab-core-pagination></body></html>"""


def test_xenforo_thread_separates_received_messages_from_forum_quotes():
    page = cf.parse_thread(XENFORO, "https://forum.test/threads/betrug.1/")
    assert page.title == "Betrug bei Kleinanzeigen?" and len(page.posts) == 2
    assert page.page_urls[1] == "https://forum.test/threads/betrug.1/page-2"
    assert len(page.page_urls) == 12
    first, second = (cf.received_messages(p) for p in page.posts)
    assert len(first) == 1 and first[0].startswith("Hallo, ich benötige Ihre")  # eingefügte Nachricht
    assert second == []  # Forenzitat („schrieb:“) und Ratschlag ohne „bekam/schrieb mir“ zählen nicht
    assert "Forenname" not in page.posts[1].text
    assert cf.is_experience(page.posts[0])           # eigenes Erlebnis („Ich habe … bekam …“)
    assert not cf.is_experience(page.posts[1])       # Ratschlag, kein eigenes Erlebnis


def test_woltlab_thread_and_pages():
    page = cf.parse_thread(WOLTLAB, "https://forum.test/thread/22/?pageNo=1")
    assert page.page_urls == ["https://forum.test/thread/22/?pageNo=1", "https://forum.test/thread/22/?pageNo=2",
                              "https://forum.test/thread/22/?pageNo=3"]
    post = page.posts[0]
    assert post.date == "2025-12-24" and "Zitat von" not in post.text
    assert cf.received_messages(post) == ["Schick mir bitte deine Handynummer, damit ich über PayPal zahlen kann"]
    assert cf.is_experience(post)
    assert "Kontakt außerhalb (WhatsApp, Mail)" not in cf.described_scams(post.text)
    assert "PayPal Freunde & Familie" not in cf.described_scams(post.text)


def test_pages_to_read_keep_first_and_newest():
    urls = [f"p{n}" for n in range(1, 21)]
    assert cf._pages_to_read(urls, 4) == ["p1", "p18", "p19", "p20"]
    assert cf._pages_to_read(urls[:3], 8) == urls[:3]


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.requests = pages, 0

    def get(self, url, cache=True):
        self.requests += 1
        return self.pages.get(url)


def test_collect_writes_review_list_and_keeps_human_labels(tmp_path):
    url = "https://forum.test/threads/betrug.1/"
    pages = {url: XENFORO.replace('href="/threads/betrug.1/page-12">12', 'href="/threads/betrug.1/">1')}
    quotes, reports = tmp_path / "foren.csv", tmp_path / "berichte.jsonl"
    rows, _ = cf.collect([url], fetch=FakeFetcher(pages), quotes_file=str(quotes),
                         reports_file=str(reports), log=lambda m: None)
    assert len(rows) == 1 and rows[0]["betrug"] == "" and rows[0]["vorschlag"] == "ja"
    assert "kaeufer.echt99" not in rows[0]["nachricht"] and "anonym@gmail.com" in rows[0]["nachricht"]
    assert load_spec(_spec_for_file(quotes)).listings == []  # ungeprüft → nicht im Training

    with quotes.open(encoding="utf-8") as f:  # Mensch prüft in Excel: „ja“, dazu eine eigene „nein“-Zeile
        reviewed = list(csv.DictReader(f))
    reviewed[0]["betrug"] = "ja"
    reviewed.append({**reviewed[0], "nachricht": "Die Sendung wurde elektronisch angekündigt.", "betrug": "nein"})
    with quotes.open("w", encoding="cp1252", newline="") as f:  # so speichert deutsches Excel
        writer = csv.DictWriter(f, fieldnames=cf.REVIEW_COLUMNS, delimiter=";")
        writer.writeheader()
        writer.writerows(reviewed)

    cf.collect([url], fetch=FakeFetcher(pages), quotes_file=str(quotes), reports_file=None, log=lambda m: None)
    listings = load_spec(_spec_for_file(quotes)).listings
    # Prüfungen bleiben erhalten – auch die Zeile, die der neue Lauf nicht mehr gefunden hat
    assert sorted(l.label for l in listings) == [0, 1]


# --------------------------------------------------------------------------- Phrasen-Kandidaten

def test_phrase_mining_ranks_scam_phrases_and_drops_redundant_parts():
    scam = ["Geld zuerst, dann schicke ich es", "Bitte Geld zuerst überweisen", "Nur Geld zuerst, sorry",
            "Geld zuerst sonst nichts", "Ich mache das nur Geld zuerst", "Geld zuerst wie immer",
            "Abholung leider nicht möglich", "Versand nur per Kurier", "Zahlung über Freunde und Familie",
            "Bin derzeit im Ausland"]
    legit = ["Abholung in Mannheim, bar bei Abholung", "Versand möglich, gerne anschauen", "Ist noch da",
             "Kann am Samstag vorbeikommen", "Preis ist fest", "Bar bei Abholung", "Funktioniert einwandfrei",
             "Nur Abholung in Heidelberg", "Gerne mit Besichtigung", "Melde mich morgen"]
    rows = log_odds(scam, legit, min_docs=3)  # nur Wortfolgen, die in ≥ 3 Betrugstexten stehen
    assert {r[0] for r in rows} == {"geld", "zuerst", "geld zuerst"} and all(r[1] > 0 for r in rows)
    found = candidates(scam, legit, LEXICON, top=10)
    phrases = [c.phrase for c in found]
    assert "geld zuerst" in phrases
    assert "zuerst" not in phrases  # steckt fast immer in einer längeren Kandidaten-Wortfolge
    assert next(c for c in found if c.phrase == "geld zuerst").covered_by == "PAYMENT_FIRST"


# --------------------------------------------------------------------------- LLM: Chat & Feintuning

def test_llm_prompt_for_chat_only_has_no_empty_listing_fields():
    text = _format_listing(Listing(messages=["Geld zuerst, dann Ware", "Abholung nicht möglich"]))
    assert "Titel:" not in text and "Preis:" not in text
    assert "Chat-Nachrichten:\nGeld zuerst, dann Ware\n---\nAbholung nicht möglich" in text


def test_finetune_examples_match_the_live_prompt_and_answer_format(tmp_path, monkeypatch):
    from scamguard import finetune
    from scamguard.models.llm_judge import parse_judgement

    scam = Listing(title="PS5", description="Nur Freunde und Familie, ich bin zurzeit im Ausland.", price=200,
                   category="elektronik", label=1)
    legit = Listing(title="Sofa", description="Abholung in Mannheim, Besichtigung gern.", price=80, label=0)
    monkeypatch.setattr(finetune, "read_split", lambda split: [scam, legit])
    stats = finetune.export(str(tmp_path), CFG)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["test.jsonl", "train.jsonl", "valid.jsonl"]
    assert stats["train"] == {"betrug": 1, "seriös": 1}

    rows = [json.loads(line) for line in (tmp_path / "train.jsonl").read_text(encoding="utf-8").splitlines()]
    system, user, answer = rows[0]["messages"]
    assert [m["role"] for m in rows[0]["messages"]] == ["system", "user", "assistant"]
    assert system["content"] == f"{SYSTEM_PROMPT}\n\n{JSON_INSTRUCTIONS}"  # exakt wie im Betrieb
    assert user["content"] == _format_listing(scam)
    parsed = parse_judgement(answer["content"])              # der Live-Parser versteht die Zielantwort
    assert parsed["scam_probability"] == pytest.approx(0.9) and parsed["scam_type"] == "paypal_freunde"
    assert parsed["red_flags"] and all(f["evidence"] for f in parsed["red_flags"])
    legit_answer = parse_judgement(rows[1]["messages"][2]["content"])
    assert legit_answer["scam_type"] == "keiner" and legit_answer["red_flags"] == []


def test_llm_server_uses_adapter_only_if_trained(tmp_path, monkeypatch):
    import huggingface_hub

    from scamguard import cli

    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *a, **kw: "/cache/qwen")
    command, _, _ = cli._llm_server_process_args(adapter=str(tmp_path))
    assert "--adapter-path" not in command                   # noch nicht trainiert → Basismodell
    (tmp_path / "adapters.safetensors").write_bytes(b"")
    command, _, where = cli._llm_server_process_args(adapter=str(tmp_path))
    assert command[command.index("--adapter-path") + 1] == str(Path(tmp_path)) and "Adapter" in where


def test_text_model_abstains_on_chats_it_was_not_trained_on(tmp_path):
    from scamguard.config import with_overrides
    from scamguard.models.text_classifier import (
        MIN_CHAT_EXAMPLES,
        TextBaselineDetector,
        train_text_baseline,
    )

    cfg = with_overrides(CFG, {"text_model": {"baseline_path": str(tmp_path / "baseline.joblib")}})
    listings = [Listing(title=f"Angebot {i}", description=d, label=y) for i in range(6)
                for d, y in (("Nur Vorkasse, ich bin im Ausland", 1), ("Abholung in Mannheim, bar", 0))]
    train_text_baseline(listings, cfg)
    detector = TextBaselineDetector(cfg)
    chat = Listing(messages=["Hallo, ist das Rad noch da?"])
    result = detector.predict(chat)
    assert result.score is None and "Chats" in result.error           # kein Raten bei unbekannter Textart
    assert detector.predict(Listing(title="PS5", description="Nur Vorkasse")).score is not None

    chats = [Listing(messages=[m], label=y) for i in range(MIN_CHAT_EXAMPLES)
             for m, y in ((f"Geld zuerst bitte {i}", 1), (f"Kann ich morgen abholen {i}", 0))]
    train_text_baseline(listings + chats, cfg)
    assert TextBaselineDetector(cfg).predict(chat).score is not None  # genug Chats gelernt → urteilt


def test_nested_quotes_do_not_create_empty_entries():
    html = """<html><body><h1 class="p-title-value">T</h1><article class="message"><div class="message-body">
    <div class="bbWrapper">Ich bekam folgende Nachricht:<blockquote class="bbCodeBlock bbCodeBlock--quote">
    <div class="bbCodeBlock-content">Bitte senden Sie Ihre gültige E-Mail Adresse für die Zahlung
    <blockquote class="bbCodeBlock bbCodeBlock--quote"><div class="bbCodeBlock-content">innen</div></blockquote>
    </div></blockquote></div></div></article></body></html>"""
    post = cf.parse_thread(html, "https://forum.test/threads/t.1/").posts[0]
    assert len(post.external) == 1 and post.external[0][0].strip().endswith("folgende Nachricht:")
