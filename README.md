# ScamGuard – Betrugserkennung für Kleinanzeigen

KI-Projekt an der DHBW Mannheim: Erkennung von Betrugsmaschen in **deutschsprachigen**
Kleinanzeigen (DE/AT/CH), mit Fokus auf Elektronik, Haushaltsgeräte und Autos.
Ein Inserat (Text, Preis, Bilder, Chatverlauf) wird hochgeladen – oder direkt im Browser per
**Extension für Chromium und Firefox** geprüft – und von mehreren unabhängigen Detektoren
bewertet. Das Ergebnis ist ein erklärbarer Risiko-Score mit markierten Fundstellen.

> **Status:** Entwurf v0.2.0. Pipeline, Web-Frontend und Browser-Extension laufen Ende-zu-Ende,
> die Modelle sind aber nur auf 24 synthetischen Demo-Inseraten trainiert. Aussagekräftig wird
> es erst mit euren echten Datensätzen.

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
 │ Lexikon,  │   │ TF-IDF-Baseline│   │ pHash, CLIP   │   │ Qwen lokal oder    │
 │ URL/Mail/ │   │ oder GBERT     │   │ Zero-Shot,    │   │ Claude, JSON       │
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
| **LLM-Judge** | Maschen-Geschichten, maschinell übersetzte Sprache, Gesamtbild | nein | lokal: Qwen (~7 GB Speicher), alternativ Claude-API (kostet) |

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
pip install -e ".[local-llm]" # lokales LLM (Qwen via MLX, nur Apple Silicon)
pip install -e ".[llm]"      # Claude als LLM-Judge
pip install -e ".[all]"      # alles
pip install -e ".[e2e]"      # Browser-Tests der Extension (danach: playwright install chromium)
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

## Browser-Extension (Chrome, Edge, Brave & Firefox)

![ScamGuard-Extension auf einer (nachgebauten) Kleinanzeigen-Seite](docs/screenshots/chromium_inserat.png)

Wie ein Adblocker markiert die Extension **Betrugsindikatoren direkt auf der Seite** und zeigt
einen **Prozent-Score** – im Panel oben rechts und als Badge am Toolbar-Symbol.

- **Automatisch** auf Kleinanzeigen-Anzeigen (`kleinanzeigen.de/s-anzeige/…`)
- **Jede andere Seite:** Toolbar-Symbol → „Diese Seite prüfen“
- **Chat-Nachrichten / E-Mails:** Text markieren → Rechtsklick → „Markierten Text mit ScamGuard prüfen“
- **Markierungen:** rot, durchgezogen, ⚠ = hoch · orange, gestrichelt = mittel · gelb, gepunktet =
  niedrig (nicht nur über Farbe unterscheidbar). Preis, Verkäufer und Bilder werden umrahmt.
  Klick auf ein Warnsignal im Panel springt zur Fundstelle.

```
Webseite ──Content Script liest aus──► Service Worker (Chromium) / Hintergrundskript (Firefox)
   ▲                                                  │ HTTP, nur lokal
   │                                                  ▼
   └── Markierungen · Panel · Badge ◄── Signale mit Fundstellen ◄── scamguard api (127.0.0.1:8000)
```

Die Extension enthält **keine eigene Erkennungslogik** – sie ist ein Client für den
ScamGuard-Server. So nutzt sie alle Modelle (Regeln, Text, Bilder, optional Claude), und ihr
pflegt die Logik nur an einer Stelle. Jedes Signal liefert dafür `highlights` (exakter
Originaltext zum Markieren) bzw. `target` (`price`, `seller`, `image:<n>`).

### Installation

1. **Server starten** (muss laufen, solange die Extension genutzt wird): `scamguard api`
2. **Chrome / Edge / Brave / Arc:** `chrome://extensions` → *Entwicklermodus* an →
   *Entpackte Erweiterung laden* → Ordner `extension/` wählen.
3. **Firefox (ab Version 140) – am einfachsten:** `python scripts/firefox_mit_extension.py`
   startet Firefox mit eigenem Entwicklungsprofil, lädt die Extension und öffnet kleinanzeigen.de
   (erneut aufrufen = Extension nach Code-Änderungen neu laden). Manuell geht es über
   `about:debugging#/runtime/this-firefox` → *Temporäres Add-on laden …* → `extension/manifest.json`.
   - Temporäre Add-ons verschwinden beim Neustart. Dauerhaft: über addons.mozilla.org als
     „unlisted“ signieren lassen (`npx web-ext sign --channel=unlisted`, kostenloser AMO-Account).
   - Fragt Firefox nach Zugriffsrechten: Toolbar-Symbol → *Zugriff erlauben*.
