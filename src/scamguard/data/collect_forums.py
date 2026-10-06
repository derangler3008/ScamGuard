"""Erfahrungsberichte aus Foren sammeln: Wie sind Leute auf Kleinanzeigen betrogen worden?

Liefert zwei Dinge:

1. Zitierte Nachrichten zum Prüfen (→ data/datensatz_fuellen_text/gesammelt_foren.csv).
   Opfer posten oft, was ihnen geschrieben wurde: als eingefügtes Zitat ohne Autor oder in
   Anführungszeichen nach „bekam folgende Nachricht:“. Diese Stellen werden gesammelt – Zitate
   anderer Forenmitglieder („X schrieb:“) nicht. Zitiert wird in Foren aber auch Harmloses (Antworten
   des Kleinanzeigen-Supports, Gesetzestexte). Darum bleibt die Spalte `betrug` LEER, bis ein Mensch
   „ja“ oder „nein“ einträgt; `vorschlag` hilft dabei (ja = enthält ein Warnsignal). Nur geprüfte
   Zeilen fließen ins Training – „nein“-Zeilen sind willkommene seriöse Gegenbeispiele. Eure
   Einträge bleiben beim nächsten `scamguard data sammeln` erhalten.
2. Erfahrungsberichte (→ data/raw/foren_erfahrungsberichte.jsonl, NICHT im Training): Beiträge, in
   denen jemand von eigenem (versuchtem) Betrug erzählt – für die Auswertung (welche Maschen
   wie oft, `summarize`) und als Quelle für neue Lexikon-Muster.

Welche Threads gelesen werden, steht in data/quellen/foren.yaml (dort ergänzen). Unterstützt werden
die verbreiteten Forensoftwares XenForo (ComputerBase, gs-forum …), WoltLab (unknowns.de …) und das
vBulletin-Archiv (Antispam e.V.).

Bewusst NICHT: Reddit (robots.txt sperrt alle Bots, Datennutzung nur mit Genehmigung), gutefrage.net
(Bot-Sperre – wird nicht umgangen), eBay-Community (Nutzungsbedingungen verbieten automatisches Lesen).

Datenschutz: Forennamen werden nicht gespeichert, Kontaktdaten anonymisiert (data/redact.py), Texte
bleiben lokal (data/ ist nicht im Git). Rechtsgrundlage: § 60d UrhG (Text- und Data-Mining für
nicht-kommerzielle Forschung); höflich wie collect_warnings: robots.txt, Pause, Cache.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from urllib.parse import urljoin, urlparse, urlunparse

import yaml
from bs4 import BeautifulSoup, Tag

from scamguard.config import load_config, resolve_path
from scamguard.data.collect_warnings import Fetcher, _clean
from scamguard.data.redact import redact
from scamguard.features.contact import analyze_contacts
from scamguard.features.language import looks_german
from scamguard.features.scam_phrases import match_phrases

SEED_FILE = "data/quellen/foren.yaml"
QUOTES_FILE = "data/datensatz_fuellen_text/gesammelt_foren.csv"
REPORTS_FILE = "data/raw/foren_erfahrungsberichte.jsonl"
DEFAULT_MAX_PAGES = 8
REVIEW_COLUMNS = ["nachricht", "betrug", "vorschlag", "warnsignale", "quelle", "quelle_titel", "datum"]

# Wie collect_warnings.QUOTE, aber über Zeilen hinweg: In Foren werden ganze Nachrichten eingefügt
FORUM_QUOTE = re.compile(r"(?:^|(?<=[\s(\[:/]))[„“\"»](?=\S)([^„“”\"»«]{15,1500}?)(?<=\S)[“”\"«]"
                         r"(?=[\s.,;:!?)\]–-]|$)", re.MULTILINE)
# Wie Betroffene eine Masche BESCHREIBEN (das Lexikon erkennt dagegen den Wortlaut der Betrüger)
MASCHEN_IN_BERICHTEN = {
    "Fake „Sicher bezahlen“/„Direkt kaufen“": r"sicher(es)? bezahlen|direkt kaufen|sichere zahlung",
    "Phishing-Link/Fake-Seite": r"phishing|fake[- ]?(link|seite)|\blink\b.{0,40}(geschickt|bekommen|erhalten|klick)",
    "PayPal Freunde & Familie": r"freunde (und|&) familie|paypal[- ]?freunde|\bf ?& ?f\b",
    "Vorkasse/Überweisung": r"vorkasse|überwiesen|überweisung|anzahlung",
    "Kurier/Spedition/Ausland": r"kurier|spedit|im ausland|aus dem ausland",
    "Ware kam nicht oder falsch": r"nie angekommen|nicht (verschickt|versendet|angekommen)|leeres? paket|ziegel|\bsteine\b",
    "Käuferschutz/Rückbuchung": r"käuferschutz|rückbuchung|konfliktfall|chargeback|geld zurückgeholt",
    "Gehacktes Konto": r"gehackt|account übernommen|konto übernommen|fremde[nr]? (account|konto)",
    "Dreiecksbetrug": r"dreiecksbetrug|dreieck",
    "Ausweis/Identität": r"ausweis|perso\b|identität",
    "Kontakt außerhalb (WhatsApp, Mail)": r"whats ?app|telegram|per (e-)?mail (weiter|kontakt)",
    "Code-Weitergabe": r"(sms|bestätigungs|sicherheits)[- ]?code|code (geschickt|weitergegeben)",
    "Fake-Zahlungsbeleg/Screenshot": r"screenshot|zahlungsbeleg|fake[- ]?(beleg|bestätigung)",
    "Wohnung/Ferienwohnung": r"wohnung|kaution|vermieter",
    "Tiere": r"welpe|hundebaby|kätzchen|kitten",
}

# Vor einem Zitat: Hinweis, dass der Beitragende etwas ERHALTEN hat („bekam folgende Nachricht:“)
RECEIVED_CUE = re.compile(
    r"(nachricht|mail|sms|whatsapp|chat|schrieb|schreibt|geschrieben|bekam|bekomme|bekommen|erhalten|"
    r"erhielt|folgende|folgendes|anfrage|antwort|antwortete|meinte|kam|wortlaut|text|zitat)", re.IGNORECASE)
CUE_WINDOW = 160  # so viele Zeichen vor dem Zitat werden nach einem Hinweis durchsucht
FIRST_PERSON = re.compile(r"\b(ich|mir|mich|mein|meine|meinen|wir|uns|unser)\b", re.IGNORECASE)
# Eigenes Erlebnis statt Ratschlag („ich habe … bekommen“, „hat mich angeschrieben“)
EXPERIENCE = re.compile(
    r"\b(ich (habe|hab|hatte|bekam|bekomme|erhielt|wurde|bin|war)|(hab|habe|hatte|wurde|bin) ich|"
    r"mir (ist|wurde|hat)|bei mir|(hat|haben|hatte) (mich|mir)|mich angeschrieben)\b", re.IGNORECASE)
SCAM_TALK = re.compile(
    r"betrug|betrüg|scam|fake|masche|phishing|abzock|reingefallen|link|paypal|überwies|vorkasse|kurier|"
    r"spedition|sicher bezahlen|direkt kaufen|gehackt|anzeige erstattet|polizei|geld weg|nie angekommen",
    re.IGNORECASE)
MIN_REPORT_CHARS = 150
MIN_QUOTE_CHARS, MIN_QUOTE_WORDS = 25, 4
MAX_QUOTE_CHARS = 1500


@dataclass
class Post:
    text: str                        # Beitragstext; fremde Zitate stehen als „…“ im Text
    external: list[tuple[str, str]] = field(default_factory=list)  # (Text davor, eingefügtes Zitat)
    date: str = ""


@dataclass
class ThreadPage:
    title: str
    posts: list[Post]
    page_urls: list[str]              # URLs aller Seiten (1 … n)


@dataclass
class Report:
    text: str
    quelle: str
    titel: str
    datum: str
    forum: str
    maschen: list[str]       # beschriebene Maschen (MASCHEN_IN_BERICHTEN)
    warnsignale: list[str]   # Lexikon-Treffer im Text


# --------------------------------------------------------------------------- Forensoftware erkennen

def _text(el: Tag) -> str:
    """Sichtbarer Text mit Zeilenumbrüchen an Absätzen/<br>, ohne Mehrfach-Leerraum."""
    for br in el.find_all("br"):
        br.replace_with("\n")
    raw = el.get_text(" ", strip=False)
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in raw.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _post_from_body(body: Tag, date: str, is_internal: Callable[[Tag], bool],
                    quote_selector: str, content_selector: str | None) -> Post:
    """Zitate anderer Mitglieder entfernen, eingefügte fremde Texte als (Kontext, Zitat) merken."""
    for junk in body.select("script, style, .bbCodeBlock-expandLink"):
        junk.decompose()
    markers: dict[str, str] = {}
    for i, quote in enumerate(body.select(quote_selector)):
        if quote.decomposed or body not in quote.parents:  # steckte in einem schon entfernten/ersetzten Zitat
            continue
        if is_internal(quote):
            quote.decompose()
            continue
        content = quote.select_one(content_selector) if content_selector else None
        marker = f"⟦Z{i}⟧"
        markers[marker] = _text(content or quote)
        quote.replace_with(f"\n{marker}\n")
    text = _text(body)
    external = []
    for marker, quoted in markers.items():
        pos = text.find(marker)
        external.append((text[max(0, pos - CUE_WINDOW):pos] if pos >= 0 else "", quoted))
        text = text.replace(marker, f"„{quoted}“")
    return Post(text=text, external=external, date=date)


def _date_of(el: Tag) -> str:
    stamp = el.select_one("time[datetime]")
    if stamp:
        return stamp["datetime"][:10]
    woltlab = el.find(attrs={"date": True})
    return str(woltlab["date"])[:10] if woltlab else ""


def _xenforo(soup: BeautifulSoup, url: str) -> ThreadPage | None:
    articles = soup.select("article.message")
    if not articles or not soup.select_one(".bbWrapper"):
        return None
    posts = []
    for art in articles:
        body = art.select_one(".message-body .bbWrapper") or art.select_one(".bbWrapper")
        if body:
            posts.append(_post_from_body(
                body, _date_of(art),
                # Zitat eines Mitglieds: Kopfzeile „Name schrieb:“ bzw. data-quote-Attribut
                is_internal=lambda q: bool(q.get("data-quote") or q.select_one(".bbCodeBlock-title")),
                quote_selector="blockquote.bbCodeBlock--quote, div.bbCodeBlock--code",
                content_selector=".bbCodeBlock-content, .bbCodeBlock-expandContent, pre"))
    title = soup.select_one("h1.p-title-value")
    pages = [int(a.get_text(strip=True)) for a in soup.select(".pageNav-page a")
             if a.get_text(strip=True).isdigit()]
    last = max(pages, default=1)
    page_urls = [url]
    if last > 1:
        anchor = next(a for a in soup.select(".pageNav-page a") if a.get_text(strip=True) == str(last))
        template = urljoin(url, anchor["href"])
        page_urls = [url] + [template.replace(f"page-{last}", f"page-{n}") for n in range(2, last + 1)]
    return ThreadPage(_text(title) if title else "", posts, page_urls)


def _woltlab(soup: BeautifulSoup, url: str) -> ThreadPage | None:
    articles = soup.select("article.wbbPost, .wbbPost")
    if not articles:
        return None
    posts = []
    for art in articles:
        body = art.select_one(".messageText")
        if body:
            posts.append(_post_from_body(
                body, _date_of(art),
                # Zitat eines Mitglieds: verweist per cite auf einen Beitrag bzw. „Zitat von …“
                is_internal=lambda q: bool(q.get("cite")) or "Zitat von" in (
                    q.select_one(".quoteBoxTitle").get_text() if q.select_one(".quoteBoxTitle") else ""),
                quote_selector="blockquote.quoteBox, .codeBox",
                content_selector=".quoteBoxContent, pre"))
    title = soup.select_one("h1.contentTitle, h1")
    pager = soup.select_one("woltlab-core-pagination[count], .pagination[data-pages]")
    last = int(pager.get("count") or pager.get("data-pages") or 1) if pager else 1
    base = urlunparse(urlparse(url)._replace(query="", fragment=""))
    page_urls = [url] + [f"{base}?pageNo={n}" for n in range(2, last + 1)]
    return ThreadPage(_text(title) if title else "", posts, page_urls)


def _vbulletin_archive(soup: BeautifulSoup, url: str) -> ThreadPage | None:
    articles = soup.select("div.post")
    if not articles or not soup.select_one(".posttext"):
        return None
    posts = [Post(text=_text(a.select_one(".posttext")), date=_vb_date(a)) for a in articles
             if a.select_one(".posttext")]
    nav = soup.select_one("#navbar")
    title = nav.get_text(" ", strip=True).split(">")[-1].strip() if nav else ""
    numbers = [int(a.get_text()) for a in soup.select("#pagenumbers a") if a.get_text().isdigit()]
    last = max(numbers, default=1)
    base = re.sub(r"(-p-\d+)?\.html$", "", url.split("?")[0])
    page_urls = [url] + [f"{base}-p-{n}.html" for n in range(2, last + 1)]
    return ThreadPage(title, posts, page_urls)


def _vb_date(post: Tag) -> str:
    raw = post.select_one(".date")
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", raw.get_text() if raw else "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


ENGINES = (_xenforo, _woltlab, _vbulletin_archive)


def parse_thread(html: str, url: str) -> ThreadPage | None:
    """Thread-Seite einer unterstützten Forensoftware auswerten (None = unbekanntes Format)."""
    soup = BeautifulSoup(html, "html.parser")
    for engine in ENGINES:
        page = engine(soup, url)
        if page is not None:
            return page
    return None


# --------------------------------------------------------------------------- Beiträge auswerten

def _quote_ok(text: str) -> bool:
    words = [t for t in text.split() if re.search(r"[^\W\d_]{2,}", t)]
    return (MIN_QUOTE_CHARS <= len(text) <= MAX_QUOTE_CHARS and len(words) >= MIN_QUOTE_WORDS
            and looks_german(text))


def received_messages(post: Post) -> list[str]:
    """Texte, die der Beitragende nach eigener Aussage erhalten hat (eingefügt oder in „…“)."""
    found = [_clean(q) for context, q in post.external if RECEIVED_CUE.search(context) and _quote_ok(_clean(q))]
    for m in FORUM_QUOTE.finditer(post.text):
        quoted = _clean(m.group(1))
        if quoted in found or not _quote_ok(quoted):
            continue
        if RECEIVED_CUE.search(post.text[max(0, m.start() - CUE_WINDOW):m.start()]):
            found.append(quoted)
    return found


def is_experience(post: Post) -> bool:
    """Erzählt jemand von eigenem (versuchtem) Betrug – statt nur Ratschläge zu geben?"""
    return (len(post.text) >= MIN_REPORT_CHARS and len(FIRST_PERSON.findall(post.text)) >= 2
            and bool(EXPERIENCE.search(post.text)) and bool(SCAM_TALK.search(post.text)))


def _signal_codes(text: str, lexicon: str) -> list[str]:
    return [s.code for s in match_phrases(text, lexicon)] + [s.code for s in analyze_contacts(text).signals]


def described_scams(text: str) -> list[str]:
    return [name for name, pattern in MASCHEN_IN_BERICHTEN.items() if re.search(pattern, text, re.IGNORECASE)]


def _key(text: str) -> str:
    return re.sub(r"\W+", "", text.lower())


def _reviewed_rows(path) -> dict[str, dict]:
    """Von Menschen geprüfte Zeilen einer früheren Prüfliste (nur neues Format mit „vorschlag“).
    Auch nach Speichern in Excel lesbar (Semikolon, Windows-Zeichensatz)."""
    from scamguard.data.discovery import _csv_options

    if not path.exists():
        return {}
    options = _csv_options(path)
    with path.open(encoding=options["encoding"], newline="") as f:
        reader = csv.DictReader(f, delimiter=options.get("sep", ","))
        if "vorschlag" not in (reader.fieldnames or []):
            return {}  # alte Datei mit automatisch gesetzten Labels – nicht übernehmen
        return {_key(r["nachricht"]): {c: r.get(c) or "" for c in REVIEW_COLUMNS}
                for r in reader if (r.get("betrug") or "").strip()}


# --------------------------------------------------------------------------- Ablauf

def load_seeds(path: str = SEED_FILE) -> tuple[list[str], int]:
    with resolve_path(path).open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return [str(u) for u in raw.get("threads") or []], int(raw.get("max_seiten", DEFAULT_MAX_PAGES))


def _pages_to_read(page_urls: list[str], max_pages: int) -> list[str]:
    """Erste Seite (Ausgangsbericht) plus die neuesten Seiten (aktuelle Maschen)."""
    if len(page_urls) <= max_pages:
        return page_urls
    return page_urls[:1] + page_urls[-(max_pages - 1):] if max_pages > 1 else page_urls[:1]


def crawl(urls: list[str], fetch: Fetcher, max_pages: int,
          log: Callable[[str], None] = print) -> Iterator[tuple[str, str, Post]]:
    """(Thread-URL, Titel, Beitrag) für alle Seiten aller Threads."""
    for url in urls:
        html = fetch.get(url, cache=False)  # erste Seite ändert sich (Seitenzahl wächst)
        page = parse_thread(html, url) if html else None
        if page is None:
            if html:
                log(f"  unbekanntes Forenformat, übersprungen: {url}")
            continue
        todo = _pages_to_read(page.page_urls, max_pages)
        log(f"  {page.title[:70]} – {len(todo)} von {len(page.page_urls)} Seiten")
        for n, page_url in enumerate(todo):
            if n:
                html = fetch.get(page_url, cache=page_url != page.page_urls[-1])  # letzte Seite wächst noch
                current = parse_thread(html, page_url) if html else None
                if current is None:
                    continue
            else:
                current = page
            for post in current.posts:
                yield url, page.title, post


def collect(urls: list[str] | None = None, max_pages: int | None = None, fetch: Fetcher | None = None,
            quotes_file: str | None = QUOTES_FILE, reports_file: str | None = REPORTS_FILE,
            log: Callable[[str], None] = print) -> tuple[list[dict], list[Report]]:
    """Threads lesen → zitierte Nachrichten (Prüfliste) + Erfahrungsberichte (JSONL, Auswertung)."""
    seeds, seed_pages = load_seeds() if urls is None else (urls, DEFAULT_MAX_PAGES)
    fetch = fetch or Fetcher(log=log)
    lexicon = str(resolve_path(load_config()["paths"]["lexicon"]))
    rows: list[dict] = []
    reports: list[Report] = []
    seen_quotes: set[str] = set()
    seen_reports: set[str] = set()
    log(f"Quelle foren … ({len(seeds)} Threads aus {SEED_FILE})")
    for thread_url, title, post in crawl(seeds, fetch, max_pages or seed_pages, log):
        forum = urlparse(thread_url).netloc.removeprefix("www.")
        for message in received_messages(post):
            message = redact(message)
            if _key(message) in seen_quotes:
                continue
            seen_quotes.add(_key(message))
            signals = _signal_codes(message, lexicon)
            rows.append({"nachricht": message, "betrug": "", "vorschlag": "ja" if signals else "?",
                         "warnsignale": " ".join(signals), "quelle": thread_url, "quelle_titel": title,
                         "datum": post.date})
        if is_experience(post):
            text = redact(post.text)
            if _key(text)[:300] not in seen_reports:
                seen_reports.add(_key(text)[:300])
                reports.append(Report(text, thread_url, title, post.date, forum, described_scams(text),
                                      _signal_codes(text, lexicon)))

    if quotes_file:
        path = resolve_path(quotes_file)
        reviewed = _reviewed_rows(path)
        for row in rows:
            row["betrug"] = reviewed.pop(_key(row["nachricht"]), {}).get("betrug", "")
        rows += reviewed.values()  # Geprüftes nie verwerfen – auch wenn der Thread nicht mehr erreichbar ist
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=REVIEW_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        done = sum(bool(r["betrug"]) for r in rows)
        log(f"Gespeichert: {path} ({len(rows)} zitierte Nachrichten, {done} davon geprüft – "
            "Spalte „betrug“ mit ja/nein füllen, nur geprüfte Zeilen zählen fürs Training)")
    if reports_file:
        path = resolve_path(reports_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for r in reports:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
        log(f"Gespeichert: {path} ({len(reports)} Erfahrungsberichte, nur zur Auswertung)")
    log(f"  {fetch.requests} Anfragen")
    return rows, reports


def summarize(reports: list[Report], top: int = 12) -> list[tuple[str, int]]:
    """Welche Maschen beschreiben Betroffene am häufigsten? (für die Auswertung)"""
    return Counter(code for r in reports for code in set(r.maschen)).most_common(top)
