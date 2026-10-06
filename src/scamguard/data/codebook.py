"""Kategoriensystem (Codebuch) für die Einstufung: data/einstufung/kategorien.yaml.

Eine Quelle für alle, die Kategorien brauchen: Web-App (Auswahllisten, Leitfaden), Konsens und
Export (annotation.py), Übereinstimmung (agreement.py) und Feintuning (Masche → Antwortformat von
Qwen). Wer eine Kategorie ändert, ändert sie nur dort.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import yaml

from scamguard.config import resolve_path

CODEBOOK_FILE = "data/einstufung/kategorien.yaml"
URTEILE = ("betrug", "serioes", "unklar")
LABEL_OF_URTEIL = {"betrug": 1, "serioes": 0}


@dataclass(frozen=True)
class Codebook:
    version: int
    regeln: tuple[str, ...]
    urteil: dict[str, dict]
    sicherheit: dict[str, str]
    maschen: dict[str, dict]
    rolle: dict[str, str]
    sprache: dict[str, str]
    signale: dict[str, dict]
    bildarten: dict[str, dict]
    verdaechtig: dict[str, str]

    def name(self, group: str, key: str | None) -> str:
        """Anzeigename einer Kategorie (z. B. name("maschen", "vorkasse")); unbekannt → Schlüssel."""
        if key is None:
            return "–"
        entry = getattr(self, group).get(key)
        return entry.get("name", key) if isinstance(entry, dict) else key

    def llm_type(self, masche: str | None) -> str | None:
        """Masche → Kategorie im Antwortformat des LLM-Judge (SCAM_TYPES)."""
        return (self.maschen.get(masche) or {}).get("llm") if masche else None

    @property
    def signal_by_rule(self) -> dict[str, str]:
        """Code der Regel-Erkennung (z. B. PRESSURE_URGENCY) → Warnsignal des Codebuchs."""
        return {rule: signal for signal, entry in self.signale.items() for rule in entry.get("regeln", [])}


@lru_cache(maxsize=2)
def load_codebook(path: str = CODEBOOK_FILE) -> Codebook:
    with resolve_path(path).open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    images = raw.get("bilder") or {}
    return Codebook(
        version=int(raw.get("version", 1)),
        regeln=tuple(raw.get("regeln") or ()),
        urteil=raw.get("urteil") or {},
        sicherheit=raw.get("sicherheit") or {},
        maschen=raw.get("maschen") or {},
        rolle=raw.get("rolle") or {},
        sprache=raw.get("sprache") or {},
        signale=raw.get("signale") or {},
        bildarten=images.get("art") or {},
        verdaechtig=images.get("verdaechtig") or {},
    )
