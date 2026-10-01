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


def whitespace_tolerant(pattern: str) -> str:
    """Leerzeichen im Muster passen auch auf Zeilenumbrüche/Mehrfach-Leerraum.

    Verkäufer brechen Zeilen beliebig um („Freunde und⏎Familie“). Damit das Lexikon lesbar
    bleibt, wird beim Laden umgeschrieben: " ?" → \\s*, " " → \\s+, in Zeichenklassen " " → \\s.
    """
    out, in_class, i = [], False, 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":  # Escape-Sequenz unverändert übernehmen
            out.append(pattern[i:i + 2])
            i += 2
            continue
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        if ch == " ":
            if in_class:
                out.append(r"\s")
            elif pattern[i + 1:i + 2] == "?":
                out.append(r"\s*")
                i += 1
            else:
                out.append(r"\s+")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


@lru_cache(maxsize=4)
def load_lexicon(path: str) -> tuple[PhraseGroup, ...]:
    with Path(path).open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    groups = []
    for code, g in (raw.get("groups") or {}).items():
        compiled = tuple(re.compile(whitespace_tolerant(p), re.IGNORECASE | re.MULTILINE | re.DOTALL)
                         for p in g.get("patterns", []))
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
                                      evidence=m.group(0)[:80], hard=group.hard,
                                      highlights=[m.group(0)]))
                break
    return signals
