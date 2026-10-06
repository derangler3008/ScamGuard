"""Phrasen-Kandidaten fürs Lexikon: Welche Wortfolgen stehen in Betrugstexten auffällig oft?

Verfahren: Log-Odds-Ratio mit informativem Dirichlet-Prior („Fightin' Words“, Monroe, Colaresi &
Quinn 2008). Es vergleicht, wie viele Betrugs- bzw. seriöse Texte eine Wortfolge enthalten, und
dämpft seltene Wortfolgen über den Prior – anders als ein einfaches Verhältnis landen so keine
Einzelfunde oben. Das Ergebnis (z-Wert) ist eine Kandidatenliste für Menschen, kein Lexikon:
Sinnvolle Treffer werden geprüft und als Muster in data/lexicons/scam_signals_de.yaml übernommen.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from scamguard.features.language import GERMAN_STOPWORDS
from scamguard.features.scam_phrases import load_lexicon

TOKEN = re.compile(r"[a-zäöüß0-9€]+(?:-[a-zäöüß0-9]+)*", re.IGNORECASE)
# Wortfolgen nur aus Füllwörtern („und die“, „ich bin“) sagen nichts über eine Masche aus
STOPWORDS = GERMAN_STOPWORDS | {
    "oder", "aber", "so", "als", "bin", "bist", "sein", "hast", "will", "du", "er", "wir", "ihr", "mich",
    "dich", "mir", "dir", "uns", "euch", "ihn", "ihm", "ihnen", "mein", "meine", "dein", "deine", "ihre",
    "sich", "ab", "da", "dann", "wenn", "dass", "ob", "kein", "keine", "ja", "nein", "hallo", "hi", "danke",
    "bitte", "gut", "doch", "was", "wer", "wo",
}


@dataclass(frozen=True)
class Candidate:
    phrase: str
    z: float              # > 0: typisch für Betrug, je höher desto deutlicher
    scam_docs: int        # in so vielen Betrugstexten enthalten
    legit_docs: int
    covered_by: str       # Lexikon-Gruppe, die die Wortfolge schon erkennt ("" = neu)


def ngrams(text: str, n_max: int = 3) -> set[str]:
    """Alle Wortfolgen (1 bis n_max Wörter) eines Texts – pro Text nur einmal gezählt."""
    tokens = [t.lower() for t in TOKEN.findall(text)]
    grams = set()
    for n in range(1, n_max + 1):
        for i in range(len(tokens) - n + 1):
            gram = tokens[i:i + n]
            if all(t in STOPWORDS for t in gram) or any(len(t) < 2 for t in gram):
                continue
            grams.add(" ".join(gram))
    return grams


def _doc_counts(texts: Iterable[str], n_max: int) -> tuple[Counter, int]:
    counts: Counter = Counter()
    docs = 0
    for text in texts:
        counts.update(ngrams(text, n_max))
        docs += 1
    return counts, docs


def log_odds(scam_texts: Iterable[str], legit_texts: Iterable[str], n_max: int = 3,
             min_docs: int = 3, prior_strength: float = 500.0) -> list[tuple[str, float, int, int]]:
    """[(Wortfolge, z, #Betrugstexte, #seriöse Texte)], absteigend nach z.

    prior_strength: Gewicht des Priors (Pseudo-Beobachtungen). Höher = seltene Wortfolgen
    werden stärker gedämpft."""
    scam, n_scam_docs = _doc_counts(scam_texts, n_max)
    legit, n_legit_docs = _doc_counts(legit_texts, n_max)
    if not n_scam_docs or not n_legit_docs:
        raise ValueError("Für den Vergleich braucht es Betrugs- UND seriöse Texte")
    total = scam + legit
    n_total = sum(total.values())
    n_s, n_l = sum(scam.values()), sum(legit.values())

    rows = []
    for gram, y_s in scam.items():
        if y_s < min_docs:
            continue
        y_l = legit.get(gram, 0)
        a = prior_strength * total[gram] / n_total  # Prior proportional zur Gesamthäufigkeit
        delta = (math.log((y_s + a) / (n_s + prior_strength - y_s - a))
                 - math.log((y_l + a) / (n_l + prior_strength - y_l - a)))
        z = delta / math.sqrt(1 / (y_s + a) + 1 / (y_l + a))
        rows.append((gram, z, y_s, y_l))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows


def _covered(phrase: str, lexicon_path: str) -> str:
    for group in load_lexicon(lexicon_path):
        if any(p.search(phrase) for p in group.patterns):
            return group.code
    return ""


def candidates(scam_texts: Iterable[str], legit_texts: Iterable[str], lexicon_path: str,
               top: int = 80, n_max: int = 3, min_docs: int = 3) -> list[Candidate]:
    """Die `top` deutlichsten Betrugs-Wortfolgen; überlappende kürzere Varianten fallen weg
    („sicher bezahlen“ statt zusätzlich „sicher“ und „bezahlen“, wenn gleich häufig)."""
    pool = [r for r in log_odds(scam_texts, legit_texts, n_max=n_max, min_docs=min_docs) if r[1] > 0]
    pool = pool[:top * 4]
    chosen: list[Candidate] = []
    for gram, z, y_s, y_l in pool:
        # Steckt fast immer in einer längeren Kandidaten-Wortfolge → die längere ist aussagekräftiger
        if any(other != gram and f" {gram} " in f" {other} " and other_s >= 0.8 * y_s
               for other, _, other_s, _ in pool):
            continue
        chosen.append(Candidate(gram, round(z, 2), y_s, y_l, _covered(gram, lexicon_path)))
        if len(chosen) >= top:
            break
    return chosen
