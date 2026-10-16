"""Betrugs-Screenshots aus Reddit holen – nur über die offizielle Reddit-API, mit eigenem Zugang.

    scamguard data sammeln --quellen reddit [--max 100]

Reddit sperrt per robots.txt alle Bots auf der Website; erlaubt ist der Zugriff über die Data API mit
registrierter App (nicht-kommerziell, Forschung). Deshalb kein Scraping der Website, sondern:

1. Auf https://www.reddit.com/prefs/apps eine App vom Typ „script“ anlegen (eigenes Konto).
2. In `.env` (nicht im Git) eintragen: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET und optional
   REDDIT_USER_AGENT (Reddit verlangt einen beschreibenden User-Agent mit Benutzername).
3. Subreddits und Suchbegriffe in data/quellen/reddit.yaml.

Ergebnis:
- Bilder der Beiträge lokal in data/raw/reddit/ (nicht im Git, nicht weitergeben – Urheberrecht).
- Texterkennung je Bild (Mac: Apple Vision, Windows: Windows-OCR). Bilder mit Text (Chats, Screenshots)
  werden zur Prüfliste data/datensatz_fuellen_text/gesammelt_reddit.csv – anonymisiert (redact.py), mit
  Vorschlag aus den Regeln. Die Spalte „betrug“ bleibt leer, bis ein Mensch sie füllt: Beiträge in
  Betrugs-Subreddits sind oft Fragen („Ist das Betrug?“), kein bestätigtes Label. Geprüfte Zeilen
  überstehen das nächste Sammeln. Gründlicher: als Aufgaben im Annotation-Workspace holen.
- Benutzernamen werden nicht gespeichert, nur der Link zum Beitrag.
"""

from __future__ import annotations

import csv
import os
import re
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import yaml

from scamguard.config import PROJECT_ROOT, load_config, resolve_path
from scamguard.data import ocr
from scamguard.data.collect_forums import _signal_codes
from scamguard.data.redact import redact

SEED_FILE = "data/quellen/reddit.yaml"
QUOTES_FILE = "data/datensatz_fuellen_text/gesammelt_reddit.csv"
IMAGE_DIR = "data/raw/reddit"
COLUMNS = ["nachricht", "betrug", "vorschlag", "warnsignale", "quelle", "quelle_titel", "bild", "datum"]
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"
IMAGE_HOSTS = ("i.redd.it", "preview.redd.it", "i.imgur.com")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
MIN_OCR_CHARS = 40  # weniger Text → eher Produktfoto als Chat/Screenshot
DELAY_SECONDS = 1.0


class MissingCredentials(RuntimeError):
    """REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET fehlen."""


def _env(name: str) -> str:
    """Umgebungsvariable oder Eintrag aus .env im Projektordner."""
    if os.environ.get(name):
        return os.environ[name]
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name and value.strip():
                return value.strip().strip('"').strip("'")
    return ""


class RedditClient:
    """App-only-OAuth (client_credentials) für öffentliche Beiträge; drosselt nach den Rate-Limit-Headern."""

    def __init__(self, transport: httpx.BaseTransport | None = None, delay: float = DELAY_SECONDS):
        client_id, secret = _env("REDDIT_CLIENT_ID"), _env("REDDIT_CLIENT_SECRET")
        if not client_id or not secret:
            raise MissingCredentials(
                "Reddit-Zugang fehlt: App vom Typ „script“ auf https://www.reddit.com/prefs/apps anlegen und "
                "REDDIT_CLIENT_ID sowie REDDIT_CLIENT_SECRET in .env eintragen (siehe .env.example).")
        agent = _env("REDDIT_USER_AGENT") or "windows:scamguard-forschung:0.12 (DHBW Mannheim)"
        self.http = httpx.Client(headers={"User-Agent": agent}, timeout=30, transport=transport,
                                 follow_redirects=True)
        self.delay, self.requests, self._last = delay, 0, 0.0
        token = self.http.post(TOKEN_URL, auth=(client_id, secret), data={"grant_type": "client_credentials"})
        token.raise_for_status()
        self.http.headers["Authorization"] = f"Bearer {token.json()['access_token']}"

    def _wait(self) -> None:
        pause = self.delay - (time.monotonic() - self._last)
        if pause > 0:
            time.sleep(pause)
        self._last = time.monotonic()

    def get(self, url: str, **params) -> httpx.Response:
        self._wait()
        response = self.http.get(url, params=params or None)
        self.requests += 1
        remaining = float(response.headers.get("x-ratelimit-remaining", 10))
        if remaining < 2:
            time.sleep(float(response.headers.get("x-ratelimit-reset", 60)))
        response.raise_for_status()
        return response

    def listing(self, source: dict, limit: int) -> Iterator[dict]:
        """Beiträge eines Subreddits (neueste zuerst) bzw. einer Suche darin."""
        sub = source["subreddit"]
        path, params = (f"/r/{sub}/search", {"q": source["suche"], "restrict_sr": 1, "sort": "new"}) \
            if source.get("suche") else (f"/r/{sub}/new", {})
        after, seen = None, 0
        while seen < limit:
            data = self.get(f"{API}{path}", limit=min(100, limit - seen), after=after, raw_json=1,
                            **params).json()["data"]
            for child in data["children"]:
                seen += 1
                yield child["data"]
            after = data.get("after")
            if not after or not data["children"]:
                return


