# Annotation-Workspace – Ablauf und Methodik

Ein Modell wird nicht besser als seine Labels. Die gesammelten Quellen liefern nur schwache Labels: Die
Warnungen von Verbraucherzentrale und Watchlist Internet sind pauschal „Betrug“, Spam-Datensätze von
Hugging Face folgen ihrer eigenen Spam-Definition, die Prüfliste der Foren ist ungelabelt. Der
Annotation-Workspace (Web-App `scamguard ui`, Tab **Einstufen im Team**) macht daraus belastbare Daten:
ein gemeinsames Kategoriensystem, Labels auf drei Ebenen, unabhängiges Doppel-Labeling mit
Übereinstimmungsmaß und begründete Entscheidung bei Konflikten.

## 1. Einrichtung

**Kategoriensystem** [`data/einstufung/kategorien.yaml`](../data/einstufung/kategorien.yaml):
Entscheidungsregeln, Urteil, Maschen, Warnsignale auf Satzebene und Bildarten, jeweils mit Definition,
Beispiel und Abgrenzung. Die Web-App zeigt es unter *Leitfaden*.

Kalibrierung (ca. eine Stunde, gesamtes Team):

1. Leitfaden gemeinsam durchgehen.
2. 15–20 Texte gemeinsam labeln und Entscheidungen begründen. Wo Entscheidungen auseinandergehen, fehlt
   eine Regel oder eine Definition ist unscharf → in `kategorien.yaml` ergänzen.
3. `version` erhöhen, Änderung im CHANGELOG festhalten. Jede Einstufung speichert die Fassung, nach der
   sie entstand.

**Gemeinsamer Ordner** (empfohlen): ein privater, synchronisierter Ordner, z. B. in OneDrive. Die Web-App
damit starten (Pfad anpassen; dauerhaft als `export` in `~/.zshrc`):

```bash
SCAMGUARD_EINSTUFUNG_ORDNER=~/OneDrive/ScamGuard-Einstufung scamguard ui
```

Jede Person schreibt nur in ihre eigenen Dateien (`aufgaben_<name>.jsonl`, `einstufungen_<name>.jsonl`,
`entscheidungen_<name>.jsonl`), gelesen werden die aller Personen – paralleles Arbeiten erzeugt keine
Sync-Konflikte. Ohne gemeinsamen Ordner: *Aufgaben holen → Mit dem Team teilen* (eigene Dateien
herunterladen, fremde hinzufügen; es wird nur ergänzt, nie ersetzt).

## 2. Eine Labeling-Runde

| Schritt | Wo | Hinweis |
|---|---|---|
| Aufgaben holen | *Aufgaben holen* oder `scamguard einstufen holen` | z. B. je 50 aus der Foren-Prüfliste, den Warnungen und einem Hugging-Face-Datensatz („ausgewogen“ an); eigene seriöse Chats über *Eigenen Text hinzufügen* |
| Labeln | *Einstufen* | Jeder Satz: Warnsignal oder keins. Dann Urteil, Sicherheit, bei Betrug Masche und wer täuscht, Sprache. Bilder: Art und ob das Bild selbst verdächtig ist. *Hinweise der Regel-Erkennung* beim ersten Lesen aus lassen. |
| Zweitmeinung | automatisch | Ein Anteil der Aufgaben (Standard 25 %, `einstufung.doppelt_anteil`) erscheint bei einer zweiten Person zuerst – unabhängig labeln, nicht absprechen. |
| Qualität prüfen | *Qualität und Konflikte* oder `scamguard einstufen bericht --regeln` | α je Ebene. Liegt das Urteil unter 0,667: Konflikte gemeinsam ansehen, Leitfaden schärfen, nächste Runde. |
| Konflikte entscheiden | *Qualität und Konflikte* | idealerweise eine dritte Person oder das Team; Begründung eintragen |
| Exportieren | *Exportieren* oder `scamguard einstufen export` | danach `scamguard retrain --ohne-demo`, für Qwen `scamguard llm-daten` |

Korrekturen: *Einstufen → Deine letzten Einstufungen korrigieren*. Eine Korrektur ist eine neue Zeile;
die alte bleibt als Verlauf erhalten, es zählt die neueste.

## 3. Maßnahmen gegen Verzerrung

