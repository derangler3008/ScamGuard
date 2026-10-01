"""Firefox mit der ScamGuard-Extension starten – zum Ausprobieren auf echten Seiten.

    scamguard api                                   # Server (in einem zweiten Terminal)
    python scripts/firefox_mit_extension.py         # Firefox + Extension + kleinanzeigen.de
    python scripts/firefox_mit_extension.py URL …   # stattdessen bestimmte Seiten öffnen
    python scripts/firefox_mit_extension.py --reload # nur Extension neu laden (nach Code-Änderungen)

- Eigenes Entwicklungsprofil (.firefox-dev-profil/, nicht im Git): eure normalen Firefox-Profile
  mit Lesezeichen und Logins bleiben unberührt.
- Die Extension wird – wie über about:debugging – als *temporäres* Add-on geladen, genau so wie es
  Mozillas `web-ext run` macht (Remote-Debugging-Protokoll, nur auf localhost). Nach dem Beenden
  von Firefox ist sie wieder weg → Skript erneut starten. Läuft das Entwicklungs-Firefox schon,
  wird die Extension nur neu geladen (praktisch nach Code-Änderungen).
- Anders als bei Selenium/Marionette läuft Firefox dabei nicht im Fernsteuerungsmodus
  (navigator.webdriver bleibt false) – Bot-Schutz von Webseiten schlägt also nicht an.
- macOS: Firefox wird über `open` als eigenständige App gestartet. Direkt aus IDEs/Tools gestartet,
  erbt es sonst deren Datenschutzrechte und findet sein Profil nicht („Could not find profile folder“).
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"
PROFILE = ROOT / ".firefox-dev-profil"
PORT_FILE = PROFILE / "scamguard-debugger-port"
DEFAULT_URLS = ["https://www.kleinanzeigen.de/"]
API_HEALTH = "http://127.0.0.1:8000/health"
FIREFOX_CANDIDATES = [
    Path("/Applications/Firefox.app/Contents/MacOS/firefox"),
    Path("/usr/bin/firefox"),
    Path("C:/Program Files/Mozilla Firefox/firefox.exe"),
]
PREFS = {
    # Remote-Debugging (nur localhost) für die Add-on-Installation
    "devtools.debugger.remote-enabled": True,
    "devtools.chrome.enabled": True,
    "devtools.debugger.prompt-connection": False,
    # Ruhiger Start ohne Begrüßungsseiten, keine Telemetrie aus dem Testprofil
    "browser.shell.checkDefaultBrowser": False,
    "browser.aboutwelcome.enabled": False,
    "browser.startup.homepage_override.mstone": "ignore",
    "datareporting.policy.dataSubmissionEnabled": False,
    "toolkit.telemetry.reportingpolicy.firstRun": False,
}


class RemoteDebugger:
    """Minimaler Client für Firefox' Remote-Debugging-Protokoll (Pakete: "<Länge>:<JSON>")."""

    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=20)
        self.buffer = b""
        self._receive()  # Begrüßung des Root-Actors

    def _chunk(self) -> bytes:
        data = self.sock.recv(65536)
        if not data:
            raise ConnectionError("Firefox hat die Verbindung getrennt")
        return data

    def _receive(self) -> dict:
        while b":" not in self.buffer:
            self.buffer += self._chunk()
        length, _, rest = self.buffer.partition(b":")
        size = int(length)
        while len(rest) < size:
            rest += self._chunk()
        self.buffer = rest[size:]
        return json.loads(rest[:size])

    def request(self, to: str, kind: str, **params) -> dict:
        packet = json.dumps({"to": to, "type": kind, **params}).encode("utf-8")
        self.sock.sendall(str(len(packet)).encode("ascii") + b":" + packet)
        while True:
            reply = self._receive()
            if reply.get("from") != to:
                continue  # Ereignisse anderer Actors überspringen
            if "error" in reply:
                raise RuntimeError(f"{reply['error']}: {reply.get('message', '')}")
            return reply

    def install_temporary_addon(self, path: Path) -> str:
        addons_actor = self.request("root", "getRoot")["addonsActor"]
        return self.request(addons_actor, "installTemporaryAddon", addonPath=str(path))["addon"]["id"]

    def close(self) -> None:
        self.sock.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _firefox_binary() -> Path:
    binary = next((p for p in FIREFOX_CANDIDATES if p.exists()), None)
    if binary is None:
        sys.exit("Firefox nicht gefunden – bitte installieren (https://www.mozilla.org/firefox/).")
    return binary


def _run_firefox(binary: Path, args: list[str]) -> None:
    """Startet Firefox (bzw. reicht Argumente an die laufende Instanz dieses Profils weiter)."""
    if sys.platform == "darwin":
        app = binary.parents[2]  # …/Firefox.app
        subprocess.run(["open", "-n", "-a", str(app), "--args", *args], check=True)
    else:
        subprocess.Popen([str(binary), *args], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _running_debugger_port() -> int | None:
    if PORT_FILE.exists():
        try:
            port = int(PORT_FILE.read_text(encoding="utf-8").strip())
        except ValueError:
            return None
        if _port_open(port):
            return port
    return None


def _start_firefox(binary: Path) -> int:
    PROFILE.mkdir(exist_ok=True)
    port = _free_port()
    (PROFILE / "user.js").write_text(
        "".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});\n" for k, v in PREFS.items()),
        encoding="utf-8",
    )
    PORT_FILE.write_text(str(port), encoding="utf-8")
    _run_firefox(binary, ["-profile", str(PROFILE), "-start-debugger-server", str(port)])
    deadline = time.time() + 60
    while not _port_open(port):
        if time.time() > deadline:
            sys.exit("Firefox antwortet nicht (Debugging-Port). Läuft bereits ein Firefox mit diesem Profil?")
        time.sleep(0.3)
    return port


def main(urls: list[str]) -> None:
    try:
        urlopen(API_HEALTH, timeout=2)
    except (URLError, OSError):
        print("⚠ ScamGuard-Server nicht erreichbar – in einem zweiten Terminal `scamguard api` starten.")

    binary = _firefox_binary()
    port = _running_debugger_port()
    fresh = port is None
    if fresh:
        port = _start_firefox(binary)

    debugger = RemoteDebugger(port)
    try:
        addon_id = debugger.install_temporary_addon(EXTENSION)
    finally:
        debugger.close()
    print(f"✓ Extension {addon_id} {'geladen' if fresh else 'neu geladen'}")

    # Seiten erst nach der Installation öffnen, damit die Content Scripts sicher aktiv sind.
    # Firefox reicht die Adressen an die bereits laufende Instanz dieses Profils weiter.
    if urls:
        _run_firefox(binary, ["-profile", str(PROFILE), *urls])
        print(f"✓ Geöffnet: {', '.join(urls)}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--reload"]:  # nur die Extension neu laden (nach Code-Änderungen), keine neuen Tabs
        main([])
    else:
        main(args or DEFAULT_URLS)
