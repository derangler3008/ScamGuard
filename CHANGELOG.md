# Changelog

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionierung nach SemVer.

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
