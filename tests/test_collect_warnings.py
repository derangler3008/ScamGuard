"""Sammeln öffentlicher Betrugswarnungen – ohne Netz (Fake-Fetcher, synthetisches HTML wie die Quellen)."""

import csv

from scamguard.config import load_config, resolve_path
from scamguard.data import collect_warnings as cw
from scamguard.data.discovery import _spec_for_file
from scamguard.data.loaders import load_spec
from scamguard.features.scam_phrases import match_phrases

LEXICON = str(resolve_path(load_config()["paths"]["lexicon"]))

ALARM_LIST = """<html><body>
<a href="/phishing/willhaben-konto-gesperrt/">Willhaben</a>
<a href="/phishing/post-paket-zoll/#top">Post</a>
<a href="/phishing-alarm/page-2/#phishing-section-pagination">2</a>
<a href="/news/irgendwas/">News</a></body></html>"""

ALARM_ITEM = """<html><body><h1>Willhaben-Support | Ihr Konto wurde eingeschränkt</h1>
<div class="container"><span>⚠️</span><h2>Phishing Alarm! Warnung vor dieser Nachricht:</h2>
<p>Guten Tag,</p><p>Ihr Konto wurde vorübergehend eingeschränkt. Bitte bestätigen Sie Ihre Daten
innerhalb von 24 Stunden.</p><p>[Konto entsperren]</p>
<svg><title>Icon</title></svg> Type Information
<p>Warnung</p><p>Phishing-Angriffe, die keiner Kategorie zugeordnet werden können.</p></div></body></html>"""

VZ_PAGE = """<html><body>
<h2>01. Oktober 2026: "PayPal" droht mit Kontolöschung</h2>
<ul><li>Sie wird unter dem Betreff "Рау Рal – Neue Anmeldung erkannt" versendet. Zudem ist der Satz
„Bitte erledigen Sie diesen Schritt innerhalb von 24 Stunden.“ rot hervorgehoben.</li></ul>
<h2>30. September 2026: Ohne Zitat</h2><p>Nur eine Erklärung der Verbraucherzentrale.</p>
<p><strong>21. September 2026:</strong> Es wird eine „Frist von 48 Stunden“ gesetzt. Weiterhin wird
die unpersönliche Anrede "Sehr geehrter Kunde" verwendet.</p></body></html>"""


class FakeFetcher:
    """Liefert vorgegebene Seiten; zählt Abrufe wie der echte Fetcher."""

    def __init__(self, pages):
        self.pages, self.requests = pages, 0

    def get(self, url, cache=True):
        self.requests += 1
        return self.pages.get(url)


def test_quotes_are_paired_by_position_even_with_mixed_styles():
    text = ('Die Mail droht: "Ihr Konto wird gesperrt" gesetzt. Dann „Bitte bestätigen Sie Ihre Daten“ '
            'und "24. September 2026". Betreff "Ihre sichere Freigabe für Online-Banking“. Danach Text.')
    assert cw.extract_quotes(text) == ["Ihr Konto wird gesperrt", "Bitte bestätigen Sie Ihre Daten",
                                       "Ihre sichere Freigabe für Online-Banking"]


def test_watchlist_list_and_item():
    items, pages = cw.parse_watchlist_alarm_list(ALARM_LIST)
    assert items == [f"{cw.WATCHLIST}/phishing/willhaben-konto-gesperrt/", f"{cw.WATCHLIST}/phishing/post-paket-zoll/"]
    assert pages == [f"{cw.WATCHLIST}/phishing-alarm/page-2/"]
    subject, body = cw.parse_watchlist_alarm_item(ALARM_ITEM)
    assert subject == "Willhaben-Support | Ihr Konto wurde eingeschränkt"
    assert body.splitlines()[0] == "Guten Tag,"                          # ohne Warn-Überschrift
    assert body.splitlines()[-1] == "[Konto entsperren]"                 # ohne Tipps/CMS-Reste
    assert "Phishing Alarm" not in body and "Icon" not in body


def test_vz_radar_keeps_only_quoted_scam_wording():
    entries = cw.parse_vz_radar(VZ_PAGE)
    assert [(d, q) for d, _, q in entries] == [
        ("01. Oktober 2026", ["Рау Рal – Neue Anmeldung erkannt",
                              "Bitte erledigen Sie diesen Schritt innerhalb von 24 Stunden."]),
        ("21. September 2026", ["Frist von 48 Stunden", "Sehr geehrter Kunde"]),
    ]                                                                    # Eintrag ohne Zitat fällt weg