4. **Paket fürs Team:** `npx web-ext build --source-dir extension --artifacts-dir dist`

**Einstellungen (Popup):** automatisch prüfen · Bilder mitprüfen · Panel anzeigen ·
Claude-Analyse (kostet API-Guthaben) · Server-Adresse (nur `127.0.0.1`/`localhost`).

### Datenschutz & Sicherheit

- Die Extension spricht **nur mit dem lokalen Server**. Host-Berechtigungen gibt es nur für
  `127.0.0.1`/`localhost`, `www.kleinanzeigen.de` und dessen Bild-Server. Verkäufername und
  Ort werden nicht übertragen.
- Mit eingeschalteter Claude-Analyse gehen Inseratstexte (optional Bilder) vom Server an die
  Claude API – sonst verlässt nichts den Rechner.
- Der Server beantwortet `/scan` nur mit Header `X-ScamGuard-Client` und ohne fremden `Origin`.
  Fremde Webseiten können ihn daher nicht heimlich nutzen (z. B. um auf eure Kosten Claude
  aufzurufen).
- Texte aus der Seite landen im Panel nur als Text, nie als HTML – ein präpariertes Inserat
  kann so keinen Code ins Panel einschleusen.
- Firefox verlangt eine Datenübertragungs-Erklärung: `websiteContent` (der lokale Server läuft
  außerhalb des Browsers).

### Grenzen & Erweiterung

- Ändert Kleinanzeigen sein Seitenlayout, fällt die Extension in den generischen Modus zurück
  (weniger Elementmarkierungen) → Selektoren in `extension/content/extract.js` anpassen
  (zuletzt geprüft: 2026-10-01).
- Auf Seiten, die sich ständig neu aufbauen (Single-Page-Apps), können Markierungen beim
  Neurendern verschwinden.
- Weitere Plattformen (willhaben.at, markt.de, …): neuen Adapter in `extract.js` ergänzen.

### Tests der Extension

```bash
pip install -e ".[e2e]" && playwright install chromium
pytest -m e2e      # echte Extension in Chromium (Playwright) und im installierten Firefox (Selenium)
```

Kleinanzeigen wird dabei **nicht** aufgerufen: Die Tests liefern nachgebaute Seiten aus
(`tests/e2e/fixtures`) und starten den Server im Testprozess. Screenshots für den Bericht:
`SCAMGUARD_SCREENSHOTS=docs/screenshots pytest -m e2e -k scam_listing`

---

## KI-Analyse mit lokalem LLM (Qwen)

Das LLM ist die „dritte Meinung“ neben Regeln und Textmodell: Es liest das Inserat wie ein Mensch,
erkennt Maschen-Geschichten und maschinell übersetzte Sprache und begründet sein Urteil mit Zitaten
(die die Extension ebenfalls markiert). Standard ist ein **lokales Open-Weight-Modell** – kostenlos,
offline, Inseratsdaten verlassen den Rechner nicht.

**Mac mit Apple Silicon** (z. B. MacBook M4 mit 16 GB):

```bash
pip install -e ".[local-llm]"      # Apple MLX
scamguard llm-server               # erster Start lädt Qwen3.5-9B (~6,6 GB) und startet den Server
scamguard api                      # zweites Terminal
```

In der Extension: Popup → *Einstellungen* → *KI-Analyse: Automatisch* (Standard). Die Extension zeigt
sofort das Ergebnis der schnellen Modelle und ergänzt danach die KI-Einschätzung.

**Modellwahl nach Hardware** (Stand 2026-10; Qwen-Modelle unter Apache 2.0):

| Rechner | Modell | Größe | Server |
|---|---|---|---|
| MacBook M4, 16 GB gemeinsamer Speicher | Qwen3.5-9B, MLX OptiQ 4-Bit (neuestes Qwen, das passt) – gemessen: 30–40 s pro Prüfung | 6,6 GB | `scamguard llm-server` |
| PC mit 16 GB Grafikspeicher (z. B. RX 7800 XT) | Qwen3.8-27B, GGUF `UD-Q3_K_XL` (alternativ `UD-IQ4_XS`, 14,3 GB) | 13,1 GB | LM Studio oder Ollama |
| PC/Mac mit wenig Speicher | Qwen3.5-4B, 4-Bit | ~3 GB | wie oben |

