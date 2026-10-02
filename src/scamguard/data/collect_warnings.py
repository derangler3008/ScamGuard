"""Öffentliche Betrugswarnungen als Textdaten sammeln – Nachrichten, E-Mails, SMS (keine Bilder).

Kleinanzeigen selbst wird NICHT gescrapt (Nutzungsbedingungen, Bot-Sperre, Personendaten). Stattdessen
Seiten, die Betrugsnachrichten im Wortlaut veröffentlichen (robots.txt erlaubt das, Stand 10/2026):

  watchlist_alarm  Watchlist Internet „Phishing-Alarm“: vollständiger Wortlaut gemeldeter Phishing-Mails/
                   -SMS, z. B. gefälschte willhaben-Nachrichten
  vz_radar         Verbraucherzentrale „Phishing-Radar“ (aktuell + Archiv): Betreffzeilen und wörtlich
                   zitierte Sätze (die Erklärtexte der Verbraucherzentrale werden NICHT übernommen –
                   sonst lernt das Modell deren Schreibstil statt der Betrugsmuster)
  watchlist_news   Watchlist-Artikel zu Kleinanzeigen/Marktplätzen/Paketen: wörtliche Zitate

Alle Texte sind Betrug (Label 1). Seriöse Gegenbeispiele müsst ihr ergänzen (eigene Chats, Extension).

Rechtliches: Text- und Data-Mining für nicht-kommerzielle wissenschaftliche Forschung (§ 60d UrhG). Die
Daten bleiben lokal (data/ ist nicht im Git), jede Zeile nennt ihre Quelle → im Bericht zitieren.
Höflich: eigener User-Agent, robots.txt wird geprüft, Pause zwischen Anfragen, Seiten-Cache.
"""

from __future__ import annotations

import csv
import hashlib
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from scamguard.config import resolve_path

USER_AGENT = ("Mozilla/5.0 (compatible; ScamGuard-DHBW-Studienprojekt; "
              "+https://github.com/derangler3008/ScamGuard)")
CACHE_DIR = "data/raw/warnungen_cache"
OUTPUT_FILE = "data/datensatz_fuellen_text/gesammelt_warnungen.csv"
DELAY_SECONDS = 1.5

WATCHLIST = "https://www.watchlist-internet.at"
WATCHLIST_ALARM = f"{WATCHLIST}/phishing-alarm/"
WATCHLIST_SITEMAP = f"{WATCHLIST}/sitemap.xml"
VZ_PAGES = [("https://www.verbraucherzentrale.de/wissen/digitale-welt/phishingradar/"
             "phishingradar-aktuelle-warnungen-6059"),
            "https://www.verbraucherzentrale.de/phishingradar-archiv-71872"]
# Artikel-URLs, die zu Kleinanzeigen-Betrug passen
NEWS_KEYWORDS = re.compile(r"kleinanzeige|willhaben|shpock|vinted|marktplatz|privatverkauf|verkauf|"
                           r"kaeufer|kaufer|paket|post-|dhl|sicher-bezahlen|dreiecksbetrug|whatsapp", re.IGNORECASE)
# Zitate: Öffnendes Zeichen steht nach Leerraum/Klammer, schließendes vor Leerraum/Satzzeichen – so klappt
# es auch, wenn Quellen Stile mischen ("…“, „…" …). Stil-Paare allein reichen nicht (“ öffnet im Englischen).
QUOTE = re.compile(r"(?:^|(?<=[\s(\[:/]))[„“\"»«‚'](?=\S)([^„“”\"»«\n]{15,600}?)(?<=\S)[“”\"«»‘'](?=[\s.,;:!?)\]–-]|$)",
                   re.MULTILINE)
ONLY_DATE = re.compile(r"^[\d.\s]*(\w+)?[\d.\s]*$")
DATE_HEADING = re.compile(r"^(\d{1,2})\. (\w+) (20\d\d):?\s*(.*)$")
COLUMNS = ["nachricht", "betrug", "masche", "art", "quelle", "quelle_titel", "datum"]


@dataclass
class Row:
    nachricht: str
    art: str
    quelle: str
    quelle_titel: str = ""
    datum: str = ""
    masche: str = "phishing"
    betrug: str = "ja"


# --------------------------------------------------------------------------- Abrufen (höflich)

