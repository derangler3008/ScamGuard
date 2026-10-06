"""Ende-zu-Ende: die echte Extension in Chromium (Playwright).

Playwright fängt die Kleinanzeigen-URLs ab und liefert die Testseiten aus – so werden auch die
URL-Matches aus dem Manifest, die https-Bild-URLs und der Badge des Service Workers mitgeprüft.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest
from conftest import EXTENSION_DIR, active_since, free_port, render

sync_api = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.e2e
expect = sync_api.expect

SCAM_URL = "https://www.kleinanzeigen.de/s-anzeige/ps5-slim-neu-ovp/1000000001-279-0000"
LEGIT_URL = "https://www.kleinanzeigen.de/s-anzeige/waschmaschine-bosch/1000000002-176-0000"
IMAGE_URL = "https://img.kleinanzeigen.de/api/v1/prod-ads/images/te/test-1?rule=$_59.AUTO"
MAIL_URL = "https://mail.example.test/posteingang"
CHAT_URL = "https://www.kleinanzeigen.de/m-nachrichten.html"
DEALER_URL = "https://www.kleinanzeigen.de/s-anzeige/winterraeder-17-zoll/1000000003-223-0000"
DEALER_IMAGES = [f"https://img.kleinanzeigen.de/api/v1/prod-ads/images/hd/haendler-{i}?rule=$_59.AUTO" for i in (1, 2)]
BADGE_JS = """async (pattern) => {
  const [tab] = await chrome.tabs.query({ url: pattern });
  return chrome.action.getBadgeText({ tabId: tab.id });
}"""


@pytest.fixture(scope="module")
def chromium(api_server, test_image_bytes, tmp_path_factory):
    ext_dir = tmp_path_factory.mktemp("chromium") / "extension"
    shutil.copytree(EXTENSION_DIR, ext_dir)
    manifest = json.loads((ext_dir / "manifest.json").read_text(encoding="utf-8"))
    # Nur im Test: Injektion in die Mail-Testseite ohne echten Nutzerklick (activeTab)
    manifest["host_permissions"].append("https://mail.example.test/*")
    (ext_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    pages = {
        SCAM_URL: render("kleinanzeigen_scam.html", ACTIVE_SINCE=active_since(2), IMAGE_URL=IMAGE_URL),
        LEGIT_URL: render("kleinanzeigen_legit.html"),
        MAIL_URL: render("mail.html"),
        CHAT_URL: render("kleinanzeigen_chat.html"),
        DEALER_URL: render("kleinanzeigen_gewerblich.html", ACTIVE_SINCE=active_since(2000),
                           IMAGE_URL_1=DEALER_IMAGES[0], IMAGE_URL_2=DEALER_IMAGES[1]),
    }

    def serve_page(route):
        body = pages.get(route.request.url)
        if body is None:
            route.fulfill(status=404, body="nicht im Test vorgesehen")
        else:
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

    # Auch Anfragen des Service Workers (Bild-Download) über context.route leiten
    os.environ.setdefault("PW_EXPERIMENTAL_SERVICE_WORKER_NETWORK_EVENTS", "1")
    with sync_api.sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                str(tmp_path_factory.mktemp("profile")),
                channel="chromium",
                headless=True,
                args=[f"--disable-extensions-except={ext_dir}", f"--load-extension={ext_dir}"],
            )
        except Exception as exc:  # noqa: BLE001 – fehlender Browser → überspringen statt Fehlschlag
            pytest.skip(f"Chromium nicht startbar (playwright install chromium?): {exc}")
        ctx.route("https://www.kleinanzeigen.de/**", serve_page)
        ctx.route("https://mail.example.test/**", serve_page)
        ctx.route("https://img.kleinanzeigen.de/**",
                  lambda route: route.fulfill(status=200, content_type="image/jpeg", body=test_image_bytes))
        worker = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
        # KI-Analyse standardmäßig aus → deterministische Tests; eigener Test für Stufe 2 unten
        worker.evaluate("url => chrome.storage.sync.set({ apiUrl: url, llmMode: 'off' })", api_server)
        yield ctx, worker
        ctx.close()


def _score(page) -> int:
    pct = page.locator("#scamguard-root .pct")
    expect(pct).to_have_text(re.compile(r"^\d{1,3} %$"), timeout=20_000)
    return int(pct.inner_text().split()[0])


def test_scam_listing_is_marked_and_scored(chromium):
    ctx, worker = chromium
    page = ctx.new_page()
    page.goto(SCAM_URL)

    assert _score(page) >= 75
    expect(page.locator("#scamguard-root .verdict")).to_contain_text("Hohes Risiko")

    marked_text = " ".join(page.locator("mark.scamguard-mark").all_inner_texts())
    assert "kleinanzeigen-de.sicher-bezahlen.top" in marked_text   # Fake-Zahlungslink
    assert "zurzeit im Ausland" in marked_text                      # Auslands-Geschichte
    assert "Freunde und" in marked_text and "Familie" in marked_text  # über <br> hinweg
    assert "Das Konsole" in marked_text                             # Artikelfehler

    seller = page.locator("#viewad-contact .userprofile-vip-details-text", has_text="Aktiv seit")
    for element in (page.locator("#viewad-price"), seller, page.locator("#viewad-image")):
        assert "scamguard-flagged" in (element.get_attribute("class") or "")

    badge = worker.evaluate(BADGE_JS, "https://www.kleinanzeigen.de/*")
    assert re.fullmatch(r"\d{1,3}%", badge)
    if shots := os.environ.get("SCAMGUARD_SCREENSHOTS"):  # optional: Bilder für die Doku
        page.set_viewport_size({"width": 1280, "height": 900})
        page.screenshot(path=f"{shots}/chromium_inserat.png")
    page.close()


def test_clicking_a_signal_jumps_to_its_mark(chromium):
    ctx, _ = chromium
    page = ctx.new_page()
    page.goto(SCAM_URL)
    _score(page)
    page.locator("#scamguard-root button.sig:not([disabled])").first.click()
    expect(page.locator(".scamguard-pulse")).to_have_count(1, timeout=3_000)
    page.close()


def test_legit_listing_stays_unremarkable(chromium):
    ctx, _ = chromium
    page = ctx.new_page()
    page.goto(LEGIT_URL)
    assert _score(page) < 40
    expect(page.locator("#scamguard-root .verdict")).to_contain_text("Unauffällig")
    assert page.locator(".scamguard-sev-high, .scamguard-sev-mid").count() == 0
    page.close()


def test_selected_text_on_any_page_is_checked(chromium):
    ctx, worker = chromium
    mail = ctx.new_page()
    mail.goto(MAIL_URL)
    message = mail.locator("#nachricht").inner_text()
    extension_id = worker.url.split("/")[2]
    popup = ctx.new_page()
    popup.goto(f"chrome-extension://{extension_id}/popup/popup.html")
    tab_id = worker.evaluate("async () => (await chrome.tabs.query({ url: 'https://mail.example.test/*' }))[0].id")
    # Gleicher Weg wie das Kontextmenü „Markierten Text mit ScamGuard prüfen“
    result = popup.evaluate(
        "([tabId, text]) => chrome.runtime.sendMessage({ type: 'scanTab', tabId, mode: 'selection', text })",
        [tab_id, message],
    )
    assert result["ok"] and result["score"] >= 0.75
    marked_text = " ".join(mail.locator("mark.scamguard-mark").all_inner_texts())
    assert "kleinanzeigen-de.sicher-bezahlen.top" in marked_text
    assert "Abholung" not in marked_text  # harmlose Nachricht bleibt unmarkiert
    popup.close()
    mail.close()


def test_popup_shows_server_status(chromium):
    ctx, worker = chromium
    extension_id = worker.url.split("/")[2]
    popup = ctx.new_page()
    popup.goto(f"chrome-extension://{extension_id}/popup/popup.html")
    expect(popup.locator("#server-status")).to_have_text("Server verbunden", timeout=5_000)
    expect(popup.locator("#permissions")).to_be_hidden()
    # Kein Inserat im aktiven Tab → Hinweis, wo die Prüfung automatisch läuft (statt „leer“)
    expect(popup.locator("#result")).to_contain_text("Öffne eine einzelne Anzeige")
    popup.close()


def test_offline_server_shows_hint_instead_of_marks(chromium, api_server):
    ctx, worker = chromium
    worker.evaluate("url => chrome.storage.sync.set({ apiUrl: url })", f"http://127.0.0.1:{free_port()}")
    try:
        page = ctx.new_page()
        page.goto(SCAM_URL)
        expect(page.locator("#scamguard-root [role=alert]")).to_contain_text("nicht erreichbar", timeout=15_000)
        assert page.locator("mark.scamguard-mark").count() == 0
        assert worker.evaluate(BADGE_JS, "https://www.kleinanzeigen.de/*") == "!"
        page.close()
    finally:
        worker.evaluate("url => chrome.storage.sync.set({ apiUrl: url })", api_server)


def test_ai_assessment_is_added_in_second_step(chromium):
    ctx, worker = chromium
    worker.evaluate("() => chrome.storage.sync.set({ llmMode: 'auto' })")
    try:
        page = ctx.new_page()
        page.goto(SCAM_URL)
        panel = page.locator("#scamguard-root")
        # Stufe 1 ist sofort sichtbar, die KI-Analyse läuft noch …
        expect(panel.locator(".ai")).to_contain_text("KI-Analyse läuft", timeout=15_000)
        # … und ergänzt danach Einschätzung und eigene Fundstellen
        expect(panel.locator(".summary")).to_contain_text("KI-Einschätzung", timeout=15_000)
        expect(panel.locator(".ai")).to_have_count(0)
        assert "auf Montage" in " ".join(page.locator("mark.scamguard-mark").all_inner_texts())
        page.close()
    finally:
        worker.evaluate("() => chrome.storage.sync.set({ llmMode: 'off' })")


def test_label_buttons_save_and_replace_training_data(chromium, label_file):
    ctx, _ = chromium
    page = ctx.new_page()
    page.goto(SCAM_URL)
    _score(page)
    panel = page.locator("#scamguard-root")
    panel.locator("button.label-btn.scam").click()
    expect(panel.locator(".label-status")).to_contain_text("Gespeichert als Betrug", timeout=10_000)
    stored = [json.loads(line) for line in label_file.read_text(encoding="utf-8").splitlines()]
    assert stored[-1]["label"] == 1 and stored[-1]["url"] == SCAM_URL and stored[-1]["image_paths"]
    # Umentscheiden ersetzt das Label, statt ein zweites anzulegen
    panel.locator("button.label-btn.ok").click()
    expect(panel.locator(".label-status")).to_contain_text("Gespeichert als seriös", timeout=10_000)
    stored = [json.loads(line) for line in label_file.read_text(encoding="utf-8").splitlines()]
    assert [r["label"] for r in stored if r["url"] == SCAM_URL] == [0]
    page.close()


def test_chat_is_checked_live_when_a_scam_message_arrives(chromium, label_file):
    ctx, _ = chromium
    page = ctx.new_page()
    page.goto(CHAT_URL)
    panel = page.locator("#scamguard-root")
    # Nur der Verlauf zählt: Vorschau anderer Unterhaltungen („Western Union“) und Eingabefeld nicht
    assert _score(page) < 40
    expect(panel.locator(".verdict")).to_contain_text("Unauffällig")
    expect(panel).to_contain_text("2 Chat-Nachrichten geprüft")

    page.evaluate("addMessage('Geld zuerst, dann verschicke ich. Abholung ist leider nicht möglich, "
                  "sonst ist es bald weg!')")
    expect(panel.locator(".verdict")).not_to_contain_text("Unauffällig", timeout=15_000)
    expect(panel).to_contain_text("3 Chat-Nachrichten geprüft")
    marked = " ".join(page.locator("mark.scamguard-mark").all_inner_texts())
    assert "Geld zuerst" in marked and "Abholung ist leider nicht möglich" in marked
    assert page.locator("textarea mark, .sc-l3 mark").count() == 0
    flagged = page.locator(".scamguard-flagged")                        # die Nachricht selbst eingerahmt
    expect(flagged).to_have_count(1)
    assert "Geld zuerst" in flagged.inner_text()
    if shots := os.environ.get("SCAMGUARD_SCREENSHOTS"):  # optional: Bild für die Doku
        page.set_viewport_size({"width": 1280, "height": 760})
        page.screenshot(path=f"{shots}/chromium_chat.png")

    panel.locator("button.label-btn.scam").click()
    expect(panel.locator(".label-status")).to_contain_text("Gespeichert als Betrug", timeout=10_000)
    stored = json.loads(label_file.read_text(encoding="utf-8").splitlines()[-1])
    assert stored["label"] == 1 and not stored.get("url")              # Postfach-URL ist kein Schlüssel
    assert stored["messages"][-1].startswith("Geld zuerst") and len(stored["messages"]) == 3
    page.close()


def test_dealer_in_new_layout_is_read_marked_and_filed(chromium, label_file, tmp_path):
    """Neues Seitenlayout: Kategorie, Fotos (ohne „ähnliche Anzeigen“), Anbieterprofil mit Rechtsform –
    gewerblich markiert, Telefonnummer nur schwach gewertet, Einstufung landet im Datensatz-Ordner."""
    from PIL import Image

    from scamguard.data import labels

    ctx, _ = chromium
    photo = tmp_path / "foto.jpg"
    Image.new("RGB", (64, 48), (90, 140, 60)).save(photo)  # kein registriertes Fake-Bild
    ctx.route("https://img.kleinanzeigen.de/api/v1/prod-ads/images/hd/**",
              lambda route: route.fulfill(status=200, content_type="image/jpeg", body=photo.read_bytes()))
    page = ctx.new_page()
    page.goto(DEALER_URL)
    _score(page)
    panel = page.locator("#scamguard-root")
    expect(panel.locator(".info")).to_contain_text("Gewerblicher Anbieter (GmbH)")
    expect(panel.locator(".info")).to_contain_text("TOP Zufriedenheit, Besonders zuverlässig")
    expect(panel.locator(".sig").filter(has_text="Telefonnummer")).to_contain_text("gewerblichen Anbietern üblich")

    panel.locator("button.label-btn.ok").click()
    expect(panel.locator(".label-status")).to_contain_text("Gespeichert als seriös", timeout=10_000)
    stored = [json.loads(line) for line in label_file.read_text(encoding="utf-8").splitlines()]
    row = next(r for r in stored if r["url"] == DEALER_URL)
    assert row["category"] == "auto" and row["seller_type"] == "gewerblich" and row["seller_legal_form"] == "GmbH"
    assert row["seller_badges"] == ["zufriedenheit_top", "besonders_zuverlaessig"] and row["seller_num_ads"] == 248
    assert len(row["image_paths"]) == 2 and not row.get("seller_name")
    assert 1990 <= row["seller_account_age_days"] <= 2010
    (manifest,) = Path(labels.DATASET_DIR).glob("serioes/auto-gewerblich/*/inserat.json")
    assert sorted(p.name for p in manifest.parent.glob("bild_*")) == ["bild_1.jpg", "bild_2.jpg"]
    page.close()
    ctx.unroute("https://img.kleinanzeigen.de/api/v1/prod-ads/images/hd/**")
