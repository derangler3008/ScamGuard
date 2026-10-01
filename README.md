# ScamGuard – Betrugserkennung für Kleinanzeigen

KI-Projekt an der DHBW Mannheim: Erkennung von Betrugsmaschen in **deutschsprachigen**
Kleinanzeigen (DE/AT/CH), mit Fokus auf Elektronik, Haushaltsgeräte und Autos.
Ein Inserat (Text, Preis, Bilder, Chatverlauf) wird hochgeladen und von mehreren
unabhängigen Detektoren bewertet. Das Ergebnis ist ein erklärbarer Risiko-Score.

> **Status:** erster Entwurf (v0.1.0). Die Pipeline läuft Ende-zu-Ende, die Modelle sind
> aber nur auf 24 synthetischen Demo-Inseraten trainiert. Aussagekräftig wird es erst mit
> euren echten Datensätzen.

---

## Architektur

```
                 ┌──────────────────── Inserat ────────────────────┐
                 │ Titel · Beschreibung · Preis · Kategorie · Chat │
                 │ Kontoalter · Bewertungen · Bilder               │
                 └──────────────────────┬──────────────────────────┘
       ┌──────────────────┬─────────────┴─────┬──────────────────────┐
       ▼                  ▼                   ▼                      ▼
 ┌───────────┐   ┌────────────────┐   ┌───────────────┐   ┌────────────────────┐
 │  Regeln   │   │   Textmodell   │   │  Bildmodell   │   │ LLM-Judge (opt.)   │
 │ Lexikon,  │   │ TF-IDF-Baseline│   │ pHash, CLIP   │   │ Claude, strukt.    │
 │ URL/Mail/ │   │ oder GBERT     │   │ Zero-Shot,    │   │ JSON-Ausgabe       │
 │ IBAN,Preis│   │ (feingetunt)   │   │ CNN (Effic.)  │   │                    │
 │ Sprache   │   │                │   │               │   │                    │
 └─────┬─────┘   └───────┬────────┘   └──────┬────────┘   └─────────┬──────────┘
       └─────────────────┴─────────┬─────────┴──────────────────────┘
                                   ▼
                     ┌───────────────────────────┐
                     │ Late Fusion (gewichtet)   │  harte Signale (z. B. Fake-
                     │ + Mindestscore bei harten │  PayPal-Domain) können nicht
                     │   Signalen                │  „weggemittelt“ werden
                     └─────────────┬─────────────┘
                                   ▼
               Score 0–1 · „unauffällig / verdächtig / hohes Risiko“
               + Liste erklärbarer Warnsignale mit Fundstelle
```

| Detektor | Erkennt z. B. | Training nötig? | Hardware |
|---|---|---|---|
| **Regeln** (`features/`) | Fake-Zahlungslinks, Lookalike-Domains (`paypa1-…`), ausländische IBAN/Telefonnummern, „Freunde & Familie“, Spediteur-/Treuhand-Geschichten, Ausweis-Forderungen, Dumpingpreise, falsche Artikel („der Auto“) | nein | CPU |
| **Text-Baseline** | Wort- und Zeichen-n-Gramme, auch Tippfehler/gebrochenes Deutsch | ja, Sekunden | CPU |
| **Text-Transformer** | Kontext, Formulierungsmuster (deutsches BERT `deepset/gbert-base`) | ja | GPU empfohlen |
| **Bild: pHash** | Wiederverwendete Fake-/Stockfotos | nein (Hash-Liste pflegen) | CPU |
| **Bild: CLIP** | Stockfoto, Screenshot, Bild passt nicht zur Kategorie | nein (Zero-Shot) | CPU ok |
| **Bild: CNN** | Muster in Betrugsbildern (EfficientNet, Transfer Learning) | ja | GPU empfohlen |
| **LLM-Judge** | Maschen-Geschichten, maschinell übersetzte Sprache, Gesamtbild | nein | API (kostet) |

---

## Schnellstart

Voraussetzung: **Python ≥ 3.10**. Das vorinstallierte macOS-`python3` ist 3.9 und damit zu
alt. Empfohlen ist 3.12, weil PyTorch und transformers dafür am stabilsten sind.

```bash
brew install python@3.12
cd ScamGuard
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"            # Kern: Regeln, Baseline, Frontend, API, Tests
```

Optionale Pakete je nach Aufgabe:

