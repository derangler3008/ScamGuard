# Datensatz füllen: ganze Inserate

Hier landen Inserate, wie ihr sie gesehen habt – das Label ist der Unterordner:

- `betrug/`  – Betrugsinserate (und Chatverläufe dazu)
- `serioes/` – echte Inserate

Erlaubt: Screenshots (`.png`, `.jpg`, `.webp`), gespeicherte Seiten (`.html`, „Seite speichern
unter …“), PDFs („Drucken → Als PDF speichern“) und `.txt`.

- **Eine Datei = ein Inserat.**
- **Ein Unterordner = ein Inserat aus mehreren Dateien**, z. B. `betrug/ps5_fall/` mit zwei
  Screenshots der Anzeige und den Produktfotos. Bilder mit wenig Text gelten als Produktfotos.

ScamGuard liest Titel, Preis, Ort, Kategorie, Kontoalter und Beschreibung beim Training selbst aus
(Texterkennung lokal mit Apple Vision, nur auf dem Mac). Danach: `scamguard retrain` oder im
Frontend „Jetzt neu trainieren“. Prüfen, was erkannt wurde: `scamguard data list`.

Einzelne Inserate gehen schneller im Frontend (`scamguard ui` → „Inserat hochladen & einstufen“).
Dateien mit `_` am Anfang werden ignoriert. Die Dateien landen nicht im Git (siehe .gitignore).