- **Label der Quelle verborgen:** erst nach dem eigenen Urteil sichtbar (Ankereffekt).
- **Regel-Hinweise standardmäßig aus:** sonst misst „Regeln gegen Mensch“ nur, ob den Regeln gefolgt wurde.
- **Eigene Reihenfolge je Person:** Das Team bearbeitet parallel verschiedene Texte; welche Texte doppelt
  gelabelt werden, folgt aus der Aufgaben-ID und ist auf allen Rechnern gleich.
- **Ausgewogenes Holen:** gleich viele Texte je Label der Quelle, damit nicht nur Betrug gelabelt wird.
- **Auffälligkeiten:** sehr schnelle Einstufung langer Texte, Betrug ohne markierten Satz, „seriös“ mit
  mehreren Warnsignalen – zum Nachprüfen, nicht automatisch falsch.

## 4. Konsens

| Lage | Status | Im Training? |
|---|---|---|
| eine Person | einfach | ja |
| alle gleich | einig | ja |
| Mehrheit (z. B. 2 : 1) | mehrheit | ja |
| Gleichstand (z. B. 1 : 1) | konflikt | nein, bis entschieden |
| Entscheidung gespeichert | entschieden | ja (außer „unklar“) |
| nur „unklar“ | unklar | nein |

Ein Satz gilt als Warnsignal, wenn **mehr als die Hälfte** der Labelnden ihn markiert hat (bei zwei
Personen also beide). Masche, Rolle, Sprache und Bildart: Mehrheit.

Der Export enthält den Status je Text (`status`, `personen`). Für ein besonders verlässliches Testset nur
Texte mit `personen >= 2` und Status `einig` oder `entschieden` verwenden.

## 5. Export und Weiterverwendung

| Datei (`data/raw/`) | Inhalt | Verwendung |
|---|---|---|
| `einstufung_konsens.jsonl` | Texte mit Konsens-Label, Masche, markierten Sätzen, Sprache, Status | Datensatz `einstufungen`: `data build`, `retrain`, `llm-daten`; bei Dubletten Vorrang vor dem Label der Quelle |
| `einstufung_saetze.jsonl` | jeder Satz mit Warnsignal oder `null` | Lexikon prüfen und erweitern, Satzklassifikator, Fehleranalyse |
| `einstufung_bilder.jsonl` | jedes Bild mit Art und „verdächtig“ | Bildmodell mit echten Bild-Labels statt geerbten; CLIP-Bildarten prüfen (`clip` im Kategoriensystem) |

Beim **Qwen-Feintuning** werden die markierten Sätze zu den `red_flags` der Zielantwort (statt der
Regel-Treffer); die Masche wird auf die Kategorien von Qwen abgebildet (z. B. Mietbetrug → vorkasse).

## 6. Kennzahlen und Dokumentation

- Kategoriensystem (Fassung, Kategorien, Entscheidungsregeln) und Ablauf der Kalibrierung.
- Umfang: Texte je Klasse und Quelle, Anteil doppelt gelabelt.
- **Krippendorffs α** je Ebene mit Anzahl gemeinsamer Einheiten, dazu paarweise Cohens κ.
  Lesart: α ≥ 0,80 verlässlich, ≥ 0,667 vorläufig verwendbar (Krippendorff 2004, *Content Analysis*).
  Die Satz-Ebene ist schwieriger als das Gesamturteil – niedrigere Werte dort sind normal.
- Konfliktquote und Entscheidungen (Beispiele mit Begründung).
- **Verlässlichkeit der Quellen:** Anteil der Texte, bei denen der Konsens dem Label der Quelle
  widerspricht – ein Maß für das Rauschen der schwachen Labels.
- **Regeln gegen Mensch:** Precision/Recall der Regel-Erkennung je Warnsignal; verpasste Sätze als
  Grundlage für neue Lexikon-Muster (jedes mit Test und harmlosen Gegenbeispielen).

## 7. Datenschutz

- Telefonnummern, Mailadressen, IBANs, Links und @-Namen werden beim Hinzufügen anonymisiert (die Form
  bleibt erhalten, z. B. „ausländische Nummer“); Name, Ort und Adresse des Inserats werden entfernt.
- **Namen im Fließtext** werden nicht erkannt – bei eigenen Texten vorher selbst entfernen.
- Fremde Chats nur mit Zustimmung der Beteiligten (die Web-App fragt danach).
- Arbeitsordner und `data/raw/` sind nicht im Git; der gemeinsame Ordner bleibt privat.
