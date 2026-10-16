"""Datensätze im Team zusammenführen – ohne Git (Personendaten, Fotos, Größe).

    scamguard team export --name Jannis              # → ZIP mit allen eigenen Daten (Ziel: --ziel oder team.ausgang)
    scamguard team import [ORDNER|ZIP …]             # ZIPs aus dem Eingang einlesen (Standard: team.eingang)

Ablauf: Jede Person exportiert ein ZIP und lädt es über den Upload-Link in den gemeinsamen Eingang
(z. B. OneDrive-Ordner mit „Dateien anfordern“). Wer zusammenführt, ruft `team import` auf – mehrfach
möglich, schon eingelesene ZIPs werden übersprungen. Danach `scamguard retrain`.

Im ZIP (Pfade wie im Projekt, ohne synthetische Daten – die erzeugt jede Person selbst):
  datensatz_fuellen_inserate/{betrug,serioes}/…   eingestufte Inserate (inserat.json + Fotos, Screenshots …)
  datensatz_fuellen_bilder/{betrug,serioes}/…     Produktfotos
  datensatz_fuellen_text/…                         Tabellen (Prüflisten mit Labels, labels_*.jsonl …)
  einstufung/*.jsonl                               Annotation-Workspace (Aufgaben, Einstufungen, Entscheidungen)
  scamguard_export.json                            wer, wann, welche Version, wie viele Beispiele

Zusammenführen: Inserat-Ordner tragen einen eindeutigen Schlüssel im Namen – gleiche Ordner werden nicht
doppelt übernommen, Unterschiede bleiben beim Bauen der Splits über den Fingerabdruck dedupliziert.
Tabellen landen als team_<person>__<datei>, Fotos unter team_<person>/. Dateien des Annotation-Workspace
sind ohnehin pro Person benannt; es gewinnt die längere (= neuere, die Dateien wachsen nur).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

from scamguard.config import load_config, resolve_path

SECTIONS = {  # Ordner im ZIP → Ordner im Projekt
    "datensatz_fuellen_inserate": "data/datensatz_fuellen_inserate",
    "datensatz_fuellen_bilder": "data/datensatz_fuellen_bilder",
    "datensatz_fuellen_text": "data/datensatz_fuellen_text",
}
SKIP_NAMES = {".gitkeep", "LIESMICH.md", "huggingface.yaml", ".DS_Store", "desktop.ini"}
SYNTHETIC = "synthetisch"
MANIFEST = "scamguard_export.json"
IMPORT_LOG = "data/raw/team_import.json"
LABEL_DIRS = ("betrug", "serioes")


def person_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "", name.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
                  .replace("ß", "ss"))
    if not slug:
        raise ValueError("Name fehlt – z. B. --name Jannis")
    return slug


def _annotation_dir() -> Path:
    from scamguard.data.annotation import Werkstatt

    return Werkstatt().folder


def _wanted(rel: PurePosixPath) -> bool:
    return (rel.name not in SKIP_NAMES and not rel.name.startswith("_")
            and SYNTHETIC not in rel.parts and not any(p.startswith(".") for p in rel.parts))


def export(name: str, target_dir: str | Path) -> tuple[Path, dict]:
    """Alle eigenen Daten als ZIP nach target_dir. Rückgabe: Pfad und Zählung."""
    from scamguard import __version__
    from scamguard.data.labels import sync_dataset_folders

    person = person_slug(name)
    sync_dataset_folders()  # ältere Einstufungen (eigene_labels.jsonl) als Ordner – sonst fehlen sie im ZIP
    target_dir = Path(target_dir).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M")
    target = target_dir / f"scamguard_{person}_{stamp}.zip"
    counts: Counter = Counter()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for section, folder in SECTIONS.items():
            root = resolve_path(folder)
            if not root.is_dir():
                continue
            for path in sorted(p for p in root.rglob("*") if p.is_file()):
                rel = PurePosixPath(path.relative_to(root).as_posix())
                if not _wanted(rel):
                    continue
                zf.write(path, f"{section}/{rel}")
                if section == "datensatz_fuellen_inserate" and path.name == "inserat.json":
                    counts[f"inserate_{rel.parts[0]}"] += 1
                elif section == "datensatz_fuellen_bilder":
                    counts["bilder"] += 1
                elif section == "datensatz_fuellen_text":
                    counts["tabellen"] += 1
        annotation = _annotation_dir()
        if annotation.is_dir():
            for path in sorted(annotation.glob("*.jsonl")):
                zf.write(path, f"einstufung/{path.name}")
                counts["einstufung"] += 1
        manifest = {"person": person, "name": name, "datum": stamp, "version": __version__, "anzahl": dict(counts)}
        zf.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
    return target, dict(counts)


def _safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Nur relative Pfade ohne „..“ (Schutz vor präparierten ZIPs)."""
    members = []
    for info in zf.infolist():
        rel = PurePosixPath(info.filename)
        if info.is_dir() or rel.is_absolute() or ".." in rel.parts or ":" in info.filename:
            continue
        members.append(info)
    return members


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _log() -> dict:
    path = resolve_path(IMPORT_LOG)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _copy_new(src: Path, dst: Path) -> bool:
    if dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _listing_root(rel: PurePosixPath) -> PurePosixPath:
    """Ordner eines Inserats (…/betrug/<kategorie>/<inserat>/) bzw. die Datei selbst."""
    for depth in (3, 2):  # betrug/kategorie/inserat/datei bzw. betrug/inserat/datei
        if len(rel.parts) > depth:
            return PurePosixPath(*rel.parts[:depth])
    return rel


