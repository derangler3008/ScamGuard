"""Upload im Streamlit-Frontend mit echtem Browser: Screenshot hineinziehen → Betrug → gespeichert.

Die App läuft als eigener Streamlit-Prozess; ihre Labels landen in einem Temp-Ordner (Wrapper-Skript),
nie in data/raw/eigene_labels.jsonl.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest
from conftest import free_port
from PIL import Image, ImageDraw, ImageFont

from scamguard.data import ocr

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]
WRAPPER = f"""
import os, runpy, sys
sys.path.insert(0, {str(ROOT / "src")!r})
from scamguard.data import labels
labels.LABEL_FILE = os.environ["SG_LABEL_FILE"]
labels.LABEL_IMAGE_DIR = os.environ["SG_LABEL_IMAGES"]
runpy.run_path({str(ROOT / "frontend" / "app.py")!r}, run_name="__main__")
"""


@pytest.fixture
def streamlit_app(tmp_path):
    port = free_port()
    wrapper = tmp_path / "app_wrapper.py"
    wrapper.write_text(WRAPPER, encoding="utf-8")
    env = {**os.environ, "SG_LABEL_FILE": str(tmp_path / "labels.jsonl"),
           "SG_LABEL_IMAGES": str(tmp_path / "label_bilder")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(wrapper), "--server.port", str(port),
         "--server.address", "127.0.0.1", "--server.headless", "true",
         "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                with urlopen(f"{url}/_stcore/health", timeout=2) as resp:
                    if resp.read() == b"ok":
                        break
            except OSError:
                if time.monotonic() > deadline or proc.poll() is not None:
                    pytest.fail("Streamlit ist nicht gestartet")
                time.sleep(0.5)
        yield url, tmp_path / "labels.jsonl"
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def _screenshot(path: Path) -> None:
    img = Image.new("RGB", (900, 520), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 40)
    small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
    draw.text((40, 40), "PlayStation 5 Disc Edition", font=font, fill="black")
    draw.text((40, 110), "120 € VB", font=font, fill="black")
    draw.text((40, 200), "Beschreibung", font=small, fill="black")
    draw.text((40, 250), "Zahlung nur über PayPal Freunde und Familie.", font=small, fill="black")
    draw.text((40, 290), "Ich bin im Ausland, Versand per DHL.", font=small, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    path.write_bytes(buf.getvalue())


@pytest.mark.skipif(not ocr.available(), reason="Texterkennung nur auf dem Mac (Apple Vision)")
def test_upload_screenshot_and_label_as_scam(streamlit_app, tmp_path):
    from playwright.sync_api import sync_playwright

    url, label_file = streamlit_app
    shot = tmp_path / "anzeige.png"
    _screenshot(shot)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.goto(url)
        page.get_by_text("Noch leer").wait_for(timeout=90_000)
        page.locator("input[type=file]").first.set_input_files(str(shot))
        page.get_by_text("PlayStation 5 Disc Edition").first.wait_for(timeout=60_000)
        page.get_by_role("button", name="⚠ Betrug").click()
        page.get_by_text("Gespeichert als").wait_for(timeout=60_000)
        browser.close()
    stored = [json.loads(line) for line in label_file.read_text(encoding="utf-8").splitlines()]
    assert len(stored) == 1
    assert stored[0]["label"] == 1 and stored[0]["title"] == "PlayStation 5 Disc Edition"
    assert stored[0]["price"] == 120.0 and "Freunde und Familie" in stored[0]["description"]
