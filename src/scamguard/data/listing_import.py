"""Ein Inserat aus Dateien lesen, die selbst gespeichert wurden – kein Scraping.

Unterstützt:
  - Screenshots (.png/.jpg/.webp) → Texterkennung (ocr.py); Bilder mit wenig Text gelten als Produktfoto
  - gespeicherte Seite (.html/.htm, Firefox/Chrome „Seite speichern unter …“)
  - PDF („Drucken → Als PDF speichern“)
  - Text (.txt oder eingefügt, z. B. Strg+A/Strg+C auf der Anzeige)

Aus dem Text werden Titel, Preis, Ort, Kategorie, Kontoalter und Beschreibung herausgesucht – nach dem
Aufbau einer Kleinanzeigen-Seite (Titel steht über dem Preis, Beschreibung unter „Beschreibung“).
Der Name des Anbieters wird bewusst nicht übernommen (Datensparsamkeit, für die Erkennung unnötig).

Genutzt vom Tab „Inserat hochladen“ im Frontend und vom Ordner data/datensatz_fuellen_inserate/.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from scamguard.data import ocr
from scamguard.data.loaders import IMAGE_SUFFIXES, normalize_category, parse_price
from scamguard.schema import Listing, badge_key, legal_form_of, seller_type_of

HTML_SUFFIXES = {".html", ".htm"}
PDF_SUFFIXES = {".pdf"}
TEXT_SUFFIXES = {".txt"}
SUPPORTED_SUFFIXES = IMAGE_SUFFIXES | HTML_SUFFIXES | PDF_SUFFIXES | TEXT_SUFFIXES
# Beim Einstufen angelegte Inserats-Ordner enthalten die Felder als JSON (labels.save_to_dataset_folder)
LISTING_JSON = "inserat.json"

SCREENSHOT_MIN_CHARS = 80  # ab so viel erkanntem Text ist ein Bild ein Screenshot, sonst ein Produktfoto

PRICE_ROW = re.compile(r"^(?:ab\s+)?\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?\s*(?:€|eur)(?:\s*vb)?$|^vb$|^zu verschenken$")
POSTCODE = re.compile(r"\b\d{5}\s+(?!(?i:km|kilometer|euro|eur|stück|stk|watt|mah)\b)[A-ZÄÖÜ][^\n•·|>›]*")
ACTIVE_SINCE = re.compile(r"aktiv seit\s+(\d{1,2})\.(\d{1,2})\.(\d{4})", re.IGNORECASE)
BADGES = re.compile(r"^(?:(?:reserviert|gelöscht|top)\s*[•·]?\s*)+", re.IGNORECASE)
COUNTER = re.compile(r"^\d+\s*/\s*\d+$")  # „1/12“ (Bildzähler)

DESCRIPTION_START = "beschreibung"
# Hier endet die Beschreibung …
DESCRIPTION_END = ("anzeige melden", "nachricht schreiben", "rechtliche angaben", "sicherheitshinweise",
                   "anzeigen-id", "anzeigennr", "privater nutzer", "gewerblicher nutzer", "aktiv seit")
# … und ab hier folgen nur noch andere Inserate
PAGE_END = ("andere anzeigen des anbieters", "alle anzeigen dieses anbieters",
            "das könnte dich auch interessieren", "ähnliche anzeigen", "weitere anzeigen")
SELLER_TYPES = ("privater nutzer", "gewerblicher nutzer")
AD_COUNT = re.compile(r"^(\d+)(?:\s+anzeigen\b.*)?$", re.IGNORECASE)  # „7 Anzeigen online“ bzw. „248“ + „Anzeigen“
# Chat-Screenshots in einem Inserats-Ordner: Dateiname beginnt mit „chat“ (chat_1.png, Chat 2.jpg …)
CHAT_FILE = re.compile(r"^chat", re.IGNORECASE)
BOILERPLATE = {
    "kleinanzeigen", "was suchst du?", "alle kategorien", "plz oder ort", "registrieren", "einloggen",
    "oder", "registrieren oder einloggen", "folgen", "anrufen", "merken", "teilen", "anzeige teilen",
    "zur merkliste hinzufügen", "nachricht senden", "karte", "satellit", "karte satellit", "standort",
    "details", "ausstattung", "mehr anzeigen", "weniger anzeigen", "hallo!", "top",
    "zum inhalt springen", "startseite", "inserieren", "meins", "anzeige aufgeben",
}


_BOILERPLATE_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(map(re.escape, sorted(BOILERPLATE, key=len, reverse=True))) + r")(?!\w)")


def _content_words(key: str) -> list[str]:
    """Wörter (≥ 3 Buchstaben), die nach Entfernen von Seitenelementen („Was suchst du?“ …) übrig bleiben."""
    return re.findall(r"[^\W\d_]{3,}", _BOILERPLATE_RE.sub(" ", key))


def _key(row: str) -> str:
    """Zeile zum Vergleichen: klein, ohne Icons am Anfang („Q “, „& “, „• “ aus der Texterkennung)."""
    s = row.strip().lower()
    return re.sub(r"^(?:[^\w\s€]+\s*|\w\s+(?=\w\w))+", "", s).strip()


def _days_since(day: str, month: str, year: str) -> int | None:
    try:
        return max(0, (datetime.now(timezone.utc).date() - date(int(year), int(month), int(day))).days)
    except ValueError:
        return None


def _is_breadcrumb(key: str) -> bool:
    return len(re.findall(r"[›>»]", key)) >= 2 or ("kleinanzeigen" in key and bool(re.search(r"[›>»]", key)))


# --------------------------------------------------------------------------- Text → Felder

def seller_info(texts: list[str]) -> dict[str, Any]:
    """Anbietertyp, Bewertungs-Abzeichen, Zahl der Anzeigen und Rechtsform aus Texten der Anbieterbox
    (eine Zeile bzw. ein Textelement je Eintrag). Der Name selbst wird nicht übernommen."""
    info: dict[str, Any] = {}
    texts = [re.sub(r"\s+", " ", t).strip() for t in texts if t and t.strip()]
    lowered = [t.lower() for t in texts]
    seller_type = next((seller_type_of(t) for t in lowered if t.startswith(SELLER_TYPES)), None)
    if seller_type:
        info["seller_type"] = seller_type
    badges = [b for t in lowered if len(t) < 40 and (b := badge_key(t))]
    if badges:
        info["seller_badges"] = list(dict.fromkeys(badges))
    for i, t in enumerate(lowered):
        m = AD_COUNT.match(t)
        nxt = lowered[i + 1] if i + 1 < len(lowered) else ""
        if m and ("anzeigen" in t or nxt.startswith("anzeigen")):
            info["seller_num_ads"] = int(m.group(1))
            break
    if form := next((f for t in texts if len(t) <= 80 and (f := legal_form_of(t))), None):
        info["seller_legal_form"] = form
    return info


def parse_rows(rows: list[str], side_rows: Iterable[str] = ()) -> dict[str, Any]:
    """Felder eines Inserats aus Textzeilen (Hauptspalte; Seitenspalte nur fürs Kontoalter)."""
    rows = [r.strip() for r in rows if r and r.strip()]
    keys = [_key(r) for r in rows]
    fields: dict[str, Any] = {}

    price_idx = next((i for i, k in enumerate(keys) if PRICE_ROW.match(k)), None)
    if price_idx is not None:
        fields["price"] = parse_price(keys[price_idx])
    title_candidates = range(price_idx - 1, -1, -1) if price_idx is not None else range(len(rows))
    title_idx = next((i for i in title_candidates if _content_words(keys[i])
                      and not _is_breadcrumb(keys[i]) and not COUNTER.match(keys[i])), None)
    if title_idx is not None:
        fields["title"] = BADGES.sub("", rows[title_idx]).strip()

    crumb = next((r for r, k in zip(rows, keys) if _is_breadcrumb(k)), None)
    fields["category"] = normalize_category(crumb or fields.get("title"))

    location = next((m.group(0) for r in rows if (m := POSTCODE.search(r))), None)
    if location:
        fields["location"] = location.strip(" ,-")  # ab der PLZ – Straße und Hausnummer bleiben weg
    for r in [*rows, *side_rows]:
        if m := ACTIVE_SINCE.search(r):
            fields["seller_account_age_days"] = _days_since(*m.groups())
            break
    name_rows = [rows[i - 1] for i, k in enumerate(keys) if i and k.startswith(SELLER_TYPES)]
    info = seller_info([*rows, *side_rows])
    info.pop("seller_legal_form", None)  # Beschreibung kann Firmen nennen („Volkswagen AG“) – nur Namenszeilen
    if form := next((f for r in [*name_rows, *side_rows] if len(r) <= 80 and (f := legal_form_of(r))), None):
        info["seller_legal_form"] = form
    fields.update(info)

    if DESCRIPTION_START in keys:
        start = keys.index(DESCRIPTION_START) + 1
        body = []
        for r, k in zip(rows[start:], keys[start:]):
            if k.startswith(DESCRIPTION_END + PAGE_END):
                break
            body.append(r)
    else:  # kein Abschnitt „Beschreibung“ (z. B. nur oberer Teil fotografiert): alles Übrige
        skip = {price_idx, title_idx}
        skip |= {i - 1 for i, k in enumerate(keys) if k.startswith(SELLER_TYPES)}  # Zeile davor = Name
        body = []
        for i, (r, k) in enumerate(zip(rows, keys)):
            if k.startswith(PAGE_END):
                break
            if (i in skip or len(_content_words(k)) < 2 or _is_breadcrumb(k)
                    or k.startswith(DESCRIPTION_END) or POSTCODE.search(r)):
                continue
            body.append(r)
    fields["description"] = "\n".join(body).strip()
    return fields


def listing_from_html(html: str) -> dict[str, Any]:
    """Gespeicherte Kleinanzeigen-Seite (gleiche Stellen wie die Browser-Extension);
    andere Seiten werden wie eingefügter Text gelesen."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("#viewad-title")
    desc_el = soup.select_one("#viewad-description-text")
    if title_el is None and desc_el is None:
        for tag in soup(["script", "style", "noscript", "svg", "template"]):
            tag.decompose()
        return parse_rows(soup.get_text("\n").splitlines())

    def text(selector: str) -> str:
        el = soup.select_one(selector)
        return el.get_text(" ", strip=True) if el else ""

    fields: dict[str, Any] = {}
    if title_el is not None:
        own = " ".join(s.strip() for s in title_el.find_all(string=True, recursive=False)).strip()
        fields["title"] = own or BADGES.sub("", title_el.get_text(" ", strip=True))
    if desc_el is not None:
        for br in desc_el.find_all("br"):
            br.replace_with("\n")
        lines = (line.strip() for line in desc_el.get_text().splitlines())
        fields["description"] = "\n".join(line for line in lines if line)
    fields["price"] = parse_price(text("#viewad-price"))
    # schema.org-Auszeichnung: im alten und im neuen Seitenlayout (seit 10/2026) gleich; sonst alte Klasse
    crumb_els = (soup.select("#vap-brdcrmb [itemprop='itemListElement'] [itemprop='name']")
                 or soup.select("#vap-brdcrmb .breadcrump-link"))
    crumbs = [e.get_text(strip=True) for e in crumb_els]
    fields["category"] = normalize_category(" > ".join(crumbs) or fields.get("title"))
    if location := text("#viewad-locality"):
        fields["location"] = location
    if m := ACTIVE_SINCE.search(text("#viewad-contact")):
        fields["seller_account_age_days"] = _days_since(*m.groups())
    profile = soup.select_one("#viewad-profile-box") or soup.select_one("#viewad-contact")
    fields.update(seller_info(list(profile.stripped_strings) if profile else []))
    if soup.select_one("#viewad-imprint-section") or soup.select_one("#viewad-bizteaser"):
        fields["seller_type"] = "gewerblich"  # Impressum bzw. Firmenprofil
    canonical = soup.select_one("link[rel=canonical]") or soup.select_one("meta[property='og:url']")
    url = (canonical.get("href") or canonical.get("content")) if canonical else None
    if url and "/s-anzeige/" in url:
        fields["url"] = url
    return fields


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


