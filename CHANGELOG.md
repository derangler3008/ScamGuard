# Changelog

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionierung nach SemVer.

## [Unveröffentlicht]

### Hinzugefügt
- Teamarbeit: `CLAUDE.md` (Claude Code liest Projektwissen und Teamregeln automatisch),
  `docs/mit_claude_arbeiten.md` mit fertigen Prompts für die Gruppenaufgaben, README-Abschnitt
  „Zusammenarbeit im Team“ (Branches `Jannis`/`Gabriel`, Pull Requests, Einstufungen teilen).

## [0.7.0] – 2026-10-05

### Hinzugefügt
- **Chat-Scan im Kleinanzeigen-Postfach** (Extension, `kleinanzeigen.de/m-nachrichten…`): prüft den
  Verlauf automatisch und jede neu eintreffende Nachricht ohne Neuladen (MutationObserver, 1,5 s
  gebündelt, kein erneuter Scan durch eigene Markierungen). Auffällige Nachrichten werden eingerahmt,
  Fundstellen markiert, das Panel nennt die Zahl geprüfter Nachrichten. Ohne feste Selektoren (das
  Postfach hat keine stabilen IDs): Verlauf = ARIA-Log bzw. innerster Scrollbereich; Vorschauen anderer
  Unterhaltungen und das Eingabefeld zählen nicht. Einstufen speichert den Verlauf (Schlüssel = Text).
- **Neue Chat-Kriterien** im Lexikon: „Geld zuerst“/Anzahlung zum Reservieren, Abholung ausgeschlossen,
  Verknappung („sonst ist es bald weg“, „habe 5 Interessenten“, „wer zuerst zahlt“), „E-Mail/
  Handynummer für die Zahlung“ (Einstieg der „Sicher bezahlen“-Masche, aus echten Foren-Zitaten),
  Tier-Transportkosten, Wohnungs-Masche (Schlüssel per Post, Kaution vorab), „Hallo Mama, neue
  Nummer“, „Sicherungskonto“ (hart), QR-Code scannen, „über die Funktion Sicher bezahlen bezahlt“.
  Gemessen: Treffer auf gesammelten Warnungen 15 % → 33 %, auf normalen Nachrichten 0,5 % → 0,9 %.
- **`scamguard data phrasen`**: Wortfolgen, die in Betrugstexten auffällig häufig sind (Log-Odds-Ratio
  mit informativem Dirichlet-Prior), mit Hinweis, ob das Lexikon sie kennt; `--hf` vergleicht zusätzlich
  mit den deutschen Hugging-Face-Nachrichten.
- **`scamguard data sammeln --quellen foren`**: Erfahrungsberichte aus Foren (Liste in
  `data/quellen/foren.yaml`; XenForo, WoltLab, vBulletin-Archiv). Erster Lauf: 37 Threads aus 9 Foren,
  222 Berichte → `data/raw/foren_erfahrungsberichte.jsonl` mit beschriebener Masche (nur Auswertung),
  dazu eine **Prüfliste** zitierter Nachrichten (`gesammelt_foren.csv`, Spalte `betrug` leer, bis ein
  Mensch ja/nein einträgt; Einträge bleiben beim erneuten Sammeln erhalten). Reddit (robots.txt sperrt
  alle Bots), gutefrage.net (Bot-Sperre) und eBay-Community (AGB) bewusst nicht.
- `data/redact.py`: anonymisiert Mails, Telefonnummern, IBANs, Link-Pfade und @Namen, behält aber die
  Form für die Merkmale (`anonym@gmail.com`, `+44 1111 111111`, IBAN mit gültiger Prüfziffer).
- **Feintuning von Qwen**: `scamguard llm-daten` erzeugt `data/finetune/{train,valid,test}.jsonl` im
  Chat-Format von mlx_lm – exakt der Prompt des Betriebs, Zielantwort aus Labels und Regel-Treffern;
  `scamguard llm-server --adapter …` bzw. `llm.local.adapter_path`; `scamguard evaluate --llm --max N`.
  Anleitung: `docs/llm_feintuning.md`.