class Fetcher:
    """GET mit robots.txt-Prüfung, Pause zwischen Netzwerkanfragen und Datei-Cache."""

    def __init__(self, cache_dir: str | None = CACHE_DIR, delay: float = DELAY_SECONDS,
                 log: Callable[[str], None] = print):
        self.cache = resolve_path(cache_dir) if cache_dir else None
        self.delay = delay
        self.log = log
        self._robots: dict[str, RobotFileParser] = {}
        self._last = 0.0
        self.requests = 0

    def _wait(self) -> None:
        pause = self.delay - (time.monotonic() - self._last)
        if pause > 0:
            time.sleep(pause)
        self._last = time.monotonic()

    def _download(self, url: str) -> str:
        from urllib.request import Request, urlopen

        self._wait()
        self.requests += 1
        with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def allowed(self, url: str) -> bool:
        host = urlparse(url)
        root = f"{host.scheme}://{host.netloc}"
        if root not in self._robots:
            parser = RobotFileParser()
            try:
                parser.parse(self._download(f"{root}/robots.txt").splitlines())
            except HTTPError as exc:  # Konvention wie urllib: 401/403 = alles verboten, sonst nichts
                parser.disallow_all = exc.code in (401, 403)
            except OSError:
                parser.parse([])
            self._robots[root] = parser
        return self._robots[root].can_fetch(USER_AGENT, url)

    def get(self, url: str, cache: bool = True) -> str | None:
        """cache=False für Übersichten/Sitemaps – die ändern sich, einzelne Meldungen nicht."""
        cached = (self.cache / f"{hashlib.sha1(url.encode()).hexdigest()}.html"
                  if self.cache and cache else None)
        if cached and cached.exists():
            return cached.read_text(encoding="utf-8")
        if not self.allowed(url):
            self.log(f"  übersprungen (robots.txt): {url}")
            return None
        try:
            html = self._download(url)
        except OSError as exc:
            self.log(f"  nicht erreichbar: {url} ({exc})")
            return None
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(html, encoding="utf-8")
        return html


# --------------------------------------------------------------------------- Auswerten (ohne Netz)

def _strings_after(start, stop=None) -> Iterable[str]:
    """Texte nach `start` (ohne dessen eigenen Text) bis vor `stop`; Skripte/Styles ausgelassen."""
    from bs4 import NavigableString

    for el in start.next_elements:
        if el is stop:
            return
        if (isinstance(el, NavigableString) and not any(p is start for p in el.parents)
                and el.parent.name not in ("script", "style", "noscript", "title", "svg")):
            yield str(el)


def _soup(html: str):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser")


def _clean(text: str) -> str:
    return re.sub(r"[ \t ]+", " ", text).strip()


def extract_quotes(text: str, min_len: int = 15) -> list[str]:
    """Wörtliche Zitate („…“, "…", »…«) ab min_len Zeichen, ohne Doppelte."""
    out: list[str] = []
    for q in QUOTE.findall(text):
        q = _clean(q)
        words = re.findall(r"[^\W\d_]{2,}", q)
        if len(q) >= min_len and len(words) >= 2 and not ONLY_DATE.match(q) and q not in out:
            out.append(q)
    return out


def parse_watchlist_alarm_list(html: str, base: str = WATCHLIST) -> tuple[list[str], list[str]]:
    """Übersichtsseite „Phishing-Alarm“ → (Meldungen, weitere Übersichtsseiten)."""
    soup = _soup(html)
    items, pages = [], []
    for a in soup.select("a[href]"):
        href = urljoin(base, a["href"].split("#")[0])
        path = urlparse(href).path.strip("/").split("/")
        if len(path) == 2 and path[0] == "phishing" and href not in items:
            items.append(href)
        elif len(path) == 2 and path[0] == "phishing-alarm" and path[1].startswith("page-") and href not in pages:
            pages.append(href)
    return items, pages


def parse_watchlist_alarm_item(html: str) -> tuple[str, str] | None:
    """Einzelne Phishing-Meldung → (Betreff, Wortlaut) oder None."""
    soup = _soup(html)
    marker = next((h for h in soup.find_all(["h2", "h3"]) if "Warnung vor dieser Nachricht" in h.get_text()), None)
    if marker is None:
        return None
    lines = []
    for el in _strings_after(marker):
        text = _clean(el)
        if not text:
            continue
        if text in ("Warnung", "Tipp", "Type Information") or text.startswith("Allgemeine Tipps"):
            break
        lines.append(text)
    body = "\n".join(lines).strip()
    title = soup.find("h1")
    subject = _clean(title.get_text(" ")) if title else ""
    return (subject, body) if len(body) >= 20 else None