# --------------------------------------------------------------------------- Dateien → Inserat

@dataclass
class ImportResult:
    listing: Listing
    photos: list[tuple[str, bytes]] = field(default_factory=list)  # (Dateiname, Inhalt) → Bildmodell
    notes: list[str] = field(default_factory=list)                 # was mit jeder Datei passiert ist

    @property
    def empty(self) -> bool:
        return not (self.listing.full_text or self.photos)


def _screenshot_rows(name: str, data: bytes, notes: list[str]) -> tuple[list[str], list[str]] | None:
    """(Haupt-, Seitenspalte) eines Screenshots; None = Produktfoto bzw. nicht lesbar."""
    try:
        lines, width = ocr.recognize(data)
    except ocr.OcrUnavailable:
        notes.append(f"{name}: als Produktfoto übernommen ({ocr.UNAVAILABLE_HINT})")
        return None
    except ValueError as exc:
        notes.append(f"{name}: übersprungen – {exc}")
        return None
    if sum(len(ln.text) for ln in lines) < SCREENSHOT_MIN_CHARS:
        notes.append(f"{name}: Produktfoto (kaum Text)")
        return None
    main, side = ocr.layout_rows(lines, width)
    notes.append(f"{name}: Screenshot – {len(main) + len(side)} Textzeilen gelesen")
    return main, side


