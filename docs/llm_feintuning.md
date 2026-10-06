# Qwen selbst feintunen (LoRA mit MLX)

ScamGuard nutzt Qwen3.5-9B als LLM-Judge. Ohne Training kennt das Modell nur allgemeines Wissen über
Betrugsmaschen. Mit Feintuning lernt es die Einstufungen des Projekts: was als Betrug gilt, welche
Masche vorliegt, welche Formulierungen auf Kleinanzeigen typisch sind. Diese Anleitung ist für den Mac
(Apple Silicon, z. B. M4 mit 16 GB); am Ende stehen Alternativen.

## 1. Lohnt es sich schon? Erst messen, dann trainieren

| Wie viele gelabelte Beispiele? | Empfehlung |
|---|---|
| unter ~200 | Noch nicht feintunen – das Modell lernt die Beispiele auswendig. Lieber Daten sammeln. |
| 300–1 000, beide Klassen ≥ 30 % | Feintunen lohnt sich als Experiment (siehe unten). |
| mehrere Tausend | Feintunen lohnt sich klar, auch größerer Rang/mehr Schichten. |

Vorher die **Ausgangszahl** festhalten (Basismodell, gleiches Testset):

```bash
cd ~/Developer/ScamGuard && source .venv/bin/activate
scamguard data build                     # feste Splits train/val/test (dieselben wie für alle Modelle)
scamguard evaluate --llm --max 100       # Qwen ohne Feintuning; dauert pro Beispiel einige Sekunden
```

## 2. Was beim LoRA-Feintuning passiert

- Die 9 Milliarden Gewichte bleiben eingefroren (4-Bit-quantisiert → „QLoRA“). Gelernt werden nur
  kleine Zusatzmatrizen in den oberen Schichten (`--num-layers 8`). Der Adapter ist wenige MB groß und
  lässt sich jederzeit weglassen – das Basismodell bleibt unverändert.
- Ein Trainingsbeispiel ist genau das, was ScamGuard im Betrieb schickt (gleicher System-Prompt, gleiches
  `<inserat>`-Format) plus die gewünschte Antwort als JSON. `--mask-prompt` sorgt dafür, dass nur die
  Antwort gelernt wird, nicht der Prompt.
- Die gewünschte Antwort baut `scamguard llm-daten` aus den Daten: Wahrscheinlichkeit aus dem Label
  (0,9 bzw. 0,1), Masche aus der Einstufung (über das Kategoriensystem auf die Qwen-Kategorien
  abgebildet) bzw. den Regel-Treffern, Warnsignale aus den im Annotation-Workspace markierten Sätzen –
  wo keine vorliegen, aus den Regel-Treffern mit wörtlichem Zitat (Weak Supervision). Das Modell lernt
  also die **Labels**, die **Begründungen** und das **Format**.

## 3. Schritt für Schritt

```bash
# Daten erzeugen
scamguard llm-daten                      # → data/finetune/train.jsonl, valid.jsonl, test.jsonl
```

Die Ausgabe zeigt, wie viele Beispiele je Split und Klasse entstanden sind, und warnt bei einseitigen
Labels.

**Qwen-Server beenden**, also `Ctrl+C` im Terminal von `scamguard start`. Modell und Training brauchen
zusammen deutlich mehr Speicher als der Server allein, und 16 GB reichen nicht für beides. Große Apps
ebenfalls schließen.

```bash
mlx_lm.lora --model mlx-community/Qwen3.5-9B-OptiQ-4bit --train --data data/finetune \
  --adapter-path models/qwen_lora --mask-prompt \
  --batch-size 1 --num-layers 8 --iters 600 --learning-rate 1e-5 \
  --grad-checkpoint --max-seq-length 2048 --steps-per-eval 100
```

| Parameter | Bedeutung | Faustregel |
|---|---|---|
| `--iters` | Trainingsschritte (je 1 Beispiel bei `--batch-size 1`) | ≈ 2–3 × Anzahl Trainingsbeispiele (2–3 Durchläufe) |
| `--learning-rate` | Schrittweite | 1e-5; bei instabilem Verlust kleiner |
| `--num-layers` | wie viele obere Schichten Adapter bekommen | 8 (wenig Daten) bis 16 |
| `--max-seq-length` | längstes Beispiel in Tokens | 2048 reicht (Beispiele heute: ~800–1 000) |
| `--grad-checkpoint` | spart Speicher, kostet etwas Zeit | auf 16 GB immer an |