def parse_vz_radar(html: str) -> list[tuple[str, str, list[str]]]:
    """Phishing-Radar → [(Datum, Überschrift, wörtliche Zitate inkl. Betreff)] je Eintrag."""
    soup = _soup(html)
    entries: list[tuple[str, str, list[str]]] = []
    headings = [h for h in soup.find_all(["h2", "h3", "strong"])
                if DATE_HEADING.match(_clean(h.get_text(" ")))]
    for i, heading in enumerate(headings):
        day, month, year, title = DATE_HEADING.match(_clean(heading.get_text(" "))).groups()
        stop = headings[i + 1] if i + 1 < len(headings) else None
        text = _clean(" ".join(_strings_after(heading, stop)))
        quotes = extract_quotes(text)
        if quotes:
            entries.append((f"{day}. {month} {year}", title, quotes))
    return entries


def parse_sitemap(xml: str) -> list[str]:
    return [u.replace("&amp;", "&") for u in re.findall(r"<loc>([^<]+)</loc>", xml)]


def parse_news_quotes(html: str) -> tuple[str, list[str]]:
    soup = _soup(html)
    body = soup.select_one("article") or soup.select_one("main") or soup
    title = soup.find("h1")
    return (_clean(title.get_text(" ")) if title else "", extract_quotes(body.get_text(" ")))


# --------------------------------------------------------------------------- Sammeln

def _watchlist_alarm(fetch: Fetcher, max_items: int | None) -> Iterable[Row]:
    queue, seen_pages, items = [WATCHLIST_ALARM], set(), []
    while queue and (max_items is None or len(items) < max_items):
        page = queue.pop(0)
        if page in seen_pages:
            continue
        seen_pages.add(page)
        html = fetch.get(page, cache=False)
        if not html:
            continue
        new_items, pages = parse_watchlist_alarm_list(html)
        items += [i for i in new_items if i not in items]
        queue += [p for p in pages if p not in seen_pages]
    for url in items[:max_items]:
        html = fetch.get(url)
        parsed = parse_watchlist_alarm_item(html) if html else None
        if parsed:
            subject, body = parsed
            yield Row(f"{subject}\n{body}" if subject else body, "phishing_wortlaut", url, subject)


def _vz_radar(fetch: Fetcher, max_items: int | None) -> Iterable[Row]:
    for url in VZ_PAGES:
        html = fetch.get(url, cache=False)  # Radar und Archiv wachsen
        for date, title, quotes in (parse_vz_radar(html) if html else [])[:max_items]:
            yield Row("\n".join(quotes), "phishing_zitate", url, title, date)


def _watchlist_news(fetch: Fetcher, max_items: int | None) -> Iterable[Row]:
    sitemap = fetch.get(WATCHLIST_SITEMAP, cache=False)
    news: list[str] = []
    for sub in parse_sitemap(sitemap or ""):
        if "warnungentipps" not in sub:
            continue
        news += [u for u in parse_sitemap(fetch.get(sub, cache=False) or "")
                 if "/news/" in u and NEWS_KEYWORDS.search(u)]
    for url in news[:max_items]:
        html = fetch.get(url)
        if not html:
            continue
        title, quotes = parse_news_quotes(html)
        for quote in quotes:
            yield Row(quote, "zitat_artikel", url, title, masche="unbekannt")


SOURCES = {"watchlist_alarm": _watchlist_alarm, "vz_radar": _vz_radar, "watchlist_news": _watchlist_news}


def collect(sources: Iterable[str] = tuple(SOURCES), max_items: int | None = None,
            fetch: Fetcher | None = None, output: str | None = OUTPUT_FILE,
            log: Callable[[str], None] = print) -> list[Row]:
    """Alle Quellen abfragen, Duplikate entfernen, als CSV in den Text-Ablageordner schreiben."""
    fetch = fetch or Fetcher(log=log)
    rows: list[Row] = []
    seen: set[str] = set()
    for name in sources:
        log(f"Quelle {name} …")
        before = len(rows)
        for row in SOURCES[name](fetch, max_items):
            key = re.sub(r"\W+", "", row.nachricht.lower())
            if key not in seen:
                seen.add(key)
                rows.append(row)
        log(f"  {len(rows) - before} Texte")
    if output:
        path = resolve_path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows({c: getattr(r, c) for c in COLUMNS} for r in rows)
        log(f"Gespeichert: {path} ({len(rows)} Texte, {fetch.requests} Anfragen)")
    return rows