def import_listing(files: list[tuple[str, bytes]], text: str = "",
                   chat_files: list[tuple[str, bytes]] = (), chat_text: str = "") -> ImportResult:
    """Ein Inserat aus hochgeladenen Dateien und/oder eingefügtem Text (plus optional Chat)."""
    rows: list[str] = []
    side: list[str] = []
    page: dict[str, Any] = {}  # Felder aus einer gespeicherten Kleinanzeigen-Seite (am genauesten)
    photos: list[tuple[str, bytes]] = []
    notes: list[str] = []

    for name, data in files:
        suffix = Path(name).suffix.lower()
        if suffix in HTML_SUFFIXES:
            fields = listing_from_html(_decode(data))
            page = {**fields, **page}  # erste Seite gewinnt
            notes.append(f"{name}: gespeicherte Seite gelesen")
        elif suffix in PDF_SUFFIXES:
            pdf_text = _pdf_text(data)
            if pdf_text.strip():
                rows.extend(pdf_text.splitlines())
                notes.append(f"{name}: PDF-Text gelesen")
            else:
                notes.append(f"{name}: PDF enthält keinen Text (eingescannt?) – bitte als Screenshot hochladen")
        elif suffix in TEXT_SUFFIXES:
            rows.extend(_decode(data).splitlines())
            notes.append(f"{name}: Text gelesen")
        elif suffix in IMAGE_SUFFIXES:
            shot = _screenshot_rows(name, data, notes)
            if shot is None:
                photos.append((name, data))
            else:  # mehrere Screenshots (gescrollt): überlappende Zeilen nur einmal
                rows.extend(r for r in shot[0] if r not in rows)
                side.extend(r for r in shot[1] if r not in side)
        else:
            notes.append(f"{name}: Dateityp wird nicht unterstützt")
    rows.extend(text.splitlines())

    fields = parse_rows(rows, side) if rows else {}
    fields.update({k: v for k, v in page.items() if v not in (None, "")})

    messages = []
    for name, data in chat_files:
        shot = _screenshot_rows(name, data, notes) if Path(name).suffix.lower() in IMAGE_SUFFIXES else None
        chat = "\n".join(shot[0] + shot[1]) if shot else _decode(data)
        if chat.strip():
            messages.append(chat.strip())
    if chat_text.strip():
        messages.append(chat_text.strip())

    listing = Listing.from_dict({**fields, "messages": messages})
    return ImportResult(listing, photos, notes)


