# Backlog

Offene Aufgaben mit Ziel und Akzeptanzkriterien. Workflow (Branch, Checks, Pull Request):
[CONTRIBUTING.md](../CONTRIBUTING.md). Erledigte Punkte wandern ins [CHANGELOG](../CHANGELOG.md).

Fachliche Entscheidungen – was als Betrug gilt, wie Kennzahlen zu bewerten sind – trifft das Team;
Tooling und Auswertungen bereiten sie vor.

## Daten und Labeling

### DATA-1 · Kalibrierungsrunde für das Kategoriensystem

**Ziel:** gemeinsames Verständnis der Kategorien, bevor im großen Stil gelabelt wird.

- 20 abwechslungsreiche Texte (verschiedene Maschen, Grenzfälle, seriöse Gegenbeispiele) per
  `scamguard einstufen holen` als Aufgaben anlegen.
- Gemeinsam labeln, Abweichungen besprechen, Definitionen in `data/einstufung/kategorien.yaml`
  schärfen, `version` erhöhen.
- **Fertig, wenn:** Kategoriensystem v2 im CHANGELOG, Liste der geklärten Grenzfälle in
  `docs/einstufung.md`.

### DATA-2 · Screenshots im Annotation-Workspace

**Ziel:** Chats aus dem Bekanntenkreis ohne Abtippen erfassen (Datenspende).

- Unter *Aufgaben holen → Eigenen Text hinzufügen* zusätzlich Screenshots annehmen, Text per
  `data/listing_import.py` (Apple Vision) auslesen.
- Einwilligung wie beim Texteingang abfragen (Zweck, lokale Speicherung, Löschung auf Wunsch);
  Kontaktdaten automatisch anonymisieren, erkannte Namen zur Kontrolle hervorheben.
- **Fertig, wenn:** Tests für Upload, Einwilligung und Anonymisierung, CHANGELOG, README-Abschnitt.

### DATA-3 · Auswertung einer Labeling-Runde

- `scamguard einstufen bericht --regeln` ausführen; Krippendorffs α je Ebene, Konflikte und
  Widerspruchsquote der Quellen interpretieren.
- **Fertig, wenn:** Kurzbericht in `docs/` mit Kennzahlen (n, α, κ) und Vorschlägen, welche
  Definitionen geschärft werden.

### DATA-4 · Inhaltsanalyse der Foren-Erfahrungsberichte

- `data/raw/foren_erfahrungsberichte.jsonl` nach den Maschen aus `kategorien.yaml` codieren,
  zwei unabhängige Codierer, Übereinstimmung mit `src/scamguard/data/agreement.py`.
- **Fertig, wenn:** Codier-Tabelle (CSV), α je Kategorie, Liste der Maschen, die ScamGuard heute nicht
  erkennt.

## Modelle und Evaluation

### EVAL-1 · Reproduzierbarer Modellvergleich

- Festes Testset: Regeln, TF-IDF, GBERT (falls trainiert), Qwen ohne Feintuning, Fusion.
- Tabelle (Precision, Recall, F1, ROC-AUC) plus Diagramm als PNG in `docs/`; Ablation: Beitrag jeder
  Lexikon-Gruppe.
- **Fertig, wenn:** ein Befehl erzeugt Tabelle und Diagramm aus den aktuellen Splits.

### EVAL-2 · Robustheit gegen umformulierte Betrugsnachrichten

- Varianten der Betrugsnachrichten im Testset ohne typische Schlüsselwörter (Grundstock von Hand
  geschrieben, weitere Vorschläge geprüft).
- Messen, welche Modelle die Varianten noch erkennen. Varianten nur in einer eigenen Testdatei,
  nie im Training.

### EVAL-3 · Fairness: Fehlalarme bei gebrochenem Deutsch

- Kleines Testset seriöser Inserate von Nicht-Muttersprachlern (Vorlage-CSV, Sammelhinweise).
- Fehlalarm-Rate im Vergleich zu muttersprachlichen Inseraten; Sprache aus der Einstufung
  (`sprache` im Export) nutzen.

### EVAL-4 · Lexikon aus verpassten Sätzen

- Aus „Regeln gegen Mensch“ die markierten, aber nicht erkannten Sätze nehmen und Muster für
  `scam_signals_de.yaml` vorschlagen; Aufnahme entscheidet das Team.
- **Fertig, wenn:** jedes neue Muster hat Tests mit Treffer und harmlosen Gegenbeispielen; Recall je
  Warnsignal vorher/nachher dokumentiert.

### MODEL-1 · Qwen feintunen (Apple Silicon)

- Nach `docs/llm_feintuning.md`: Datenmenge und Balance prüfen, Ausgangswert messen, trainieren,
  erneut messen. Der Qwen-Server muss für das Training beendet sein (Speicher).
- **Fertig, wenn:** Vorher/Nachher-Kennzahlen auf demselben Testset, Adapter-Pfad dokumentiert.

### MODEL-2 · Fusion-Gewichte lernen

- Logistische Regression auf den Val-Scores statt fester Gewichte, Scores kalibrieren.

## Produkt

### UX-1 · Nutzertest der Extension

- 5–8 Personen, Think-Aloud-Aufgaben, SUS-Fragebogen auf Deutsch, Protokollvorlage, Auswertungsskript.
  Auch Personen ohne Technikerfahrung einbeziehen.

### BUG · Fehlermeldungen

Vorlage: Wo (URL bzw. Ansicht), was passiert, was erwartet war. Zuerst einen Test schreiben, der den
Fehler zeigt, dann beheben.
