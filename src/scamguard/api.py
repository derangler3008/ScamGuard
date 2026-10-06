"""REST-API (FastAPI) – Backend für die Browser-Extension und andere Clients.

Start:  scamguard api   →  Doku unter http://127.0.0.1:8000/docs
Bilder werden nur temporär gespeichert und nach dem Scan gelöscht.

Schutz gegen Cross-Site-Anfragen: Ohne Gegenmaßnahme könnte jede geöffnete Webseite im
Hintergrund an http://127.0.0.1:8000/scan posten (mit use_llm=true sogar auf Kosten des API-Kontos).
Deshalb verlangt /scan den Header `X-ScamGuard-Client`. Webseiten können eigene Header nur nach
einem CORS-Preflight senden, den diese API nie erlaubt – die Extension (mit Host-Berechtigung),
Skripte und Tests dagegen schon. Zusätzlich werden fremde `Origin`-Header abgewiesen.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile

from scamguard import __version__
from scamguard.config import load_config
from scamguard.data.discovery import label_of
from scamguard.data.labels import count_labels, save_label, save_label_images
from scamguard.data.loaders import IMAGE_SUFFIXES, normalize_category, parse_price
from scamguard.pipeline import ScamGuard
from scamguard.schema import Listing, ModelResult

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGES = 10
CLIENT_HEADER = "X-ScamGuard-Client"
EXTENSION_SCHEMES = ("chrome-extension", "moz-extension")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

app = FastAPI(title="ScamGuard API", version=__version__,
              description="Betrugserkennung für deutschsprachige Kleinanzeigen")


def require_trusted_client(request: Request) -> None:
    if not request.headers.get(CLIENT_HEADER):
        raise HTTPException(403, f"Header {CLIENT_HEADER} fehlt (Schutz vor Cross-Site-Anfragen)")
    origin = request.headers.get("origin")
    if origin:
        parsed = urlparse(origin)
        if parsed.scheme not in EXTENSION_SCHEMES and parsed.hostname not in LOCAL_HOSTS:
            raise HTTPException(403, f"Origin {origin} ist nicht erlaubt")


LLM_FLAGS = {"true": True, "1": True, "on": True, "false": False, "0": False, "off": False}


@lru_cache(maxsize=2)
def _guard(use_llm: bool) -> ScamGuard:
    return ScamGuard(overrides={"llm": {"enabled": use_llm}})


def _llm_provider() -> str:
    return load_config()["llm"].get("provider", "local")


def resolve_llm_mode(mode: str) -> bool:
    """"auto" = nur ein lokales LLM automatisch nutzen (kostenlos, Daten bleiben auf dem Rechner);
    Claude (kostenpflichtig) nur, wenn ausdrücklich "true" angefragt wird."""
    mode = mode.strip().lower()
    if mode == "auto":
        return _llm_provider() == "local"
    if mode in LLM_FLAGS:
        return LLM_FLAGS[mode]
    raise HTTPException(422, "use_llm muss auto, true oder false sein")


@app.get("/health")
def health() -> dict:
    guard = _guard(False)
    llm = load_config()["llm"]
    provider = llm.get("provider", "local")
    model = llm.get(provider, {}).get("model", "")
    return {"status": "ok", "version": __version__,
            "detectors": {d.name: d.available for d in guard.detectors},
            "llm": {"provider": provider, "model": model, "auto": provider == "local"},
            "labels": count_labels()}


def _parse_listing(listing: str) -> dict:
    try:
        data = json.loads(listing)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, f"listing ist kein gültiges JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(422, "listing muss ein JSON-Objekt sein")
    data.pop("label", None)  # Labels kommen nie aus dem Inserat selbst
    # Clients (z. B. die Extension) schicken Rohtexte von der Seite: "15 € VB", "Elektronik > Handy"
    data["price"] = parse_price(data.get("price"))
    data["category"] = normalize_category(data.get("category"))
    return data


def _read_images(images: list[UploadFile] | None) -> list[tuple[str, bytes]]:
    """Hochgeladene Bilder prüfen → [(Dateiendung, Inhalt)]."""
    images = images or []
    if len(images) > MAX_IMAGES:
        raise HTTPException(413, f"Maximal {MAX_IMAGES} Bilder")
    result = []
    for upload in images:
        content = upload.file.read(MAX_IMAGE_BYTES + 1)
        if len(content) > MAX_IMAGE_BYTES:
            raise HTTPException(413, f"Bild {upload.filename} ist größer als 10 MB")
        suffix = Path(upload.filename or "").suffix.lower()
        result.append((suffix if suffix in IMAGE_SUFFIXES else ".jpg", content))
    return result


# Die Extension prüft zweistufig: erst ohne, dann mit LLM. Stufe 2 übernimmt die Ergebnisse der
# übrigen Detektoren aus Stufe 1 (gleiches Inserat, gleiche Bilder), statt sie neu zu berechnen.
FAST_CACHE_SIZE = 64
_fast_results: OrderedDict[str, dict[str, ModelResult]] = OrderedDict()
_fast_lock = threading.Lock()


def _cache_key(data: dict, images: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    for _suffix, content in images:
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


@app.post("/scan", dependencies=[Depends(require_trusted_client)])
def scan(  # sync: FastAPI führt es im Threadpool aus, der Scan blockiert so nicht den Event-Loop
    listing: Annotated[str, Form(description="Inserat als JSON (Felder siehe schema.Listing)")],
    images: Annotated[list[UploadFile] | None, File()] = None,
    use_llm: Annotated[str, Form(description="auto | true | false")] = "false",
) -> dict:
    run_llm = resolve_llm_mode(use_llm)
    data = _parse_listing(listing)
    image_data = _read_images(images)
    key = _cache_key(data, image_data)
    with _fast_lock:
        reuse = {name: r for name, r in _fast_results.get(key, {}).items() if name != "llm"} if run_llm else None

    with tempfile.TemporaryDirectory(prefix="scamguard_") as tmp:
        data["image_paths"] = []
        for i, (suffix, content) in enumerate(image_data):
            path = Path(tmp) / f"img_{i}{suffix}"
            path.write_bytes(content)
            data["image_paths"].append(str(path))
        result = _guard(run_llm).scan(Listing.from_dict(data), reuse=reuse)

    if not run_llm:
        with _fast_lock:
            _fast_results[key] = {r.name: r for r in result.model_results}
            _fast_results.move_to_end(key)
            while len(_fast_results) > FAST_CACHE_SIZE:
                _fast_results.popitem(last=False)
    return result.to_dict()


@app.post("/label", dependencies=[Depends(require_trusted_client)])
def label(
    listing: Annotated[str, Form(description="Inserat als JSON")],
    label: Annotated[str, Form(description="betrug | serioes (auch ja/nein, 1/0 …)")],
    images: Annotated[list[UploadFile] | None, File()] = None,
) -> dict:
    """Eigene Einstufung speichern → Trainingsdaten (data/raw/eigene_labels.jsonl)."""
    value = label_of(label)
    if value is None:
        raise HTTPException(422, "label muss betrug oder serioes sein")
    data = _parse_listing(listing)
    data.update(label=value, source="eigene_labels",
                image_paths=save_label_images(_read_images(images)))
    count = save_label(Listing.from_dict(data))
    return {"ok": True, "label": "betrug" if value else "serioes", "count": count}