```bash
pip install -e ".[data]"     # Hugging-Face-Datensätze laden
pip install -e ".[text]"     # GBERT feintunen (torch, transformers)
pip install -e ".[vision]"   # Bildmodelle (torch, torchvision, CLIP, imagehash)
pip install -e ".[llm]"      # Claude als LLM-Judge
pip install -e ".[all]"      # alles
```

Einmal durchspielen:

```bash
scamguard data list                  # welche Datensätze sind registriert?
scamguard data build                 # → data/processed/{train,val,test}.jsonl
scamguard train text-baseline        # Sekunden
scamguard evaluate                   # Precision/Recall/F1/AUC pro Modell
scamguard scan examples/inserat_beispiel.json
scamguard ui                         # Frontend → http://127.0.0.1:8501
scamguard api                        # REST-API → http://127.0.0.1:8000/docs
pytest                               # Tests
```

Frontend und API sind standardmäßig **nur auf dem eigenen Rechner** erreichbar. Für eine
Live-Demo im Kurs: `scamguard ui --public`.

---

## Trainingsdaten hinzufügen (der „Space“ für eure Datensätze)

**Alles läuft über eine Datei: [`src/scamguard/data/registry.py`](src/scamguard/data/registry.py).**
Dort steht pro Datensatz ein `DatasetSpec`-Eintrag. Vorlagen für CSV, Hugging-Face-Text,
Phishing-Mails und Bild-Datensätze sind schon drin, nur deaktiviert.

```python
DatasetSpec(
    name="hf_mein_datensatz",
    source="huggingface",                  # oder "csv", "jsonl", "parquet"
    path="organisation/datensatz-name",    # Hugging-Face-ID
    split="train",
    column_map={"description": "text"},   # unser Feld → Spalte im Datensatz
    label_column="label",
    label_map={1: Label.SCAM, 0: Label.LEGIT},
    enabled=True,
    license="CC-BY-4.0",
    notes="Wofür ist der Datensatz gut?",
)
```

- **`column_map`** übersetzt fremde Spaltennamen in unser Schema (`schema.Listing`).
  Das Spezialfeld `"image"` übernimmt Bildspalten und speichert sie nach `data/images/`.
- **Label**: entweder `label_column` + `label_map`, oder `fixed_label=Label.SCAM`, wenn ein
  Datensatz *nur* Betrugsfälle enthält (z. B. eine Phishing-Sammlung). Dann braucht ihr
  zwingend auch legitime Beispiele aus einer anderen Quelle.
- **`transform`**: Python-Funktion für alles, was nicht 1:1 passt (Spalten zusammenführen,
  filtern). Beispiel: `_beispiel_transform` in der Registry.
- **`german_only=True`** (Standard) verwirft nicht-deutsche Texte beim Import.
- `scamguard data build` dedupliziert über alle Quellen (kein Train/Test-Leck durch doppelte
  Inserate), teilt stratifiziert auf und schreibt `data/processed/stats.json`.

### Eigene Labels über das Frontend

Im Tab **„Labeln“** kann jedes gescannte Inserat mit dem echten Ergebnis (Betrug/seriös +
Masche) gespeichert werden → `data/raw/eigene_labels.jsonl` (in der Registry schon aktiv).
Bilder landen in `data/images/eigene_labels/`. **Vorher personenbezogene Daten schwärzen.**

### Wo Daten herkommen können (ohne Scraping)

Scraping von Kleinanzeigen ist technisch geblockt und verstößt gegen die Nutzungsbedingungen.
Sinnvolle Alternativen:

- **Hugging Face Hub**: Suchbegriffe `phishing`, `spam`, `fraud`, `scam`, `german`, `sms spam`,
  `fake reviews`. Deutschsprachige Daten sind selten. Englische Daten nur mit mehrsprachigem
  Modell (`FacebookAI/xlm-roberta-base`) oder nach Übersetzung nutzen.
- **Öffentliche Warnungen**: Verbraucherzentrale (Phishing-Radar), polizei-beratung.de,
  Sicherheitshinweise der Plattformen. Beschriebene Maschen abtippen → `fixed_label=SCAM`.
- **Eigene Sammlung**: Betrugsversuche aus dem Umfeld (Screenshots abtippen, anonymisieren).
- **Seriöse Gegenbeispiele**: eigene/befreundete echte Inserate. Wichtig, sonst lernt das Modell
  nur „Inserat = Betrug“.
- **Synthetisch (mit Vorsicht)**: Varianten bekannter Maschen per LLM generieren. Immer als
  eigene `source` markieren und **nie** im Testset verwenden.

