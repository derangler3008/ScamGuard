# Changelog

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionierung nach SemVer.

## [0.4.0] – 2026-10-02

### Hinzugefügt
- **Ablageordner ohne Code:** `data/datensatz_fuellen_text/` (CSV/TSV/JSONL/Parquet, deutsche
  Excel-CSV, Spalten- und Label-Erkennung, `huggingface.yaml`) und `data/datensatz_fuellen_bilder/`
  (Unterordner `betrug/` und `serioes/` = Label).
- **Selbst einstufen in der Extension:** Buttons *Betrug*/*Seriös* im Panel → `POST /label` →
  `data/raw/eigene_labels.jsonl`; erneutes Einstufen ersetzt das alte Label. Popup zeigt die Anzahl.
  Streamlit-Labeln nutzt denselben Speicherweg.
- `scamguard retrain [--bilder] [--transformer]`: alles neu einlesen, trainieren, auswerten.
- PyTorch + CLIP installiert: Bildhinweise (Stockfoto, Screenshot, falsche Kategorie) laufen.

### Geändert / optimiert (Senior-Review)
- CLIP kodiert jedes Bild nur einmal, Vergleichstexte einmal beim Laden, Rechnung auf der
  Apple-GPU (MPS): ~8× schneller pro Bild, Ergebnisse identisch.
- Stufe 2 der Extension nutzt die Ergebnisse aus Stufe 1 wieder (Server-Zwischenspeicher) –
  nur das LLM rechnet neu; der Service Worker lädt Inseratsbilder nur einmal.
- Textmodell: Text wird einmal statt zweimal vektorisiert, Merkmalsnamen zwischengespeichert,
  neues Modell wird ohne Server-Neustart geladen, Speichern atomar.
- Bildhinweise ohne trainiertes CNN liefern keinen eigenen Score mehr (sonst hätte „nichts
  Auffälliges im Bild“ den Gesamtscore von Betrugsinseraten gesenkt).
- Reine Bild-Datensätze fließen nicht ins Texttraining; Inserate ohne Text bekommen kein Text-Urteil.
- Parquet-Spalten werden aus dem Schema gelesen statt aus der ganzen Datei.
- `append_jsonl` entfernt (durch `labels.save_label` ersetzt).

## [0.3.1] – 2026-10-01

### Hinzugefügt
- `scamguard start`: startet lokales LLM (Qwen) und API in einem Terminal, `Ctrl+C` beendet beides
  (`--ohne-llm` nur API).

### Geändert
- KI-Analyse etwa doppelt so schnell (MacBook M4: ca. 6 s unauffällig, 16–20 s Betrug statt
  30–40 s): höchstens 4 Warnsignale, kurze Begründungen und Zitate.
- Qwen-Server startet bei vorhandenem Modell ohne Netzabfrage (`HF_HUB_OFFLINE`) und mit dem
  aktuellen Aufruf `python -m mlx_lm server`.
- Alle Startanweisungen (README, Popup, Panel, Fehlermeldungen) nennen `scamguard start`;
  README erklärt „command not found“ (virtuelle Umgebung aktivieren).

## [0.3.0] – 2026-10-01

### Hinzugefügt
- **Lokales LLM als KI-Analyse:** Qwen3.5-9B (MLX, 4-Bit mixed precision) läuft auf dem Mac über
  `scamguard llm-server`; kostenlos, offline, Inseratsdaten bleiben auf dem Rechner. Neuer
  `llm.provider: local` spricht jeden OpenAI-kompatiblen Server an (MLX, Ollama, LM Studio,
  llama.cpp) – Claude bleibt als `provider: anthropic` verfügbar.
- Robuste JSON-Auswertung für lokale Modelle (Denk-Blöcke, Code-Zäune, Prozentangaben,
  unbekannte Werte), Wiederholung bei ungültiger Antwort; Denkmodus von Qwen3.5 abgeschaltet.
- Extension: **zweistufige Prüfung** – sofortiges Ergebnis aus Regeln/Text/Bildern, danach ergänzt
  die KI-Analyse Einschätzung, Score und Fundstellen. Popup-Einstellung „KI-Analyse“:
  Automatisch (nur lokales Modell) / Immer / Aus, mit Anzeige des Server-Modells.
- API: `use_llm=auto` (LLM nur, wenn lokal), `/health` meldet LLM-Provider und Modell.

### Sicherheit
- Der lokale MLX-Server wird ohne CORS-Freigabe für fremde Webseiten gestartet
  (Standard von mlx_lm wäre `*`).

## [0.2.0] – 2026-10-01

### Hinzugefügt
- **Browser-Extension für Chromium und Firefox** (Manifest V3, `extension/`): markiert
  Betrugsindikatoren direkt auf Kleinanzeigen-Seiten (Text, Preis, Verkäufer, Bilder), zeigt den
  Risiko-Score als Panel und Toolbar-Badge; „Diese Seite prüfen“ für beliebige Seiten und
  Kontextmenü „Markierten Text prüfen“ für Chats/E-Mails; Popup mit Serverstatus und Einstellungen.
- Signale liefern `highlights` (exakter Originaltext) und `target` (`price`, `seller`, `image:<n>`).
- API normalisiert Rohwerte von Webseiten (Preis „120 € VB“, Brotkrumenpfad als Kategorie).
- Browser-Ende-zu-Ende-Tests: Playwright/Chromium und Selenium/Firefox (`pytest -m e2e`),
  nachgebaute Testseiten, optionale Screenshots.

### Geändert
- `/scan` verlangt den Header `X-ScamGuard-Client` und weist fremde `Origin`s ab
  (Schutz gegen Cross-Site-Anfragen auf den lokalen Server).
- Lexikon-Muster greifen auch über Zeilenumbrüche („Freunde und⏎Familie“).
- Kategorie-Erkennung wertet Brotkrumenpfade von hinten aus (Haushaltsgeräte unter Elektronik,
  Fahrräder unter „Auto, Rad & Boot“).
- Textmodell-Erklärungen nur noch ab 4 Zeichen; Bildsignale nennen „Bild n“ statt Dateinamen.

## [0.1.0] – 2026-10-01

### Hinzugefügt
- Einheitliches Datenschema (`Listing`, `Signal`, `ModelResult`, `ScanResult`).
- Datensatz-Registry mit Vorlagen für Hugging Face, CSV, JSONL und Parquet; Loader mit
  Spalten-Mapping, Label-Mapping, Transform-Funktionen, Sprachfilter und Bildspeicherung.
- `data build`: Deduplizierung über alle Quellen, stratifizierter Train/Val/Test-Split, Statistik.
- Regel-Detektor: Betrugsphrasen-Lexikon (YAML), Lookalike-/Homoglyph-Domains, URL-Shortener,
  verdächtige TLDs, Punycode, E-Mail-Imitation, IBAN (mit Prüfsumme), ausländische
  Telefonnummern, Preisplausibilität, Kontoalter, Sprach-Heuristiken (Artikelfehler,
  Umlaut-Ersatz, Englisch-Anteil).
- Textmodelle: TF-IDF-Baseline mit Erklärung; GBERT-Feintuning mit Klassengewichtung.
- Bildmodelle: pHash-Abgleich, CLIP-Zero-Shot (Stockfoto/Screenshot/Kategorie), CNN-Feintuning.
- LLM-Judge (Claude) mit strukturierter JSON-Ausgabe, server-seitigem Fallback und Datensparsamkeit.
- Late Fusion mit Mindestscore bei harten Signalen.
- Streamlit-Frontend (Prüfen + Labeln), FastAPI-REST-API, CLI, 31 Tests.
