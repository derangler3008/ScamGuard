"""Qwen auf dem Windows-PC mit LoRA feintunen – AMD-GPU (ROCm) oder NVIDIA (CUDA), ohne MLX.

Gegenstück zu `mlx_lm.lora` auf dem Mac (docs/llm_feintuning.md). Gleiche Trainingsdaten, gleicher Prompt:

    scamguard data build && scamguard llm-daten          # → data/finetune/{train,valid,test}.jsonl
    .venv-lora\\Scripts\\python scripts\\qwen_lora_pc.py test --ohne-adapter   # Ausgangswert (Basismodell)
    .venv-lora\\Scripts\\python scripts\\qwen_lora_pc.py train                 # → models/qwen_lora_pc/
    .venv-lora\\Scripts\\python scripts\\qwen_lora_pc.py test                  # gleiches Testset, mit Adapter
    .venv-lora\\Scripts\\python scripts\\qwen_lora_pc.py export                # → models/qwen_pc_merged/ (+ GGUF)

- Läuft in einer eigenen Umgebung (.venv-lora: PyTorch für ROCm/CUDA, transformers, peft), damit die
  ScamGuard-Umgebung unverändert bleibt.
- Standardmodell Qwen3.5-4B in bf16 (~9 GB): passt mit LoRA auf eine 16-GB-Grafikkarte. Das 9B-Modell vom
  Mac braucht in bf16 ~19 GB, ein 4-Bit-QLoRA scheitert unter Windows mit AMD an bitsandbytes.
- Gelernt wird nur die Antwort (wie `--mask-prompt`), der Prompt entsteht mit `enable_thinking: False` –
  genau so, wie ScamGuard das Modell im Betrieb fragt.
- Nur die Sprachschichten bekommen Adapter; der Bildteil von Qwen3.5 bleibt eingefroren.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "Qwen/Qwen3.5-4B"
DEFAULT_DATA = "data/finetune"
DEFAULT_ADAPTER = "models/qwen_lora_pc"
DEFAULT_MERGED = "models/qwen_pc_merged"
END = "<|im_end|>"
# Sprachschichten von Qwen3.5: volle Attention, lineare Attention (Gated DeltaNet), MLP
TARGETS = r"(self_attn\.(q_proj|k_proj|v_proj|o_proj)|linear_attn\.(in_proj_qkv|in_proj_z|out_proj)" \
          r"|mlp\.(gate_proj|up_proj|down_proj))"


def _path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else ROOT / path


def _read(data_dir: Path, split: str) -> list[list[dict]]:
    file = data_dir / f"{split}.jsonl"
    if not file.exists():
        sys.exit(f"{file} fehlt – vorher `scamguard data build` und `scamguard llm-daten` ausführen.")
    return [json.loads(line)["messages"] for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]


def _label(messages: list[dict]) -> int:
    return int(json.loads(messages[-1]["content"])["scam_probability"] >= 0.5)


def _prompt(tokenizer, messages: list[dict]) -> str:
    return tokenizer.apply_chat_template(messages[:2], tokenize=False, add_generation_prompt=True,
                                         enable_thinking=False)


def _encode(tokenizer, messages: list[dict], max_len: int) -> dict | None:
    """input_ids + labels; Prompt-Tokens zählen nicht zum Verlust (-100)."""
    prompt = tokenizer(_prompt(tokenizer, messages), add_special_tokens=False)["input_ids"]
    answer = tokenizer(messages[-1]["content"] + END + "\n", add_special_tokens=False)["input_ids"]
    if len(prompt) + len(answer) > max_len:
        return None
    return {"input_ids": prompt + answer, "labels": [-100] * len(prompt) + answer}


def _device():
    import torch

    if not torch.cuda.is_available():
        sys.exit("Keine GPU gefunden. PyTorch mit ROCm (AMD) bzw. CUDA (NVIDIA) in .venv-lora installieren.")
    return torch.device("cuda")


def _load(model_id: str, adapter: Path | None = None, trainable: bool = False):
    import torch
    from transformers import AutoModelForImageTextToText, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForImageTextToText.from_pretrained(model_id, dtype=torch.bfloat16).to(_device())
    if adapter is not None:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, str(adapter), is_trainable=trainable)
    return tokenizer, model


def _vram() -> str:
    import torch

    return f"{torch.cuda.max_memory_allocated() / 1e9:.1f} GB"


def _loss(model, batch) -> float:
    import torch

    with torch.no_grad():
        return model(**batch).loss.item()


def _batch(example: dict, device) -> dict:
    import torch

    ids = torch.tensor([example["input_ids"]], device=device)
    return {"input_ids": ids, "attention_mask": torch.ones_like(ids),
            "labels": torch.tensor([example["labels"]], device=device)}


def cmd_train(args) -> int:
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import get_cosine_schedule_with_warmup

    data_dir, out = _path(args.daten), _path(args.adapter)
    train_msgs, valid_msgs = _read(data_dir, "train"), _read(data_dir, "valid")
    n_scam = sum(_label(m) for m in train_msgs)
    print(f"Training: {len(train_msgs)} Beispiele ({n_scam} Betrug, {len(train_msgs) - n_scam} seriös), "
          f"Validierung: {len(valid_msgs)}")
    if len(train_msgs) < 200:
        print("Hinweis: unter ~200 Beispielen lernt das Modell eher auswendig (docs/llm_feintuning.md) – "
              "als Test in Ordnung, für Kennzahlen mehr Daten sammeln.")

    tokenizer, model = _load(args.modell)
    layers = model.config.text_config.num_hidden_layers
    top = range(layers - args.schichten, layers) if 0 < args.schichten < layers else range(layers)
    pattern = rf"model\.language_model\.layers\.({'|'.join(map(str, top))})\.{TARGETS}"
    model = get_peft_model(model, LoraConfig(r=args.rang, lora_alpha=2 * args.rang, lora_dropout=0.05,
                                             target_modules=pattern, task_type="CAUSAL_LM"))
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()
    model.config.use_cache = False
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Modell {args.modell}: Adapter in {len(top)} von {layers} Schichten, "
          f"{trainable / 1e6:.1f} Mio. trainierbare Parameter")

    device = _device()
    encode = [_encode(tokenizer, m, args.max_laenge) for m in train_msgs]
    train = [e for e in encode if e]
    valid = [e for e in (_encode(tokenizer, m, args.max_laenge) for m in valid_msgs) if e]
    if len(train) < len(encode):
        print(f"{len(encode) - len(train)} Beispiele länger als {args.max_laenge} Tokens – übersprungen.")

    steps = math.ceil(len(train) * args.epochen / args.akkumulation)
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr,
                                  weight_decay=0.0)
    scheduler = get_cosine_schedule_with_warmup(optimizer, max(1, steps // 10), steps)
    rng = random.Random(args.seed)
    history, best, step, started = [], math.inf, 0, time.time()

    def validate() -> float:
        model.eval()
        loss = sum(_loss(model, _batch(e, device)) for e in valid) / max(1, len(valid))
        model.train()
        return loss

    model.train()
    for epoch in range(1, args.epochen + 1):
        order = list(range(len(train)))
        rng.shuffle(order)
        running, count = 0.0, 0
        for i, idx in enumerate(order, 1):
            loss = model(**_batch(train[idx], device)).loss / args.akkumulation
            loss.backward()
            running, count = running + loss.item() * args.akkumulation, count + 1
            if i % args.akkumulation and i != len(order):
                continue
            torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad(set_to_none=True)
            step += 1
            if step % args.log_alle == 0 or step == steps:
                print(f"  Schritt {step:4d}/{steps}  Epoche {epoch}  Train loss {running / count:.3f}  "
                      f"lr {scheduler.get_last_lr()[0]:.1e}  {time.time() - started:5.0f} s  VRAM {_vram()}")
                running, count = 0.0, 0
        val = validate() if valid else math.nan
        history.append({"epoche": epoch, "schritt": step, "val_loss": round(val, 4)})
        improved = not valid or val < best
        print(f"Epoche {epoch}: Val loss {val:.3f}" + ("  → bisher bester, gespeichert" if improved else
              "  (schlechter als zuvor – Modell lernt auswendig? weniger Epochen)"))
        if improved:
            best = val
            model.save_pretrained(out)

    (out / "training_pc.json").write_text(json.dumps({
        "basismodell": args.modell, "daten": str(data_dir), "beispiele": len(train), "epochen": args.epochen,
        "lr": args.lr, "rang": args.rang, "schichten": len(top), "max_laenge": args.max_laenge,
        "akkumulation": args.akkumulation, "verlauf": history, "dauer_s": round(time.time() - started),
        "vram_max": _vram()}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Adapter: {out}  (bester Val loss {best:.3f}, {time.time() - started:.0f} s, VRAM max {_vram()})")
    print("Weiter: `test` (gleiches Testset wie der Ausgangswert), dann `export`.")
    return 0


def _parse(text: str) -> dict | None:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        answer = json.loads(text[start:end + 1])
        float(answer["scam_probability"])
        return answer
    except (ValueError, KeyError, TypeError):
        return None


def cmd_test(args) -> int:
    import torch

    data_dir = _path(args.daten)
    messages = _read(data_dir, "test")[: args.max or None]
    adapter = None if args.ohne_adapter else _path(args.adapter)
    model_id = args.modell
    if adapter is not None:
        if not (adapter / "adapter_config.json").exists():
            sys.exit(f"Kein Adapter in {adapter} – erst `train` ausführen oder `--ohne-adapter`.")
        model_id = json.loads((adapter / "adapter_config.json").read_text())["base_model_name_or_path"]
    tokenizer, model = _load(model_id, adapter)
    model.eval()
    device = _device()
    tp = fp = fn = tn = invalid = 0
    started = time.time()
    for i, msgs in enumerate(messages, 1):
        ids = tokenizer(_prompt(tokenizer, msgs), add_special_tokens=False, return_tensors="pt").to(device)
        with torch.no_grad():
            out = model.generate(**ids, max_new_tokens=args.max_tokens, do_sample=False,
                                 pad_token_id=tokenizer.pad_token_id)
        answer = _parse(tokenizer.decode(out[0, ids["input_ids"].shape[1]:], skip_special_tokens=True))
        truth = _label(msgs)
        if answer is None:
            invalid += 1
            pred = 0
        else:
            pred = int(float(answer["scam_probability"]) >= 0.5)
        tp, fp = tp + (pred and truth), fp + (pred and not truth)
        fn, tn = fn + (not pred and truth), tn + (not pred and not truth)
        print(f"  {i:3d}/{len(messages)}  Label {'Betrug' if truth else 'seriös':6s}  "
              f"Modell {'ungültig' if answer is None else answer['scam_probability']}")
    n = len(messages)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    name = "Basismodell" if adapter is None else f"mit Adapter {adapter.name}"
    print(f"\n{model_id} {name} – Testset {n} Beispiele, {(time.time() - started) / max(1, n):.1f} s je Beispiel")
    print(f"  Accuracy {(tp + tn) / max(1, n):.2f}  Precision {precision:.2f}  Recall {recall:.2f}  F1 {f1:.2f}  "
          f"ungültiges JSON {invalid}/{n}")
    return 0


def cmd_export(args) -> int:
    import subprocess

    import torch
    from transformers import AutoProcessor

    adapter, out = _path(args.adapter), _path(args.ziel)
    if not (adapter / "adapter_config.json").exists():
        sys.exit(f"Kein Adapter in {adapter} – erst `train` ausführen.")
    model_id = json.loads((adapter / "adapter_config.json").read_text())["base_model_name_or_path"]
    _, model = _load(model_id, adapter)
    print(f"Füge Adapter in {model_id} ein …")
    model = model.merge_and_unload()
    model.to(torch.bfloat16).save_pretrained(out)
    AutoProcessor.from_pretrained(model_id).save_pretrained(out)
    print(f"Zusammengeführtes Modell: {out}")
    convert = _path(args.llama_cpp) / "convert_hf_to_gguf.py" if args.llama_cpp else None
    if convert is None or not convert.exists():
        print("GGUF für LM Studio/Ollama: llama.cpp herunterladen und mit `--llama-cpp <Ordner>` erneut "
              "aufrufen (braucht convert_hf_to_gguf.py).")
        return 0
    gguf = out.with_name(f"{out.name}-{args.gguf_typ}.gguf")
    subprocess.run([sys.executable, str(convert), str(out), "--outfile", str(gguf),
                    "--outtype", args.gguf_typ], check=True)
    print(f"GGUF: {gguf} – in LM Studio importieren (`lms import`) und in config.yaml als llm.local.model eintragen.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Qwen-LoRA auf dem PC (ROCm/CUDA)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("train", help="LoRA-Adapter trainieren")
    p.add_argument("--modell", default=DEFAULT_MODEL, help=f"Hugging-Face-ID (Standard {DEFAULT_MODEL})")
    p.add_argument("--daten", default=DEFAULT_DATA)
    p.add_argument("--adapter", default=DEFAULT_ADAPTER, help="Zielordner des Adapters")
    p.add_argument("--epochen", type=int, default=3, help="Durchläufe über die Daten (Faustregel 2–3)")
    p.add_argument("--lr", type=float, default=1e-4, help="Lernrate (LoRA mit PEFT: 5e-5 bis 2e-4)")
    p.add_argument("--rang", type=int, default=16, help="LoRA-Rang")
    p.add_argument("--schichten", type=int, default=8, help="obere Schichten mit Adapter (0 = alle)")
    p.add_argument("--max-laenge", type=int, default=2048, help="längstes Beispiel in Tokens")
    p.add_argument("--akkumulation", type=int, default=4, help="Beispiele je Optimierungsschritt")
    p.add_argument("--log-alle", type=int, default=5, help="Ausgabe alle N Schritte")
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("test", help="Testset auswerten (Accuracy, Precision, Recall, F1)")
    p.add_argument("--modell", default=DEFAULT_MODEL, help="nur mit --ohne-adapter (sonst aus dem Adapter)")
    p.add_argument("--daten", default=DEFAULT_DATA)
    p.add_argument("--adapter", default=DEFAULT_ADAPTER)
    p.add_argument("--ohne-adapter", action="store_true", help="Basismodell messen (Ausgangswert)")
    p.add_argument("--max", type=int, help="nur die ersten N Beispiele")
    p.add_argument("--max-tokens", type=int, default=512)
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("export", help="Adapter einfügen und speichern, optional als GGUF")
    p.add_argument("--adapter", default=DEFAULT_ADAPTER)
    p.add_argument("--ziel", default=DEFAULT_MERGED)
    p.add_argument("--llama-cpp", help="Ordner von llama.cpp (mit convert_hf_to_gguf.py) für GGUF")
    p.add_argument("--gguf-typ", default="q8_0", choices=["q8_0", "bf16", "f16"])
    p.set_defaults(func=cmd_export)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
