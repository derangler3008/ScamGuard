"""Kommandozeile: `scamguard <befehl>` (nach `pip install -e .`).

  scamguard data list                 Registrierte Datensätze anzeigen
  scamguard data build [--only A B]   Datensätze laden → data/processed/{train,val,test}.jsonl
  scamguard data sammeln [--max N]    Öffentliche Betrugswarnungen (Mails, SMS, Chats) als Textdaten
  scamguard data ordner               Eigene Labels als Ordner ablegen (Label/Kategorie/Inserat)
  scamguard einstufen holen|bericht|export   Annotation-Workspace (sonst Web-App, Tab „Einstufen im Team“)
  scamguard retrain [--bilder] [--ohne-demo]  Daten + eigene Labels einlesen, neu trainieren
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

    if args.action == "sammeln":
        from scamguard.data import collect_forums, collect_warnings

        chosen = args.quellen or [*collect_warnings.SOURCES, "foren"]
        rows = []
        if warning_sources := [q for q in chosen if q in collect_warnings.SOURCES]:
            rows += collect_warnings.collect(sources=warning_sources, max_items=args.max)
        if "foren" in chosen:
            quotes, reports = collect_forums.collect(max_pages=args.max)
            rows += quotes
            if top := collect_forums.summarize(reports):
                print("Häufigste Maschen in den Erfahrungsberichten: "
                      + ", ".join(f"{code} ({n})" for code, n in top))
        if rows:
            print("Nächster Schritt: seriöse Gegenbeispiele ergänzen, dann `scamguard retrain`.")
        return 0
    if args.action == "phrasen":
        return _phrasen(args)
    if args.action == "ordner":
        from scamguard.data.labels import DATASET_DIR, sync_dataset_folders

        print(f"{sync_dataset_folders()} eingestufte Inserate nach {DATASET_DIR}/ übernommen "
              "(Label/Kategorie/Inserat mit inserat.json und Fotos).")
        return 0
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


def _phrasen(args) -> int:
    """Wortfolgen, die in Betrugstexten auffällig häufig sind → Kandidaten fürs Lexikon."""
    import csv

    from scamguard.config import resolve_path
    from scamguard.data.loaders import load_spec
    from scamguard.data.phrases import candidates
    from scamguard.data.registry import get_specs

    specs = [s for s in get_specs(include_disabled=True)
             if s.enabled or (args.hf and s.source == "huggingface")]
    texts: dict[int, list[str]] = {0: [], 1: []}
    for spec in specs:
        for listing in load_spec(spec).listings:
            if listing.label in texts and listing.full_text:
                texts[listing.label].append(listing.full_text)
    print(f"Vergleiche {len(texts[1])} Betrugs- mit {len(texts[0])} seriösen Texten …")
    if len(texts[0]) < 100 and not args.hf:
        print("Hinweis: wenige seriöse Texte → schwache Unterschiede. Mit --hf die deutschen "
              "Hugging-Face-Nachrichten als Vergleich einbeziehen oder seriöse Chats labeln.")
    try:
        found = candidates(texts[1], texts[0], str(resolve_path(load_config()["paths"]["lexicon"])),
                           top=args.top)
    except ValueError as exc:
        print(f"Abbruch: {exc}")
        return 1
    print(f"{'z':>6} {'Betrug':>6} {'seriös':>6}  {'schon im Lexikon':22}  Wortfolge")
    for c in found:
        print(f"{c.z:6.1f} {c.scam_docs:6d} {c.legit_docs:6d}  {c.covered_by or '– neu –':22}  {c.phrase}")
    out = resolve_path("data/raw/phrasen_kandidaten.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["wortfolge", "z", "betrug_texte", "serioes_texte", "lexikon_gruppe"])
        writer.writerows((c.phrase, c.z, c.scam_docs, c.legit_docs, c.covered_by) for c in found)
    print(f"Gespeichert: {out} – sinnvolle neue Wortfolgen als Muster in data/lexicons/scam_signals_de.yaml "
          "übernehmen (Gegenprobe: Test mit harmlosen Sätzen).")
    return 0


def _cmd_llm_data(args) -> int:
    """Feintuning-Daten für Qwen (LoRA mit mlx_lm) aus den festen Splits erzeugen."""
    from scamguard.finetune import OUTPUT_DIR, export

    try:
        stats = export(args.ausgabe or OUTPUT_DIR)
    except FileNotFoundError as exc:
        print(exc)
        return 1
    for name, counts in stats.items():
        print(f"  {name:5s}: {sum(counts.values()):5d} Beispiele ({counts['betrug']} Betrug, {counts['seriös']} seriös)")
    train = stats["train"]
    if min(train["betrug"], train["seriös"]) < 0.2 * max(sum(train.values()), 1):
        print("Achtung: sehr einseitige Labels – das Modell lernt sonst „immer Betrug“. Erst seriöse "
              "Beispiele ergänzen (Extension, Web-App, `data sammeln` + Prüfliste).")
    print("Nächste Schritte (Anleitung: docs/llm_feintuning.md):\n"
          "  1. Qwen-Server beenden (16 GB reichen nicht für Server + Training)\n"
          f"  2. mlx_lm.lora --model {load_config()['llm']['local']['model']} --train "
          f"--data {args.ausgabe or OUTPUT_DIR} --adapter-path models/qwen_lora --mask-prompt "
          "--batch-size 1 --num-layers 8 --iters 600 --learning-rate 1e-5 --grad-checkpoint --max-seq-length 2048\n"
          "  3. scamguard llm-server --adapter models/qwen_lora   (oder llm.local.adapter_path in config.yaml)")
    return 0


def _fmt(value: float | None, pct: bool = False) -> str:
    if value is None:
        return "–"
    return f"{value:.0%}" if pct else f"{value:.2f}"


def _cmd_annotate(args) -> int:
    """Annotation-Workspace ohne Web-App: Aufgaben holen, Qualitätsbericht, Export."""
    from scamguard.data.annotation import EXPORT_DIR, Werkstatt, person_key
    from scamguard.data.registry import get_specs

    ws = Werkstatt()
    if args.action == "holen":
        person = person_key(args.person or "")
        if not person:
            print("Bitte --person NAME angeben (z. B. --person Jannis).")
            return 1
        if args.datei:
            path = Path(args.datei).expanduser()
            print(f"{path.name}: {ws.zusammenfuehren(path.name, path.read_bytes(), person)} neue Zeilen übernommen")
            return 0
        if not args.datensatz:
            print("Bitte --datensatz NAME (siehe `scamguard data list`) oder --datei angeben.")
            return 1
        spec = get_specs([args.datensatz])[0]
        new, existing = ws.hinzufuegen_aus_datensatz(spec, person, args.max, not args.unausgewogen)
        print(f"{new} neue Aufgaben aus {spec.name}" + (f" ({existing} waren schon da)" if existing else ""))
        return 0

    if args.action == "export":
        counts = ws.export()
        print(f"{counts['konsens']} Texte, {counts['saetze']} Sätze, {counts['bilder']} Bilder → {EXPORT_DIR}/einstufung_*.jsonl")
        print("Weiter mit `scamguard retrain --ohne-demo` bzw. `scamguard data build` und `scamguard llm-daten`.")
        return 0

    report = ws.bericht()
    print(f"Aufgaben {report['aufgaben']} · eingestuft {report['eingestuft']} · von zwei oder mehr "
          f"{report['doppelt_eingestuft']} · Konflikte {len(report['konflikte'])}")
    print("Je Person: " + (", ".join(f"{p} {n}" for p, n in sorted(report["personen"].items())) or "–"))
    print("Status: " + (", ".join(f"{s} {n}" for s, n in sorted(report["status"].items())) or "–"))
    print("\nÜbereinstimmung (Krippendorffs α; ≥ 0,80 verlässlich, ≥ 0,667 vorläufig):")
    for r in report["uebereinstimmung"]:
        print(f"  {r['ebene']:28s} n={r['einheiten']:4d}  α={_fmt(r['alpha']):>5s}  gleich={_fmt(r['prozent'], True):>4s}"
              f"  {r['einschaetzung']}")
    for r in report["kappa"]:
        print(f"  Cohens κ {r['paar']}: {_fmt(r['kappa'])} (n={r['n']})")
    for q in report["quellen"]:
        print(f"Quelle {q['quelle']}: {q['widerspruch']} von {q['n']} widersprechen eurem Urteil")
    for a in report["auffaelligkeiten"][:20]:
        print(f"Hinweis {a['aufgabe'][:8]} ({a['person']}): {a['hinweis']}")
    if args.regeln:
        check = ws.regeln_gegen_mensch()
        print(f"\nRegeln gegen Mensch ({check['saetze']} Sätze): Precision {_fmt(check['gesamt']['precision'], True)}, "
              f"Recall {_fmt(check['gesamt']['recall'], True)}")
        for r in check["je_signal"]:
            print(f"  {r['name']:38s} richtig={r['tp']:3d} fehlalarm={r['fp']:3d} verpasst={r['fn']:3d}  "
                  f"P={_fmt(r['precision'], True):>4s} R={_fmt(r['recall'], True):>4s}")
        for row in check["verpasst"]:
            print(f"  verpasst [{row['signal']}]: {row['text'][:110]}")
    return 0


def _cmd_train(args) -> int:
    from scamguard.data.build import read_split
    from scamguard.training import train_model

    out = train_model(args.model, read_split("train"), read_split("val"), load_config())
    print(f"Modell gespeichert: {out}")
    print("Nächster Schritt: `scamguard evaluate`")
    return 0


def _cmd_retrain(args) -> int:
    """Nach neuen Daten/Labels: alles neu einlesen, Modelle neu trainieren, kurz auswerten."""
    from scamguard.training import retrain

    result = retrain(transformer=args.transformer, images=args.bilder, include_demo=not args.ohne_demo)
    _print_build(result.reports, result.stats)
    for model, path in result.saved.items():
        print(f"  {model} gespeichert: {path}")
    for model, reason in result.skipped.items():
        print(f"  {model} nicht trainiert: {reason}")
    fusion = result.fusion
    auc = f", AUC {fusion['roc_auc']:.2f}" if "roc_auc" in fusion else ""
    print(f"Testset ({fusion['n']} Beispiele): Precision {fusion['precision']:.2f}, "
          f"Recall {fusion['recall']:.2f}, F1 {fusion['f1']:.2f}{auc}")
    print("Ein laufender Server (`scamguard start`) nutzt das neue Textmodell ab dem nächsten Scan.")
    return 0


def _cmd_evaluate(args) -> int:
    from scamguard.evaluate import evaluate

    guard = None
    if args.llm:
        from scamguard.pipeline import ScamGuard

        guard = ScamGuard(overrides={"llm": {"enabled": True}})
        print("Mit LLM: pro Beispiel einige Sekunden – mit --max eine Stichprobe nehmen.")
    report = evaluate(args.split, guard=guard, limit=args.max)
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


UI_URL = "http://127.0.0.1:8501"  # Streamlit-Standardport


def _streamlit_running(url: str) -> bool:
    from urllib.request import urlopen

    try:
        with urlopen(f"{url}/_stcore/health", timeout=2) as resp:
            return resp.read().strip() == b"ok"
    except OSError:
        return False


def _cmd_ui(args) -> int:
    if not args.public and _streamlit_running(UI_URL):
        # Sonst startet Streamlit still eine zweite Kopie auf dem nächsten Port (8502 …)
        import webbrowser

        print(f"Die ScamGuard-App läuft bereits: {UI_URL} – wird im Browser geöffnet.\n"
              "Zum Neustarten erst das laufende Terminal mit Ctrl+C beenden.")
        webbrowser.open(UI_URL)
        return 0
    app = PROJECT_ROOT / "frontend" / "app.py"
    # Standard: nur lokal erreichbar. --public z. B. für eine Live-Demo im Kurs.
    address = "0.0.0.0" if args.public else "127.0.0.1"
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app),
                            "--server.address", address, "--browser.gatherUsageStats", "false"],
                           cwd=PROJECT_ROOT)


def _probe_host(host: str) -> str:
    return "127.0.0.1" if host in ("", "0.0.0.0") else host


def _port_busy(host: str, port: int) -> bool:
    import socket

    try:  # create_connection kann IPv4 und IPv6 (z. B. --host ::1)
        with socket.create_connection((_probe_host(host), port), timeout=1):
            return True
    except OSError:
        return False


def _http_json(url: str, headers: dict[str, str] | None = None) -> dict | None:
    """GET mit kurzem Timeout; None, wenn dort nichts mit JSON antwortet."""
    from urllib.request import Request, urlopen

    try:
        with urlopen(Request(url, headers=headers or {}), timeout=2) as resp:
            data = json.loads(resp.read())
    except (OSError, ValueError):  # URLError/HTTPError sind OSError, JSONDecodeError ist ValueError
        return None
    return data if isinstance(data, dict) else None


def _api_port_check(host: str, port: int, command: str = "start") -> int | None:
    """Exit-Code, falls der API-Port schon belegt ist (statt uvicorns „Errno 48“), sonst None."""
    if not _port_busy(host, port):
        return None
    url = f"http://{_probe_host(host)}:{port}"
    health = _http_json(f"{url}/health", {"X-ScamGuard-Client": "cli"})
    if health and "detectors" in health:
        print(f"ScamGuard läuft bereits auf {url} (Version {health.get('version', '?')}) – nichts zu tun.\n"
              "Zum Neustarten erst das laufende Terminal mit Ctrl+C beenden.")
        return 0
    print(f"Port {port} ist von einem anderen Programm belegt. Anderen Port wählen, z. B.\n"
          f"  scamguard {command} --port {port + 1}\n"
          "und in der Extension (Popup → Server-Adresse) dieselbe Adresse eintragen.")
    return 1


def _local_llm_running(base_url: str) -> bool:
    models = _http_json(f"{base_url.rstrip('/')}/models")
    return bool(models and models.get("data"))


def _serve_api(args) -> int:
    import uvicorn

    uvicorn.run("scamguard.api:app", host=args.host, port=args.port, reload=False)
    return 0


def _cmd_api(args) -> int:
    code = _api_port_check(args.host, args.port, "api")
    return code if code is not None else _serve_api(args)


MLX_HINT = ("MLX läuft nur auf Macs mit Apple Silicon. Auf anderen Rechnern einen OpenAI-kompatiblen\n"
            "Server nutzen und llm.local.base_url/model in config.yaml anpassen, z. B.:\n"
            "  Ollama:    ollama pull <qwen-modell> && ollama serve   → http://127.0.0.1:11434/v1\n"
            "  LM Studio: Modell laden, Server starten              → http://127.0.0.1:1234/v1")


def _mlx_available() -> bool:
    import importlib.util

    return (sys.platform == "darwin" and platform.machine() == "arm64"
            and importlib.util.find_spec("mlx_lm") is not None)


def _llm_server_process_args(model: str | None = None,
                             adapter: str | None = None) -> tuple[list[str], dict[str, str], str]:
    """Befehl, Umgebung und Modell für den lokalen MLX-Server (Qwen), optional mit LoRA-Adapter."""
    import os

    from scamguard.config import resolve_path

    local = load_config()["llm"]["local"]
    model = model or local["model"]
    adapter = adapter or local.get("adapter_path")
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
    where = f"{model} auf http://127.0.0.1:{port}/v1"
    if adapter:
        path = resolve_path(adapter)
        if (path / "adapters.safetensors").exists():
            command += ["--adapter-path", str(path)]
            where += f" mit feingetuntem Adapter {adapter}"
        else:
            print(f"Adapter {path} nicht gefunden (adapters.safetensors fehlt) – starte das Basismodell.")
    return command, env, where


def _cmd_llm_server(args) -> int:
    if not _mlx_available():
        print(MLX_HINT if sys.platform != "darwin" else
              'mlx-lm fehlt → pip install -e ".[local-llm]"')
        return 1
    base_url = load_config()["llm"]["local"]["base_url"]
    if _local_llm_running(base_url):
        print(f"Das lokale LLM läuft bereits ({base_url}) – nichts zu tun.")
        return 0
    command, env, where = _llm_server_process_args(args.model, args.adapter)
    print(f"Starte {where} …")
    return subprocess.call(command, env=env)


def _cmd_start(args) -> int:
    """Alles für die Extension in einem Terminal: lokales LLM (falls eingerichtet) + ScamGuard-API.
    Ctrl+C beendet beides."""
    code = _api_port_check(args.host, args.port)
    if code is not None:  # vor dem LLM prüfen – sonst startet Qwen umsonst und wird gleich wieder beendet
        return code
    llm_cfg = load_config()["llm"]
    base_url = llm_cfg["local"]["base_url"]
    llm_process = None
    if args.ohne_llm:
        print("KI-Analyse aus: Qwen-Server wird nicht gestartet.")
    elif llm_cfg.get("provider") != "local":
        print(f"LLM-Provider ist „{llm_cfg.get('provider')}“ – kein lokales Modell zu starten.")
    elif _local_llm_running(base_url):
        print(f"Qwen läuft bereits ({base_url}) – wird mitbenutzt.")
    elif _mlx_available():
        command, env, where = _llm_server_process_args()
        print(f"Starte Qwen: {where} (bereit nach ca. 10–20 s) …")
        llm_process = subprocess.Popen(command, env=env)
    else:
        print(f"Kein MLX verfügbar – lokales LLM bitte separat starten ({base_url}).")
    print(f"Starte ScamGuard-API auf http://{args.host}:{args.port} … (Beenden mit Ctrl+C)")
    try:
        return _serve_api(args)
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
    p.add_argument("action", choices=["list", "build", "sammeln", "phrasen", "ordner"],
                   help="sammeln = Betrugswarnungen und Foren-Erfahrungen als Textdaten holen; "
                        "phrasen = typische Betrugs-Wortfolgen finden (Kandidaten fürs Lexikon); "
                        "ordner = bisherige eigene Labels in die Datensatz-Ordner übernehmen")
    p.add_argument("--only", nargs="+", help="Nur diese Datensätze (Namen aus registry.py)")
    p.add_argument("--max", type=int, help="sammeln: höchstens so viele Seiten je Quelle bzw. Thread")
    p.add_argument("--quellen", nargs="+", choices=["watchlist_alarm", "vz_radar", "watchlist_news", "foren"],
                   help="sammeln: nur diese Quellen")
    p.add_argument("--top", type=int, default=60, help="phrasen: so viele Kandidaten ausgeben")
    p.add_argument("--hf", action="store_true",
                   help="phrasen: auch inaktive Hugging-Face-Datensätze aus huggingface.yaml einbeziehen")
    p.set_defaults(func=_cmd_data)

    p = sub.add_parser("einstufen", help="Annotation-Workspace: Aufgaben holen, Qualität prüfen, exportieren")
    p.add_argument("action", choices=["holen", "bericht", "export"],
                   help="holen = Texte als Aufgaben aufnehmen; bericht = Übereinstimmung, Konflikte, "
                        "Auffälligkeiten; export = Konsens in die Trainingsdaten (data/raw/einstufung_*.jsonl)")
    p.add_argument("--person", help="holen: wer die Aufgaben hinzufügt (z. B. Jannis)")
    p.add_argument("--datensatz", help="holen: Name aus `scamguard data list`, z. B. datei:gesammelt_foren.csv")
    p.add_argument("--datei", help="holen: Datei aus dem Team übernehmen (aufgaben_/einstufungen_<name>.jsonl)")
    p.add_argument("--max", type=int, default=50, help="holen: höchstens so viele neue Aufgaben (Standard 50)")
    p.add_argument("--unausgewogen", action="store_true",
                   help="holen: zufällig ziehen statt gleich viele je Label der Quelle")
    p.add_argument("--regeln", action="store_true", help="bericht: Regel-Erkennung satzgenau gegen die Einstufungen prüfen")
    p.set_defaults(func=_cmd_annotate)

    p = sub.add_parser("train", help="Modell trainieren")
    p.add_argument("model", choices=["text-baseline", "text-transformer", "image"])
    p.set_defaults(func=_cmd_train)

    p = sub.add_parser("retrain", help="Alle Datensätze + eigene Labels neu einlesen und trainieren")
    p.add_argument("--bilder", action="store_true", help="Bildmodell (CNN) mittrainieren")
    p.add_argument("--transformer", action="store_true", help="GBERT-Textmodell mittrainieren (langsam)")
    p.add_argument("--ohne-demo", action="store_true",
                   help="Die 24 künstlichen Demo-Inserate weglassen (für echte Auswertungen)")
    p.set_defaults(func=_cmd_retrain)

    p = sub.add_parser("evaluate", help="Modelle auswerten")
    p.add_argument("--split", default="test", choices=["train", "val", "test"])
    p.add_argument("--llm", action="store_true", help="LLM-Judge mitbewerten (z. B. vor/nach Feintuning)")
    p.add_argument("--max", type=int, help="nur die ersten N Beispiele (LLM ist langsam)")
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
    p.add_argument("--adapter", help="LoRA-Adapter aus dem Feintuning (Ordner, z. B. models/qwen_lora)")
    p.set_defaults(func=_cmd_llm_server)

    p = sub.add_parser("llm-daten", help="Trainingsdaten fürs Feintuning des lokalen LLM (LoRA) erzeugen")
    p.add_argument("--ausgabe", help="Zielordner (Standard: data/finetune)")
    p.set_defaults(func=_cmd_llm_data)

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
