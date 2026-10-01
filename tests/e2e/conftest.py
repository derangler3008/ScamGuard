"""Gemeinsame Fixtures für die Browser-Ende-zu-Ende-Tests (Chromium und Firefox).

Einmalig:  pip install -e ".[e2e]"  &&  playwright install chromium
Start:     pytest -m e2e        (ohne Browser/Pakete werden die Tests übersprungen)

Kleinanzeigen.de wird dabei NICHT aufgerufen: Die Tests liefern nachgebaute Seiten aus
(tests/e2e/fixtures), der ScamGuard-Server läuft im Testprozess.
"""

from __future__ import annotations

import io
import socket
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXTENSION_DIR = ROOT / "extension"
FIXTURES = Path(__file__).parent / "fixtures"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def render(name: str, **values: str) -> str:
    html = (FIXTURES / name).read_text(encoding="utf-8")
    for key, value in values.items():
        html = html.replace("{{" + key + "}}", value)
    return html


def active_since(days_ago: int) -> str:
    today = datetime.now(UTC).astimezone().date()  # lokales Datum, wie es die Extension sieht
    return (today - timedelta(days=days_ago)).strftime("%d.%m.%Y")


@pytest.fixture(scope="session")
def test_image_bytes() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (320, 240), "white")
    draw = ImageDraw.Draw(img)
    for x in range(0, 320, 40):
        draw.rectangle((x, 0, x + 20, 240), fill=(200, 40, 40))
    draw.ellipse((100, 60, 220, 180), fill=(30, 30, 160))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class _FakeLLM:
    """Antwortet wie der echte LLM-Judge, aber sofort und ohne Modell."""

    name = "llm"
    available = True
    unavailable_reason = None

    def predict(self, listing):
        from scamguard.schema import ModelResult, Signal

        time.sleep(1.0)  # die Extension zeigt so sichtbar „KI-Analyse läuft …“
        return ModelResult("llm", score=0.95, signals=[
            Signal("llm", "LLM_SUMMARY", "Typische Vorkasse-Masche – Masche: vorkasse, Sprache: "
                   "leichte_fehler (Test-LLM)", 0.95),
            Signal("llm", "LLM_AUSLAND", "Verkäufer angeblich im Ausland", 0.95,
                   evidence="auf Montage", highlights=["auf Montage"]),
        ])


@pytest.fixture(scope="session")
def api_server(test_image_bytes, tmp_path_factory):
    """Echter ScamGuard-Server (uvicorn) – das Testbild ist als bekanntes Fake-Bild registriert."""
    pytest.importorskip("imagehash")
    import uvicorn

    from scamguard import api
    from scamguard.config import load_config, with_overrides
    from scamguard.models.image_model import phash_of
    from scamguard.pipeline import ScamGuard

    tmp = tmp_path_factory.mktemp("api")
    image = tmp / "bekanntes_fake.jpg"
    image.write_bytes(test_image_bytes)
    hashes = tmp / "hashes.txt"
    hashes.write_text(phash_of(image) + "\n", encoding="utf-8")
    cfg = with_overrides(load_config(), {
        "image_model": {"known_fake_hashes": str(hashes), "use_clip_consistency": False},
    })
    guard = ScamGuard(cfg)
    # Für Anfragen mit KI-Analyse: echtes Setup, aber simuliertes LLM (kein Modell/Server nötig)
    guard_llm = ScamGuard(cfg)
    guard_llm.detectors = [d for d in guard_llm.detectors if d.name != "llm"] + [_FakeLLM()]

    # Eigene Labels in einen Temp-Ordner statt in data/raw/
    from scamguard.data import labels

    original_labels = labels.LABEL_FILE, labels.LABEL_IMAGE_DIR
    labels.LABEL_FILE, labels.LABEL_IMAGE_DIR = str(tmp / "eigene_labels.jsonl"), str(tmp / "label_bilder")

    original = api._guard
    api._guard = lambda use_llm: guard_llm if use_llm else guard
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("ScamGuard-Testserver startet nicht")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)
    api._guard = original
    labels.LABEL_FILE, labels.LABEL_IMAGE_DIR = original_labels


@pytest.fixture(scope="session")
def label_file(api_server):
    from scamguard.data import labels

    return Path(labels.LABEL_FILE)