def _visible(path: Path) -> bool:
    return not path.name.startswith(("_", "."))


def _item_files(folder: Path) -> list[Path]:
    return sorted(f for f in folder.rglob("*") if f.is_file() and _visible(f)
                  and (f.suffix.lower() in SUPPORTED_SUFFIXES or f.name == LISTING_JSON))


def listing_items(folder: Path) -> list[list[Path]]:
    """Inserate in einem Label-Ordner (betrug/, serioes/): je Datei, je Unterordner – oder je
    Inserats-Ordner in einem Kategorie-Ordner (betrug/auto/<inserat>/, beim Einstufen angelegt)."""
    items: list[list[Path]] = []
    for entry in sorted(filter(_visible, folder.iterdir())):
        if entry.is_file():
            if entry.suffix.lower() in SUPPORTED_SUFFIXES:
                items.append([entry])
            continue
        nested = [d for d in sorted(entry.iterdir()) if d.is_dir() and _visible(d) and (d / LISTING_JSON).is_file()]
        if nested and not (entry / LISTING_JSON).is_file():
            items.extend(files for d in nested if (files := _item_files(d)))
        elif files := _item_files(entry):
            items.append(files)
    return items


def import_paths(paths: list[Path]) -> ImportResult:
    """Wie import_listing, für Dateien aus dem Ordner data/datensatz_fuellen_inserate/.
    Produktfotos bleiben, wo sie liegen (image_paths zeigt auf die Originaldateien).
    Liegt eine inserat.json bei, gelten deren Felder und alle Bilder als Fotos – ohne Texterkennung."""
    manifest = next((p for p in paths if p.name == LISTING_JSON), None)
    if manifest is not None:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        fields = {k: v for k, v in data.items() if k not in ("label", "bilder", "image_paths", "source")}
        listing = Listing.from_dict(fields)
        listing.image_paths = [str(p) for p in paths if p.suffix.lower() in IMAGE_SUFFIXES]
        return ImportResult(listing, [], [f"{manifest.parent.name}: beim Einstufen abgelegt"])
    chats = [p for p in paths if CHAT_FILE.match(p.name)]
    result = import_listing([(str(p), p.read_bytes()) for p in paths if p not in chats],
                            chat_files=[(str(p), p.read_bytes()) for p in chats])
    result.listing.image_paths = [name for name, _ in result.photos]
    return result
