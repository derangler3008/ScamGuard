# Mit Claude Code am Projekt arbeiten

Für alle im Team, die mit Claude Code (Terminal, Desktop-App oder VS Code) mitentwickeln. Claude
liest beim Start automatisch `CLAUDE.md` im Projektordner und kennt damit Aufbau, Befehle und
Teamregeln. Ihr müsst das Projekt also nicht jedes Mal erklären, sondern könnt direkt loslegen.

## Einmalig einrichten

```bash
git clone https://github.com/derangler3008/ScamGuard.git
cd ScamGuard
git switch Jannis          # bzw. Gabriel – euer eigener Branch
claude                     # Claude Code im Projektordner starten
```

Erster Prompt (richtet alles ein und prüft, ob es läuft):

```text
Richte das Projekt auf meinem Rechner ein (Python-3.12-venv, pip install -e ".[dev]") und führe
die schnellen Tests aus. Sag mir danach kurz, ob alles läuft und was ich unter meinem
Betriebssystem anders machen muss als im README beschrieben.
```

## Vor jeder Arbeitssitzung

```text
Hole den aktuellen Stand von main in meinen Branch (git pull origin main), löse Konflikte mit mir
gemeinsam und fasse in drei Sätzen zusammen, was sich seit meinem letzten Stand geändert hat.
```

## Fertige Prompts für die Gruppenaufgaben

Kopieren, bei Bedarf anpassen, absenden. Claude baut, testet und erklärt. Entscheidungen und
Auswertung (was ist Betrug, was bedeuten die Zahlen) bleiben eure Eigenleistung.

### Datensatz: Datenspende-Modus

```text
Baue in die Web-App einen Tab „Datenspende“: Freunde und Kommilitonen laden Screenshots oder Text
von Kleinanzeigen-Chats hoch, bestätigen vorher eine Einwilligung (Zweck: DHBW-Studienprojekt,
Speicherung nur lokal, Löschung auf Wunsch) und stufen selbst als Betrug oder seriös ein.
Kontaktdaten automatisch mit data/redact.py anonymisieren, Namen im Text zur Kontrolle hervorheben.
Mit Tests, Changelog und kurzem README-Abschnitt.
```

### Datensatz: Übereinstimmung beim Einstufen messen

```text
Schreib einen Befehl `scamguard data kappa`, der zwei oder drei Label-Dateien
(labels_<name>.jsonl) vergleicht: gemeinsame Inserate finden, Cohen's bzw. Fleiss' Kappa berechnen,
die Fälle mit Uneinigkeit auflisten. Mit Tests. Erklär mir danach, wie ich die Werte im Bericht
interpretiere.
```

### Inhaltsanalyse der Foren-Erfahrungsberichte

```text
Hilf mir bei einer qualitativen Inhaltsanalyse von data/raw/foren_erfahrungsberichte.jsonl:
Schlag ein Kategoriensystem für Betrugsmaschen mit Definition und Ankerbeispiel vor (ich passe es
an), erstelle eine Codier-Tabelle (CSV) für zwei unabhängige Codierer und ein Skript für die
Übereinstimmung. Zum Schluss: Welche der beschriebenen Maschen erkennt ScamGuard heute nicht?
```

### Modellvergleich für den Bericht

```text
Erstelle einen reproduzierbaren Modellvergleich auf dem festen Testset: Regeln, TF-IDF, GBERT
(falls trainiert), Qwen ohne Feintuning und Fusion. Ausgabe als Tabelle (Precision, Recall, F1,
ROC-AUC) plus Diagramm als PNG in docs/. Dazu eine Ablation: Welche Lexikon-Gruppe trägt wie viel
bei? Erklär die Ergebnisse, aber schreib keine Schlussfolgerungen für den Bericht – die formuliere
ich.
```

### Robustheit: umformulierte Betrugsnachrichten

```text
Baue einen Robustheitstest: Aus den Betrugsnachrichten im Testset sollen Varianten ohne die
typischen Schlüsselwörter entstehen (ich schreibe 20 davon selbst, du schlägst weitere vor, ich
prüfe sie). Miss, welche Modelle die Varianten noch erkennen. Varianten nur in einer eigenen
Testdatei, nie im Training.
```

### Fairness: Fehlalarme bei gebrochenem Deutsch

```text
Ich möchte prüfen, ob ScamGuard seriöse Inserate von Nicht-Muttersprachlern zu oft als Betrug
markiert. Hilf mir, ein kleines Testset aufzubauen (Vorlage-CSV, Hinweise zum Sammeln), und
schreib eine Auswertung der Fehlalarm-Rate im Vergleich zu muttersprachlichen Inseraten.
```

### Nutzertest der Extension

```text
Bereite einen Nutzertest der Browser-Extension mit 5–8 Personen vor: Aufgaben (Think-Aloud),
SUS-Fragebogen auf Deutsch, Protokollvorlage und ein Skript, das die SUS-Werte auswertet.
Achte darauf, dass auch ältere Personen ohne Technikerfahrung mitmachen können.
```

### Lexikon aus echten Fällen erweitern

```text
Führe `scamguard data phrasen --hf` aus, zeig mir die 30 interessantesten neuen Kandidaten und
schlag Muster für scam_signals_de.yaml vor. Ich entscheide, welche aufgenommen werden; zu jedem
Muster bitte Tests mit Treffer und mit harmlosen Gegenbeispielen.
```

### Qwen feintunen (Mac mit Apple-Chip)

```text
Führe mich durch das Feintuning nach docs/llm_feintuning.md: zuerst prüfen, ob genug ausgewogene
Daten da sind, dann Ausgangswert messen, trainieren, erneut messen. Bevor du den Qwen-Server
beendest oder das Training startest, frag mich.
```

### Fehler finden und beheben

```text
In der Extension passiert Folgendes: <beschreiben, wo und was>. Finde die Ursache, schreib zuerst
einen Test, der den Fehler zeigt, und behebe ihn dann.
```

## Fertig werden: Pull Request

```text
Prüfe meine Änderungen wie ein Senior-Reviewer (Lint, Tests, Lesbarkeit, Datenschutz – keine
echten Personendaten, keine Datendateien im Commit), committe sie mit einer deutschen Beschreibung
und erstelle einen Pull Request von meinem Branch nach main.
```

## Gut zu wissen

- **Prüfen statt blind übernehmen:** Claude kann sich irren, besonders bei Zahlen und Fakten. Tests
  und Kennzahlen selbst anschauen.
- **Daten nie hochladen:** Echte Chats und Einstufungen bleiben auf eurem Rechner. Zum Teilen gibt es
  eine Datei `labels_<name>.jsonl` über einen privaten Ordner (README, „Zusammenarbeit im Team“).
- **Hilfsmittelerklärung:** Notiert, wofür ihr Claude genutzt habt, das gehört in den Bericht.
