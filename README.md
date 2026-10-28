# ScamGuard

**Betrugserkennung für deutschsprachige Kleinanzeigen – im Browser, in Echtzeit, erklärbar.**

ScamGuard prüft Inserate und Chatverläufe auf Plattformen wie Kleinanzeigen (DE/AT/CH) auf bekannte
Betrugsmaschen: Fake-Zahlungslinks, Vorkasse-Geschichten, PayPal „Freunde & Familie“,
Dreiecksbetrug, Datenabfragen und mehr. Mehrere unabhängige Detektoren bewerten Text, Preis, Bilder
und Nachrichten; das Ergebnis ist ein Risiko-Score mit markierten Fundstellen direkt auf der Seite.

Entstanden als Projekt an der DHBW Mannheim. Aktueller Stand: **v0.13.0** ([Changelog](CHANGELOG.md)).

- **Browser-Extension** für Chromium und Firefox: markiert Warnsignale wie ein Adblocker, prüft auch das
  Postfach live, jede neue Nachricht ohne Neuladen.
- **Lokales LLM** (Qwen über MLX): KI-Analyse ohne Cloud – die Inseratsdaten bleiben auf dem Rechner.
- **Web-App** zum Prüfen per Screenshot, gespeicherter Seite oder Text, mit Annotation-Workspace
  fürs Team.
- **Datenpipeline** ohne Scraping: eigene Labels, Ablageordner, Hugging Face, öffentliche Warnungen,
  Foren-Erfahrungsberichte.

