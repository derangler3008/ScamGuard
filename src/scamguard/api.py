"""REST-API (FastAPI) – z. B. für eine spätere Browser-Erweiterung oder App.

Start:  scamguard api   →  Doku unter http://127.0.0.1:8000/docs
Bilder werden nur temporär gespeichert und nach dem Scan gelöscht.
"""

from __future__ import annotations

import json
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from scamguard import __version__
from scamguard.pipeline import ScamGuard
from scamguard.schema import Listing

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGES = 10

app = FastAPI(title="ScamGuard API", version=__version__,
              description="Betrugserkennung für deutschsprachige Kleinanzeigen")


@lru_cache(maxsize=2)
def _guard(use_llm: bool) -> ScamGuard:
    return ScamGuard(overrides={"llm": {"enabled": use_llm}})


@app.get("/health")
def health() -> dict:
    guard = _guard(False)
    return {"status": "ok", "version": __version__,
            "detectors": {d.name: d.available for d in guard.detectors}}


@app.post("/scan")
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

    with tempfile.TemporaryDirectory(prefix="scamguard_") as tmp:
        paths = []
        for i, upload in enumerate(images):
            content = upload.file.read(MAX_IMAGE_BYTES + 1)
            if len(content) > MAX_IMAGE_BYTES:
                raise HTTPException(413, f"Bild {upload.filename} ist größer als 10 MB")
            suffix = Path(upload.filename or "").suffix.lower() or ".jpg"
            path = Path(tmp) / f"img_{i}{suffix}"
            path.write_bytes(content)
            paths.append(str(path))
        data["image_paths"] = paths
        result = _guard(use_llm).scan(Listing.from_dict(data))
    return result.to_dict()