def test_collect_writes_a_dataset_the_folder_understands(tmp_path):
    item_url = f"{cw.WATCHLIST}/phishing/willhaben-konto-gesperrt/"
    pages = {cw.WATCHLIST_ALARM: ALARM_LIST, item_url: ALARM_ITEM, cw.VZ_PAGES[0]: VZ_PAGE}
    out = tmp_path / "gesammelt_warnungen.csv"
    rows = cw.collect(["watchlist_alarm", "vz_radar"], fetch=FakeFetcher(pages), output=str(out), log=lambda m: None)
    assert [r.art for r in rows] == ["phishing_wortlaut", "phishing_zitate", "phishing_zitate"]
    with out.open(encoding="utf-8") as f:
        assert next(csv.reader(f)) == cw.COLUMNS

    spec = _spec_for_file(out)                     # so erkennt der Ablageordner die Datei
    assert spec.enabled
    listings = load_spec(spec).listings
    assert len(listings) == 3 and all(l.label == 1 and l.messages for l in listings)
    assert "eingeschränkt" in listings[0].messages[0]


def test_robots_txt_is_respected(tmp_path, monkeypatch):
    fetch = cw.Fetcher(cache_dir=str(tmp_path), delay=0, log=lambda m: None)
    calls = []

    def download(url):
        calls.append(url)
        return "User-agent: *\nDisallow: /privat/\n" if url.endswith("robots.txt") else "<html>ok</html>"

    monkeypatch.setattr(fetch, "_download", download)
    assert fetch.get("https://example.test/privat/seite") is None
    assert fetch.get("https://example.test/oeffentlich") == "<html>ok</html>"
    assert fetch.get("https://example.test/oeffentlich") == "<html>ok</html>"  # zweites Mal aus dem Cache
    assert calls == ["https://example.test/robots.txt", "https://example.test/oeffentlich"]


def _codes(text):
    return {s.code for s in match_phrases(text, LEXICON)}


def test_new_message_and_mail_criteria():
    assert "ACCOUNT_PHISHING" in _codes("Es wurde ein Anmeldeversuch bemerkt. Bitte verifizieren Sie Ihr Konto.")
    assert "PARCEL_CUSTOMS" in _codes("Ihr Paket konnte nicht zugestellt werden, Zollgebühr von 2 € begleichen.")
    assert "CODE_REQUEST" in _codes("Sorry, hab dir versehentlich einen Code geschickt, schick mir bitte den Code")
    assert "PAYMENT_PENDING_STORY" in _codes("Die Zahlung ist bereits reserviert und wird freigegeben, sobald du bestätigst.")
    assert "ADVANCE_FEE_SPAM" in _codes("Herzlichen Glückwunsch, Sie wurden als Gewinner ausgewählt!")
    assert "BUYER_NO_QUESTIONS" in _codes("Ich kaufe es ohne zu besichtigen, zahle den vollen Preis plus Versand.")


def test_ordinary_chat_messages_stay_clean():
    for text in ("Hallo, ist die Waschmaschine noch da? Ich könnte morgen um 18 Uhr abholen.",
                 "Super, dann bis Samstag. Ich bringe das Geld bar mit.",
                 "Kannst du mir noch ein Foto vom Typenschild schicken?",
                 "Ich melde mich innerhalb von 24 Stunden, sobald ich mit meiner Frau gesprochen habe."):
        assert not (_codes(text) - {"PRESSURE_URGENCY"}), text


def test_robots_txt_forbidden_means_nothing_allowed(tmp_path, monkeypatch):
    from urllib.error import HTTPError

    fetch = cw.Fetcher(cache_dir=None, delay=0, log=lambda m: None)

    def download(url):
        raise HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(fetch, "_download", download)
    assert not fetch.allowed("https://example.test/seite")


def test_overview_pages_are_not_cached(tmp_path, monkeypatch):
    fetch = cw.Fetcher(cache_dir=str(tmp_path), delay=0, log=lambda m: None)
    monkeypatch.setattr(fetch, "_download", lambda url: "" if url.endswith("robots.txt") else "<html/>")
    fetch.get("https://example.test/uebersicht", cache=False)
    fetch.get("https://example.test/meldung")
    assert len(list(tmp_path.iterdir())) == 1
