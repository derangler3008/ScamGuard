"""Ende-zu-Ende: die echte Extension im installierten Firefox (Selenium, temporäres Add-on).

Firefox-Besonderheiten, die hier mitgeprüft werden: Hintergrundskript statt Service Worker,
`browser.*`-Namensraum, Panel-Styles ohne adoptedStyleSheets (Xray), moz-extension-Origin am Server.

Die Testseiten kommen von einem lokalen HTTP-Server. Damit der Kleinanzeigen-Adapter sie erkennt,
wird in der Test-Kopie der Extension 127.0.0.1 als zusätzlicher Host eingetragen.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import EXTENSION_DIR, active_since, free_port, render

webdriver = pytest.importorskip("selenium.webdriver")
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import WebDriverWait

pytestmark = pytest.mark.e2e

FIREFOX_CANDIDATES = [
    "/Applications/Firefox.app/Contents/MacOS/firefox",
    "/usr/bin/firefox",
    "C:/Program Files/Mozilla Firefox/firefox.exe",
]
GECKO_ID = "scamguard@dhbw-ki-projekt"
EXTENSION_UUID = "5c4e7a1e-0b7d-4a51-9a39-5ca0fe7e57e1"  # feste interne UUID → Popup-URL bekannt
POPUP_URL = f"moz-extension://{EXTENSION_UUID}/popup/popup.html"
ADAPTER_REGEX = r"/(^|\.)kleinanzeigen\.de$/"


@pytest.fixture(scope="module")
def site():
    """Lokaler Webserver für die Testseiten."""
    pages = {
        "/s-anzeige/ps5-slim-neu-ovp": render("kleinanzeigen_scam.html", ACTIVE_SINCE=active_since(2), IMAGE_URL=""),
        "/s-anzeige/waschmaschine-bosch": render("kleinanzeigen_legit.html"),
        "/posteingang": render("mail.html"),
        "/m-nachrichten.html": render("kleinanzeigen_chat.html"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = pages.get(self.path)
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write((body or "nicht im Test vorgesehen").encode("utf-8"))

        def log_message(self, *args):  # Testausgabe ruhig halten
            pass

    server = ThreadingHTTPServer(("127.0.0.1", free_port()), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _wait_for_port(port: int, timeout: float = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def _start_driver(binary: str, profile: Path):
    """Startet Firefox mit Wegwerf-Profil und verbindet Selenium.

    macOS: Firefox wird über LaunchServices (`open`) als eigenständige App gestartet und Selenium
    verbindet sich per Marionette. Direkt aus einer anderen App (IDE, Terminal-App …) gestartet, erbt
    Firefox deren macOS-Datenschutzrechte und darf sein eigenes Datenverzeichnis nicht lesen
    („Could not find profile folder.“).
    """
    if sys.platform != "darwin":
        options = Options()
        options.binary_location = binary
        options.add_argument("-headless")
        options.profile = str(profile)
        return webdriver.Firefox(options=options), None

    port = free_port()
    with (profile / "user.js").open("a", encoding="utf-8") as prefs:
        prefs.write(f'user_pref("marionette.port", {port});\n')
    app = str(Path(binary).parents[2])  # …/Firefox.app
    subprocess.run(["open", "-n", "-g", "-a", app, "--args", "-headless", "-no-remote",
                    "-profile", str(profile), "-marionette", "-remote-allow-system-access"], check=True)
    if not _wait_for_port(port):
        raise WebDriverException("Firefox-Marionette nicht erreichbar")
    service = Service(service_args=["--connect-existing", "--marionette-port", str(port)])
    return webdriver.Firefox(service=service, options=Options()), profile


@pytest.fixture(scope="module")
def firefox(api_server, site, tmp_path_factory):
    binary = next((p for p in FIREFOX_CANDIDATES if Path(p).exists()), None)
    if binary is None:
        pytest.skip("Firefox ist nicht installiert")

    ext_dir = tmp_path_factory.mktemp("firefox") / "extension"
    shutil.copytree(EXTENSION_DIR, ext_dir)
    manifest = json.loads((ext_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["content_scripts"][0]["matches"] += ["http://127.0.0.1/s-anzeige/*", "http://127.0.0.1/m-nachrichten*"]
    (ext_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    extract_js = ext_dir / "content" / "extract.js"
    source = extract_js.read_text(encoding="utf-8")
    assert ADAPTER_REGEX in source, "Adapter-Erkennung in extract.js geändert – Test anpassen"
    extract_js.write_text(source.replace(ADAPTER_REGEX, r"/(^|\.)kleinanzeigen\.de$|^127\.0\.0\.1$/"),
                          encoding="utf-8")

    profile = tmp_path_factory.mktemp("firefox-profile")
    uuids = json.dumps(json.dumps({GECKO_ID: EXTENSION_UUID}))  # Pref-Wert ist selbst ein JSON-String
    (profile / "user.js").write_text(
        f'user_pref("extensions.webextensions.uuids", {uuids});\n'
        'user_pref("browser.shell.checkDefaultBrowser", false);\n'
        'user_pref("datareporting.policy.dataSubmissionEnabled", false);\n'
        'user_pref("browser.startup.homepage_override.mstone", "ignore");\n',
        encoding="utf-8",
    )
    try:
        driver, launched_profile = _start_driver(binary, profile)
    except WebDriverException as exc:
        subprocess.run(["pkill", "-f", str(profile)], check=False)  # evtl. per `open` gestartet
        pytest.skip(f"Firefox/geckodriver nicht startbar: {exc.msg}")
    try:
        driver.set_script_timeout(20)
        driver.install_addon(str(ext_dir), temporary=True)
        driver.get(POPUP_URL)
        driver.execute_async_script(
            "const done = arguments[arguments.length - 1];"
            "browser.storage.sync.set({ apiUrl: arguments[0], llmMode: 'off' }).then(() => done(true));",
            api_server,
        )
        yield driver
    finally:
        driver.quit()
        if launched_profile:  # per `open` gestartet → nur diese Instanz (Wegwerf-Profil) beenden
            subprocess.run(["pkill", "-f", str(launched_profile)], check=False)


def _panel(driver):
    return driver.find_element(By.ID, "scamguard-root").shadow_root


def _score(driver) -> int:
    def ready(d):
        try:
            text = _panel(d).find_element(By.CSS_SELECTOR, ".pct").text
        except WebDriverException:
            return False
        return text if text.endswith("%") else False

    return int(WebDriverWait(driver, 20).until(ready).split()[0])


def _marked_text(driver) -> str:
    return " ".join(m.text for m in driver.find_elements(By.CSS_SELECTOR, "mark.scamguard-mark"))


def test_scam_listing_is_marked_and_scored(firefox, site):
    firefox.get(f"{site}/s-anzeige/ps5-slim-neu-ovp")
    assert _score(firefox) >= 75
    assert "Hohes Risiko" in _panel(firefox).find_element(By.CSS_SELECTOR, ".verdict").text
    marked = _marked_text(firefox)
    for expected in ("kleinanzeigen-de.sicher-bezahlen.top", "zurzeit im Ausland", "Freunde und", "Familie"):
        assert expected in marked
    assert "scamguard-flagged" in firefox.find_element(By.ID, "viewad-price").get_attribute("class")
    # Panel-Styles greifen (Fallback auf <style>, weil Firefox adoptedStyleSheets blockiert)
    card = _panel(firefox).find_element(By.CSS_SELECTOR, ".card")
    assert card.value_of_css_property("border-radius") == "12px"
    if shots := os.environ.get("SCAMGUARD_SCREENSHOTS"):  # optional: Bilder für die Doku
        firefox.set_window_size(1280, 900)
        firefox.save_screenshot(f"{shots}/firefox_inserat.png")


def test_legit_listing_stays_unremarkable(firefox, site):
    firefox.get(f"{site}/s-anzeige/waschmaschine-bosch")
    assert _score(firefox) < 40
    assert not firefox.find_elements(By.CSS_SELECTOR, ".scamguard-sev-high, .scamguard-sev-mid")


def test_selected_text_on_any_page_is_checked(firefox, site):
    firefox.get(f"{site}/posteingang")
    mail_tab = firefox.current_window_handle
    message = firefox.find_element(By.ID, "nachricht").text
    firefox.switch_to.new_window("tab")
    firefox.get(POPUP_URL)
    result = firefox.execute_async_script(
        """const [text, done] = [arguments[0], arguments[arguments.length - 1]];
        browser.tabs.query({ url: 'http://127.0.0.1/posteingang' })
          .then(([tab]) => browser.runtime.sendMessage({ type: 'scanTab', tabId: tab.id, mode: 'selection', text }))
          .then(done, (err) => done({ ok: false, error: String(err) }));""",
        message,
    )
    assert result["ok"], result
    assert result["score"] >= 0.75
    firefox.close()
    firefox.switch_to.window(mail_tab)
    marked = _marked_text(firefox)
    assert "kleinanzeigen-de.sicher-bezahlen.top" in marked
    assert "Abholung" not in marked


def test_popup_shows_server_status(firefox):
    firefox.get(POPUP_URL)
    status = WebDriverWait(firefox, 10).until(
        lambda d: (el := d.find_element(By.ID, "server-status")).text != "Server …" and el)
    assert status.text == "Server verbunden"
    assert not firefox.find_element(By.ID, "permissions").is_displayed()


def test_chat_is_checked_live_when_a_scam_message_arrives(firefox, site):
    firefox.get(f"{site}/m-nachrichten.html")
    assert _score(firefox) < 40  # Vorschau anderer Unterhaltungen und Eingabefeld zählen nicht
    firefox.execute_script("addMessage(arguments[0])", "Geld zuerst, dann verschicke ich. Abholung ist "
                           "leider nicht möglich, sonst ist es bald weg!")
    WebDriverWait(firefox, 15).until(
        lambda d: "Unauffällig" not in _panel(d).find_element(By.CSS_SELECTOR, ".verdict").text)
    assert "Geld zuerst" in _marked_text(firefox)
    flagged = firefox.find_elements(By.CSS_SELECTOR, ".scamguard-flagged")
    assert len(flagged) == 1 and "Geld zuerst" in flagged[0].text
