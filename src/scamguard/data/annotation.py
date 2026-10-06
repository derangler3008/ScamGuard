"""Annotation-Workspace: Aufgaben, Einstufungen mehrerer Personen, Konsens, Qualität und Export.

Arbeitsordner (Umgebungsvariable SCAMGUARD_EINSTUFUNG_ORDNER, sonst config.yaml → einstufung.ordner,
Standard data/einstufung/). Jede Person schreibt nur ihre eigenen Dateien; gelesen werden die aller
Personen. Darum geht auch ein gemeinsamer Ordner (z. B. DHBW-OneDrive), ohne dass sich Änderungen
gegenseitig überschreiben:

  aufgaben_<person>.jsonl        was eingestuft werden soll (Kontaktdaten beim Hinzufügen anonymisiert)
  einstufungen_<person>.jsonl    Einstufungen dieser Person
  entscheidungen_<person>.jsonl  Entscheidungen bei Konflikten

Es wird nur angehängt, nie überschrieben: Eine Korrektur ist eine neue Zeile, es zählt die neueste.

Eine Einstufung hat drei Ebenen (Kategorien: data/einstufung/kategorien.yaml):
  Text    Urteil (Betrug/seriös/unklar), Sicherheit, Masche, Rolle, Sprache
  Satz    welcher Satz welches Warnsignal enthält
  Bild    Bildart und ob das Bild selbst verdächtig ist

Ein festgelegter Anteil der Aufgaben (einstufung.doppelt_anteil) wird von zwei Personen unabhängig
eingestuft. Welche das sind, ergibt sich aus der Aufgaben-ID – alle Rechner kommen ohne Absprache
auf dieselbe Auswahl. Daraus entsteht die Übereinstimmung (agreement.py).

Export (lokal nach data/raw/, nicht in den gemeinsamen Ordner):
  einstufung_konsens.jsonl   Texte mit Konsens-Label, Masche und satzgenauen Warnsignalen
                             → Datensatz „einstufungen“ (registry.py) für data build, retrain, llm-daten
  einstufung_saetze.jsonl    jeder Satz mit seinem Warnsignal (oder keinem) – Lexikon-Prüfung, Satzmodelle
  einstufung_bilder.jsonl    jedes Bild mit Bildart und „verdächtig“ – Bildmodell, Prüfung von CLIP
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
from collections import Counter, defaultdict
from collections.abc import Hashable, Iterable
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from scamguard.config import PROJECT_ROOT, load_config, resolve_path
from scamguard.data import agreement
from scamguard.data.build import listing_fingerprint
from scamguard.data.codebook import LABEL_OF_URTEIL, URTEILE, Codebook, load_codebook
from scamguard.data.redact import redact
from scamguard.schema import Listing

# Persönlicher Pfad zum gemeinsamen Ordner, ohne config.yaml (liegt im Git) zu ändern:
#   SCAMGUARD_EINSTUFUNG_ORDNER=~/OneDrive/ScamGuard-Einstufung scamguard ui
FOLDER_ENV = "SCAMGUARD_EINSTUFUNG_ORDNER"
EXPORT_DIR = "data/raw"
EXPORT_FILES = {"konsens": "einstufung_konsens.jsonl", "saetze": "einstufung_saetze.jsonl",
                "bilder": "einstufung_bilder.jsonl"}
KINDS = ("aufgaben", "einstufungen", "entscheidungen")
SHARED_FILE_RE = re.compile(rf"({'|'.join(KINDS)})_([a-z0-9]+)\.jsonl")
MAX_SAETZE = 80
SCHNELL_S = 3.0  # schneller als das bei längeren Texten → Hinweis „sehr schnell eingestuft“

# --------------------------------------------------------------------------- Sätze

# Nach diesen Abkürzungen endet kein Satz („ca. 50 Euro“, „Tel. 0151 …“); einzelne Buchstaben
# („z. B.“, „u. a.“) und Ordnungszahlen („am 3. Oktober“) werden zusätzlich erkannt.
ABKUERZUNGEN = frozenset([
    "bzw", "ca", "nr", "tel", "usw", "etc", "inkl", "zzgl", "ggf", "evtl", "vgl", "mio", "tsd", "std",
    "min", "dr", "hr", "fr", "str", "mfg", "lg", "vg", "bspw", "max", "mind", "abs", "geb", "gem",
])
_CANDIDATE = re.compile(r"([.!?…]+)[\"“”')\]]*\s+")


def _is_boundary(text: str, m: re.Match) -> bool:
    following = text[m.end():m.end() + 1]
    if not (following.isupper() or following.isdigit() or following in "\"„“(["):
        return False
    if set(m.group(1)) & set("!?…"):
        return True
    word = re.search(r"(\w+)$", text[:m.start()])
    if not word:
        return True
    w = word.group(1)
    return not (len(w) == 1 or w.lower() in ABKUERZUNGEN or (w.isdigit() and len(w) <= 2))


def split_sentences(text: str) -> list[str]:
    """Deutscher Text → Sätze. Zeilenumbrüche trennen immer (Chats bestehen oft aus Zeilen)."""
    sentences = []
    for line in (text or "").splitlines():
        line, start = line.strip(), 0
        for m in _CANDIDATE.finditer(line):
            if _is_boundary(line, m):
                sentences.append(line[start:m.end()].strip())
                start = m.end()
        if line[start:].strip():
            sentences.append(line[start:].strip())
    return sentences


@dataclass(frozen=True)
class Satz:
    nr: int    # fortlaufend über Titel, Beschreibung und Nachrichten (ab 1)
    teil: str  # „Titel“, „Beschreibung“, „Nachricht 2“ …
    text: str


def saetze(listing: Listing) -> list[Satz]:
    parts = [("Titel", [listing.title.strip()] if listing.title.strip() else []),
             ("Beschreibung", split_sentences(listing.description))]
    many = len(listing.messages) > 1
    parts += [(f"Nachricht {i}" if many else "Nachricht", split_sentences(m))
              for i, m in enumerate(listing.messages, 1)]
    result: list[Satz] = []
    for teil, texts in parts:
        for text in texts:
            if len(result) >= MAX_SAETZE:
                return result
            result.append(Satz(len(result) + 1, teil, text))
    return result


def regel_signale(text: str, cfg: dict | None = None, codebook: Codebook | None = None) -> set[str]:
    """Welche Warnsignale des Codebuchs die Regel-Erkennung in diesem Text (z. B. einem Satz) findet."""
    from scamguard.features.contact import analyze_contacts
    from scamguard.features.scam_phrases import match_phrases

    cfg = cfg or load_config()
    by_rule = (codebook or load_codebook()).signal_by_rule
    codes = {s.code for s in match_phrases(text, str(resolve_path(cfg["paths"]["lexicon"])))}
    codes |= {s.code for s in analyze_contacts(text).signals}
    return {by_rule[c] for c in codes if c in by_rule}


# --------------------------------------------------------------------------- Datensätze

def person_key(name: str) -> str:
    """„Jannis“ → „jannis“; nur a–z und Ziffern (Teil des Dateinamens)."""
    s = (name or "").strip().lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    return re.sub(r"[^a-z0-9]+", "", s)[:30]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")  # Ortszeit mit Zeitzone


def _from_dict(cls, data: dict):
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def _portable(path: str) -> str:
    """Bildpfade innerhalb des Projekts relativ speichern – so passen sie auf allen Rechnern."""
    p = Path(path)
    try:
        return str(p.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(p)


@dataclass
class Aufgabe:
    id: str
    listing: Listing
    quelle: str
    quelle_label: int | None = None  # Label der Quelle (nur zum Vergleich, nie vor dem Einstufen gezeigt)
    bilder: list[str] = field(default_factory=list)  # Inhalts-Hash je Bild, Reihenfolge wie image_paths
    person: str = ""
    zeit: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> Aufgabe:
        return cls(id=str(data["id"]), listing=Listing.from_dict(data.get("listing") or {}),
                   quelle=data.get("quelle", ""), quelle_label=data.get("quelle_label"),
                   bilder=list(data.get("bilder") or []), person=data.get("person", ""),
                   zeit=data.get("zeit", ""))

    @property
    def nur_bilder(self) -> bool:
        return not self.listing.full_text and bool(self.listing.image_paths)

    @property
    def art(self) -> str:
        if self.nur_bilder:
            return "Bild"
        if self.listing.chat_only:
            return "Chat" if len(self.listing.messages) > 1 else "Nachricht"
        return "Inserat mit Chat" if self.listing.messages else "Inserat"


def make_aufgabe(listing: Listing, quelle: str, person: str) -> Aufgabe:
    """Kontaktdaten anonymisieren, Name/Ort/Adresse entfernen, stabile ID bilden.

    Die ID hängt nur vom (anonymisierten) Inhalt ab – derselbe Text bekommt auf jedem Rechner
    dieselbe ID, auch wenn ihn zwei Personen unabhängig hinzufügen."""
    paths = [_portable(p) for p in listing.image_paths]
    clean = Listing.from_dict({
        **listing.to_dict(),
        "title": redact(listing.title), "description": redact(listing.description),
        "messages": [redact(m) for m in listing.messages], "image_paths": paths,
        "seller_name": None, "location": None, "url": None,
        "label": None, "scam_type": None, "source": None, "warnsignale": [],
    })
    hashes = [_file_hash(resolve_path(p)) for p in paths]
    key = (listing_fingerprint(clean) if clean.full_text
           else hashlib.sha1("|".join(sorted(hashes)).encode()).hexdigest())
    return Aufgabe(id=key[:16], listing=clean, quelle=quelle, quelle_label=listing.label,
                   bilder=hashes, person=person, zeit=_now())


@dataclass
class Einstufung:
    aufgabe: str
    person: str
    urteil: str                                         # betrug | serioes | unklar
    sicherheit: str | None = None
    masche: str | None = None
    rolle: str | None = None
    sprache: str | None = None
    saetze: list[dict] = field(default_factory=list)   # nur markierte: {"nr", "text", "signal"}
    bilder: list[dict] = field(default_factory=list)   # {"bild": Hash, "art", "verdaechtig"}
    notiz: str = ""
    dauer_s: float | None = None
    zeit: str = ""
    leitfaden: int = 0                                  # Fassung des Kategoriensystems

    def signal_of(self) -> dict[int, str]:
        return {int(s["nr"]): s["signal"] for s in self.saetze if s.get("signal")}


@dataclass
class Entscheidung:
    aufgabe: str
    person: str
    urteil: str
    masche: str | None = None
    begruendung: str = ""
    zeit: str = ""
    leitfaden: int = 0


@dataclass
class Konsens:
    aufgabe: Aufgabe
    status: str  # einfach | einig | mehrheit | konflikt | unklar | entschieden
    urteil: str | None
    masche: str | None = None
    rolle: str | None = None
    sprache: str | None = None
    saetze: dict[int, str] = field(default_factory=dict)    # Satznummer → Warnsignal
    bilder: dict[str, dict] = field(default_factory=dict)   # Bild-Hash → {"art", "verdaechtig"}
    einstufungen: list[Einstufung] = field(default_factory=list)

    @property
    def label(self) -> int | None:
        return LABEL_OF_URTEIL.get(self.urteil) if self.urteil else None


def _majority(values: Iterable[Any], order: Iterable[Any] = ()) -> tuple[Any, bool]:
    """Häufigster Wert (bei Gleichstand der erste in `order`) und ob er eindeutig vorn liegt."""
    counts = Counter(v for v in values if v is not None)
    if not counts:
        return None, True
    rank = {v: i for i, v in enumerate(order)}
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], rank.get(kv[0], len(rank)), str(kv[0])))
    return ranked[0][0], len(ranked) == 1 or ranked[0][1] > ranked[1][1]


def image_urteil(bilder: list[dict]) -> str:
    """Bild-Aufgaben haben kein Text-Urteil: verdächtiges Bild → Betrug, alle unauffällig → seriös."""
    flags = [b.get("verdaechtig") for b in bilder]
    if "ja" in flags:
        return "betrug"
    return "serioes" if flags and all(f == "nein" for f in flags) else "unklar"


# --------------------------------------------------------------------------- Ablage

_CACHE: dict[Path, tuple[int, int, list[dict]]] = {}


def _read(path: Path) -> list[dict]:
    """JSONL lesen; unverändert gebliebene Dateien kommen aus dem Zwischenspeicher."""
    stat = path.stat()
    cached = _CACHE.get(path)
    if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
        return cached[2]
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # z. B. halb synchronisierte Zeile im gemeinsamen Ordner
    _CACHE[path] = (stat.st_mtime_ns, stat.st_size, records)
    return records


def _write_jsonl(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")  # atomar ersetzen, nie eine halb geschriebene Datei
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _prf(c: Counter) -> dict:
    tp, fp, fn = c["tp"], c["fp"], c["fn"]
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None}


class Werkstatt:
    def __init__(self, folder: str | Path | None = None, doppelt_anteil: float | None = None,
                 codebook: Codebook | None = None):
        settings = load_config().get("einstufung") or {}
        path = Path(str(folder or os.environ.get(FOLDER_ENV) or settings.get("ordner") or "data/einstufung"))
        path = path.expanduser()
        self.folder = path if path.is_absolute() else resolve_path(path)
        self.doppelt_anteil = float(settings.get("doppelt_anteil", 0.25) if doppelt_anteil is None
                                    else doppelt_anteil)
        self.codebook = codebook or load_codebook()

    # -- lesen ----------------------------------------------------------------

    def _records(self, kind: str) -> list[dict]:
        if not self.folder.exists():
            return []
        return [r for p in sorted(self.folder.glob(f"{kind}_*.jsonl")) for r in _read(p)]

    def aufgaben(self) -> dict[str, Aufgabe]:
        tasks: dict[str, Aufgabe] = {}
        for record in self._records("aufgaben"):
            if record.get("id") and record["id"] not in tasks:
                tasks[record["id"]] = Aufgabe.from_dict(record)
        return tasks

    def einstufungen(self) -> dict[str, dict[str, Einstufung]]:
        """Aufgabe → Person → neueste Einstufung."""
        latest: dict[str, dict[str, Einstufung]] = defaultdict(dict)
        for record in self._records("einstufungen"):
            # Dateien aus dem Team sind fremde Eingaben: Namen auf a–z/0–9 begrenzen (Anzeige als Text)
            person = person_key(str(record.get("person") or ""))
            if not (record.get("aufgabe") and person and record.get("urteil") in URTEILE):
                continue
            e = _from_dict(Einstufung, {**record, "person": person})
            old = latest[e.aufgabe].get(e.person)
            if old is None or e.zeit >= old.zeit:
                latest[e.aufgabe][e.person] = e
        return dict(latest)

    def entscheidungen(self) -> dict[str, Entscheidung]:
        latest: dict[str, Entscheidung] = {}
        for record in self._records("entscheidungen"):
            if record.get("aufgabe") and record.get("urteil") in URTEILE:
                d = _from_dict(Entscheidung, {**record, "person": person_key(str(record.get("person") or ""))})
                if d.aufgabe not in latest or d.zeit >= latest[d.aufgabe].zeit:
                    latest[d.aufgabe] = d
        return latest

    def doppelt(self, aufgabe_id: str) -> bool:
        """Soll diese Aufgabe von zwei Personen eingestuft werden? Folgt aus der ID (überall gleich)."""
        try:
            return int(aufgabe_id[:8], 16) / 0xFFFFFFFF < self.doppelt_anteil
        except ValueError:
            return False

    def warteschlange(self, person: str) -> list[Aufgabe]:
        """Offene Aufgaben dieser Person: zuerst Zweitmeinungen, dann noch von niemandem eingestufte
        (in einer eigenen Reihenfolge je Person, damit sich das Team nicht doppelt bearbeitet)."""
        done = self.einstufungen()
        second, fresh = [], []
        for task in self.aufgaben().values():
            by = done.get(task.id, {})
            if person in by:
                continue
            if not by:
                fresh.append(task)
            elif self.doppelt(task.id) and len(by) < 2:
                second.append(task)
        random.Random(person).shuffle(fresh)
        return second + fresh

    def meine(self, person: str, n: int = 5) -> list[Einstufung]:
        own = [by[person] for by in self.einstufungen().values() if person in by]
        return sorted(own, key=lambda e: e.zeit, reverse=True)[:n]

    # -- schreiben ------------------------------------------------------------

    def _append(self, kind: str, person: str, records: list[dict]) -> None:
        if not records:
            return
        if not person:
            raise ValueError("Bitte zuerst einen Namen angeben.")
        self.folder.mkdir(parents=True, exist_ok=True)
        with (self.folder / f"{kind}_{person}.jsonl").open("a", encoding="utf-8") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in records)

    def hinzufuegen(self, listings: Iterable[Listing], quelle: str, person: str,
                    max_neu: int | None = None, ausgewogen: bool = True, seed: int = 0) -> tuple[int, int]:
        """Texte/Bilder als Aufgaben aufnehmen. Rückgabe: (neu, schon vorhanden).

        max_neu: höchstens so viele neue, zufällig gezogen; ausgewogen = gleich viele je Label der
        Quelle (z. B. Spam/kein Spam), damit nicht nur eine Klasse eingestuft wird."""
        known = set(self.aufgaben())
        candidates: dict[str, Aufgabe] = {}
        existing = 0
        for listing in listings:
            task = make_aufgabe(listing, quelle, person)
            if task.id in known or task.id in candidates:
                existing += 1
            else:
                candidates[task.id] = task
        picked = list(candidates.values())
        if max_neu is not None and len(picked) > max_neu:
            rng = random.Random(seed)
            groups: dict[Any, list[Aufgabe]] = defaultdict(list)
            for task in picked:
                groups[task.quelle_label if ausgewogen else None].append(task)
            share = max_neu // len(groups)
            chosen = [t for key in sorted(groups, key=str)
                      for t in rng.sample(groups[key], min(share, len(groups[key])))]
            ids = {t.id for t in chosen}
            rest = [t for t in picked if t.id not in ids]
            chosen += rng.sample(rest, max_neu - len(chosen))
            picked = chosen
        self._append("aufgaben", person, [asdict(t) for t in picked])
        return len(picked), existing

    def hinzufuegen_aus_datensatz(self, spec, person: str, max_neu: int | None = 50,
                                  ausgewogen: bool = True) -> tuple[int, int]:
        from scamguard.data.loaders import iter_listings

        return self.hinzufuegen(iter_listings(spec), spec.name, person, max_neu, ausgewogen)

    def pruefen(self, e: Einstufung, task: Aufgabe | None = None) -> list[str]:
        """Fehlende oder unbekannte Angaben (die Web-App zeigt sie vor dem Speichern)."""
        cb, problems = self.codebook, []
        if e.urteil not in URTEILE:
            problems.append("Bitte ein Urteil wählen.")
        if e.urteil == "betrug" and not e.masche and not (task and task.nur_bilder):
            problems.append("Bei Betrug bitte die Masche wählen (notfalls „Sonstige Masche“).")
        if task and task.nur_bilder and not e.bilder:
            problems.append("Bitte mindestens ein Bild einstufen.")
        for value, allowed, what in ((e.masche, cb.maschen, "Masche"), (e.rolle, cb.rolle, "Rolle"),
                                     (e.sprache, cb.sprache, "Sprache"), (e.sicherheit, cb.sicherheit, "Sicherheit")):
            if value is not None and value not in allowed:
                problems.append(f"Unbekannte {what}: {value}")
        problems += [f"Unbekanntes Warnsignal: {s.get('signal')}" for s in e.saetze
                     if s.get("signal") not in cb.signale]
        problems += [f"Unbekannte Bildart: {b.get('art')}" for b in e.bilder
                     if b.get("art") is not None and b.get("art") not in cb.bildarten]
        return problems

    def speichern(self, e: Einstufung) -> None:
        task = self.aufgaben().get(e.aufgabe)
        if problems := self.pruefen(e, task):
            raise ValueError(" ".join(problems))
        if e.urteil != "betrug":
            e.masche = e.rolle = None
        e.zeit, e.leitfaden = _now(), self.codebook.version
        self._append("einstufungen", e.person, [asdict(e)])

    def entscheiden(self, d: Entscheidung) -> None:
        if d.urteil not in URTEILE:
            raise ValueError("Bitte ein Urteil wählen.")
        if d.urteil != "betrug":
            d.masche = None
        d.zeit, d.leitfaden = _now(), self.codebook.version
        self._append("entscheidungen", d.person, [asdict(d)])

    def zusammenfuehren(self, name: str, content: bytes, person: str) -> int:
        """Datei aus dem Team übernehmen. Zeilen werden nur ergänzt, nie ersetzt. Rückgabe: neue Zeilen."""
        m = SHARED_FILE_RE.fullmatch(name)
        if not m:
            raise ValueError(f"„{name}“ ist keine Workspace-Datei (aufgaben_/einstufungen_/"
                             "entscheidungen_<name>.jsonl).")
        if m.group(2) == person:
            raise ValueError("Das ist deine eigene Datei – sie liegt schon im Arbeitsordner.")
        target = self.folder / name
        known = {line.strip() for line in target.read_text(encoding="utf-8").splitlines()} if target.exists() else set()
        new = []
        for line in content.decode("utf-8-sig").splitlines():
            line = line.strip()
            if not line or line in known:
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError:
                continue
            known.add(line)
            new.append(line)
        if new:
            self.folder.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as f:
                f.writelines(line + "\n" for line in new)
        return len(new)

    # -- Konsens --------------------------------------------------------------

    def konsens(self) -> dict[str, Konsens]:
        tasks, decisions, cb = self.aufgaben(), self.entscheidungen(), self.codebook
        result = {}
        for aid, by in self.einstufungen().items():
            if aid not in tasks:
                continue  # Einstufung kam schon an, die Aufgabe (noch) nicht
            ratings = list(by.values())
            decision = decisions.get(aid)
            votes = [e.urteil for e in ratings if e.urteil in LABEL_OF_URTEIL]
            if decision:
                status, urteil = "entschieden", decision.urteil if decision.urteil in LABEL_OF_URTEIL else None
            elif not votes:
                status, urteil = "unklar", None
            else:
                top, unique = _majority(votes)
                urteil = top if unique else None
                status = ("konflikt" if not unique else "einfach" if len(votes) == 1
                          else "einig" if len(set(votes)) == 1 else "mehrheit")
            scam = [e for e in ratings if e.urteil == "betrug"]
            k = Konsens(tasks[aid], status, urteil, einstufungen=ratings,
                        sprache=_majority((e.sprache for e in ratings), cb.sprache)[0])
            if urteil == "betrug":
                k.masche = (decision.masche if decision and decision.masche
                            else _majority((e.masche for e in scam), cb.maschen)[0])
                k.rolle = _majority((e.rolle for e in scam), cb.rolle)[0]
            # Satz zählt als Warnsignal, wenn mehr als die Hälfte der Einstufenden ihn markiert hat
            flagged: dict[int, list[str]] = defaultdict(list)
            for e in ratings:
                for nr, signal in e.signal_of().items():
                    flagged[nr].append(signal)
            k.saetze = {nr: _majority(signals, cb.signale)[0] for nr, signals in sorted(flagged.items())
                        if len(signals) * 2 > len(ratings)}
            per_image: dict[str, list[dict]] = defaultdict(list)
            for e in ratings:
                for b in e.bilder:
                    per_image[b.get("bild", "")].append(b)
            k.bilder = {h: {"art": _majority((b.get("art") for b in bs), cb.bildarten)[0],
                            "verdaechtig": _majority((b.get("verdaechtig") for b in bs), cb.verdaechtig)[0]}
                        for h, bs in per_image.items() if h}
            result[aid] = k
        return result

    # -- Qualität -------------------------------------------------------------

    def auffaelligkeiten(self) -> list[dict]:
        tasks, notes = self.aufgaben(), []
        for aid, by in self.einstufungen().items():
            task = tasks.get(aid)
            if task is None:
                continue
            length = len(task.listing.full_text)
            for e in by.values():
                flags = len(e.signal_of())
                hint = None
                if e.dauer_s is not None and e.dauer_s < SCHNELL_S and length > 200:
                    hint = f"in {e.dauer_s:.0f} s eingestuft – bei {length} Zeichen sehr schnell"
                elif e.urteil == "betrug" and not e.masche and not task.nur_bilder:
                    hint = "Betrug ohne Masche"
                elif e.urteil == "serioes" and flags >= 2:
                    hint = f"seriös, aber {flags} Warnsignale markiert – bewusst?"
                elif e.urteil == "betrug" and flags == 0 and task.listing.full_text:
                    hint = "Betrug, aber kein Satz markiert – welcher Satz verrät es?"
                if hint:
                    notes.append({"aufgabe": aid, "person": e.person, "hinweis": hint})
        return notes

    def bericht(self) -> dict:
        """Kennzahlen für die Qualität der Einstufungen (Web-App und `scamguard einstufen bericht`)."""
        tasks, konsens = self.aufgaben(), self.konsens()
        # nur Einstufungen, deren Aufgabe schon da ist (bei geteilten Dateien kann sie später ankommen)
        ratings = {aid: by for aid, by in self.einstufungen().items() if aid in tasks}
        units: dict[str, dict[Hashable, list]] = defaultdict(dict)
        for aid, by in ratings.items():
            es = list(by.values())
            units["Urteil (Betrug/seriös)"][aid] = [e.urteil for e in es if e.urteil in LABEL_OF_URTEIL]
            units["Masche"][aid] = [e.masche for e in es if e.urteil == "betrug" and e.masche]
            task = tasks.get(aid)
            if len(es) < 2 or task is None:
                continue
            marks = [e.signal_of() for e in es]
            for s in saetze(task.listing):
                values = [m.get(s.nr, "keins") for m in marks]
                units["Satz: Warnsignal ja/nein"][(aid, s.nr)] = [v != "keins" for v in values]
                units["Satz: Art des Warnsignals"][(aid, s.nr)] = values
            images: dict[str, list[dict]] = defaultdict(list)
            for e in es:
                for b in e.bilder:
                    images[b.get("bild", "")].append(b)
            for h, bs in images.items():
                units["Bild: Art"][(aid, h)] = [b["art"] for b in bs if b.get("art")]
                units["Bild: verdächtig"][(aid, h)] = [b["verdaechtig"] for b in bs if b.get("verdaechtig")]

        levels = ["Urteil (Betrug/seriös)", "Masche", "Satz: Warnsignal ja/nein", "Satz: Art des Warnsignals",
                  "Bild: Art", "Bild: verdächtig"]
        reliability = []
        for level in levels:
            alpha = agreement.krippendorff_alpha(units[level])
            reliability.append({"ebene": level, "einheiten": agreement.shared_units(units[level]),
                                "alpha": alpha, "prozent": agreement.percent_agreement(units[level]),
                                "einschaetzung": agreement.rating(alpha)})
        by_person: dict[str, dict[str, str]] = defaultdict(dict)
        for aid, by in ratings.items():
            for person, e in by.items():
                if e.urteil in LABEL_OF_URTEIL:
                    by_person[person][aid] = e.urteil

        sources: dict[str, Counter] = defaultdict(Counter)
        for k in konsens.values():
            if k.label is not None and k.aufgabe.quelle_label is not None:
                sources[k.aufgabe.quelle]["n"] += 1
                sources[k.aufgabe.quelle]["widerspruch"] += int(k.label != k.aufgabe.quelle_label)
        return {
            "aufgaben": len(tasks),
            "eingestuft": len(ratings),
            "doppelt_eingestuft": sum(len(by) >= 2 for by in ratings.values()),
            "personen": dict(Counter(p for by in ratings.values() for p in by)),
            "status": dict(Counter(k.status for k in konsens.values())),
            "labels": dict(Counter(k.urteil for k in konsens.values() if k.label is not None)),
            "uebereinstimmung": reliability,
            "kappa": agreement.pairwise_kappa(by_person),
            "konflikte": [aid for aid, k in konsens.items() if k.status == "konflikt"],
            "auffaelligkeiten": self.auffaelligkeiten(),
            "quellen": [{"quelle": q, "n": c["n"], "widerspruch": c["widerspruch"]} for q, c in sorted(sources.items())],
        }

    def regeln_gegen_mensch(self, cfg: dict | None = None, beispiele: int = 15) -> dict:
        """Satzgenauer Vergleich Regel-Erkennung ↔ Konsens: Precision/Recall je Warnsignal und
        Beispiele für verpasste Sätze (Kandidaten fürs Lexikon) und Fehlalarme."""
        cb = self.codebook
        per_signal = {s: Counter() for s, entry in cb.signale.items() if entry.get("regeln")}
        overall, missed, false_alarms = Counter(), [], []
        for k in self.konsens().values():
            for s in saetze(k.aufgabe.listing):
                human, rules = k.saetze.get(s.nr), regel_signale(s.text, cfg, cb)
                overall["tp" if human and rules else "fp" if rules else "fn" if human else "tn"] += 1
                for signal, c in per_signal.items():
                    if human == signal and signal in rules:
                        c["tp"] += 1
                    elif signal in rules:
                        c["fp"] += 1
                    elif human == signal:
                        c["fn"] += 1
                if human and not rules and len(missed) < beispiele:
                    missed.append({"signal": human, "text": s.text})
                elif rules and not human and len(false_alarms) < beispiele:
                    false_alarms.append({"signal": ", ".join(sorted(rules)), "text": s.text})
        return {
            "saetze": sum(overall.values()),
            "gesamt": _prf(overall),
            "je_signal": [{"signal": s, "name": cb.name("signale", s), **_prf(c)}
                          for s, c in per_signal.items() if sum(c.values())],
            "verpasst": missed,
            "fehlalarme": false_alarms,
        }

    # -- Export ---------------------------------------------------------------

    def export(self, out_dir: str | Path | None = None) -> dict[str, int]:
        out = resolve_path(out_dir or EXPORT_DIR)
        rows: dict[str, list[dict]] = {"konsens": [], "saetze": [], "bilder": []}
        for k in self.konsens().values():
            task, sentences = k.aufgabe, saetze(k.aufgabe.listing)
            if k.label is not None and task.listing.full_text:
                rows["konsens"].append({
                    **task.listing.to_dict(), "label": k.label, "scam_type": k.masche, "source": "einstufungen",
                    "warnsignale": [{"signal": k.saetze[s.nr], "text": s.text} for s in sentences if s.nr in k.saetze],
                    # Zusatzfelder für Auswertungen (Fairness, Qualität) – Listing ignoriert sie beim Laden
                    "aufgabe": task.id, "status": k.status, "personen": len(k.einstufungen),
                    "sprache": k.sprache, "rolle": k.rolle, "herkunft": task.quelle,
                })
            rows["saetze"] += [{"aufgabe": task.id, "nr": s.nr, "teil": s.teil, "text": s.text,
                                "signal": k.saetze.get(s.nr), "label": k.label, "personen": len(k.einstufungen)}
                               for s in sentences]
            rows["bilder"] += [{"aufgabe": task.id, "bild": path, "hash": h, **k.bilder[h], "label": k.label}
                               for path, h in zip(task.listing.image_paths, task.bilder) if h in k.bilder]
        for name, part in rows.items():
            _write_jsonl(part, out / EXPORT_FILES[name])
        return {name: len(part) for name, part in rows.items()}
