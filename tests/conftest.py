"""Gemeinsame Test-Einstellungen."""

import os

# Tests sehen nur config.yaml, nie die rechnerspezifische config.local.yaml – vor allen Imports setzen,
# weil Testmodule die Konfiguration schon beim Import laden
os.environ["SCAMGUARD_CONFIG_LOCAL"] = ""

import pytest

from scamguard.data import labels


@pytest.fixture(autouse=True)
def isolated_dataset_folder(tmp_path, monkeypatch):
    """Einstufungen landen auch im Datensatz-Ordner – in Tests nie im echten data/datensatz_fuellen_inserate/."""
    monkeypatch.setattr(labels, "DATASET_DIR", str(tmp_path / "datensatz_fuellen_inserate"))