### Geändert
- LLM-Prompt: Chats zwischen Käufer und Verkäufer (Betrug in beide Richtungen), Chat-Maschen ergänzt;
  reine Chats ohne leere Inseratsfelder, Abschnitt heißt „Chat-Nachrichten“.
- Textmodelle urteilen über reine Chats erst, wenn sie je Klasse mindestens 20 Chats gelernt haben (wird
  beim Training mitgespeichert) – das Demo-Modell hielt „Hallo, ist das noch da?“ sonst für 72 % Betrug.

### Behoben
- IBAN-Erkennung nahm Folgewörter mit vier Zeichen („oder“, „bitte“) als Kontonummer-Block mit; die IBAN
  war dann ungültig und wurde übersehen – samt einer zweiten IBAN direkt dahinter.
- Seiten in ISO-8859-1 (ältere Foren) werden mit dem richtigen Zeichensatz gelesen.
- README nannte noch die alten Button-Beschriftungen mit Symbolen.

## [0.6.1] – 2026-10-02

### Behoben
- Sprachfilter verwarf kurze deutsche Inserate ohne Füllwörter („iPhone 13 128GB – Top Zustand, Akku 89 %“)
  und deutsche Phishing-Betreffzeilen – seriöse Inserate sind oft knapp, das hätte die Trainingsdaten
  einseitig gemacht. Jetzt wird nur bei klaren Hinweisen auf Englisch verworfen; Links/Domains zählen
  nicht mit („mob-willhaben.at“). Gesammelte Warnungen: 617 statt 610 Texte geladen.
- Eigene Einstufungen (`eigene_labels`) laufen nie durch den Sprachfilter.
- Gefunden beim Prüfen, ob Teammitglieder ihre Einstufungen als Datei teilen können (geht: JSONL in
  `data/datensatz_fuellen_text/`).

## [0.6.0] – 2026-10-02

### Hinzugefügt
- **`scamguard data sammeln`**: öffentliche Betrugswarnungen als Textdaten (Nachrichten, E-Mails, SMS –
  keine Bilder): Watchlist Internet „Phishing-Alarm“ (vollständiger Wortlaut, ~400 Meldungen),
  Phishing-Radar der Verbraucherzentrale (Betreffzeilen und Zitate, ohne deren Erklärtexte), Zitate
  aus Watchlist-Artikeln zu Kleinanzeigen/Marktplätzen → `data/datensatz_fuellen_text/
  gesammelt_warnungen.csv`, Quelle je Zeile. Kleinanzeigen selbst wird bewusst nicht gescrapt.
  Erster Lauf: 619 Texte (399 Phishing-Wortlaut, 174 VZ-Zitate, 46 Artikel-Zitate), davon 610 deutsch.
  Artikel-Zitate nur als ganze Sätze (≥ 40 Zeichen, ≥ 5 Wörter) – Domains/Namen fielen sonst mit hinein.
- Warnung beim Neu-Trainieren, wenn eine Klasse unter 15 % liegt (z. B. nur gesammelte Betrugstexte).
  Höflich: robots.txt (401/403 = verboten), 1,5 s Pause, Cache nur für unveränderliche Einzelseiten.
- **Neue Erkennungskriterien für Nachrichten/Mails/SMS** im Lexikon: Konto-gesperrt-Phishing,
  Paket-/Zoll-Masche, Code-Weitergabe (Konto-Übernahme, hart), „Zahlung reserviert/wird freigegeben“,
  Gewinn-/Erbschafts-/Rendite-Spam, Käufer ohne Besichtigung; „innerhalb von 24 Stunden“ als Zeitdruck.

## [0.5.2] – 2026-10-02

### Geändert
- Keine Emojis mehr (User-Wunsch: „sieht zu sehr nach KI aus“). Extension: schlichte SVG-Linien-Icons
  (`extension/lib/icons.js`, Schild mit Lupe wie das Toolbar-Icon), Schweregrade als CSS-Formen
  (Dreieck/Raute/Kreis – weiterhin nicht nur über Farbe unterscheidbar), Buttons nur mit Text.
  Web-App: Text-Tabs, farbige Labels („Hoch“, „aktiv“), dezente Material-Icons, App-Icon = Extension-Icon.
