"""Lädt config.yaml und löst Pfade relativ zum Projektordner auf."""

from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# src/scamguard/config.py → Projektordner liegt zwei Ebenen über dem Paket
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@lru_cache(maxsize=4)
def load_config(path: str | None = None) -> dict[str, Any]:
    """Liest die YAML-Konfiguration. Ergebnis wird gecacht – nicht verändern, sondern kopieren."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(f"Konfiguration nicht gefunden: {cfg_path}")
    with cfg_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def with_overrides(cfg: dict, overrides: dict) -> dict:
    """Erzeugt eine Kopie der Konfiguration mit überschriebenen Werten (z. B. aus dem Frontend)."""
    return _deep_merge(cfg, overrides)


def resolve_path(relative: str | Path) -> Path:
    """Macht einen Pfad aus der Konfiguration absolut (relativ zum Projektordner)."""
    p = Path(relative)
    return p if p.is_absolute() else PROJECT_ROOT / p
