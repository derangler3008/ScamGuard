"""Nach `git pull`: Was ist seit der installierten Version zu tun? (ohne Abhängigkeiten, Mac und Windows)

    python scripts/update_check.py

Vergleicht die installierte Version (Metadaten in .venv, gesetzt beim letzten `pip install -e`) mit der
Version im Code (pyproject.toml) und gibt die Abschnitte „Nach dem Update“ aus dem CHANGELOG aus, die
seitdem dazugekommen sind – plus den passenden `pip install`-Befehl. Ist alles aktuell, bleibt es still.

Läuft automatisch beim Start von Claude Code (.claude/settings.json, Hook „SessionStart“); die Ausgabe
landet dann im Kontext und Claude arbeitet die Schritte ab (CLAUDE.md).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Extra → Paket, an dem man erkennt, dass es installiert ist
EXTRAS = {"dev": "pytest", "data": "datasets", "automl": "flaml", "text": "transformers", "vision": "torchvision",
          "llm": "anthropic", "local-llm": "mlx_lm", "e2e": "playwright"}


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text)[:3])


def _venv_python() -> Path | None:
    for candidate in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"):
        if candidate.exists():
            return candidate
    return None


def _installed(python: Path) -> tuple[str | None, list[str]]:
    """Installierte ScamGuard-Version und vorhandene Extras in der .venv."""
    probe = ("import importlib.metadata as m, importlib.util as u, json\n"
             "try: v = m.version('scamguard')\nexcept m.PackageNotFoundError: v = None\n"
             f"print(json.dumps([v, [e for e, p in {EXTRAS!r}.items() if u.find_spec(p)]]))")
    try:
        out = subprocess.run([str(python), "-c", probe], capture_output=True, text=True, timeout=60, check=True)
        import json

        version, extras = json.loads(out.stdout.strip().splitlines()[-1])
        return version, extras
    except (subprocess.SubprocessError, ValueError, IndexError):
        return None, []


def _steps_since(installed: tuple[int, ...], current: tuple[int, ...]) -> list[str]:
    """„Nach dem Update“-Abschnitte aller Versionen nach `installed` bis `current` (älteste zuerst)."""
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    blocks = []
    for match in re.finditer(r"^## \[(\d+\.\d+\.\d+)\][^\n]*\n(.*?)(?=^## \[|\Z)", changelog, re.MULTILINE | re.DOTALL):
        version = _version(match.group(1))
        if installed < version <= current:
            for step in re.finditer(r"^### (Nach dem Update[^\n]*)\n(.*?)(?=^### |\Z)", match.group(2), re.MULTILINE | re.DOTALL):
                blocks.append((version, f"v{match.group(1)} – {step.group(1)}:\n{step.group(2).strip()}"))
    return [text for _, text in sorted(blocks)]


def main() -> int:
    current_text = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.MULTILINE)
    current = current_text.group(1) if current_text else "?"
    python = _venv_python()
    if python is None:
        print(f"[ScamGuard {current}] Keine virtuelle Umgebung .venv – Einrichtung nach CONTRIBUTING.md, „Setup“.")
        return 0
    installed, extras = _installed(python)
    if installed == current:
        return 0  # alles aktuell – still bleiben
    extras_arg = f'".[{",".join(sorted(set(extras) | {"dev"}))}]"'
    print(f"[ScamGuard] Code ist v{current}, installiert ist v{installed or 'nichts'}. Nach dem Aktualisieren:")
    print(f"- Pakete aktualisieren: {python.relative_to(ROOT)} -m pip install -e {extras_arg}")
    steps = _steps_since(_version(installed or "0.0.0"), _version(current))
    for step in steps:
        print(f"- {step}")
    print("- Laufende Dienste (scamguard start/ui) neu starten und die Extension im Browser neu laden.")
    print("Hinweis für Claude: diese Schritte ausführen (CLAUDE.md); Downloads über 1 GB und Installationen "
          "außerhalb von .venv vorher mit der Person abstimmen. Danach meldet dieses Skript nichts mehr.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