def import_zip(path: Path, log: dict | None = None) -> dict:
    """Ein Export-ZIP einlesen. Rückgabe: Zählung (neu/vorhanden je Bereich)."""
    counts: Counter = Counter()
    with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory(prefix="scamguard_team_") as tmp:
        try:
            manifest = json.loads(zf.read(MANIFEST))
        except KeyError as exc:
            raise ValueError(f"{path.name}: kein ScamGuard-Export ({MANIFEST} fehlt)") from exc
        person = person_slug(manifest.get("person") or manifest.get("name") or "")
        members = _safe_members(zf)
        for info in members:
            zf.extract(info, tmp)
        root = Path(tmp)

        inserate = root / "datensatz_fuellen_inserate"
        if inserate.is_dir():
            target = resolve_path(SECTIONS["datensatz_fuellen_inserate"])
            seen: set[PurePosixPath] = set()
            for src in sorted(p for p in inserate.rglob("*") if p.is_file()):
                rel = PurePosixPath(src.relative_to(inserate).as_posix())
                if not _wanted(rel) or rel.parts[0] not in LABEL_DIRS:
                    continue
                unit = _listing_root(rel)
                if len(rel.parts) == 2:  # einzelne Datei direkt unter betrug/ → mit Personenkürzel
                    dst = target / rel.parts[0] / f"team_{person}__{rel.name}"
                else:
                    dst = target / Path(*rel.parts)
                if unit not in seen:
                    seen.add(unit)
                    exists = (target / Path(*unit.parts)).exists() if len(rel.parts) > 2 else dst.exists()
                    counts[f"inserate_{rel.parts[0]}_{'vorhanden' if exists else 'neu'}"] += 1
                _copy_new(src, dst)

        bilder = root / "datensatz_fuellen_bilder"
        if bilder.is_dir():
            target = resolve_path(SECTIONS["datensatz_fuellen_bilder"])
            for src in sorted(p for p in bilder.rglob("*") if p.is_file()):
                rel = PurePosixPath(src.relative_to(bilder).as_posix())
                if _wanted(rel) and rel.parts[0] in LABEL_DIRS:
                    dst = target / rel.parts[0] / f"team_{person}" / Path(*rel.parts[1:])
                    counts["bilder_neu" if _copy_new(src, dst) else "bilder_vorhanden"] += 1

        text = root / "datensatz_fuellen_text"
        if text.is_dir():
            target = resolve_path(SECTIONS["datensatz_fuellen_text"])
            target.mkdir(parents=True, exist_ok=True)
            for src in sorted(p for p in text.iterdir() if p.is_file()):
                if _wanted(PurePosixPath(src.name)):
                    shutil.copy2(src, target / f"team_{person}__{src.name}")  # neuerer Export ersetzt
                    counts["tabellen"] += 1

        einstufung = root / "einstufung"
        if einstufung.is_dir():
            target = _annotation_dir()
            target.mkdir(parents=True, exist_ok=True)
            for src in sorted(einstufung.glob("*.jsonl")):
                dst = target / src.name
                if not dst.exists() or src.stat().st_size > dst.stat().st_size:  # Dateien wachsen nur
                    shutil.copy2(src, dst)
                    counts["einstufung_aktualisiert"] += 1

    if log is not None:
        log[_sha1(path)] = {"datei": path.name, "person": person, "eingelesen": time.strftime("%Y-%m-%d %H:%M"),
                            "anzahl": dict(counts)}
    return {"person": manifest.get("name") or person, **counts}


def import_inbox(paths: list[str | Path] | None = None, force: bool = False) -> list[dict]:
    """Alle Export-ZIPs aus den angegebenen Ordnern/Dateien (Standard: team.eingang) einlesen."""
    if not paths:
        inbox = (load_config().get("team") or {}).get("eingang")
        if not inbox:
            raise ValueError("Kein Eingang angegeben – Ordner bzw. ZIP angeben oder team.eingang in "
                             "config.local.yaml eintragen.")
        paths = [inbox]
    zips: list[Path] = []
    for p in (Path(x).expanduser() for x in paths):
        if p.is_dir():
            zips += sorted(p.rglob("*.zip"))
        elif p.suffix.lower() == ".zip" and p.exists():
            zips.append(p)
        else:
            raise FileNotFoundError(f"Nicht gefunden: {p}")
    log = _log()
    results = []
    for z in zips:
        if not force and _sha1(z) in log:
            results.append({"person": log[_sha1(z)]["person"], "datei": z.name, "übersprungen": True})
            continue
        try:
            results.append({"datei": z.name, **import_zip(z, log)})
        except (ValueError, zipfile.BadZipFile) as exc:
            results.append({"datei": z.name, "fehler": str(exc)})
    path = resolve_path(IMPORT_LOG)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    return results