> Die trainierbaren Modelle sind bisher nur auf synthetischen Demo-Daten trainiert. Aussagekräftige
> Kennzahlen entstehen mit dem laufenden Labeling ([Daten & Labeling](#daten--labeling)).

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
 │  Regeln   │   │   Textmodell   │   │  Bildmodell   │   │ LLM-Judge          │
 │ Lexikon,  │   │ TF-IDF-Baseline│   │ pHash, CLIP   │   │ Qwen lokal oder    │
 │ URL/Mail/ │   │ oder GBERT     │   │ Zero-Shot,    │   │ Claude API, JSON   │
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

| Detektor | Erkennt z. B. | Training | Hardware |
|---|---|---|---|
| **Regeln** (`features/`) | Fake-Zahlungslinks, Lookalike-Domains (`paypa1-…`), ausländische IBAN/Telefonnummern, „Freunde & Familie“, Spediteur-/Treuhand-Geschichten, Ausweis-Forderungen, Dumpingpreise (Preisliste mit 43 häufig betroffenen Artikeln von iPhone über Grafikkarten bis Rassetiere, ohne Zubehör/Suchanzeigen), falsche Artikel („der Auto“); in Nachrichten/Mails/SMS: Konto-gesperrt-Phishing, Paket-/Zoll-SMS, Code-Weitergabe, „Zahlung reserviert“, Zeitdruck, „Geld zuerst“; Anbieter: junges Konto (stärker: heute angelegt + teure Ware), keine Bewertungen, Firma als „Privater Nutzer“ | nein | CPU |
| **Text-Baseline** | Wort- und Zeichen-n-Gramme, auch Tippfehler/gebrochenes Deutsch | Sekunden | CPU |
| **Text-Transformer** | Kontext, Formulierungsmuster (`deepset/gbert-base`, feingetunt) | ja | GPU empfohlen |
| **Bild: pHash** | wiederverwendete Fake-/Stockfotos; Fotos aus eigenen Betrugs-Labels in neuen Anzeigen (lokale Rückwärtssuche) | nein (Hash-Liste, Labels) | CPU |
| **Bild: CLIP** | Stockfoto, Screenshot, Bild passt nicht zur Kategorie | nein (Zero-Shot) | CPU |
| **Bild: CNN** | Muster in Betrugsbildern (EfficientNet-B0, Transfer Learning) | ja | GPU empfohlen |
| **LLM-Judge** | Maschen-Geschichten, maschinell übersetzte Sprache, Gesamtbild | nein | lokal: Qwen (~7 GB), alternativ Claude API |
| **AutoML** (Welle 2) | Zusammenspiel aller Merkmale (Kontoalter × Preis × Warnsignale × Wortwahl); bestes Verfahren aus LightGBM, XGBoost, CatBoost, Random Forest, Extra Trees, LogReg, Deep Forest; Erklärung per SHAP | ja (Minuten) | CPU, alle Kerne |

**Modelle im Überblick:** Selbst trainiert werden GBERT (`models/text_classifier.py` →
`train_text_transformer`) und EfficientNet-B0 (`models/image_model.py` → `train_image_model`), beide
per Transfer Learning. Vortrainiert im Einsatz: CLIP (Bildhinweise), Qwen (KI-Analyse) und Apple Vision bzw.
Windows-OCR (Texterkennung beim Screenshot-Upload). Die TF-IDF-Baseline ist bewusst klassisches ML und dient als
Messlatte für die neuronalen Modelle.

**Wellen:** Welle 1 bewertet ungelabelte Texte mit Qwen vor (geplant, `docs/backlog.md`). Welle 2
(`scamguard automl`) trainiert klassische Verfahren auf einer Merkmalstabelle (`features/tabular.py`:
alle Regelmerkmale, Warnsignale, Anbieterprofil, Kategorie, Wortwahl als SVD-Komponenten). FLAML sucht
Verfahren und Einstellungen per Kreuzvalidierung, der Deep Forest (`models/deep_forest.py`) tritt
gegen die Gewinner an. AutoML läuft in einem eigenen Prozess: LightGBM/XGBoost und PyTorch bringen je
eine OpenMP-Bibliothek mit, die sich in einem Prozess gegenseitig blockieren. Im Scan urteilt AutoML
erst ab 20 gelabelten Beispielen je Klasse.

---

## Quickstart

Voraussetzung: **Python ≥ 3.10**, empfohlen 3.12 (stabilste Basis für PyTorch und transformers). Das
vorinstallierte macOS-`python3` (3.9) ist zu alt.

```bash
brew install python@3.12
git clone https://github.com/derangler3008/ScamGuard.git && cd ScamGuard
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # Kern: Regeln, Baseline, Web-App, API, Tests
```

**Windows** (PowerShell; Python 3.12 von python.org oder `winget install Python.Python.3.12`):

```powershell
git clone https://github.com/derangler3008/ScamGuard.git; cd ScamGuard
py -3.12 -m venv .venv; .venv\Scripts\activate
pip install -e ".[dev]"
```

> **`command not found: scamguard`?** Die CLI liegt in der virtuellen Umgebung: in jedem neuen Terminal
> `source .venv/bin/activate` (Windows: `.venv\Scripts\activate`) – oder direkt `.venv/bin/scamguard start`.

**VS Code:** Das Repo bringt Aufgaben mit (`.vscode/tasks.json`, macOS und Windows): `Strg+Umschalt+B`
startet den Server, Web-App, Tests und AutoML über *Terminal → Aufgabe ausführen*. Unter *Ausführen und
Debuggen* lassen sich API und Tests mit Haltepunkten starten. Unter Windows startet `scamguard start` nur
die API; Qwen läuft dort über LM Studio oder Ollama (Abschnitt „LLM“).

Optionale Extras:

```bash
pip install -e ".[data]"       # Hugging-Face-Datensätze
pip install -e ".[text]"       # GBERT feintunen (torch, transformers)
pip install -e ".[vision]"     # Bildmodelle (torch, torchvision, CLIP, imagehash)
pip install -e ".[local-llm]"  # lokales LLM (Qwen via MLX, nur Apple Silicon)
pip install -e ".[llm]"        # Claude API als LLM-Judge
pip install -e ".[automl]"     # Welle 2: FLAML, LightGBM, XGBoost, CatBoost, SHAP (Mac: brew install libomp)
pip install -e ".[all]"        # alles
pip install -e ".[e2e]"        # Browser-Tests der Extension (danach: playwright install chromium)
```

Erste Schritte:

```bash
scamguard data list                  # registrierte Datensätze
scamguard data build                 # → data/processed/{train,val,test}.jsonl
scamguard retrain                    # einlesen + trainieren + auswerten
scamguard automl                     # Welle 2: AutoML-Suche, Bestenliste, Auswertung (5 Min.)
scamguard evaluate                   # Precision/Recall/F1/AUC pro Modell und Quelle
scamguard scan examples/inserat_beispiel.json
scamguard ui                         # Web-App → http://127.0.0.1:8501
scamguard api                        # REST-API → http://127.0.0.1:8000 (Startseite), /docs
scamguard start                      # für die Extension: Qwen + API zusammen
pytest                               # Tests
```

Web-App und API lauschen standardmäßig **nur lokal**. Für Demos im Netzwerk: `scamguard ui --public`.

---

## Browser-Extension (Chrome, Edge, Brave & Firefox)

![ScamGuard-Extension auf einer (nachgebauten) Kleinanzeigen-Seite](docs/screenshots/chromium_inserat.png)

Die Extension markiert **Betrugsindikatoren direkt auf der Seite** und zeigt einen **Prozent-Score** –
im Panel oben rechts und als Badge am Toolbar-Symbol.

- **Automatisch** auf Kleinanzeigen-Anzeigen (`kleinanzeigen.de/s-anzeige/…`).
- **Im Postfach** (`kleinanzeigen.de/m-nachrichten…`): prüft den Chatverlauf und jede neu eintreffende
  Nachricht ohne Neuladen. Auffällige Nachrichten werden eingerahmt, Fundstellen markiert („Geld zuerst“,
  „sonst ist es bald weg“, „schick mir deine Handynummer für die Zahlung“ …).
- **Jede andere Seite:** Toolbar-Symbol → „Diese Seite prüfen“.
- **WhatsApp, E-Mail, andere Chats:** Text markieren → Rechtsklick → „Markierten Text mit ScamGuard prüfen“.
- **Markierungen:** rot, durchgezogen, Dreieck = hoch · orange, gestrichelt = mittel · gelb, gepunktet =
  niedrig (nicht nur über Farbe unterscheidbar). Preis, Verkäufer, Bilder und Nachrichten werden
  umrahmt; ein Klick auf ein Warnsignal springt zur Fundstelle.
- **Anbieter im Blick:** Das Panel zeigt Kontoalter, Bewertungs-Abzeichen („TOP Zufriedenheit“,
  „Besonders zuverlässig“), Zahl der Anzeigen und ob privat oder gewerblich. Händler, Firmen (Rechtsform
  wie GmbH, UG, GbR im Namen) und Dienstleistungen/Werbung ohne Festpreis werden als solche markiert –
  ihre geschäftliche Telefonnummer, Adresse oder Website zählt dann kaum als Warnsignal.
- **Labeln:** *Betrug* / *Seriös* im Panel speichert das Inserat bzw. den Chatverlauf als Trainingsdaten –
  mit Fotos, Kategorie und Anbieterprofil, zusätzlich als Ordner im Datensatz (siehe
  [Ablage beim Labeln](#ablage-beim-labeln)).

![Chat-Scan im (nachgebauten) Kleinanzeigen-Postfach: neue Nachricht eingerahmt, Fundstellen markiert](docs/screenshots/chromium_chat.png)

```
Webseite ──Content Script liest aus──► Service Worker (Chromium) / Hintergrundskript (Firefox)
   ▲                                                  │ HTTP, nur lokal
   │                                                  ▼
   └── Markierungen · Panel · Badge ◄── Signale mit Fundstellen ◄── scamguard api (127.0.0.1:8000)
```

Die Extension enthält **keine eigene Erkennungslogik**, sie ist ein Client des ScamGuard-Servers. So
nutzt sie alle Modelle, und die Logik wird an genau einer Stelle gepflegt. Jedes Signal liefert dafür
`highlights` (exakter Originaltext) bzw. `target` (`price`, `seller`, `image:<n>`).

### Installation

1. **Server starten** (muss laufen, solange die Extension genutzt wird):
   `scamguard start` – startet Qwen und die API zusammen, `Ctrl+C` beendet beides.
   Ohne KI-Analyse: `scamguard start --ohne-llm`.
2. **Chrome / Edge / Brave / Arc:** `chrome://extensions` → *Entwicklermodus* →
   *Entpackte Erweiterung laden* → Ordner `extension/`.
3. **Firefox (ab 140):** `python scripts/firefox_mit_extension.py` startet Firefox mit eigenem
   Entwicklungsprofil und geladener Extension (erneut aufrufen = nach Code-Änderungen neu laden).
   Manuell: `about:debugging#/runtime/this-firefox` → *Temporäres Add-on laden …* → `extension/manifest.json`.
   - Temporäre Add-ons verschwinden beim Neustart. Dauerhaft: als „unlisted“ signieren
     (`npx web-ext sign --channel=unlisted`, kostenloser AMO-Account).
   - Fragt Firefox nach Zugriffsrechten: Toolbar-Symbol → *Zugriff erlauben*.
4. **Paket bauen:** `npx web-ext build --source-dir extension --artifacts-dir dist`

**Einstellungen (Popup):** automatisch prüfen · Bilder mitprüfen · Panel anzeigen · KI-Analyse
(automatisch/immer/nie) · Server-Adresse (nur `127.0.0.1`/`localhost`).

### Datenschutz & Sicherheit

- Die Extension spricht **nur mit dem lokalen Server**. Host-Berechtigungen gibt es nur für
  `127.0.0.1`/`localhost`, `www.kleinanzeigen.de` und dessen Bild-Server. Verkäufername und Ort werden
  nicht übertragen; aus dem Namen wird nur eine Rechtsform (GmbH, UG …) abgeleitet.
- Nur mit eingeschalteter Claude-Analyse gehen Inseratstexte (optional Bilder) an die Claude API – sonst
  verlässt nichts den Rechner.
- Der Server beantwortet `/scan` nur mit Header `X-ScamGuard-Client` und ohne fremden `Origin`. Fremde
  Webseiten können ihn daher nicht heimlich nutzen (etwa um kostenpflichtige API-Aufrufe auszulösen).
- Seiteninhalte landen im Panel nur als Text, nie als HTML – ein präpariertes Inserat kann keinen Code
  einschleusen.
- Rückwärtssuche: Das Panel bietet Links zu Google Lens und TinEye für das Titelfoto. Erst der Klick
  öffnet die Suche in einem neuen Tab und gibt die Bildadresse an diesen Dienst weiter; ScamGuard selbst
  fragt dort nie automatisch an. Automatisch und lokal läuft nur der Abgleich mit Fotos aus eigenen
  Betrugs-Labels (pHash).
- Firefox-Datenübertragungs-Erklärung: `websiteContent` (der lokale Server läuft außerhalb des Browsers).

### Bekannte Grenzen

- Ändert Kleinanzeigen das Seitenlayout, fällt die Extension in den generischen Modus zurück (weniger
  Elementmarkierungen) → Selektoren in `extension/content/extract.js` anpassen. Seit 10/2026 laufen zwei
  Layouts parallel; gelesen wird über IDs und die schema.org-Auszeichnung, die beide gemeinsam haben
  (zuletzt geprüft: 2026-10-06).
- **Postfach:** nur eingeloggt sichtbar, ohne stabile IDs. Der Verlauf wird ohne feste Selektoren gefunden
  (ARIA-Log bzw. innerster Scrollbereich) – getestet an einem Nachbau. Eigene und fremde Nachrichten
  werden nicht unterschieden. Fallback: Chat markieren → Rechtsklick → prüfen.
- **Reine Chats im Textmodell:** Ohne gelernte Chats rät ein Textmodell dort nur (das Demo-Modell hielt
  „Hallo, ist das noch da?“ für 72 % Betrug). Es urteilt deshalb erst ab 20 Chat-Beispielen pro Klasse;
  bis dahin entscheiden Regeln und LLM.
- Single-Page-Apps können Markierungen beim Neurendern entfernen.
- Weitere Plattformen (willhaben.at, markt.de, …): neuen Adapter in `extract.js` ergänzen.

### Tests der Extension

```bash
pip install -e ".[e2e]" && playwright install chromium
pytest -m e2e      # echte Extension in Chromium (Playwright) und im installierten Firefox (Selenium)
```

Kleinanzeigen wird dabei **nicht** aufgerufen: Die Tests liefern nachgebaute Seiten aus
(`tests/e2e/fixtures`) und starten den Server im Testprozess. Screenshots aktualisieren:
`SCAMGUARD_SCREENSHOTS=docs/screenshots pytest -m e2e -k "scam_listing or chat"`

---

## LLM: lokal mit Qwen

Das LLM ist die dritte Meinung neben Regeln und Textmodell: Es liest das Inserat im Zusammenhang,
erkennt Maschen-Geschichten und maschinell übersetzte Sprache und begründet sein Urteil mit Zitaten,
die die Extension ebenfalls markiert. Standard ist ein **lokales Open-Weight-Modell** – kostenlos,
offline, ohne Datenabfluss.

**Mac mit Apple Silicon** (z. B. MacBook M4, 16 GB):

```bash
pip install -e ".[local-llm]"      # Apple MLX
scamguard start                    # Qwen + API; erster Start lädt Qwen3.5-9B (~6,6 GB)
```

In der Extension: Popup → *Einstellungen* → *KI-Analyse: Automatisch* (Standard). Das Ergebnis der
schnellen Modelle erscheint sofort, die KI-Einschätzung wird nachgereicht.

**Modellwahl nach Hardware** (Stand 2026-10; Qwen-Modelle unter Apache 2.0):

| Rechner | Modell | Größe | Server |
|---|---|---|---|
| MacBook M4, 16 GB | Qwen3.5-9B, MLX OptiQ 4-Bit – gemessen: ca. 6 s (unauffällig) bis 20 s (Betrug) | 6,6 GB | `scamguard llm-server` |
| PC mit 16 GB VRAM (z. B. RX 7800 XT) | Qwen3.8-27B, GGUF `UD-Q3_K_XL` (alternativ `UD-IQ4_XS`, 14,3 GB) | 13,1 GB | llama.cpp (startet mit `scamguard start`), LM Studio oder Ollama |
| wenig Speicher | Qwen3.5-4B, 4-Bit | ~3 GB | wie oben |

**Windows/Linux (AMD/NVIDIA) mit llama.cpp:** fertigen Build von
[llama.cpp](https://github.com/ggml-org/llama.cpp/releases) entpacken (AMD ohne ROCm: `win-vulkan-x64`),
GGUF-Modell herunterladen (z. B. `unsloth/Qwen3.8-27B-GGUF`) und beides in **`config.local.yaml`** eintragen
(nicht im Git, gilt nur auf diesem Rechner, wird über `config.yaml` gelegt). Dann startet `scamguard start`
(VS Code: `Strg+Umschalt+B`) Qwen und API zusammen, wie auf dem Mac:

```yaml
llm:
  local:
    model: qwen3.8-27b
    timeout: 180
    llama_cpp:
      server: C:/Pfad/zu/llama.cpp/llama-server.exe
      gguf: C:/Pfad/zu/Qwen3.8-27B-UD-Q3_K_XL.gguf
      context: 8192                      # optional; gpu_layers: 99 (Standard, alles auf der GPU)
```

**Alternativ LM Studio oder Ollama:** Modell laden, Server starten und in `config.local.yaml` eintragen –
ohne Code-Änderung:

```yaml
llm:
  provider: local
  local:
    base_url: http://127.0.0.1:1234/v1   # LM Studio (Ollama: http://127.0.0.1:11434/v1)
    model: qwen3.8-27b                   # Modellname, wie ihn der Server anzeigt
```

**Cloud statt lokal:** `provider: anthropic` und `ANTHROPIC_API_KEY`. In der Extension dann
*KI-Analyse: Immer* – *Automatisch* nutzt bewusst nie ein kostenpflichtiges Modell.

Details:
- Qwen3.5 „denkt“ standardmäßig vor jeder Antwort; ScamGuard schaltet das ab (`enable_thinking: false`) –
  für die Klassifikation reicht die direkte Antwort, und sie ist deutlich schneller.
- Lokale Server erzwingen das JSON-Format nicht immer; die Antwort wird validiert, normalisiert und bei
  ungültigem JSON einmal deterministisch neu angefragt.
- MLX- und llama.cpp-Server laufen nur auf `127.0.0.1` ohne CORS-Freigabe für fremde Seiten.
- 16-GB-Mac: Solange Qwen läuft, sind rund 7 GB belegt; `Ctrl+C` gibt den Speicher frei.
- Vergleich mit den anderen Modellen: `scamguard evaluate --llm --max 100`.
- Vergleichsfälle (`llm.beispiele`, Standard 4): Qwen sieht die ähnlichsten von Menschen gelabelten
  Inserate aus eigenen Labels und Team-Importen – so wirkt jeder neue Datensatz sofort, ohne Training.
  Nur beim lokalen Modell; das Panel zeigt, womit verglichen wurde.

### Qwen mit Fotos (provisorisch, Mac)

Qwen3.5 versteht auch Bilder; die Gewichte dafür stecken schon im heruntergeladenen Modell. Der normale
Server (`mlx_lm`) liest aber nur Text. Mit `mlx-vlm` in einer eigenen Umgebung prüft dasselbe Modell bis zu
4 Fotos pro Inserat mit (verkleinert; z. B. „Foto zeigt ein anderes Modell“, „Glas gesprungen, Text sagt
‚sehr gut‘“):

```bash
python3.12 -m venv .venv-vlm && .venv-vlm/bin/pip install mlx-vlm   # ca. 1 GB, eigene Umgebung
```

Dann in `config.local.yaml`:

```yaml
llm:
  local:
    bilder: true
    vlm_python: .venv-vlm/bin/python
```

`scamguard start` startet dann `scripts/qwen_vlm_server.py` statt `mlx_lm` (gleiches Modell, gleicher
Port, keine Freigabe für fremde Webseiten). Eine Prüfung dauert mit Fotos etwa 20–35 s statt 5–15 s; bei
Details (genaues Modell) irrt Qwen sich gelegentlich. Zurück zum reinen Text: `bilder: false`, neu starten.

### Qwen feintunen (LoRA)

Mit genug eigenen Labels lässt sich Qwen per LoRA anpassen – lokal mit MLX, ohne Cloud:

```bash
scamguard data build && scamguard llm-daten     # → data/finetune/{train,valid,test}.jsonl
# Qwen-Server vorher beenden (16 GB reichen nicht für beides)
mlx_lm.lora --model mlx-community/Qwen3.5-9B-OptiQ-4bit --train --data data/finetune \
  --adapter-path models/qwen_lora --mask-prompt --batch-size 1 --num-layers 8 --iters 600 \
  --learning-rate 1e-5 --grad-checkpoint --max-seq-length 2048
scamguard llm-server --adapter models/qwen_lora  # oder dauerhaft: llm.local.adapter_path in config.yaml
```

Die Trainingsbeispiele nutzen exakt den Prompt des Betriebs. Die Zielantwort entsteht aus den Labels,
den im Team markierten Sätzen und – wo keine vorliegen – den Regel-Treffern. Ablauf, Speicherbedarf,
Datenmengen und Alternativen: **[docs/llm_feintuning.md](docs/llm_feintuning.md)**.

---

## Daten & Labeling

Kein Weg braucht Python-Code. Nach neuen Daten neu trainieren: Web-App, Tab *Meine Daten & Training* →
**Jetzt neu trainieren**, oder `scamguard retrain` (ein laufender Server nutzt das neue Modell sofort).
Für belastbare Kennzahlen ohne die Demo-Daten: `scamguard retrain --ohne-demo`.

### Annotation-Workspace (Tab *Einstufen im Team*)

Labeling mit Qualitätssicherung für mehrere Personen. Ablauf und Methodik:
**[docs/einstufung.md](docs/einstufung.md)**.

- **Kategoriensystem** [`data/einstufung/kategorien.yaml`](data/einstufung/kategorien.yaml):
  Entscheidungsregeln, Urteil, 11 Maschen, 11 Warnsignale auf Satzebene, Bildarten – jeweils mit
  Definition, Beispiel und Abgrenzung; in der Web-App als *Leitfaden*.
- **Drei Ebenen je Text:** Gesamturteil (Betrug/seriös/unklar, Sicherheit, Masche, wer täuscht, Sprache),
  **jeder Satz** mit seinem Warnsignal, **jedes Bild** (Art, selbst verdächtig?).
- **Aufgaben** aus jedem Datensatz (Foren-Prüfliste, Warnungen, Hugging Face, eigene Labels) oder als
  eigener Text – Kontaktdaten werden anonymisiert, das Label der Quelle bleibt bis zum eigenen Urteil
  verborgen, optional ausgewogen gezogen.
- **Doppel-Labeling:** Ein fester Anteil (Standard 25 %) wird von zwei Personen unabhängig gelabelt →
  **Krippendorffs α** und Cohens κ je Ebene, Konfliktliste mit Entscheidung und Begründung, Hinweise auf
  auffällige Einstufungen.
- **Regeln gegen Mensch:** Precision/Recall der Regel-Erkennung je Warnsignal auf Satzebene; verpasste
  Sätze als Kandidaten fürs Lexikon; Widerspruchsquote zum Label der Quelle.
- **Export** nach `data/raw/einstufung_*.jsonl` (Datensatz `einstufungen`): Das Konsens-Label hat beim
  Bauen der Splits Vorrang vor dem Label der Quelle; markierte Sätze werden beim Qwen-Feintuning zu den
  Begründungen.

```bash
scamguard einstufen holen --person Jannis --datensatz datei:gesammelt_foren.csv --max 50
scamguard einstufen bericht --regeln     # Übereinstimmung, Konflikte, Regeln gegen Mensch
scamguard einstufen export               # danach retrain / llm-daten
```

### Weitere Datenquellen

| Quelle | Weg | Ablage |
|---|---|---|
| Einzelnes Inserat | Web-App, Tab *Inserat hochladen & einstufen*: Screenshot(s), `.html`, PDF oder Text → Felder werden ausgelesen (Apple Vision bzw. Windows-OCR, lokal) → *Betrug*/*Seriös* | `eigene_labels.jsonl` + Ordner-Ablage |
| Chatverläufe als Bild | dort im Bereich *Chatverlauf mit dem Anbieter*: Chat-Screenshots (PNG/JPG) hineinziehen – der Text wird per Texterkennung gelesen; ein Chat allein (ohne Inserat) geht auch | `eigene_labels.jsonl` + Ordner-Ablage |
| Live-Inserate und Chats | Extension-Panel → *Betrug*/*Seriös* (erneut klicken ersetzt die alte Einstufung) | `eigene_labels.jsonl` + Ordner-Ablage |
| Viele Inserate | Dateien in `data/datensatz_fuellen_inserate/{betrug,serioes}/` – eine Datei = ein Inserat, ein Unterordner = ein Inserat aus mehreren Dateien; Chat-Screenshots darin heißen `chat…` (z. B. `chat_1.png`) | wird beim Training ausgelesen |
| Fertige Tabellen | CSV (auch Excel-CSV mit `;`), TSV, JSONL, Parquet in `data/datensatz_fuellen_text/`; Spalten und Labels werden an üblichen Namen erkannt; Vorlage `_vorlage_inserate.csv` | automatisch erkannt |
| Hugging Face | Eintrag in `data/datensatz_fuellen_text/huggingface.yaml` | automatisch erkannt |
| Reddit (offizielle API) | `scamguard data sammeln --quellen reddit`: Bilder aus `data/quellen/reddit.yaml`, Chats per Texterkennung → Prüfliste (eigener API-Zugang in `.env`) | `gesammelt_reddit.csv`, Bilder in `data/raw/reddit/` |
| Synthetisch | `python scripts/synthetische_daten.py chats` bzw. `qwen-chats` (Chat-Screenshots) bzw. `fotos` (KI-Produktfotos, GPU) | `…/synthetisch/{betrug,serioes}/`, **nur Training** |
| Produktfotos | `data/datensatz_fuellen_bilder/{betrug,serioes}/`, Bildmodell mit `scamguard retrain --bilder` | automatisch erkannt |
| Sonderfälle | `DatasetSpec` mit `transform`-Funktion in `src/scamguard/data/registry.py` | – |

Bei Screenshots werden nur der erkannte Text und Produktfotos gespeichert. Anbietername, Straße und
Hausnummer werden beim Auslesen nicht übernommen. `scamguard data list` (oder Tab *Meine Daten &
Training*) zeigt, was erkannt wurde, inklusive Hinweisen wie „keine Label-Spalte“. `scamguard data build`
dedupliziert über alle Quellen (auch über anonymisierte Varianten) und teilt stratifiziert auf.

### Ablage beim Labeln

Jede manuelle Einstufung (Extension, Upload, Formular) landet zusätzlich als Ordner im Datensatz –
sortiert nach Label und Kategorie, gewerbliche Anbieter getrennt:

```
data/datensatz_fuellen_inserate/
├── betrug/elektronik/iphone-15-pro-nur-versand__3f9a1c02de/   inserat.json · bild_1.jpg · bild_2.jpg
└── serioes/immobilien-gewerblich/reihenhaus-travemuende__a81b77c410/
```

`inserat.json` enthält alle Felder samt Anbieterprofil (ohne Namen), Masche und Adresse der Anzeige.
Stuft man dieselbe Anzeige neu ein, wandert der Ordner mit (anderes Label, andere Kategorie). Beim
Training werden solche Ordner ohne Texterkennung gelesen und mit `eigene_labels.jsonl` dedupliziert.
Bisherige Labels übernehmen: `scamguard data ordner`.

### Öffentliche Warnungen sammeln: `scamguard data sammeln`

Holt Betrugsnachrichten im Wortlaut (Phishing-Mails, SMS, Chat-Maschen) von Seiten, die sie
veröffentlichen – **nicht** von Kleinanzeigen selbst (Nutzungsbedingungen, Bot-Sperre, Personendaten):
Watchlist Internet „Phishing-Alarm“, Phishing-Radar der Verbraucherzentrale und Zitate aus
Watchlist-Artikeln zu Marktplätzen. Ergebnis: `data/datensatz_fuellen_text/gesammelt_warnungen.csv`
(Label *Betrug*, Quelle je Zeile). robots.txt wird beachtet, 1,5 s Pause zwischen Anfragen,
Seiten-Cache. Rechtsgrundlage: Text- und Data-Mining für nicht-kommerzielle Forschung (§ 60d UrhG);
die Daten bleiben lokal.

**Foren-Erfahrungsberichte** (`--quellen foren`): liest die Threads aus `data/quellen/foren.yaml`
(ComputerBase, gs-forum, mtb-news, unknowns.de, Antispam e.V. …) und liefert

- `data/raw/foren_erfahrungsberichte.jsonl` – Berichte Betroffener mit der beschriebenen Masche (nur
  Auswertung, nicht im Training). Erster Lauf: 222 Berichte aus 9 Foren; am häufigsten:
  Vorkasse/Überweisung, Käuferschutz/Rückbuchung, Fake „Sicher bezahlen“, gehackte Konten.
- `data/datensatz_fuellen_text/gesammelt_foren.csv` – **Prüfliste** zitierter Nachrichten. Foren zitieren
  auch Harmloses (Support-Antworten, Gesetzestexte), deshalb bleibt die Spalte `betrug` leer, bis sie
  manuell gefüllt wird (`vorschlag` hilft); manuelle Einträge überstehen das nächste Sammeln. Gründlicher:
  im Annotation-Workspace als Aufgaben holen.

Forennamen werden nicht gespeichert; Mailadressen, Telefonnummern, IBANs und Link-Pfade werden
anonymisiert, ihre Form bleibt für die Merkmale erhalten (`anonym@gmail.com`, `+44 1111 111111`).
Bewusst nicht gesammelt: die **Reddit-Website** (robots.txt sperrt alle Bots – Reddit nur über die
offizielle API, siehe unten), **gutefrage.net** (Bot-Sperre), **eBay-Community** (Nutzungsbedingungen).

### Reddit über die offizielle API: `scamguard data sammeln --quellen reddit`

Holt Screenshots aus Betrugs-Subreddits (Liste in `data/quellen/reddit.yaml`) über die Reddit Data API –
kein Scraping der Website. Einmalig auf https://www.reddit.com/prefs/apps eine App vom Typ „script“
anlegen und `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` in `.env` eintragen
(Vorlage `.env.example`; Reddit schaltet neue Apps ggf. erst frei). Bilder landen lokal in
`data/raw/reddit/` (nicht weitergeben), Bilder mit Text werden per Texterkennung gelesen, anonymisiert und
zur Prüfliste `data/datensatz_fuellen_text/gesammelt_reddit.csv`. Die Spalte `betrug` füllt ein Mensch –
Beiträge dort sind oft Fragen („Ist das Betrug?“), kein bestätigtes Label. Benutzernamen werden nicht
gespeichert.

### Synthetische Daten: `scripts/synthetische_daten.py`

Damit Pipeline und Modelle vor dem eigenen Labeling etwas zu tun haben: `chats` erzeugt nachgebaute
Chat-Screenshots im Postfach-Stil (Fake-Zahlungslink, Freunde & Familie, Kurier, Code-Weitergabe,
WhatsApp-Wechsel, Ausweis, Tier-Transport, Mietkaution – und gewöhnliche Gespräche) mit Inseratstext;
`qwen-chats` lässt die Gespräche vom lokalen Qwen schreiben (Szenario, Stil, Tippfehler und holpriges
Deutsch variieren – deutlich vielfältiger als feste Vorlagen; Qwen-Server muss laufen);
`fotos` erzeugt KI-Produktfotos mit SDXL-Turbo auf der GPU (`.venv-lora`): makellose Katalogbilder wie
in Fake-Inseraten bzw. gewöhnliche Handyfotos. Der Ordner `synthetisch/` ist eine eigene Quelle und landet
beim Bauen der Splits **nur im Training**, nie in Validierung oder Test – Kennzahlen bleiben ehrlich.

**Klassenbalance:** Diese Quellen liefern fast nur Betrug. Ohne seriöse Nachrichten als Gegenstück lernt
ein Modell „kurze Nachricht = Betrug“. Seriöse Chats ergänzen (Extension, Annotation-Workspace);
`scamguard evaluate` zeigt die Kennzahlen je Quelle und macht solche Verzerrungen sichtbar.

### Lexikon datengestützt erweitern: `scamguard data phrasen`

Vergleicht Betrugs- mit seriösen Texten und listet Wortfolgen, die in Betrugstexten auffällig häufig sind
(Log-Odds-Ratio mit informativem Dirichlet-Prior, Monroe et al. 2008), markiert, ob das Lexikon sie
schon kennt. `--hf` zieht die deutschen Hugging-Face-Nachrichten als Vergleich hinzu (ohne sie ins
Training zu übernehmen). Ergebnis: `data/raw/phrasen_kandidaten.csv`. Kandidaten werden manuell als
Muster in `data/lexicons/scam_signals_de.yaml` übernommen und mit harmlosen Sätzen gegengetestet.

### Datenstrategie

Kleinanzeigen zu scrapen ist technisch geblockt und verstößt gegen die Nutzungsbedingungen; einen
öffentlichen deutschen Datensatz zu Kleinanzeigen-Betrug gibt es nicht (Stand 10/2026). Der Kern des
Datensatzes wird deshalb selbst aufgebaut:

- **Seriöse Inserate und Chats** (viele, einfach): normale Anzeigen in der Extension als *Seriös* labeln,
  eigene Chats im Annotation-Workspace hinzufügen.
- **Betrugsfälle:** Watchlist Internet, Phishing-Radar der Verbraucherzentrale, polizei-beratung.de,
  Sicherheitshinweise von Kleinanzeigen, Foren. Screenshots hochladen, **Quelle dokumentieren**, Bilder
  nicht weitergeben (Urheberrecht).
- **Eigene Verkäufe:** Betrugsnachrichten als Screenshot sichern – nicht antworten, nichts anklicken.
  Keine Fake-Inserate einstellen.
- **Hugging Face** (Ergänzung für Nachrichten): `tanaos/synthetic-spam-detection-dataset-german`
  (15 000 synthetische SMS, Labels teils fehlerhaft), eine maschinell übersetzte SMS-Spam-Sammlung,
  `shaw/scambench-training` (66 typisch deutsche Maschen). Einen deutschen Kleinanzeigen-Chat-Datensatz
  gibt es dort nicht; vorbereitet in `huggingface.yaml` (`aktiv: false`).
- **Synthetisch (mit Vorsicht):** Varianten bekannter Maschen nur als eigene `source` und **nie** im Testset.
- **Kaggle/Zenodo** (Recherche 10/2026, Suchbegriffe `classified ads fraud`, `online marketplace scam`,
  `e-commerce fraud detection`): kein Datensatz zu deutschen Kleinanzeigen. Am nächsten kommt EMSCAD
  („Real or Fake Job Postings“, 17 880 englische Stellenanzeigen, 866 Betrug, 2012–2014) – nur mit
  mehrsprachigem Modell sinnvoll; Craigslist-Studien veröffentlichen ihre Daten meist nicht.
  Transaktions-Datensätze („credit card fraud“) enthalten keinen Text und passen nicht.
- **Foren** (z. B. Reddit-Communities zu Kleinanzeigen-Betrug, Verbraucherzentralen): wertvoll für
  Maschen und Formulierungen. Reddit verbietet Bots per robots.txt → Website nur von Hand lesen, automatisch
  nur über die offizielle API (`--quellen reddit`); `scamguard data sammeln --quellen
  foren` holt nur Quellen, die es erlauben.
- **Was wir bewusst nicht tun:** Kleinanzeigen scrapen (auch nicht über Scraper-Dienste) und
  „Honeypots“ (Fake-Inserate, um Betrüger anzulocken) – beides verstößt gegen die Nutzungsbedingungen,
  Honeypots täuschen zudem echte Interessenten. Die Merkmale aus solchen Studien (Preisausreißer,
  Konto am selben Tag angelegt, übersetzte Sprache, kopierte Fotos) prüft ScamGuard direkt im Inserat.

---

## Projektstruktur

```
ScamGuard/
├── config.yaml                  Schwellen, Fusion-Gewichte, Modellpfade, LLM, Annotation
├── data/
│   ├── datensatz_fuellen_inserate/ ganze Inserate (Screenshots, .html, PDF): betrug/, serioes/
│   ├── datensatz_fuellen_text/  Tabellen (CSV/JSONL/Parquet), huggingface.yaml
│   ├── datensatz_fuellen_bilder/ Produktfotos: betrug/, serioes/
│   ├── einstufung/kategorien.yaml Kategoriensystem (Arbeitsdateien daneben, nicht im Git)
│   ├── lexicons/                Betrugsphrasen, Preisreferenzen, Fake-Bild-Hashes
│   ├── quellen/foren.yaml       Foren-Threads für `data sammeln --quellen foren`
│   ├── samples/                 synthetische Demo-Inserate
│   ├── raw/  images/            Rohdaten (nicht im Git)
│   ├── processed/               train/val/test nach `data build` (nicht im Git)
│   └── finetune/                Feintuning-Daten für Qwen (nicht im Git)
├── models/                      trainierte Gewichte (nicht im Git)
├── examples/                    Beispiel-Inserat für `scamguard scan`
├── frontend/
│   ├── app.py                   Streamlit: prüfen, labeln, Daten & Training
│   └── einstufen.py             Annotation-Workspace (Sätze, Bilder, Übereinstimmung, Konflikte)
├── extension/                   Browser-Extension (Manifest V3, Chromium + Firefox)
│   ├── background.js            Service Worker/Hintergrundskript: Server, Badge, Kontextmenü
│   ├── content/                 extract.js (Auslesen) · highlight.js (Markieren) · panel.js
│   └── popup/                   Toolbar-Popup: Score, Serverstatus, Einstellungen
├── docs/                        einstufung.md · llm_feintuning.md · backlog.md · screenshots/
├── scripts/                     Hilfsskripte (Firefox-Entwicklungsprofil, Icons)
├── src/scamguard/
│   ├── schema.py                Datenschema (Listing, Signal, ScanResult)
│   ├── data/                    Discovery, Loader, Build/Split, Labels, Import/OCR, Collector,
│   │                            Anonymisierung, Phrasen, Annotation, Übereinstimmung, Kategoriensystem
│   ├── features/                URL/Mail/IBAN/Telefon, Sprache, Lexikon, Preis, Merkmalstabelle
│   ├── models/                  rules, text_classifier, image_model, llm_judge, automl, deep_forest, fusion
│   ├── pipeline.py              alle Detektoren → Fusion
│   ├── training.py · evaluate.py · finetune.py
│   ├── api.py                   FastAPI (Backend für die Extension)
│   └── cli.py                   `scamguard …`
└── tests/                       Unit-/Integrationstests · e2e/ = Browser-Tests
```

---

## Entwicklung

Setup, Checks, Branch- und PR-Workflow sowie Konventionen: **[CONTRIBUTING.md](CONTRIBUTING.md)**.
Offene Aufgaben mit Akzeptanzkriterien: **[docs/backlog.md](docs/backlog.md)**.

**Labels im Team teilen:** Am einfachsten arbeiten alle im selben privaten Ordner (z. B. OneDrive):
`SCAMGUARD_EINSTUFUNG_ORDNER=<Pfad> scamguard ui`. Jede Person schreibt nur eigene Dateien, gelesen
werden alle. Ohne gemeinsamen Ordner lassen sich die Dateien im Workspace unter „Mit dem Team teilen“
austauschen. Schnelle Labels aus Extension und Upload (`data/raw/eigene_labels.jsonl`) können dort als
Aufgaben zur Zweitprüfung geholt oder als `labels_<name>.jsonl` nach `data/datensatz_fuellen_text/`
kopiert werden. Bilder werden nicht übertragen; keine echten Namen, Nummern oder Adressen teilen.

**Gelabelte Inserate im Team zusammenführen:** Inserate, Fotos und Labels kommen **nicht ins Git**
(`.gitignore` – das Repo ist öffentlich; Personendaten, Urheberrecht, Größe). Stattdessen gibt jede Person
ihre Daten als ZIP ab, zusammengeführt und trainiert wird an einer Stelle:

1. **Abgeben (alle):** `scamguard team export --name <Vorname>` → `scamguard_<name>_<datum>.zip` mit allen
   eigenen Einstufungen (Inserat-Ordner mit Fotos, Prüflisten/Tabellen, Produktfotos, Dateien des
   Annotation-Workspace; ohne synthetische Daten). Die Datei über den **Upload-Link** hochladen – den Link
   gibt es im Team, er steht bewusst nicht im öffentlichen Repo. Neu exportieren und erneut hochladen geht
   jederzeit.
2. **Zusammenführen (eine Person):** Der Upload-Link zeigt auf einen privaten OneDrive-Ordner („Dateien
   anfordern“). Dessen Pfad einmal in `config.local.yaml` eintragen (`team: {eingang: <Pfad>}`; Mac z. B.
   `~/Library/CloudStorage/OneDrive-Persönlich/…/Eingang`), dann `scamguard team import` und
   `scamguard retrain`. Schon eingelesene ZIPs werden übersprungen, gleiche Inserate nicht doppelt
   übernommen; Tabellen landen als `team_<name>__<datei>`, Fotos unter `team_<name>/`.

Darauf achten, dass beide Klassen vorkommen – nur Betrug ergibt ein einseitiges Modell (`retrain` warnt).
Echte Inserate verteilen sich auf Training, Validierung und Test; erst damit sind die Kennzahlen
aussagekräftig. Ab etwa 20 Beispielen je Klasse urteilt auch AutoML.

**Arbeitsbereiche:**

| Bereich | Umfang |
|---|---|
| Daten & Evaluation | Quellen und Lizenzen, Labeling und Kategoriensystem, festes Testset, Metriken, Fehleranalyse |
| Text & NLP | Regeln und Lexikon, Textmodelle (Baseline, GBERT), LLM-Prompt und Feintuning |
| Bild, Frontend & Extension | Bildmodelle (CLIP, CNN, Hash-Liste), Web-App, API, Extension-Adapter, Nutzertests |

## Roadmap

1. Echte, im Team gelabelte Daten → Baseline neu trainieren → erste belastbare Kennzahlen.
2. GBERT feintunen (Colab / bwUniCluster, `scamguard train text-transformer`).
3. Fusion-Gewichte lernen statt setzen (logistische Regression auf Val-Scores), Scores kalibrieren.
4. Bildmodell mit echten Inseratsfotos und Bild-Labels aus dem Workspace trainieren, CLIP-Schwellen justieren.
5. LLM-Judge auf dem Testset gegen die anderen Modelle messen (Kosten vs. Nutzen), Qwen feintunen.
6. Preisreferenzen mit echten Marktdaten ersetzen (aktuell Platzhalter).

## Datenschutz, Fairness, Kosten

- **Datenschutz (DSGVO):** Keine Namen, Telefonnummern, Adressen oder IBANs echter Personen in
  Trainingsdaten. Der LLM-Judge sendet keinen Verkäufernamen und keinen Ort und ist standardmäßig aus.
  Bilder gehen nur mit `send_images: true` an eine API.
- **Fairness:** Fehlerhaftes Deutsch ist ein *schwaches* Signal (niedrige Gewichte) – ehrliche
  Nicht-Muttersprachler dürfen nicht pauschal als Betrüger gelten. Falsch-Positive werden gezielt ausgewertet.
- **Bewertung:** Kennzahlen auf den synthetischen Demo-Daten sind bedeutungslos. Die Auswertung pro
  Quelle (`evaluate`) zeigt, ob ein Modell Betrug oder nur die Quelle gelernt hat.
- **LLM-Kosten:** Die KI-Analyse ist standardmäßig an, aber nur mit dem lokalen Qwen kostenlos; mit `provider: anthropic` schaltet sie die Web-App nicht von selbst ein (`--ohne-llm` bzw. Schalter zum Abschalten). Modell und Effort in `config.yaml`.
  API-Keys per Umgebungsvariable (`ANTHROPIC_API_KEY`), nie im Code.
