"""Heuristiken für „gebrochenes Deutsch“ und Sprach-Auffälligkeiten.

Bewusst ohne große Abhängigkeiten. Die Merkmale sind *schwache* Indizien:
Auch ehrliche Verkäufer schreiben fehlerhaft (Dialekt, Legasthenie, Nicht-Muttersprachler).
Deshalb sind die Gewichte niedrig – erst in Kombination mit anderen Signalen relevant.

Ausbaustufe (TODO Team): Rechtschreibprüfung per Hunspell/LanguageTool,
Perplexität eines deutschen Sprachmodells als „Natürlichkeits“-Score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from scamguard.schema import Signal

WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß]+")

# Häufige Funktionswörter. Wörter, die es in beiden Sprachen gibt (in, an, was, will, so, man,
# also), stehen bewusst in keiner der Listen.
GERMAN_STOPWORDS = {
    "der", "die", "das", "den", "dem", "des", "und", "ist", "nicht", "ich", "sie", "es",
    "ein", "eine", "einen", "einem", "einer", "eines", "mit", "für", "auf", "zu", "von", "im",
    "am", "um", "aus", "bei", "nach", "vor", "über", "unter", "ohne", "bis", "durch", "gegen",
    "zum", "zur", "vom", "beim", "ins", "auch", "noch", "wie", "nur", "schon", "mal", "sehr",
    "wird", "werden", "wurde", "sind", "war", "waren", "hat", "habe", "haben", "hatte",
    "kann", "können", "muss", "müssen", "soll", "sollte", "wollen", "möchte", "gibt",
    "aber", "oder", "wenn", "dass", "weil", "als", "dann", "denn", "doch", "da", "hier",
    "kein", "keine", "keinen", "mir", "mich", "mein", "meine", "meinen", "dir", "dich",
    "wir", "uns", "ihr", "ihnen", "euch", "er", "ihm", "ihn", "sich", "wer", "wo",
    "gerne", "gern", "bitte", "alle", "alles", "viel", "viele", "mehr", "wegen", "selbst",
    "gut", "neu", "inkl", "ca", "ab", "ja", "nein", "nichts", "dabei",
}
ENGLISH_STOPWORDS = {
    "the", "and", "is", "are", "you", "your", "with", "for", "this", "that", "it", "of",
    "to", "on", "be", "have", "has", "were", "can", "my", "we", "not", "but", "or", "if",
    "at", "by", "from", "as", "very", "new", "used", "item", "please", "payment",
    "shipping", "condition", "available",
}
_UMLAUT_RE = re.compile(r"[äöüÄÖÜß]")

# Wörter, die korrekt mit Umlaut geschrieben werden, aber oft mit ae/oe/ue (Tastatur ohne Umlaute)
UMLAUT_SUBSTITUTES = {
    "fuer", "ueber", "moechte", "koennen", "koennte", "muessen", "waere", "haette",
    "schoen", "groesse", "zurueck", "kaeufer", "verkaeufer", "geraet", "zustaende",
    "natuerlich", "gruesse", "taeglich", "spaeter", "frueher", "pruefen", "gewaehrleistung",
}

# Grammatisches Geschlecht häufiger Artikel in Inseraten (n = Neutrum, m = Maskulinum, f = Femininum).
# Für die Prüfung zählen nur eindeutig falsche Artikel im Singular:
#   Neutrum: "der"/"die" X falsch  |  Maskulinum: "das" X falsch  |  Femininum: "das" X falsch
# ("die" bei Maskulina nicht prüfen: Plural ist oft identisch – "die Artikel", "die Wagen")
NOUN_GENDER = {
    "handy": "n", "auto": "n", "fahrzeug": "n", "fahrrad": "n", "iphone": "n", "tablet": "n",
    "notebook": "n", "smartphone": "n", "sofa": "n", "bett": "n", "gerät": "n", "paket": "n",
    "laptop": "m", "fernseher": "m", "kühlschrank": "m", "trockner": "m", "wagen": "m",
    "computer": "m", "monitor": "m", "schrank": "m", "tisch": "m", "stuhl": "m", "preis": "m",
    "kinderwagen": "m", "staubsauger": "m", "herd": "m", "artikel": "m", "zustand": "m",
    "waschmaschine": "f", "spülmaschine": "f", "kamera": "f", "konsole": "f", "uhr": "f",
    "couch": "f", "kommode": "f", "mikrowelle": "f", "kaffeemaschine": "f", "rechnung": "f",
    "lieferung": "f", "zahlung": "f", "garantie": "f", "anzeige": "f",
}
_WRONG_ARTICLES = {"n": {"der", "die", "eine"}, "m": {"das", "eine"}, "f": {"das", "ein"}}
ARTICLE_NOUN_RE = re.compile(r"\b(der|die|das|ein|eine)\s+([A-Za-zÄÖÜäöüß]+)\b(?!-)", re.IGNORECASE)


@dataclass
class LanguageFeatures:
    n_words: int = 0
    german_ratio: float = 0.0
    english_ratio: float = 0.0
    capitalized_ratio: float = 0.0
    umlaut_substitutes: int = 0
    article_errors: list[str] = field(default_factory=list)
    caps_ratio: float = 0.0
    exclamations: int = 0
    signals: list[Signal] = field(default_factory=list)

    def as_vector(self) -> dict[str, float]:
        return {
            "n_words": self.n_words,
            "german_ratio": self.german_ratio,
            "english_ratio": self.english_ratio,
            "capitalized_ratio": self.capitalized_ratio,
            "umlaut_substitutes": self.umlaut_substitutes,
            "article_errors": len(self.article_errors),
            "caps_ratio": self.caps_ratio,
            "exclamations": self.exclamations,
        }


def looks_german(text: str, min_ratio: float = 0.05) -> bool:
    """Grobe Spracherkennung über Stoppwörter + Umlaute (für den Sprachfilter beim Datenimport).

    Im Zweifel True: Lieber ein fremdsprachiges Beispiel behalten als deutsche Daten verwerfen.
    """
    words = [w.lower() for w in WORD_RE.findall(text)]
    if len(words) < 5:
        return True  # zu kurz für ein Urteil → nicht verwerfen
    de = sum(w in GERMAN_STOPWORDS for w in words) / len(words)
    en = sum(w in ENGLISH_STOPWORDS for w in words) / len(words)
    if _UMLAUT_RE.search(text):
        de += 0.05
    return de >= min_ratio and de >= en


def analyze_language(text: str) -> LanguageFeatures:
    feats = LanguageFeatures()
    words = WORD_RE.findall(text)
    feats.n_words = len(words)
    if feats.n_words == 0:
        return feats
    lower = [w.lower() for w in words]
    src = "rules"

    feats.german_ratio = sum(w in GERMAN_STOPWORDS for w in lower) / len(lower)
    feats.english_ratio = sum(w in ENGLISH_STOPWORDS for w in lower) / len(lower)

    # Substantive werden im Deutschen großgeschrieben → in normalem Text ~20–35 % großgeschriebene Wörter.
    # Satzanfänge mitzuzählen verfälscht etwas, ist für eine Heuristik aber ausreichend.
    feats.capitalized_ratio = sum(w[0].isupper() for w in words) / len(words)
    feats.caps_ratio = sum(len(w) > 2 and w.isupper() for w in words) / len(words)
    feats.umlaut_substitutes = sum(w in UMLAUT_SUBSTITUTES for w in lower)
    feats.exclamations = len(re.findall(r"!{2,}", text))

    for article, noun in ARTICLE_NOUN_RE.findall(text):
        gender = NOUN_GENDER.get(noun.lower())
        if gender and article.lower() in _WRONG_ARTICLES[gender]:
            feats.article_errors.append(f"{article} {noun}")

    long_enough = feats.n_words >= 20
    if long_enough and feats.english_ratio > 0.08 and feats.english_ratio > feats.german_ratio / 2:
        feats.signals.append(Signal(src, "MIXED_ENGLISH", "Auffällig viel Englisch im deutschen Text",
                                    0.2))
    if long_enough and feats.capitalized_ratio < 0.08 and feats.caps_ratio < 0.3:
        feats.signals.append(Signal(src, "NO_NOUN_CAPITALIZATION",
                                    "Substantive fast durchgehend kleingeschrieben", 0.1))
    if feats.article_errors:
        feats.signals.append(Signal(
            src, "ARTICLE_ERRORS", "Falsche Artikel bei Alltagswörtern (Hinweis auf Nicht-Muttersprachler/MT)",
            min(0.15 * len(feats.article_errors), 0.35), evidence=", ".join(feats.article_errors[:3]),
            highlights=list(feats.article_errors),
        ))
    if feats.umlaut_substitutes >= 3:
        feats.signals.append(Signal(src, "UMLAUT_SUBSTITUTES",
                                    "Umlaute durchgehend als ae/oe/ue geschrieben", 0.1))
    if feats.caps_ratio > 0.3 and feats.n_words >= 8:
        feats.signals.append(Signal(src, "SHOUTING", "Sehr viele Wörter in GROSSBUCHSTABEN", 0.1))
    return feats
