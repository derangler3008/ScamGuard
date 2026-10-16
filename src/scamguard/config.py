"""Lädt config.yaml und löst Pfade relativ zum Projektordner auf.

Rechnerspezifisches (z. B. llama.cpp auf dem Windows-PC) steht in config.local.yaml – nicht im Git, wird
über config.yaml gelegt. SCAMGUARD_CONFIG_LOCAL setzt einen anderen Pfad, leer = keine (Tests)."""

from __future__ import annotations

import copy
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# src/scamguard/config.py → Projektordner liegt zwei Ebenen über dem Paket
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
LOCAL_CONFIG_PATH = PROJECT_ROOT / "config.local.yaml"


def local_config_path() -> Path | None:
    value = os.environ.get("SCAMGUARD_CONFIG_LOCAL")
    if value is None:
        return LOCAL_CONFIG_PATH
    return Path(value) if value else None


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
    """Liest die YAML-Konfiguration (ohne Pfad: config.yaml + config.local.yaml).
    Ergebnis wird gecacht – nicht verändern, sondern kopieren."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(f"Konfiguration nicht gefunden: {cfg_path}")
    with cfg_path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    local = local_config_path() if path is None else None
    if local is not None and local.exists():
        with local.open(encoding="utf-8") as f:
            cfg = _deep_merge(cfg, yaml.safe_load(f) or {})
    return cfg


def with_overrides(cfg: dict, overrides: dict) -> dict:
    """Erzeugt eine Kopie der Konfiguration mit überschriebenen Werten (z. B. aus dem Frontend)."""
    return _deep_merge(cfg, overrides)


def resolve_path(relative: str | Path) -> Path:
    """Macht einen Pfad aus der Konfiguration absolut (relativ zum Projektordner)."""
    p = Path(relative)
    return p if p.is_absolute() else PROJECT_ROOT / p
