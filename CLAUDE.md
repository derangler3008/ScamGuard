@CONTRIBUTING.md

## Für Claude: Projekt auf den neuesten Stand bringen

- **Beim Sitzungsstart** führt ein Hook (`.claude/settings.json`) `scripts/update_check.py` aus. Steht im
  Kontext eine Meldung „[ScamGuard] Code ist vX, installiert ist vY“, die genannten Schritte zuerst
  erledigen – noch vor der eigentlichen Aufgabe – und der Person kurz sagen, was gemacht wurde.
- **„Hol den neuesten Stand“ o. Ä.:** auf dem eigenen Branch `git pull origin main` (bzw. `main`
  hineinmergen, Konflikte mit der Person klären), danach `python scripts/update_check.py` ausführen und die
  Schritte abarbeiten. Dasselbe nach jedem anderen Pull oder Merge.
- **Abarbeiten:** `pip install -e …` immer mit dem Python aus `.venv` (Befehl steht in der Meldung).
  Schritte, die nur für ein Betriebssystem gelten („Windows-PC“, „Mac“), nur dort ausführen; als optional
  markierte Schritte anbieten statt ungefragt ausführen. Downloads über 1 GB (Modelle, ROCm/CUDA) und
  Installationen außerhalb von `.venv` vorher mit der Person abstimmen.
- **Prüfen:** danach `pytest -m "not e2e"` und erneut `python scripts/update_check.py` – es bleibt still,
  wenn alles erledigt ist. Laufende Dienste neu starten, an das Neuladen der Extension erinnern.
- **Daten kommen nicht über Git:** nie Datendateien committen. „Gib meine Daten ab“ o. Ä.:
  `scamguard team export --name <Vorname der Person>` und der Person sagen, dass sie das ZIP über den
  Upload-Link des Teams hochladen soll (Link steht nicht im Repo – nicht danach suchen, sondern die Person
  fragen bzw. auf das Team verweisen). Zusammenführen: `scamguard team import`, dann `scamguard retrain`
  (README, „Gelabelte Inserate im Team zusammenführen“).
- **Neue Version veröffentlichen:** im CHANGELOG unter der Version einen Abschnitt „### Nach dem Update“
  mit den Schritten für alle anlegen – genau den liest `update_check.py` für die anderen aus.
