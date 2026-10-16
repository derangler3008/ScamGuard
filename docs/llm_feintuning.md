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

## 5. Windows-PC mit AMD- oder NVIDIA-Grafikkarte

Statt MLX: PyTorch mit ROCm (AMD) bzw. CUDA (NVIDIA), Hugging Face PEFT und
[`scripts/qwen_lora_pc.py`](../scripts/qwen_lora_pc.py). Gleiche Daten (`data/finetune`), gleicher Prompt
(`enable_thinking: false`), gelernt wird nur die Antwort. Getestet auf RX 7800 XT (16 GB), Windows 11,
AMD-Treiber 26.8, ROCm 7.2.1.

**Modell:** Qwen3.5-4B in bf16 (~9 GB). Das 9B-Modell braucht in bf16 ~19 GB, und 4-Bit-QLoRA
(bitsandbytes) gibt es unter Windows mit AMD nicht. Adapter bekommen nur die Sprachschichten.

**Einmalig: eigene Umgebung `.venv-lora`** (die ScamGuard-Umgebung bleibt unverändert). AMD: Treiber ab
26.2.2, Wheels laut [AMD-Anleitung](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/windows/install-pytorch.html):

```powershell
py -3.12 -m venv .venv-lora
$b = "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1"
.venv-lora\Scripts\pip install --no-cache-dir "$b/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl" `
  "$b/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl" "$b/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl" `
  "$b/rocm-7.2.1.tar.gz"
.venv-lora\Scripts\pip install --no-cache-dir "$b/torch-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl" `
  "$b/torchaudio-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl" "$b/torchvision-0.24.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl"
.venv-lora\Scripts\pip install transformers peft accelerate datasets sentencepiece "protobuf<6"
.venv-lora\Scripts\python -c "import torch; print(torch.cuda.is_available())"   # → True
```

NVIDIA: stattdessen `pip install torch --index-url https://download.pytorch.org/whl/cu128`.

Dieselben ROCm-Wheels in der ScamGuard-Umgebung `.venv` (danach `pip install -e ".[text,vision]"`)
bringen GBERT (`scamguard train text-transformer`) und das Bildmodell (`retrain --bilder`) auf die
Grafikkarte – der Code wählt sie automatisch (`best_torch_device`).

**Ablauf:**

```powershell
scamguard data build; scamguard llm-daten                                  # wie auf dem Mac
.venv-lora\Scripts\python scripts\qwen_lora_pc.py test --ohne-adapter     # Ausgangswert
.venv-lora\Scripts\python scripts\qwen_lora_pc.py train                   # → models/qwen_lora_pc/
.venv-lora\Scripts\python scripts\qwen_lora_pc.py test                    # gleiches Testset
.venv-lora\Scripts\python scripts\qwen_lora_pc.py export --llama-cpp <llama.cpp-Quellordner>
```

`train` speichert nach jeder Epoche den Adapter mit dem bisher besten Val loss und schreibt Parameter und
Verlauf nach `models/qwen_lora_pc/training_pc.json`. `export` fügt den Adapter ins Modell ein
(`models/qwen_pc_merged/`) und erzeugt mit `convert_hf_to_gguf.py` aus dem
[llama.cpp-Quellcode](https://github.com/ggml-org/llama.cpp) eine GGUF-Datei (`q8_0`, ~4,5 GB). Die trägt man
in `config.local.yaml` unter `llm.local.llama_cpp.gguf` ein (README, Abschnitt „LLM“).

Probelauf (Qwen3.5-0.8B, 16 Demo-Beispiele, 2 Epochen): 38 s, 5,4 GB VRAM, gültiges JSON in allen
Testfällen. Die linearen Attention-Schichten von Qwen3.5 laufen ohne `flash-linear-attention` über die
PyTorch-Referenz – korrekt, aber langsamer.

## 6. Alternativen

- **Ohne Training (Few-Shot):** 3–6 ähnliche, bereits gelabelte Fälle in den Prompt stellen
  (Retrieval). Bei wenigen Daten oft genauso gut, und leicht zu ändern. Wäre eine kleine Erweiterung in
  `models/llm_judge.py`.
- **Cloud-GPU** (Google Colab, kostenlos mit T4): dasselbe JSONL-Format (`messages`) funktioniert mit
  Hugging Face TRL/PEFT oder Unsloth. Den Adapter danach für LM Studio/Ollama konvertieren, oder
  direkt dort nutzen. Nur anonymisierte Daten hochladen.
- **Desktop-PC mit großer GPU:** größeres Qwen (z. B. 27B in LM Studio) ohne Feintuning gegen 9B mit
  Feintuning vergleichen.

## 7. Experimente

| Experiment | Messen |
|---|---|
| Qwen Basismodell (Zero-Shot) vs. Qwen + LoRA | Precision, Recall, F1, ROC-AUC auf demselben Testset |
| LoRA-Varianten (`--num-layers`, `--iters`) | Val loss, F1, Overfitting |
| Kosten | Trainingszeit, Antwortzeit je Inserat, Speicher |
| Fehleranalyse | Welche seriösen Fälle hält das Modell für Betrug, und warum? |