def image_urls(post: dict) -> list[str]:
    """Bild-Adressen eines Beitrags: Einzelbild oder Galerie (nur Reddit-/Imgur-Bildserver)."""
    urls = []
    if post.get("is_gallery"):
        meta = post.get("media_metadata") or {}
        for item in (post.get("gallery_data") or {}).get("items", []):
            source = (meta.get(item.get("media_id"), {}).get("s") or {})
            if source.get("u"):
                urls.append(source["u"])
    elif post.get("url"):
        urls.append(post["url"])
    return [u for u in urls if any(host in u for host in IMAGE_HOSTS)
            and (u.split("?")[0].lower().endswith(IMAGE_SUFFIXES) or "preview.redd.it" in u)]


def _key(text: str) -> str:
    return re.sub(r"\W+", "", text.lower())


def _reviewed_rows(path: Path) -> dict[str, dict]:
    """Von Menschen geprüfte Zeilen (Spalte „betrug“ gefüllt) aus einer früheren Prüfliste."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {_key(r["nachricht"]): {c: r.get(c) or "" for c in COLUMNS}
                for r in csv.DictReader(f) if (r.get("betrug") or "").strip()}


def _ocr_text(data: bytes) -> str:
    lines, width = ocr.recognize(data)
    main, side = ocr.layout_rows(lines, width)
    return "\n".join(main + side).strip()


def collect(max_posts: int | None = None, client: RedditClient | None = None,
            quotes_file: str | None = QUOTES_FILE, log: Callable[[str], None] = print) -> list[dict]:
    """Beiträge mit Bildern holen → Bilder speichern → Texterkennung → Prüfliste."""
    if not ocr.available():
        raise ocr.OcrUnavailable(ocr.UNAVAILABLE_HINT)
    seeds = yaml.safe_load(resolve_path(SEED_FILE).read_text(encoding="utf-8")) or {}
    limit = max_posts or int(seeds.get("max_beitraege", 100))
    client = client or RedditClient()
    lexicon = str(resolve_path(load_config()["paths"]["lexicon"]))
    image_dir = resolve_path(IMAGE_DIR)
    image_dir.mkdir(parents=True, exist_ok=True)
    rows, seen, photos = [], set(), 0
    for source in seeds.get("quellen", []):
        label = f"r/{source['subreddit']}" + (f" „{source['suche']}“" if source.get("suche") else "")
        log(f"Quelle {label} … (bis zu {limit} Beiträge)")
        for post in client.listing(source, limit):
            for n, url in enumerate(image_urls(post), 1):
                suffix = Path(url.split("?")[0]).suffix.lower() or ".jpg"
                target = image_dir / f"{post['id']}_{n}{suffix if suffix in IMAGE_SUFFIXES else '.jpg'}"
                if not target.exists():
                    try:
                        target.write_bytes(client.get(url).content)
                    except httpx.HTTPError as exc:
                        log(f"  Bild nicht geladen ({exc.__class__.__name__}): {url}")
                        continue
                try:
                    text = _ocr_text(target.read_bytes())
                except ValueError:
                    continue
                if len(text) < MIN_OCR_CHARS:
                    photos += 1
                    continue
                message = redact(text)
                if _key(message) in seen:
                    continue
                seen.add(_key(message))
                signals = _signal_codes(message, lexicon)
                rows.append({"nachricht": message, "betrug": "", "vorschlag": "ja" if signals else "?",
                             "warnsignale": " ".join(signals),
                             "quelle": f"https://www.reddit.com{post.get('permalink', '')}",
                             "quelle_titel": redact(post.get("title", "")),
                             "bild": target.relative_to(PROJECT_ROOT).as_posix(),
                             "datum": time.strftime("%Y-%m-%d", time.gmtime(post.get("created_utc", 0)))})
    if quotes_file:
        path = resolve_path(quotes_file)
        reviewed = _reviewed_rows(path)
        for row in rows:
            row["betrug"] = reviewed.pop(_key(row["nachricht"]), {}).get("betrug", "")
        rows += reviewed.values()  # Geprüftes nie verwerfen
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        done = sum(bool(r["betrug"]) for r in rows)
        log(f"Gespeichert: {path} ({len(rows)} Screenshots mit Text, {done} geprüft; {photos} Fotos ohne Text "
            f"übersprungen) – Spalte „betrug“ mit ja/nein füllen, nur geprüfte Zeilen zählen fürs Training")
    log(f"  {client.requests} API-Anfragen, Bilder in {image_dir}")
    return rows
