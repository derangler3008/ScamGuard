"""Abgleich mit dem Betrugsmaschen-Lexikon (data/lexicons/scam_signals_de.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from scamguard.schema import Signal


@dataclass(frozen=True)
class PhraseGroup:
    code: str
    message: str
    weight: float
    hard: bool
    patterns: tuple[re.Pattern, ...]


@lru_cache(maxsize=4)
def load_lexicon(path: str) -> tuple[PhraseGroup, ...]:
    with Path(path).open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    groups = []
    for code, g in (raw.get("groups") or {}).items():
        compiled = tuple(re.compile(p, re.IGNORECASE | re.MULTILINE) for p in g.get("patterns", []))
        groups.append(PhraseGroup(
            code=code.upper(),
            message=g.get("message", code),
            weight=float(g.get("weight", 0.3)),
            hard=bool(g.get("hard", False)),
            patterns=compiled,
        ))
    return tuple(groups)


def match_phrases(text: str, lexicon_path: str) -> list[Signal]:
    """Ein Signal pro Gruppe (nicht pro Treffer), damit lange Texte nicht überbewertet werden."""
    signals = []
    for group in load_lexicon(lexicon_path):
        for pattern in group.patterns:
            m = pattern.search(text)
            if m:
                signals.append(Signal("rules", group.code, group.message, group.weight,
                                      evidence=m.group(0)[:80], hard=group.hard))
                break
    return signals
