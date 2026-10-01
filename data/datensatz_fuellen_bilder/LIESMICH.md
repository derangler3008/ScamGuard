# Datensatz füllen: Bilder

Bilder (`.jpg`, `.jpeg`, `.png`, `.webp`) einfach in den passenden Unterordner legen –
der Ordner ist das Label:

- `betrug/`  – Bilder aus Betrugsinseraten (gestohlene Produktfotos, Fake-Screenshots …)
- `serioes/` – Bilder aus echten Inseraten

Unterordner dürfen weitere Ordner enthalten. Andere Ordnernamen: `scam`/`fake` bzw. `echt`/`legit`.
Danach trainieren: `scamguard retrain --bilder` (Bildmodell, braucht ein paar hundert Bilder pro Klasse).
Prüfen, was erkannt wurde: `scamguard data list`

Die Bilder landen nicht im Git (siehe .gitignore).
