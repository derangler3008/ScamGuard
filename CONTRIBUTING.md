# Contributing

Leitfaden für die Entwicklung an ScamGuard: Setup, Workflow, Konventionen. Produktüberblick und
Architektur stehen im [README](README.md), offene Aufgaben mit Akzeptanzkriterien im
[Backlog](docs/backlog.md).

## Überblick

Betrugserkennung für deutschsprachige Kleinanzeigen (Inserate und Chats). Python 3.12, Paket in
`src/scamguard/`, CLI `scamguard`.

- **Detektoren:** Regeln/Lexikon (`data/lexicons/scam_signals_de.yaml`), Textmodell (TF-IDF bzw.
  GBERT), Bildmodell (pHash/CLIP/CNN), LLM-Judge (lokal Qwen3.5-9B über MLX, optional Claude API).
  Gesamturteil per Late Fusion (`models/fusion.py`).
- **Oberflächen:** Streamlit-Web-App (`frontend/app.py`), FastAPI (`api.py`), Browser-Extension
  (`extension/`, MV3, Chromium und Firefox) mit Live-Chat-Scan im Kleinanzeigen-Postfach.
- **Daten:** Ablageordner `data/datensatz_fuellen_*` werden automatisch erkannt (`data/discovery.py`);
  schnelle Labels in `data/raw/eigene_labels.jsonl`; Labeling im Team über den Annotation-Workspace
  (`data/annotation.py`, Kategoriensystem `data/einstufung/kategorien.yaml`, Export
  `data/raw/einstufung_*.jsonl`).

## Setup

```bash
git clone https://github.com/derangler3008/ScamGuard.git && cd ScamGuard
git switch <eigener-branch>                 # z. B. Jannis oder Gabriel
python3.12 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                     # weitere Extras: README, „Quickstart“
```

`scamguard` ist nur in der aktivierten `.venv` verfügbar. Qwen (`scamguard start`) läuft nur auf Macs mit
Apple Silicon; unter Windows/Linux LM Studio oder Ollama nutzen (README, Abschnitt „LLM“).

## Checks

```bash
ruff check src tests frontend scripts   # muss sauber sein
pytest -m "not e2e"                     # Unit- und Integrationstests (Sekunden)
pytest -m e2e                           # Browser-Tests (Playwright/Chromium, Selenium/Firefox)
```

## Workflow

- **Branches:** Jede Person arbeitet auf dem eigenen Branch (`Jannis`, `Gabriel`, …). Änderungen gehen
  per **Pull Request** nach `main`. Vor dem Start einer Aufgabe `git pull origin main`.
  Kein Force-Push, nicht direkt auf fremde Branches pushen.
- **Commits:** klein und thematisch, Beschreibung auf Deutsch im Imperativ bzw. als Ergebnis
  („Einstufung: Konflikte entscheiden“), Versionssprünge als `ScamGuard vX.Y.Z: …`.
- **Pull Request – Checkliste:**
  - Lint und Tests grün, neue Funktionen mit Tests
  - keine echten Personendaten, keine Datendateien im Diff
  - bei Features: Version in `pyproject.toml` und `src/scamguard/__init__.py` (bei Extension-Änderungen
    auch `extension/manifest.json`) und Eintrag in `CHANGELOG.md`
- **Nichts ersatzlos löschen** (Dateien, Funktionen, Daten), ohne es vorher im Team abzustimmen:
  was, warum, was daran hängt.

## Konventionen

- **Sprache:** Oberfläche, Doku und Kommentare auf Deutsch, im Stil des vorhandenen Codes.
  **Keine Emojis** in Web-App und Extension; Icons als SVG/CSS.
- **Keine Daten im Git:** `data/raw/`, `data/processed/`, `data/finetune/`, `models/`, der
  Annotation-Arbeitsordner und die Inhalte der Ablageordner sind ignoriert. Keine echten Namen,
  Telefonnummern, Adressen oder Mailadressen in Code, Tests oder Doku (Platzhalter wie
  `anonym@gmail.com`); gesammelte Texte über `data/redact.py` anonymisieren.
- **Kein Scraping gesperrter Seiten:** Kleinanzeigen (Nutzungsbedingungen), Reddit (robots.txt sperrt
  alle Bots), gutefrage.net (Bot-Sperre) u. Ä. nicht automatisiert auslesen, Bot-Sperren nicht umgehen.
  Collector (`data/collect_*.py`) prüfen robots.txt und drosseln Anfragen – neue Quellen ebenso.
- **Lexikon** (`scam_signals_de.yaml`): Jedes neue Muster braucht einen Test mit Treffer und harmlose
  Gegenbeispiele, die nicht anschlagen dürfen (`tests/test_chat_and_forums.py`). Neue Gruppen im
  Kategoriensystem einem Warnsignal zuordnen (`tests/test_einstufung.py` prüft das).
- **Labels werden manuell vergeben:** Die Spalte `betrug` der Prüfliste `gesammelt_foren.csv` und die
  Einstufungen im Annotation-Workspace stammen ausschließlich von Menschen. Keine automatisch erzeugten
  Labels im Arbeitsordner; Tests nutzen `tmp_path`.
- **Kategoriensystem** `data/einstufung/kategorien.yaml`: Änderungen nur nach Abstimmung im Team, dann
  `version` erhöhen und CHANGELOG-Eintrag.
- **Saubere Evaluation:** Das Testset aus `scamguard data build` nie zum Tunen nutzen; synthetische
  Daten nur ins Training. Einseitige Labels vermeiden – `retrain` warnt.
- **Extension:** Seiteninhalte nur per `textContent` einfügen, nie als HTML; Server-Anfragen nur an
  127.0.0.1 mit Header `X-ScamGuard-Client`.

## Befehle

| Befehl | Zweck |
|---|---|
| `scamguard ui` | Web-App: Inserat prüfen und labeln, Annotation-Workspace, Daten und Training |
| `scamguard start` | Qwen und API für die Extension |
| `scamguard data list` / `data build` | Datensätze anzeigen / Splits bauen |
| `scamguard data sammeln [--quellen foren]` | öffentliche Warnungen und Foren-Erfahrungen holen |
| `scamguard data phrasen [--hf]` | Kandidaten für neue Lexikon-Muster |
| `scamguard data ordner` | bisherige eigene Labels in die Datensatz-Ordner übernehmen |
| `scamguard einstufen holen\|bericht\|export` | Annotation-Workspace per CLI (`docs/einstufung.md`) |
| `scamguard retrain` | alles neu einlesen, trainieren, kurz auswerten |
| `scamguard evaluate [--llm --max 100]` | Kennzahlen pro Modell und Quelle |
| `scamguard llm-daten` | Feintuning-Daten für Qwen (`docs/llm_feintuning.md`) |