---

## Projektstruktur

```
ScamGuard/
├── config.yaml                  Schwellen, Fusion-Gewichte, Modellpfade, LLM-Einstellungen
├── data/
│   ├── lexicons/                ← Betrugsphrasen, Preisreferenzen, Fake-Bild-Hashes (YAML/TXT)
│   ├── samples/                 synthetische Demo-Inserate
│   ├── raw/  images/            eure Rohdaten (nicht im Git)
│   └── processed/               train/val/test nach `data build` (nicht im Git)
├── models/                      trainierte Gewichte (nicht im Git)
├── examples/                    Beispiel-Inserat für `scamguard scan`
├── frontend/app.py              Streamlit-Oberfläche (Prüfen + Labeln)
├── src/scamguard/
│   ├── schema.py                einheitliches Datenschema (Listing, Signal, ScanResult)
│   ├── data/registry.py         ← HIER Datensätze eintragen
│   ├── data/loaders.py          HF/CSV/JSONL/Parquet → Listing
│   ├── data/build.py            Dedup + Split
│   ├── features/                URL/Mail/IBAN/Telefon, Sprache, Lexikon, Preis
│   ├── models/                  rules, text_classifier, image_model, llm_judge, fusion
│   ├── pipeline.py              alle Detektoren → Fusion
│   ├── evaluate.py              Kennzahlen pro Modell und Quelle
│   ├── api.py                   FastAPI
│   └── cli.py                   `scamguard …`
└── tests/
```

---

## Vorschlag: Aufgabenteilung für 3 Personen

| Person | Schwerpunkt | Erste Aufgaben |
|---|---|---|
| **A – Daten & Evaluation** | Datensätze finden, Registry, Labeln, Metriken | 3–5 Quellen recherchieren und eintragen, Labeling-Leitfaden schreiben, Testset fixieren (nie zum Tuning nutzen!), `evaluate` für den Bericht |
| **B – Text/NLP** | Regeln, Lexikon, Textmodelle, LLM | Lexikon mit echten Fällen erweitern, Baseline vs. GBERT vergleichen, Rechtschreib-Features (Hunspell/LanguageTool), LLM-Prompt evaluieren |
| **C – Bild & Frontend** | Bildmodelle, UI, API | Bilddatensatz aufbauen, CLIP-Prompts testen, CNN trainieren, Fake-Hash-Liste pflegen, Frontend polieren |

Gemeinsam: Fusion-Gewichte auf dem Validierungsset lernen (Stacking), Fehleranalyse
(falsch-positive seriöse Inserate!), Projektbericht.

## Nächste Schritte (Roadmap)

1. Echte Datensätze eintragen → Baseline neu trainieren → erste ehrliche Zahlen.
2. GBERT feintunen (Google Colab / bwUniCluster, `scamguard train text-transformer`).
3. Fusion-Gewichte lernen statt raten (logistische Regression auf Val-Scores) und Scores kalibrieren.
4. Bild-Pipeline mit echten Inseratsfotos trainieren, CLIP-Schwellen auf Val-Daten justieren.
5. LLM-Judge auf dem Testset gegen die anderen Modelle messen (Kosten vs. Nutzen).
6. Preisreferenzen mit echten Marktdaten ersetzen (aktuell Platzhalter).

## Wichtige Hinweise

- **Datenschutz (DSGVO)**: Keine Namen, Telefonnummern, Adressen oder IBANs echter Personen
  in Trainingsdaten. Der LLM-Judge sendet bewusst keinen Verkäufernamen und keinen Ort und ist
  standardmäßig aus. Bilder gehen nur mit `send_images: true` an die API.
- **Fairness**: Fehlerhaftes Deutsch ist ein *schwaches* Signal (niedrige Gewichte). Ehrliche
  Nicht-Muttersprachler dürfen nicht pauschal als Betrüger gelten. Falsch-Positive bitte
  gezielt auswerten.
- **Bewertung**: Zahlen auf den synthetischen Demo-Daten sind bedeutungslos. Auswertung pro
  Quelle (`evaluate` zeigt das) verhindert, dass ein Modell nur die Quelle statt Betrug lernt.
- **LLM-Kosten**: `llm.enabled` ist standardmäßig `false`. Modell und Effort stehen in
  `config.yaml`. API-Key per `ANTHROPIC_API_KEY` oder `ant auth login`, nie im Code.
