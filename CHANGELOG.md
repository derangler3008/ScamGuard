# Changelog

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionierung nach SemVer.

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
