# Datensatz füllen: Text

Lege hier Dateien mit Inseraten oder Nachrichten ab – sie werden beim nächsten
`scamguard retrain` (bzw. `scamguard data build`) automatisch eingelesen.

**Formate:** `.csv` (auch deutsche Excel-CSV mit `;`), `.tsv`, `.jsonl`, `.parquet`

**Spalten** (Name egal ob deutsch/englisch, Groß-/Kleinschreibung egal):

| Bedeutung | erkannte Spaltennamen |
|---|---|
| Label (Pflicht) | `betrug`, `label`, `fake`, `scam`, `ist_betrug`, `klasse` |
| Titel | `titel`, `title`, `überschrift` |
| Text | `beschreibung`, `text`, `description`, `inhalt` |
| Nachrichten | `nachrichten`, `nachricht`, `chat`, `messages` |
| Preis | `preis`, `price` |
| Kategorie | `kategorie`, `category` |
| Masche | `masche`, `scam_type` |

**Label-Werte:** `ja`/`nein`, `betrug`/`seriös`, `fake`/`echt`, `scam`/`legit`, `spam`/`ham`, `1`/`0`

Vorlage: `_vorlage_inserate.csv` (Dateien mit `_` am Anfang werden ignoriert – Kopie ohne `_` anlegen).
Hugging-Face-Datensätze: in `huggingface.yaml` eintragen.
Prüfen, was erkannt wurde: `scamguard data list`

Datenschutz: keine Namen, Telefonnummern oder Adressen echter Personen – vorher schwärzen.
Die Datendateien landen nicht im Git (siehe .gitignore).

**Prüfliste aus Foren** (`gesammelt_foren.csv`, von `scamguard data sammeln --quellen foren`):
Nachrichten, die Betroffene in Foren zitiert haben. Die Spalte `betrug` ist leer – in Excel/Numbers
öffnen, pro Zeile `ja` (Betrügernachricht) oder `nein` (harmlos, z. B. Support-Antwort) eintragen, als
CSV speichern. Leere Zeilen werden beim Training übersprungen; eure Einträge bleiben beim nächsten
Sammeln erhalten. `vorschlag` = ja heißt nur: enthält ein bekanntes Warnsignal.
