"""Vergleichsfälle für das LLM: die ähnlichsten von Menschen gelabelten Inserate gehen mit in den Prompt.

So fließt der Teamdatensatz ohne Training ins LLM – jedes neue Label und jeder `scamguard team import`
wirkt beim nächsten Scan. Quellen: eigene Labels (`data/raw/eigene_labels.jsonl`) und die Inserat-Ordner
(`data/datensatz_fuellen_inserate/{betrug,serioes}/…/inserat.json`, auch aus Team-Importen). Synthetische
Daten und Ordner mit `_` am Anfang bleiben draußen – das Modell soll sich an echten Fällen orientieren.

Auswahl: TF-IDF-Ähnlichkeit zum geprüften Inserat ohne Füllwörter, Zahlen und Standardfloskeln
(`IGNORED_WORDS` – sonst wirken Lackschuhe und Grafikkarte über „Privatverkauf unter Ausschluss jeglicher
Gewährleistung“ ähnlich), nur wirklich ähnliche Fälle (`MIN_SIMILARITY` – ein unpassender Vergleich führt eher
in die Irre als keiner), je Klasse höchstens die Hälfte, damit nicht schon
die Auswahl das Urteil vorgibt. Das geprüfte Inserat selbst wird nie gezeigt, bei Auswertungen auch kein Fall
aus der ausgewerteten Menge (`exclude`). Texte sind anonymisiert (Telefon, Mail, IBAN), Anbieternamen gibt
es ohnehin nicht.

Feintuning (`scamguard llm-daten`) trainiert ohne Vergleichsfälle – das Modell lernt die Aufgabe, die
Vergleichsfälle kommen erst bei der Prüfung dazu.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from scamguard.config import resolve_path
from scamguard.schema import Listing

LABEL_DIRS = {"betrug": 1, "serioes": 0}
SKIP_DIRS = {"synthetisch"}
TEXT_CHARS = 260          # pro Fall: reicht für Masche und Ton, hält den Prompt kurz (lokales LLM)
REFRESH_S = 30            # Dateien höchstens alle 30 s neu einlesen
# Wörter, die in fast jedem Inserat stehen – sie machen beliebige Inserate „ähnlich“. Maschen-Wörter (PayPal,
# Freunde, Vorkasse, Überweisung, WhatsApp …) bleiben bewusst drin, die sollen Fälle verbinden.
IGNORED_WORDS = frozenset({
    "sie", "ihnen", "ihr", "uns", "unsere", "unser", "unseren", "wir", "ich", "es", "ein", "eine", "einen",
    "einem", "einer", "sich", "nicht", "wie", "nur", "auch", "noch", "sehr", "alle", "alles", "man", "hier",
    "bei", "mit", "von", "vom", "zum", "zur", "für", "auf", "aus", "an", "im", "in", "am", "um", "bis",
    "nach", "über", "unter", "einfach", "gerne", "gern", "schon", "mal", "dabei", "dazu", "wurde", "wird",
    "werden", "kann", "können", "habe", "hat", "haben", "ist", "sind", "war",
    "privatverkauf", "privat", "privatperson", "ausschluss", "jeglicher", "gewährleistung", "garantie",
    "rücknahme", "rückgabe", "umtausch", "sachmängelhaftung", "haftung", "handelt", "erfolgt", "artikel",
    "angebot", "verkaufe", "verkauf", "verkaufen", "preis", "vb", "festpreis", "verhandlungsbasis", "zustand",
    "neu", "neuwertig", "gebraucht", "guter", "gutem", "top", "versand", "versenden", "abholung", "abholen",
    "abzuholen", "bilder", "fotos", "siehe", "bild", "fragen", "anfragen", "melden", "nachricht", "schreiben",
    "interesse", "nichtraucherhaushalt", "tierfreier", "haushalt", "kleinanzeigen", "anzeige", "inserat",
    "euro", "eur", "stück", "möglich", "leider", "keine", "kein", "per",
})
TOKEN_PATTERN = r"(?u)\b\w*[^\W\d_]\w*\b"  # nur Wörter mit Buchstaben: „43“ (Schuhgröße) ≠ „12“ (GB)
# Kosinus-Ähnlichkeit (TF-IDF ohne IGNORED_WORDS) unter den 38 Inseraten (Stand 10/2026): Paare aus
# verschiedenen Kategorien 99 % unter 0,037; darüber nur Sinnvolles (Winterräder VW ↔ Porsche, zwei
# „heute, PayPal Freunde“-Betrugsfälle). Gleiche Artikelart/Masche liegt bei 0,05–0,9.
MIN_SIMILARITY = 0.05
MIN_SHARED_TERMS = 2  # ein einziges gemeinsames Wort („Mail“) macht noch keinen ähnlichen Fall


def _visible(path: Path, root: Path) -> bool:
    return not any(part.startswith("_") or part in SKIP_DIRS for part in path.relative_to(root).parts)


def load_labeled(label_file: Path, listing_dir: Path) -> list[Listing]:
    """Alle von Menschen gelabelten Inserate, dedupliziert (eigene Labels und Ordner-Ablage überschneiden sich)."""
    from scamguard.data.build import listing_fingerprint
    from scamguard.data.loaders import looks_binary
    from scamguard.data.redact import redact

    found: list[Listing] = []
    if label_file.exists():
        for line in label_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                found.append(Listing.from_dict(json.loads(line)))
    for name, label in LABEL_DIRS.items():
        root = listing_dir / name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("inserat.json")):
            if not _visible(path, listing_dir):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            found.append(Listing.from_dict({**data, "label": label}))

    seen, out = set(), []
    for listing in found:
        if listing.label not in (0, 1) or not listing.full_text or looks_binary(listing.full_text):
            continue  # z. B. ein versehentlich als Text gelesenes Bild
        fp = listing_fingerprint(listing)
        if fp in seen:
            continue
        seen.add(fp)
        listing.title, listing.description = redact(listing.title), redact(listing.description)
        listing.messages = [redact(m) for m in listing.messages]
        out.append(listing)
    return out


def count_reviewed() -> dict[str, int]:
    """Alle von Menschen eingestuften Inserate (eigene Labels + Ordner-Ablage, ohne Doppelte) – genau die
    Menge, aus der die Vergleichsfälle kommen. `eigene` = davon über Upload/Extension/Web-App gelabelt."""
    from scamguard.data import labels

    reviewed = load_labeled(resolve_path(labels.LABEL_FILE), resolve_path(labels.DATASET_DIR))
    scam = sum(listing.label == 1 for listing in reviewed)
    return {"gesamt": len(reviewed), "betrug": scam, "serioes": len(reviewed) - scam,
            "eigene": labels.count_labels()["gesamt"]}


class ExampleBank:
    def __init__(self, count: int = 4, label_file: str | Path | None = None, listing_dir: str | Path | None = None):
        self.count = count
        self._label_file, self._listing_dir = label_file, listing_dir
        self._lock = threading.Lock()
        self._checked = 0.0
        self._signature: tuple | None = None
        self._listings: list[Listing] = []
        self._fingerprints: list[str] = []
        self._vectorizer = self._matrix = None
        self._excluded: frozenset[str] = frozenset()

    def exclude(self, fingerprints) -> None:
        """Diese Inserate nie zeigen – z. B. die Testmenge bei `scamguard evaluate --llm`, sonst sähe das
        LLM die Labels der Menge, an der es gemessen wird."""
        self._excluded = frozenset(fingerprints)

    def _paths(self) -> tuple[Path, Path]:
        from scamguard.data import labels

        return (Path(self._label_file) if self._label_file else resolve_path(labels.LABEL_FILE),
                Path(self._listing_dir) if self._listing_dir else resolve_path(labels.DATASET_DIR))

    def _refresh(self) -> None:
        if time.monotonic() - self._checked < REFRESH_S and self._signature is not None:
            return
        self._checked = time.monotonic()
        label_file, listing_dir = self._paths()
        files = [label_file] if label_file.exists() else []
        files += [p for p in listing_dir.rglob("inserat.json")] if listing_dir.is_dir() else []
        signature = tuple(sorted((str(p), p.stat().st_mtime) for p in files))
        if signature == self._signature:
            return
        from sklearn.feature_extraction.text import TfidfVectorizer

        from scamguard.data.build import listing_fingerprint
        from scamguard.data.phrases import STOPWORDS

        listings = load_labeled(label_file, listing_dir)
        self._listings, self._signature = listings, signature
        self._fingerprints = [listing_fingerprint(listing) for listing in listings]
        if listings:
            self._vectorizer = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1,
                                               stop_words=sorted(STOPWORDS | IGNORED_WORDS),
                                               token_pattern=TOKEN_PATTERN)
            self._matrix = self._vectorizer.fit_transform([_retrieval_text(listing) for listing in listings])
            self._terms = self._vectorizer.get_feature_names_out()

    def select(self, listing: Listing) -> list[Listing]:
        """Die ähnlichsten gelabelten Inserate über `MIN_SIMILARITY`, je Klasse höchstens `count // 2`.
        Kein Auffüllen mit der anderen Klasse: gibt es zu Konzertkarten nur ähnliche Betrugsfälle, sähe sonst
        auch ein ehrliches Ticket-Inserat ausschließlich Betrugsbeispiele."""
        if self.count <= 0:
            return []
        from scamguard.data.build import listing_fingerprint

        with self._lock:
            self._refresh()
            if not self._listings:
                return []
            own = listing_fingerprint(listing)
            scores = self._similarities(listing)
            ranked = [i for i in scores.argsort()[::-1]
                      if scores[i] >= MIN_SIMILARITY and self._fingerprints[i] != own
                      and self._fingerprints[i] not in self._excluded
                      and not (listing.url and self._listings[i].url == listing.url)]
            per_class = {label: [i for i in ranked if self._listings[i].label == label] for label in (1, 0)}
            half = max(1, self.count // 2)
            picked = per_class[1][:half] + per_class[0][:half]
            picked.sort(key=lambda i: -scores[i])  # ähnlichster Fall zuerst
            return [self._listings[i] for i in picked]

    def _similarities(self, listing: Listing):
        """Kosinus-Ähnlichkeit zu allen Fällen. Wörter, die in keinem Fall vorkommen, zählen mit (als seltenste
        Wörter) – sonst bliebe von „Schwarze Lackschuhe, Versand möglich“ nur „möglich“ übrig, und jedes
        Inserat mit „möglich“ wäre der perfekte Vergleichsfall. Fälle mit weniger als `MIN_SHARED_TERMS`
        gemeinsamen Wörtern bekommen 0."""
        import numpy as np

        vec = self._vectorizer
        query = vec.transform([_retrieval_text(listing)])
        query.data = np.ones_like(query.data)  # transform() hat schon normiert: Gewichte neu aus tf und idf
        counts: dict[str, int] = {}
        for term in vec.build_analyzer()(_retrieval_text(listing)):
            counts[term] = counts.get(term, 0) + 1
        vocab, idf = vec.vocabulary_, vec.idf_
        weights = {t: (1 + np.log(n)) * (idf[vocab[t]] if t in vocab else idf.max()) for t, n in counts.items()}
        norm = np.sqrt(sum(w * w for w in weights.values()))
        if not norm:
            return np.zeros(self._matrix.shape[0])
        query.data = np.array([weights[t] for t in self._terms[query.indices]]) / norm
        scores = (self._matrix @ query.T).toarray().ravel()
        shared = ((self._matrix > 0).astype(np.int32) @ (query > 0).astype(np.int32).T).toarray().ravel()
        scores[shared < MIN_SHARED_TERMS] = 0.0
        return scores

    def prompt_block(self, listing: Listing) -> str:
        return prompt_block(self.select(listing))


def prompt_block(examples: list[Listing]) -> str:
    if not examples:
        return ""
    intro = ("Ähnliche Inserate aus unseren eigenen, von Menschen geprüften Einstufungen. Sie zeigen, wie wir "
             "vergleichbare Fälle bewertet haben – nutze sie als Orientierung, bewerte das Inserat unten aber "
             "eigenständig nach seinem Inhalt.")
    parts = ["<vergleichsfaelle>", intro]
    for n, ex in enumerate(examples, 1):
        parts.append(f"Fall {n} – {'BETRUG' if ex.label == 1 else 'SERIÖS'}: {_summary(ex)}")
    parts.append("</vergleichsfaelle>")
    return "\n".join(parts)


def _retrieval_text(listing: Listing) -> str:
    return f"{listing.title}\n{listing.category}\n{(listing.description or '')[:1500]}\n" + "\n".join(listing.messages)


def _summary(listing: Listing) -> str:
    facts = [listing.category]
    if listing.price is not None:
        facts.append(f"{listing.price:.0f} €")
    if listing.seller_type:
        facts.append(listing.seller_type)
    if listing.seller_account_age_days is not None:
        facts.append(f"Konto {listing.seller_account_age_days} Tage")
    text = " ".join((listing.description or " | ".join(listing.messages)).split())
    if len(text) > TEXT_CHARS:
        text = text[:TEXT_CHARS].rsplit(" ", 1)[0] + " …"
    title = f"„{listing.title}“ " if listing.title else "Chat "
    return f"{title}({', '.join(facts)}) – {text}"