Während des Trainings meldet mlx_lm regelmäßig `Train loss` und alle 100 Schritte `Val loss`. Steigt der
Val loss wieder, während der Train loss weiter fällt, lernt das Modell auswendig → weniger `--iters`.

```bash
# Testverlust des Adapters (Testset aus data/finetune)
mlx_lm.lora --model mlx-community/Qwen3.5-9B-OptiQ-4bit --adapter-path models/qwen_lora \
  --data data/finetune --test

# Einsetzen: einmalig …
scamguard llm-server --adapter models/qwen_lora
# … oder dauerhaft in config.yaml (dann nutzt auch `scamguard start` den Adapter):
#   llm:
#     local:
#       adapter_path: models/qwen_lora

# Vergleich mit der Ausgangszahl aus Schritt 1 (gleiches Testset!)
scamguard evaluate --llm --max 100
```

Fehlt `models/qwen_lora/adapters.safetensors`, startet ScamGuard das Basismodell und sagt das.

## 4. Gute Daten sind wichtiger als Parameter

- **Ausgewogen:** Die gesammelten Warnungen (`data sammeln`) sind fast nur Betrug im Phishing-Stil.
  Allein damit lernt Qwen „Phishing-Mail = Betrug, alles andere seriös“ oder „immer Betrug“. Seriöse
  Inserate und Chats gehören dazu: Extension, Web-App, `nein`-Zeilen der Foren-Prüfliste.
- **Vielfältig:** verschiedene Maschen, Kategorien, Inserate und reine Chats, Käufer- und
  Verkäuferseite.
- **Konsistent:** gemeinsames Kategoriensystem und Doppel-Labeling mit gemessener Übereinstimmung im
  Annotation-Workspace ([einstufung.md](einstufung.md)).
- **Testset nie trainieren:** `llm-daten` nutzt die festen Splits aus `data build`, damit bleibt
  Qwen fair mit den anderen Modellen vergleichbar. Synthetische Beispiele nur ins Training, nie ins
  Testset, und als eigene Quelle markieren.
- **Bessere Begründungen:** Sätze im Annotation-Workspace markieren – sie werden zu den `red_flags`
  der Zielantwort. Optional zusätzlich eigene `summary`-Texte für 50–100 Beispiele.
- **Datenschutz:** `data/finetune/` und `models/` landen nicht im Git. Keine echten Namen,
  Telefonnummern oder Adressen in den Trainingsdaten.

## 5. Alternativen

- **Ohne Training (Few-Shot):** 3–6 ähnliche, bereits gelabelte Fälle in den Prompt stellen
  (Retrieval). Bei wenigen Daten oft genauso gut, und leicht zu ändern. Wäre eine kleine Erweiterung in
  `models/llm_judge.py`.
- **Cloud-GPU** (Google Colab, kostenlos mit T4): dasselbe JSONL-Format (`messages`) funktioniert mit
  Hugging Face TRL/PEFT oder Unsloth. Den Adapter danach für LM Studio/Ollama konvertieren, oder
  direkt dort nutzen. Nur anonymisierte Daten hochladen.
- **Desktop-PC mit großer GPU:** größeres Qwen (z. B. 27B in LM Studio) ohne Feintuning gegen 9B mit
  Feintuning vergleichen.

## 6. Experimente

| Experiment | Messen |
|---|---|
| Qwen Basismodell (Zero-Shot) vs. Qwen + LoRA | Precision, Recall, F1, ROC-AUC auf demselben Testset |
| LoRA-Varianten (`--num-layers`, `--iters`) | Val loss, F1, Overfitting |
| Kosten | Trainingszeit, Antwortzeit je Inserat, Speicher |
| Fehleranalyse | Welche seriösen Fälle hält das Modell für Betrug, und warum? |
