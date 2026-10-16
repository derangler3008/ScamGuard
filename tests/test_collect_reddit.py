"""Reddit-Sammler: nur offizielle API, Bilder → Texterkennung → anonymisierte Prüfliste (ohne Netz)."""

import csv
import io

import httpx
import pytest
from PIL import Image

from scamguard.data import collect_reddit, ocr
from scamguard.data.ocr import OcrLine


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buf, format="PNG")
    return buf.getvalue()


POSTS = {"data": {"after": None, "children": [
    {"data": {"id": "abc", "title": "Ist das Betrug? Schreibt mir anonym@gmail.com", "permalink": "/r/x/comments/abc/",
              "created_utc": 1760000000, "url": "https://i.redd.it/chat.png", "author": "geheim"}},
    {"data": {"id": "gal", "title": "Galerie", "permalink": "/r/x/comments/gal/", "created_utc": 1760000000,
              "is_gallery": True, "gallery_data": {"items": [{"media_id": "m1"}]},
              "media_metadata": {"m1": {"s": {"u": "https://preview.redd.it/foto.jpg?width=640"}}}}},
    {"data": {"id": "txt", "title": "Nur Text", "permalink": "/r/x/comments/txt/", "url": "https://example.com/a"}},
]}}


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v1/access_token":
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(200, json={"access_token": "t"})
    if request.url.host == "oauth.reddit.com":
        assert request.headers["authorization"] == "Bearer t"
        return httpx.Response(200, json=POSTS)
    return httpx.Response(200, content=_png())


def test_image_urls_only_from_image_hosts():
    urls = [collect_reddit.image_urls(p["data"]) for p in POSTS["data"]["children"]]
    assert urls == [["https://i.redd.it/chat.png"], ["https://preview.redd.it/foto.jpg?width=640"], []]


def test_missing_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(collect_reddit, "PROJECT_ROOT", tmp_path)  # keine .env
    with pytest.raises(collect_reddit.MissingCredentials):
        collect_reddit.RedditClient()


def test_collect_writes_redacted_review_list(monkeypatch, tmp_path):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "geheim")
    monkeypatch.setattr(collect_reddit, "PROJECT_ROOT", tmp_path)
    seeds = tmp_path / "reddit.yaml"
    seeds.write_text("quellen:\n  - subreddit: x\n", encoding="utf-8")
    monkeypatch.setattr(collect_reddit, "SEED_FILE", str(seeds))
    monkeypatch.setattr(collect_reddit, "IMAGE_DIR", str(tmp_path / "raw" / "reddit"))
    quotes = tmp_path / "gesammelt_reddit.csv"
    texts = iter([
        [OcrLine("Bitte zahle per PayPal Freunde und Familie, schreib mir auf WhatsApp +44 7911 123456", 0, 10, 0, 500)],
        [],  # Foto ohne Text
    ])
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "recognize", lambda data: (next(texts), 600))
    client = collect_reddit.RedditClient(transport=httpx.MockTransport(_handler), delay=0)

    rows = collect_reddit.collect(max_posts=10, client=client, quotes_file=str(quotes), log=lambda msg: None)
    assert len(rows) == 1
    row = rows[0]
    assert row["betrug"] == "" and row["vorschlag"] == "ja"           # Label vergibt ein Mensch
    assert "7911 123456" not in row["nachricht"]                        # Telefonnummer anonymisiert
    assert row["quelle"] == "https://www.reddit.com/r/x/comments/abc/" and "geheim" not in str(row)
    assert (tmp_path / row["bild"]).exists()
    with quotes.open(encoding="utf-8") as f:
        saved = list(csv.DictReader(f))
    assert saved[0]["nachricht"] == row["nachricht"]


def test_reviewed_rows_survive_next_collect(tmp_path):
    path = tmp_path / "liste.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=collect_reddit.COLUMNS)
        writer.writeheader()
        writer.writerow({"nachricht": "Geld zuerst!", "betrug": "ja"})
        writer.writerow({"nachricht": "Noch da?", "betrug": ""})
    reviewed = collect_reddit._reviewed_rows(path)
    assert list(reviewed) == ["geldzuerst"]
