"""REST-API (FastAPI) – Backend für die Browser-Extension und andere Clients.

Start:  scamguard api   →  Doku unter http://127.0.0.1:8000/docs
Bilder werden nur temporär gespeichert und nach dem Scan gelöscht.

Schutz gegen Cross-Site-Anfragen: Ohne Gegenmaßnahme könnte jede geöffnete Webseite im
Hintergrund an http://127.0.0.1:8000/scan posten (mit use_llm=true sogar auf eure API-Kosten).
Deshalb verlangt /scan den Header `X-ScamGuard-Client`. Webseiten können eigene Header nur nach
einem CORS-Preflight senden, den diese API nie erlaubt – die Extension (mit Host-Berechtigung),
Skripte und Tests dagegen schon. Zusätzlich werden fremde `Origin`-Header abgewiesen.
"""

from __future__ import annotations

import json
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile

from scamguard import __version__
from scamguard.data.loaders import normalize_category, parse_price
from scamguard.pipeline import ScamGuard
from scamguard.schema import Listing

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGES = 10
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
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


@lru_cache(maxsize=2)
def _guard(use_llm: bool) -> ScamGuard:
    return ScamGuard(overrides={"llm": {"enabled": use_llm}})


@app.get("/health")
def health() -> dict:
    guard = _guard(False)
    return {"status": "ok", "version": __version__,
            "detectors": {d.name: d.available for d in guard.detectors}}


@app.post("/scan", dependencies=[Depends(require_trusted_client)])
def scan(  # sync: FastAPI führt es im Threadpool aus, der Scan blockiert so nicht den Event-Loop
    listing: Annotated[str, Form(description="Inserat als JSON (Felder siehe schema.Listing)")],
    images: Annotated[list[UploadFile] | None, File()] = None,
    use_llm: Annotated[bool, Form()] = False,
) -> dict:
    images = images or []
    try:
        data = json.loads(listing)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, f"listing ist kein gültiges JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(422, "listing muss ein JSON-Objekt sein")
    data.pop("label", None)  # Labels haben im Scan nichts zu suchen
    if len(images) > MAX_IMAGES:
        raise HTTPException(413, f"Maximal {MAX_IMAGES} Bilder")
    # Clients (z. B. die Extension) schicken Rohtexte von der Seite: "15 € VB", "Elektronik > Handy"
    data["price"] = parse_price(data.get("price"))
    data["category"] = normalize_category(data.get("category"))

    with tempfile.TemporaryDirectory(prefix="scamguard_") as tmp:
        paths = []
        for i, upload in enumerate(images):
            content = upload.file.read(MAX_IMAGE_BYTES + 1)
            if len(content) > MAX_IMAGE_BYTES:
                raise HTTPException(413, f"Bild {upload.filename} ist größer als 10 MB")
            suffix = Path(upload.filename or "").suffix.lower()
            path = Path(tmp) / f"img_{i}{suffix if suffix in IMAGE_SUFFIXES else '.jpg'}"
            path.write_bytes(content)
            paths.append(str(path))
        data["image_paths"] = paths
        result = _guard(use_llm).scan(Listing.from_dict(data))
    return result.to_dict()
