"""Kommandozeile: `scamguard <befehl>` (nach `pip install -e .`).

  scamguard data list                 Registrierte Datensätze anzeigen
  scamguard data build [--only A B]   Datensätze laden → data/processed/{train,val,test}.jsonl
  scamguard retrain [--bilder]        Datensätze + eigene Labels einlesen, Modelle neu trainieren
  scamguard train text-baseline       TF-IDF + LogReg (Sekunden, CPU)
  scamguard train text-transformer    GBERT-Feintuning (GPU empfohlen)
  scamguard train image               CNN-Feintuning auf Inseratsbildern
  scamguard evaluate [--split test]   Kennzahlen pro Modell und Quelle
  scamguard scan inserat.json [--llm] Einzelnes Inserat prüfen
  scamguard images hash bild.jpg ...  pHash für die Liste bekannter Fake-Bilder
  scamguard ui [--public]             Streamlit-Frontend starten (Standard: nur lokal)
  scamguard api                       REST-API starten (FastAPI)
  scamguard llm-server [--model ID]   Lokales LLM (Qwen, MLX) für die KI-Analyse starten
  scamguard start [--ohne-llm]        Qwen-Server + API zusammen starten (für die Extension)
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from scamguard.config import PROJECT_ROOT, load_config


def _print_build(reports, stats) -> None:
    for r in reports:
        line = (f"{r.name:32s} Zeilen={r.rows:6d} übernommen={len(r.listings):6d} "
                f"ohne_Label={r.skipped_no_label} nicht_deutsch={r.skipped_not_german} leer={r.skipped_empty}")
        if r.error:
            line += f"  FEHLER: {r.error}"
        elif r.note:
            line += f"  Hinweis: {r.note}"
        print(line)
    splits = ", ".join(f"{k} {v['n']} ({v['scam']} Betrug)" for k, v in stats["splits"].items())
    print(f"→ {stats['total']} Beispiele, {stats['with_images']} mit Bildern, "
          f"{stats['duplicates_removed']} Duplikate entfernt · {splits}")


def _cmd_data(args) -> int:
    from scamguard.data.registry import get_specs

    if args.action == "list":
        for s in get_specs(include_disabled=True):
            status = "AKTIV " if s.enabled else "aus   "
            print(f"[{status}] {s.name:32s} {s.source:12s} {s.modality:10s} {s.path}")
            if s.notes:
                print(f"         {s.notes}")
        return 0

    from scamguard.data.build import build_dataset

    _print_build(*build_dataset(args.only))
    return 0


def _train(model: str, train, val, cfg) -> Path:
    if model == "text-baseline":
        from scamguard.models.text_classifier import train_text_baseline

        return train_text_baseline(train, cfg)
    if model == "text-transformer":
        from scamguard.models.text_classifier import train_text_transformer

        return train_text_transformer(train, val, cfg)
    from scamguard.models.image_model import train_image_model

    return train_image_model(train, val, cfg)


def _cmd_train(args) -> int:
    from scamguard.data.build import read_split

    out = _train(args.model, read_split("train"), read_split("val"), load_config())
    print(f"Modell gespeichert: {out}")
    print("Nächster Schritt: `scamguard evaluate`")
    return 0


def _cmd_retrain(args) -> int:
    """Nach neuen Daten/Labels: alles neu einlesen, Modelle neu trainieren, kurz auswerten."""
    from scamguard.data.build import build_dataset, read_split
    from scamguard.evaluate import evaluate

    _print_build(*build_dataset())
    cfg = load_config()
    train, val = read_split("train"), read_split("val")
    models = ["text-baseline"] + ["text-transformer"] * args.transformer + ["image"] * args.bilder
    for model in models:
        print(f"Trainiere {model} …")
        print(f"  gespeichert: {_train(model, train, val, cfg)}")
    report = evaluate("test")
    fusion = report["models"]["FUSION"]
    auc = f", AUC {fusion['roc_auc']:.2f}" if "roc_auc" in fusion else ""
    print(f"Testset ({fusion['n']} Beispiele): Precision {fusion['precision']:.2f}, "
          f"Recall {fusion['recall']:.2f}, F1 {fusion['f1']:.2f}{auc}")
    print("Ein laufender Server (`scamguard start`) nutzt das neue Textmodell ab dem nächsten Scan.")
    return 0


def _cmd_evaluate(args) -> int:
    from scamguard.evaluate import evaluate

    report = evaluate(args.split)
    print(f"Split: {report['split']}  (Schwelle {report['threshold']})\n")
    for title, block in (("Modelle", report["models"]), ("Quellen (Fusion)", report["sources"])):
        print(f"{title}:")
        print(f"  {'Name':22s} {'n':>5s} {'Prec':>6s} {'Rec':>6s} {'F1':>6s} {'AUC':>6s}")
        for name, m in block.items():
            auc = f"{m['roc_auc']:.3f}" if "roc_auc" in m else "  –  "
            print(f"  {name:22s} {m['n']:5d} {m['precision']:6.3f} {m['recall']:6.3f} {m['f1']:6.3f} {auc:>6s}")
        print()
    return 0


def _cmd_scan(args) -> int:
    from scamguard.pipeline import ScamGuard
    from scamguard.schema import Listing

    listing = Listing.from_dict(json.loads(Path(args.file).read_text(encoding="utf-8")))
    guard = ScamGuard(overrides={"llm": {"enabled": True}} if args.llm else None)
    result = guard.scan(listing)
    print(f"Score: {result.score:.2f}  →  {result.verdict.upper()}\n")
    for s in result.signals:
        print(f"  [{s.source:11s}] {s.message}" + (f"  ({s.evidence})" if s.evidence else ""))
    print()
    for r in result.model_results:
        state = f"{r.score:.2f}" if r.score is not None else "–"
        print(f"  {r.name:12s} {state:>5s}  {r.error or ''}")
    return 0


def _cmd_images(args) -> int:
    from scamguard.models.image_model import phash_of

    for path in args.paths:
        print(f"{phash_of(path)}  # {Path(path).name}")
    return 0


def _cmd_ui(args) -> int:
    app = PROJECT_ROOT / "frontend" / "app.py"
    # Standard: nur lokal erreichbar. --public z. B. für eine Live-Demo im Kurs.
    address = "0.0.0.0" if args.public else "127.0.0.1"
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app),
                            "--server.address", address, "--browser.gatherUsageStats", "false"],
                           cwd=PROJECT_ROOT)


def _cmd_api(args) -> int:
    import uvicorn

    uvicorn.run("scamguard.api:app", host=args.host, port=args.port, reload=False)
    return 0


MLX_HINT = ("MLX läuft nur auf Macs mit Apple Silicon. Auf anderen Rechnern einen OpenAI-kompatiblen\n"
            "Server nutzen und llm.local.base_url/model in config.yaml anpassen, z. B.:\n"
            "  Ollama:    ollama pull <qwen-modell> && ollama serve   → http://127.0.0.1:11434/v1\n"
            "  LM Studio: Modell laden, Server starten              → http://127.0.0.1:1234/v1")


def _mlx_available() -> bool:
    import importlib.util

    return (sys.platform == "darwin" and platform.machine() == "arm64"
            and importlib.util.find_spec("mlx_lm") is not None)


def _llm_server_process_args(model: str | None = None) -> tuple[list[str], dict[str, str], str]:
    """Befehl, Umgebung und Modell für den lokalen MLX-Server (Qwen)."""
    import os

    local = load_config()["llm"]["local"]
    model = model or local["model"]
    port = urlparse(local["base_url"]).port or 8080
    env = dict(os.environ)
    try:  # Modell schon heruntergeladen → ohne Netzabfrage bei Hugging Face starten
        from huggingface_hub import snapshot_download

        snapshot_download(model, local_files_only=True)
        env["HF_HUB_OFFLINE"] = "1"
    except Exception:  # noqa: BLE001 – nicht im Cache: beim Start herunterladen (~6–7 GB)
        print(f"Modell {model} ist noch nicht heruntergeladen – der erste Start lädt es (~6–7 GB).")
    command = [
        # Die Modell-ID muss genau der in den Anfragen entsprechen, sonst lädt mlx_lm das Modell neu
        sys.executable, "-m", "mlx_lm", "server", "--model", model,
        "--host", "127.0.0.1", "--port", str(port),
        # Qwen3.5 „denkt“ sonst vor jeder Antwort – für die Einstufung unnötig und langsam
        "--chat-template-args", json.dumps({"enable_thinking": False}),
        # Kein CORS für fremde Webseiten: nur der ScamGuard-Server (ohne Browser) nutzt das Modell
        "--allowed-origins", "http://127.0.0.1:8000",
    ]
    return command, env, f"{model} auf http://127.0.0.1:{port}/v1"


def _cmd_llm_server(args) -> int:
    if not _mlx_available():
        print(MLX_HINT if sys.platform != "darwin" else
              'mlx-lm fehlt → pip install -e ".[local-llm]"')
        return 1
    command, env, where = _llm_server_process_args(args.model)
    print(f"Starte {where} …")
    return subprocess.call(command, env=env)


def _cmd_start(args) -> int:
    """Alles für die Extension in einem Terminal: lokales LLM (falls eingerichtet) + ScamGuard-API.
    Ctrl+C beendet beides."""
    llm_cfg = load_config()["llm"]
    llm_process = None
    if args.ohne_llm:
        print("KI-Analyse aus: Qwen-Server wird nicht gestartet.")
    elif llm_cfg.get("provider") != "local":
        print(f"LLM-Provider ist „{llm_cfg.get('provider')}“ – kein lokales Modell zu starten.")
    elif _mlx_available():
        command, env, where = _llm_server_process_args()
        print(f"Starte Qwen: {where} (bereit nach ca. 10–20 s) …")
        llm_process = subprocess.Popen(command, env=env)
    else:
        print(f"Kein MLX verfügbar – lokales LLM bitte separat starten ({llm_cfg['local']['base_url']}).")
    print(f"Starte ScamGuard-API auf http://{args.host}:{args.port} … (Beenden mit Ctrl+C)")
    try:
        return _cmd_api(args)
    finally:
        if llm_process and llm_process.poll() is None:
            llm_process.terminate()
            try:
                llm_process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                llm_process.kill()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scamguard", description="Betrugserkennung für Kleinanzeigen")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("data", help="Datensätze verwalten")
    p.add_argument("action", choices=["list", "build"])
    p.add_argument("--only", nargs="+", help="Nur diese Datensätze (Namen aus registry.py)")
    p.set_defaults(func=_cmd_data)

    p = sub.add_parser("train", help="Modell trainieren")
    p.add_argument("model", choices=["text-baseline", "text-transformer", "image"])
    p.set_defaults(func=_cmd_train)

    p = sub.add_parser("retrain", help="Alle Datensätze + eigene Labels neu einlesen und trainieren")
    p.add_argument("--bilder", action="store_true", help="Bildmodell (CNN) mittrainieren")
    p.add_argument("--transformer", action="store_true", help="GBERT-Textmodell mittrainieren (langsam)")
    p.set_defaults(func=_cmd_retrain)

    p = sub.add_parser("evaluate", help="Modelle auswerten")
    p.add_argument("--split", default="test", choices=["train", "val", "test"])
    p.set_defaults(func=_cmd_evaluate)

    p = sub.add_parser("scan", help="Ein Inserat (JSON-Datei) prüfen")
    p.add_argument("file")
    p.add_argument("--llm", action="store_true", help="LLM-Judge (Claude) zuschalten")
    p.set_defaults(func=_cmd_scan)

    p = sub.add_parser("images", help="Bild-Werkzeuge")
    p.add_argument("action", choices=["hash"])
    p.add_argument("paths", nargs="+")
    p.set_defaults(func=_cmd_images)

    p = sub.add_parser("ui", help="Streamlit-Frontend starten")
    p.add_argument("--public", action="store_true",
                   help="Im ganzen Netzwerk erreichbar machen (Standard: nur dieser Rechner)")
    p.set_defaults(func=_cmd_ui)

    p = sub.add_parser("llm-server", help="Lokales LLM (MLX, Apple Silicon) starten")
    p.add_argument("--model", help="Hugging-Face-ID eines MLX-Modells (Standard: config.yaml)")
    p.set_defaults(func=_cmd_llm_server)

    p = sub.add_parser("start", help="Qwen-Server und API zusammen starten (für die Extension)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--ohne-llm", action="store_true", help="Nur die API, ohne lokales LLM")
    p.set_defaults(func=_cmd_start)

    p = sub.add_parser("api", help="REST-API starten")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=_cmd_api)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError, KeyError, ImportError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
