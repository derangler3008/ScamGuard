"""DATENSATZ-REGISTRY – für Sonderfälle mit Python-Code.

Der einfache Weg ohne Code: Dateien in die Ablageordner legen (siehe discovery.py):
  data/datensatz_fuellen_text/    CSV/JSONL/Parquet + huggingface.yaml
  data/datensatz_fuellen_bilder/  Unterordner betrug/ und serioes/ mit Bildern

Hier eintragen, wenn ein Datensatz Sonderbehandlung braucht (z. B. Texte zusammensetzen):

  1. Unten in `DATASETS` einen `DatasetSpec(...)`-Eintrag anlegen (Vorlagen kopieren).
  2. `column_map`: Welche Spalte des Datensatzes entspricht welchem Feld unseres Schemas?
       Felder: title, description, price, category, location, seller_account_age_days,
               seller_num_ratings, messages, scam_type  +  Spezialfeld "image" (Bildspalte)
  3. Label festlegen – eine der beiden Varianten:
       a) `label_column` + `label_map` (z. B. {"spam": 1, "ham": 0})
       b) `fixed_label` (der ganze Datensatz ist Betrug bzw. legitim, z. B. eine Phishing-Sammlung)
  4. `enabled=True` setzen.
  5. `scamguard data list`   → Übersicht und Status
     `scamguard data build`  → lädt alles, dedupliziert, teilt in train/val/test (data/processed/)

Passt ein Datensatz nicht in das Spalten-Schema (z. B. Text muss erst zusammengesetzt werden),
gebt eine `transform`-Funktion mit: bekommt eine Zeile (dict), gibt eine Zeile zurück (oder None
zum Überspringen). Beispiel: `_beispiel_transform` unten.

Lizenzen beachten! Für den Projektbericht pro Datensatz Quelle + Lizenz dokumentieren.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from scamguard.schema import Label


@dataclass
class DatasetSpec:
    name: str
    source: Literal["huggingface", "csv", "jsonl", "parquet", "imagefolder", "listingfolder"]
    path: str                                   # HF-ID ("org/name"), Datei oder Bilderordner
    modality: Literal["text", "image", "multimodal"] = "text"
    split: str = "train"                        # HF-Split
    subset: str | None = None                   # HF-Konfiguration/Subset
    column_map: dict[str, str] = field(default_factory=dict)  # unser Feld → Quellspalte
    label_column: str | None = None
    label_map: dict[Any, int] = field(default_factory=dict)
    fixed_label: int | None = None
    category: str | None = None                 # feste Kategorie, falls der Datensatz keine hat
    transform: Callable[[dict], dict | None] | None = None
    german_only: bool = True                    # nicht-deutsche Texte beim Import verwerfen
    max_rows: int | None = None                 # zum schnellen Testen begrenzen
    enabled: bool = False
    license: str = "unbekannt – vor Nutzung prüfen!"
    notes: str = ""
    hf_kwargs: dict[str, Any] = field(default_factory=dict)  # z. B. {"revision": "main"}
    read_kwargs: dict[str, Any] = field(default_factory=dict)  # pandas.read_csv: sep, encoding …
    base_dir: str | None = None                 # relative Bildpfade in der Datei beziehen sich hierauf


def _beispiel_transform(row: dict) -> dict | None:
    """Beispiel: Betreff + Text einer Phishing-Mail zu einer Nachricht zusammenführen."""
    text = f"{row.get('subject', '')}\n{row.get('body', '')}".strip()
    if len(text) < 20:
        return None  # zu kurz → überspringen
    return {"messages": [text], "label": row.get("label")}


DATASETS: list[DatasetSpec] = [
    # ------------------------------------------------------------------ eigene Daten
    DatasetSpec(
        name="demo_synthetisch",
        source="jsonl",
        path="data/samples/sample_listings.jsonl",
        enabled=True,
        license="eigene synthetische Beispiele (frei)",
        notes="Nur zum Testen der Pipeline – NICHT für Aussagen über Modellqualität verwenden.",
    ),
    DatasetSpec(
        name="eigene_labels",
        source="jsonl",
        path="data/raw/eigene_labels.jsonl",     # wird vom Labeling-Tab im Frontend befüllt
        enabled=True,
        german_only=False,                       # selbst eingestuft → nie per Sprachfilter verwerfen
        license="eigene Daten",
        notes="Im Frontend gescannte und von uns gelabelte Inserate.",
    ),

    # ------------------------------------------------------------------ VORLAGEN (deaktiviert)
    DatasetSpec(
        name="vorlage_eigene_csv",
        source="csv",
        path="data/raw/eigene_sammlung.csv",
        column_map={"title": "titel", "description": "text", "price": "preis", "category": "kategorie"},
        label_column="betrug",
        label_map={"ja": Label.SCAM, "nein": Label.LEGIT, 1: Label.SCAM, 0: Label.LEGIT},
        notes="Manuell gesammelte Inserate (z. B. Screenshots abgetippt, Polizei-/Verbraucherzentrale-Fälle).",
    ),
    DatasetSpec(
        name="vorlage_hf_text",
        source="huggingface",
        path="ORGANISATION/DATENSATZNAME",          # ← echte HF-ID eintragen
        split="train",
        column_map={"description": "text"},
        label_column="label",
        label_map={1: Label.SCAM, 0: Label.LEGIT},
        notes="Z. B. deutschsprachige Spam-/Betrugsnachrichten.",
    ),
    DatasetSpec(
        name="vorlage_hf_phishing_mails",
        source="huggingface",
        path="ORGANISATION/PHISHING-DATENSATZ",     # ← echte HF-ID eintragen
        transform=_beispiel_transform,
        label_column="label",
        label_map={1: Label.SCAM, 0: Label.LEGIT},
        german_only=False,
        notes="Englische Phishing-Mails: nur sinnvoll mit mehrsprachigem Modell (xlm-roberta) "
              "oder nach Übersetzung. Sonst german_only=True lassen.",
    ),
    DatasetSpec(
        name="vorlage_hf_bilder",
        source="huggingface",
        path="ORGANISATION/BILD-DATENSATZ",         # ← echte HF-ID eintragen
        modality="image",
        column_map={"image": "image", "title": "caption"},
        label_column="label",
        label_map={1: Label.SCAM, 0: Label.LEGIT},
        german_only=False,
        notes="Bildspalte wird nach data/images/<name>/ gespeichert.",
    ),
]


def get_specs(names: list[str] | None = None, include_disabled: bool = False) -> list[DatasetSpec]:
    """Datensätze aus diesem Modul plus alles aus den Ablageordnern (data/datensatz_fuellen_*)."""
    from scamguard.data.discovery import discover_specs

    specs = [*DATASETS, *discover_specs()]
    if names:
        known = {s.name: s for s in specs}
        missing = [n for n in names if n not in known]
        if missing:
            raise KeyError(f"Unbekannte Datensätze: {missing}. Verfügbar: {sorted(known)}")
        return [known[n] for n in names]
    return [s for s in specs if s.enabled or include_disabled]
