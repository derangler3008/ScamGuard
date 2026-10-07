# Datensatz füllen: ganze Inserate

Hier landen Inserate so, wie sie auf der Plattform zu sehen waren – das Label ist der Unterordner:

- `betrug/`  – Betrugsinserate (und Chatverläufe dazu)
- `serioes/` – echte Inserate

Erlaubt: Screenshots (`.png`, `.jpg`, `.webp`), gespeicherte Seiten (`.html`, „Seite speichern
unter …“), PDFs („Drucken → Als PDF speichern“) und `.txt`.

- **Eine Datei = ein Inserat.**
- **Ein Unterordner = ein Inserat aus mehreren Dateien**, z. B. `betrug/ps5_fall/` mit zwei
  Screenshots der Anzeige und den Produktfotos. Bilder mit wenig Text gelten als Produktfotos.
- **Chatverläufe** in einem solchen Unterordner: Dateiname beginnt mit `chat` (`chat_1.png`,
  `chat_2.png`, auch `.txt`). Ihr Text wird per Texterkennung gelesen und als Chat-Nachrichten übernommen.

**Automatisch beim Labeln:** Jede Einstufung aus Extension oder Web-App wird hier abgelegt, sortiert
nach Kategorie (gewerbliche Anbieter getrennt), z. B. `serioes/auto-gewerblich/<titel>__<schlüssel>/`
mit `inserat.json` (alle Felder, ohne Anbietername; Telefonnummern und Mailadressen anonymisiert) und
allen Fotos des Inserats (bis 20). Stuft man dieselbe Anzeige neu ein, wird der Ordner aktualisiert bzw.
verschoben – so lassen sich auch fehlende Fotos nachholen. Bisherige Labels übernehmen:
`scamguard data ordner`.

ScamGuard liest Titel, Preis, Ort, Kategorie, Kontoalter, Anbieterprofil und Beschreibung beim
Training selbst aus (Texterkennung lokal mit Apple Vision, nur auf dem Mac; Ordner mit `inserat.json`
ohne Texterkennung). Danach: `scamguard retrain` oder in der Web-App „Jetzt neu trainieren“. Prüfen,
was erkannt wurde: `scamguard data list`.

Einzelne Inserate gehen schneller in der Web-App (`scamguard ui` → „Inserat hochladen & einstufen“).
Dateien und Ordner mit `_` am Anfang werden ignoriert. Die Dateien landen nicht im Git (siehe .gitignore).
