# ScamGuard – Hinweise für Claude Code

Claude Code liest diese Datei automatisch. Sie gilt für alle drei im Team. Fertige Prompts zum
Kopieren stehen in `docs/mit_claude_arbeiten.md`.

## Projekt

DHBW-Mannheim-KI-Projekt (Gruppe aus drei Personen): **Betrugserkennung für deutschsprachige
Kleinanzeigen** (Inserate und Chats). Python 3.12, Paket in `src/scamguard/`, CLI `scamguard`.

- **Detektoren:** Regeln/Lexikon (`data/lexicons/scam_signals_de.yaml`), Textmodell (TF-IDF bzw.
  GBERT), Bildmodell (pHash/CLIP/CNN), LLM-Judge (lokal Qwen3.5-9B über MLX, optional Claude).
  Gesamturteil per Late Fusion (`models/fusion.py`).
- **Oberflächen:** Streamlit-Web-App (`frontend/app.py`), FastAPI (`api.py`), Browser-Extension
  (`extension/`, MV3, Chromium und Firefox) mit Live-Chat-Scan im Kleinanzeigen-Postfach.
- **Daten:** Ablageordner `data/datensatz_fuellen_*` werden automatisch erkannt
  (`data/discovery.py`); eigene Einstufungen in `data/raw/eigene_labels.jsonl`.
- Überblick und Begründungen: `README.md`, Änderungen: `CHANGELOG.md`.

## Einrichten und prüfen

```bash
python3.12 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                 # weitere Extras siehe README („Schnellstart“)
ruff check src tests frontend scripts   # muss sauber sein
pytest -m "not e2e"                     # schnelle Tests (Sekunden)
pytest -m e2e                           # Browser-Tests (Playwright/Chromium, Selenium/Firefox)
```

`scamguard` gibt es nur in der aktivierten `.venv`. Qwen (`scamguard start`) läuft nur auf Macs mit
Apple-Chip; unter Windows/Linux LM Studio oder Ollama nutzen (README, Abschnitt „KI-Analyse“).

## Arbeitsweise im Team

- **Branches:** Jede Person arbeitet auf dem eigenen Branch (`Jannis`, `Gabriel`; die dritte Person
  auf `main` oder einem eigenen Branch) und bringt Änderungen per **Pull Request** nach `main`.
  Vorher `main` hineinholen (`git pull origin main`). Kein Force-Push, nicht direkt auf fremde
  Branches pushen.
- **Commits** selbst machen und verständlich auf Deutsch beschreiben: Das ist die sichtbare
  Eigenleistung. KI-Unterstützung gehört in die Hilfsmittelerklärung des Berichts.
- **Nichts ersatzlos löschen** (Dateien, Funktionen, Daten), ohne es vorher im Team bzw. beim
  Menschen am Rechner anzusprechen: was, warum, was daran hängt.
- Nach jeder Änderung: Lint und Tests grün, bei neuen Funktionen Tests ergänzen, bei Features
  Version (`pyproject.toml`, `src/scamguard/__init__.py`, bei Extension auch `manifest.json`) und
  `CHANGELOG.md` pflegen.

## Regeln für Code und Daten

- **Sprache:** Oberfläche, Doku und Kommentare auf Deutsch, im Stil des vorhandenen Codes.
  **Keine Emojis** in Web-App und Extension (Team-Entscheidung); Icons als SVG/CSS.
- **Keine Daten ins Git:** `data/raw/`, `data/processed/`, `data/finetune/`, `models/` und die
  Inhalte der Ablageordner sind ignoriert – so lassen. Keine echten Namen, Telefonnummern,
  Adressen oder Mailadressen in Code, Tests oder Doku (Platzhalter wie `anonym@gmail.com`);
  gesammelte Texte über `data/redact.py` anonymisieren.
- **Kein Scraping gesperrter Seiten:** Kleinanzeigen (Nutzungsbedingungen), Reddit (robots.txt
  sperrt alle Bots), gutefrage.net (Bot-Sperre) und Ähnliches nicht automatisiert auslesen,
  Bot-Sperren nicht umgehen. Sammler (`data/collect_*.py`) prüfen robots.txt und warten zwischen
  Anfragen – bei neuen Quellen genauso.
- **Lexikon** (`scam_signals_de.yaml`): Jedes neue Muster braucht einen Test mit passendem Satz und
  harmlose Gegenbeispiele, die NICHT anschlagen dürfen (`tests/test_chat_and_forums.py`).
- **Fair auswerten:** Das Testset aus `scamguard data build` nie zum Tunen nutzen; synthetische
  Daten nur ins Training. Einseitige Labels (fast nur Betrug) vermeiden – `retrain` warnt.
- **Prüfliste** `gesammelt_foren.csv`: Die Spalte `betrug` füllen Menschen, nicht die KI.
- Extension: Inhalte der Seite nur per `textContent` einfügen, nie als HTML; Server-Anfragen nur an
  127.0.0.1 mit Header `X-ScamGuard-Client`.

## Wichtige Befehle

| Befehl | Zweck |
|---|---|
| `scamguard ui` | Web-App: Inserat hochladen und einstufen, Daten und Training |
| `scamguard start` | Qwen und API für die Extension |
| `scamguard data list` / `data build` | Datensätze anzeigen / Splits bauen |
| `scamguard data sammeln [--quellen foren]` | öffentliche Warnungen und Foren-Erfahrungen holen |
| `scamguard data phrasen [--hf]` | Kandidaten für neue Lexikon-Muster |
| `scamguard retrain` | alles neu einlesen, trainieren, kurz auswerten |
| `scamguard evaluate [--llm --max 100]` | Kennzahlen pro Modell und Quelle |
| `scamguard llm-daten` | Feintuning-Daten für Qwen (`docs/llm_feintuning.md`) |