**Andere Rechner (Windows/Linux, AMD- oder NVIDIA-GPU):** GGUF-Modell in LM Studio oder Ollama laden,
dessen Server starten und in `config.yaml` eintragen – am Code ändert sich nichts:

```yaml
llm:
  provider: local
  local:
    base_url: http://127.0.0.1:1234/v1   # LM Studio (Ollama: http://127.0.0.1:11434/v1)
    model: qwen3.8-27b                   # Modellname, wie ihn der Server anzeigt
```

**Claude statt lokal:** `provider: anthropic` und API-Key (`ANTHROPIC_API_KEY`). In der Extension
dann *KI-Analyse: Immer* wählen – *Automatisch* nutzt bewusst nie ein kostenpflichtiges Modell.

Hinweise:
- Qwen3.5 „denkt“ standardmäßig vor jeder Antwort. ScamGuard schaltet das ab
  (`enable_thinking: false`): Für die Einstufung reicht die direkte Antwort, und sie kommt viel schneller.
- Lokale Modelle erzwingen das JSON-Format nicht immer; ScamGuard prüft die Antwort, normalisiert
  sie und fragt bei ungültigem JSON ein zweites Mal (deterministisch) nach.
- Der MLX-Server läuft nur auf `127.0.0.1` und gibt fremden Webseiten keine CORS-Freigabe
  (Standard von mlx_lm wäre „jede Seite“).
- 16-GB-Mac: Solange Qwen läuft, sind rund 7 GB belegt. `Ctrl+C` beendet den Server und gibt den
  Speicher frei.
- Für den Projektbericht: `scamguard evaluate` mit `llm.enabled: true` vergleicht das LLM auf euren
  Testdaten mit den anderen Modellen.

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
├── extension/                   Browser-Extension (Manifest V3, Chromium + Firefox)
│   ├── manifest.json
│   ├── background.js            Service Worker/Hintergrundskript: Server, Badge, Kontextmenü
│   ├── content/                 extract.js (Auslesen) · highlight.js (Markieren) · panel.js
│   └── popup/                   Toolbar-Popup: Score, Serverstatus, Einstellungen
├── docs/screenshots/            Screenshots der Extension (aus den E2E-Tests)
├── scripts/                     Hilfsskripte (z. B. Extension-Icons erzeugen)
├── src/scamguard/
│   ├── schema.py                einheitliches Datenschema (Listing, Signal, ScanResult)
│   ├── data/registry.py         ← HIER Datensätze eintragen
│   ├── data/loaders.py          HF/CSV/JSONL/Parquet → Listing
│   ├── data/build.py            Dedup + Split
│   ├── features/                URL/Mail/IBAN/Telefon, Sprache, Lexikon, Preis
│   ├── models/                  rules, text_classifier, image_model, llm_judge, fusion
│   ├── pipeline.py              alle Detektoren → Fusion
│   ├── evaluate.py              Kennzahlen pro Modell und Quelle
│   ├── api.py                   FastAPI (Backend für Extension & Co.)
│   └── cli.py                   `scamguard …`
└── tests/                       Unit-Tests · e2e/ = Browser-Tests der Extension
```

---

## Vorschlag: Aufgabenteilung für 3 Personen

| Person | Schwerpunkt | Erste Aufgaben |
|---|---|---|
| **A – Daten & Evaluation** | Datensätze finden, Registry, Labeln, Metriken | 3–5 Quellen recherchieren und eintragen, Labeling-Leitfaden schreiben, Testset fixieren (nie zum Tuning nutzen!), `evaluate` für den Bericht |
| **B – Text/NLP** | Regeln, Lexikon, Textmodelle, LLM | Lexikon mit echten Fällen erweitern, Baseline vs. GBERT vergleichen, Rechtschreib-Features (Hunspell/LanguageTool), LLM-Prompt evaluieren |
| **C – Bild, Frontend & Extension** | Bildmodelle, UI, API, Browser-Extension | Bilddatensatz aufbauen, CLIP-Prompts testen, CNN trainieren, Fake-Hash-Liste pflegen, Extension-Adapter für weitere Plattformen, Nutzertests mit der Extension |

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
