"""Merkmalstabelle für Welle 2 (AutoML): ein Inserat → eine Zeile mit festen Spalten.

Spalten:
- die numerischen Regelmerkmale (`extract_features(...).vector`: Links, IBAN, Sprache, Preis …),
- je Warnsignal dessen Stärke (`signal_<code>`, 0 = nicht gefunden),
- Anbieterprofil und Kategorie (`category_<name>`),
- Text als verdichtete TF-IDF-Komponenten (`text_komponente_<n>`, SVD).

Welche Spalten es gibt, legt `FeatureBuilder.fit` auf dem Trainingssplit fest (auch die Text-
komponenten lernt er nur dort → nichts aus Val/Test fließt ein). Spalten ohne Schwankung fallen weg.
"""

from __future__ import annotations

from scamguard.features import extract_features
from scamguard.schema import CATEGORIES, CATEGORY_NAMES, Listing

MISSING = -1.0  # unbekannte Werte (z. B. Kontoalter nicht angegeben); Baumverfahren trennen das sauber ab

FEATURE_LABELS = {
    "n_urls": "Anzahl Links", "n_emails": "Mailadressen im Text", "n_phones": "Telefonnummern im Text",
    "n_ibans": "IBANs im Text", "has_lookalike_domain": "nachgeahmte Domain", "has_shortener": "Kurzlink",
    "has_foreign_phone": "ausländische Telefonnummer", "has_foreign_iban": "ausländische IBAN",
    "n_words": "Textlänge (Wörter)", "german_ratio": "Anteil deutscher Wörter",
    "english_ratio": "Anteil englischer Wörter", "capitalized_ratio": "Anteil großgeschriebener Wörter",
    "umlaut_substitutes": "Umlaut-Ersatz (ae/oe/ue)", "article_errors": "Artikelfehler",
    "caps_ratio": "Anteil Großbuchstaben", "exclamations": "Ausrufezeichen", "price": "Preis",
    "price_ratio_to_min": "Preis im Verhältnis zum Marktpreis", "price_known_item": "Marktpreis bekannt",
    "price_missing": "kein Preis angegeben", "seller_commercial": "gewerblicher Anbieter",
    "seller_private": "privater Anbieter", "seller_rated": "Anbieter hat Bewertungen",
    "seller_top_rating": "TOP-Zufriedenheit", "seller_n_badges": "Anzahl Abzeichen",
    "seller_account_age_days": "Kontoalter (Tage)", "seller_num_ads": "Anzeigen des Anbieters",
    "seller_num_ratings": "Bewertungen des Anbieters", "seller_legal_form": "Rechtsform im Anbieternamen",
    "n_phrase_groups": "Maschen-Formulierungen", "n_images": "Anzahl Fotos", "chat_only": "reiner Chat",
    "n_messages": "Chat-Nachrichten", "text_length": "Textlänge (Zeichen)",
}


def _num(value) -> float:
    return MISSING if value is None else float(value)


def base_row(listing: Listing, cfg: dict) -> tuple[dict[str, float], dict[str, str]]:
    """Merkmale ohne Text-Komponenten; dazu die Meldung je gefundenem Warnsignal (für Erklärungen)."""
    feats = extract_features(listing, cfg)
    row = dict(feats.vector)
    messages: dict[str, str] = {}
    for s in feats.signals:
        if s.info:
            continue
        key = f"signal_{s.code.lower()}"
        row[key] = max(row.get(key, 0.0), s.weight)
        messages.setdefault(key, s.message)
    row.update(
        seller_account_age_days=_num(listing.seller_account_age_days),
        seller_num_ads=_num(listing.seller_num_ads),
        seller_num_ratings=_num(listing.seller_num_ratings),
        seller_private=float(listing.seller_type == "privat"),
        seller_legal_form=float(bool(listing.seller_legal_form)),
        price_missing=float(listing.price is None),
        chat_only=float(listing.chat_only),
        n_messages=float(len(listing.messages)),
        text_length=float(len(listing.full_text)),
    )
    for cat in CATEGORIES:
        row[f"category_{cat}"] = float(listing.category == cat)
    return row, messages


class TextComponents:
    """TF-IDF (Wörter und Wortpaare) → wenige SVD-Komponenten: Wortwahl als Zahlen für Baumverfahren."""

    def __init__(self, n_components: int = 20):
        self.n_components = n_components

    def fit(self, texts: list[str]) -> TextComponents | None:
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer

        for min_df in (2, 1):  # bei sehr wenigen Texten gibt es kein Wort in zwei Dokumenten
            self.tfidf = TfidfVectorizer(ngram_range=(1, 2), min_df=min_df, max_features=20000,
                                         sublinear_tf=True)
            try:
                x = self.tfidf.fit_transform(texts)
                break
            except ValueError:
                continue
        else:
            return None
        k = min(self.n_components, x.shape[1] - 1, len(texts) - 1)
        if k < 1:
            return None
        self.svd = TruncatedSVD(k, random_state=42).fit(x)
        return self

    @property
    def names(self) -> list[str]:
        return [f"text_komponente_{i + 1}" for i in range(self.svd.n_components)]

    def transform(self, texts: list[str]):
        return self.svd.transform(self.tfidf.transform(texts))


class FeatureBuilder:
    def __init__(self, text_components: int = 20):
        self.text_components = text_components
        self.columns: list[str] = []
        self.text: TextComponents | None = None

    def _frame(self, listings: list[Listing], cfg: dict):
        import pandas as pd

        rows = [base_row(listing, cfg)[0] for listing in listings]
        df = pd.DataFrame(rows).fillna(0.0)  # Warnsignal nicht gefunden → 0
        if self.text is not None:
            comps = self.text.transform([listing.full_text for listing in listings])
            df = pd.concat([df, pd.DataFrame(comps, columns=self.text.names, index=df.index)], axis=1)
        return df

    def fit(self, listings: list[Listing], cfg: dict):
        texts = [listing.full_text for listing in listings]
        self.text = TextComponents(self.text_components).fit(texts) if self.text_components else None
        df = self._frame(listings, cfg)
        self.columns = [c for c in df.columns if df[c].nunique() > 1]
        return df[self.columns]

    def transform(self, listings: list[Listing], cfg: dict):
        return self._frame(listings, cfg).reindex(columns=self.columns, fill_value=0.0)


def feature_label(name: str, signal_messages: dict[str, str] | None = None) -> str:
    """Spaltenname → lesbare Bezeichnung für die Erklärung im Panel."""
    if name in FEATURE_LABELS:
        return FEATURE_LABELS[name]
    if name.startswith("signal_"):
        message = (signal_messages or {}).get(name)
        return f"Warnsignal „{message}“" if message else f"Warnsignal {name[7:].upper()}"
    if name.startswith("category_"):
        return f"Kategorie {CATEGORY_NAMES.get(name[9:], name[9:])}"
    if name.startswith("text_komponente_"):
        return f"Wortwahl (Textmuster {name.rsplit('_', 1)[1]})"
    return name


def format_value(name: str, value: float) -> str | None:
    if name.startswith("text_komponente_"):
        return None  # abstrakte Zahl – für Menschen nicht aussagekräftig
    if value == MISSING and name.startswith("seller_"):
        return "unbekannt"
    if name.startswith(("signal_", "category_", "has_")) or name in {
            "seller_commercial", "seller_private", "seller_rated", "seller_top_rating", "seller_legal_form",
            "price_known_item", "price_missing", "chat_only"}:
        return "ja" if value > 0 else "nein"
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}"
