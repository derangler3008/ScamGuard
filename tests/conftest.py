"""Gemeinsame Test-Einstellungen."""

import pytest

from scamguard.data import labels


@pytest.fixture(autouse=True)
def isolated_dataset_folder(tmp_path, monkeypatch):
    """Einstufungen landen auch im Datensatz-Ordner – in Tests nie im echten data/datensatz_fuellen_inserate/."""
    monkeypatch.setattr(labels, "DATASET_DIR", str(tmp_path / "datensatz_fuellen_inserate"))