- `huggingface.yaml`: zwei deutsche Spam-Datensätze vorbereitet (aus), README: konkrete Datenquellen.

### Behoben
- Content Scripts für „markierten Text prüfen“ kommen jetzt aus dem Manifest (eine Liste statt zwei) –
  sonst hätte das neue Icon-Skript auf fremden Seiten gefehlt (von den Browser-Tests gefunden).

## [0.5.1] – 2026-10-02

### Behoben
- `scamguard ui` bei schon laufender App: öffnet sie im Browser und meldet „läuft bereits“, statt still
  eine zweite Kopie auf Port 8502 zu starten.
- `watchdog` als Abhängigkeit: Streamlit bemerkt Code-Änderungen effizient, der verwirrende
  Start-Hinweis („xcode-select --install / pip install watchdog“) entfällt.
- Extension unverändert (bleibt 0.5.0).

## [0.5.0] – 2026-10-02

### Hinzugefügt
- **Inserat hochladen & einstufen** (neuer erster Tab im Frontend): Screenshots, gespeicherte Seite
  (`.html`), PDF oder Text hineinziehen → Titel, Preis, Ort, Kategorie, Kontoalter und Beschreibung
  werden ausgelesen → *⚠ Betrug* / *✓ Seriös* klicken. Chatverlauf optional. Felder lassen sich
  korrigieren, müssen aber nicht. Die ScamGuard-Einschätzung ist beim Einstufen standardmäßig verborgen
  (kein Anker-Effekt) und wird nach dem Klick gezeigt.
- Texterkennung lokal mit Apple Vision (`data/ocr.py`): sehr hohe Screenshots in Kacheln, rechte
  Seitenspalte (Anbieter/Login) vom Inserat getrennt, umbrochene Absätze wieder zusammengesetzt;
  ~0,2–0,6 s pro Screenshot. Bilder mit wenig Text gelten als Produktfotos.
- `data/listing_import.py`: Kleinanzeigen-Seiten über dieselben Stellen wie die Extension, sonst
  Textanalyse (Titel über dem Preis, Abschnitt „Beschreibung“). Anbietername, Straße und Hausnummer
  werden nicht übernommen.
- Ordner `data/datensatz_fuellen_inserate/{betrug,serioes}/` für viele Inserate auf einmal
  (Datei = Inserat, Unterordner = Inserat aus mehreren Dateien).
- Tab **Meine Daten & Training**: Zähler, Ordner im Finder öffnen, alle Datensätze, Button
  „Jetzt neu trainieren“ (inkl. CNN/GBERT) mit Kennzahlen; `scamguard retrain --ohne-demo`.
- Extension-Popup erklärt auf Seiten ohne Inserat, wo die automatische Prüfung läuft.

### Geändert
- Einstufen im Tab „Felder selbst eingeben“ direkt unter dem Ergebnis (ersetzt den Tab „Labeln“).
- Trainingslogik aus der CLI nach `scamguard/training.py` (CLI und Frontend nutzen dieselbe).
- „Über das Projekt“ und README: welche Bausteine neuronale Netze sind, was vortrainiert ist und was
  ihr trainiert; Abschnitt „Eigenleistung“.

## [0.4.1] – 2026-10-02

### Behoben
- `scamguard start`/`api` bei schon laufendem ScamGuard: klare Meldung „läuft bereits“ (Exit 0)
  statt uvicorns `[Errno 48] address already in use`; Qwen wird dabei gar nicht erst gestartet.
- Port von einem anderen Programm belegt → Hinweis mit freiem Port (`--port`), Exit 1.
- Läuft Qwen schon (z. B. separat per `scamguard llm-server`), nutzt `start` es mit, statt einen
  zweiten Qwen-Server zu starten; `llm-server` meldet ebenfalls „läuft bereits“.
- Extension unverändert (bleibt 0.4.0).

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
