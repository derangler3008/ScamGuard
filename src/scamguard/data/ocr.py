"""Texterkennung (OCR) für Screenshots von Inseraten – lokal mit Apple Vision (macOS, neuronales Netz
von Apple, ohne Download und ohne Cloud).

`recognize` liefert Zeilen mit Position in Pixeln. `layout_rows` setzt daraus wieder lesbare Zeilen
zusammen: Desktop-Screenshots haben rechts eine Seitenspalte (Anbieter, Login-Hinweis), die sonst
mitten in die Beschreibung rutschen würde.
"""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass
from functools import lru_cache

# Sehr hohe Bilder (ganze Seite als Screenshot) verkleinert Vision intern – dann leidet die
# Erkennung. Deshalb in Kacheln von höchstens 1,5 × Bildbreite lesen, mit etwas Überlappung.
TILE_RATIO = 1.5
TILE_OVERLAP = 120
UNAVAILABLE_HINT = ("Texterkennung für Screenshots gibt es bisher nur auf dem Mac (Apple Vision). "
                    "Auf anderen Rechnern die Seite als .html oder PDF speichern oder den Text einfügen.")


@dataclass(frozen=True)
class OcrLine:
    text: str
    top: float     # Pixel, 0 = oberer Bildrand
    height: float
    left: float
    right: float


class OcrUnavailable(RuntimeError):
    """Auf diesem System gibt es keine Texterkennung."""


@lru_cache(maxsize=1)
def _vision():
    if sys.platform != "darwin":
        return None
    try:
        import Vision
        from Foundation import NSData
    except ImportError:
        return None
    return Vision, NSData


def available() -> bool:
    return _vision() is not None


def _recognize_tile(png: bytes) -> list[tuple[str, float, float, float, float]]:
    """Vision auf einem Bildausschnitt; Koordinaten normiert (0..1), Ursprung oben links."""
    vision, nsdata = _vision()
    handler = vision.VNImageRequestHandler.alloc().initWithData_options_(
        nsdata.dataWithBytes_length_(png, len(png)), None)
    request = vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["de-DE", "en-US"])
    request.setUsesLanguageCorrection_(True)
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise ValueError(f"Texterkennung fehlgeschlagen: {error}")
    out = []
    for obs in request.results() or []:
        candidates = obs.topCandidates_(1)
        text = str(candidates[0].string()).strip() if candidates else ""
        if text:
            box = obs.boundingBox()  # Vision: Ursprung unten links
            out.append((text, 1 - box.origin.y - box.size.height, box.size.height,
                        box.origin.x, box.origin.x + box.size.width))
    return out


def recognize(data: bytes) -> tuple[list[OcrLine], int]:
    """Alle Textzeilen eines Bildes plus Bildbreite.
    Wirft OcrUnavailable (kein Mac) bzw. ValueError (kein Bild)."""
    if not available():
        raise OcrUnavailable(UNAVAILABLE_HINT)
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Datei ist kein lesbares Bild") from exc
    width, height = image.size
    tile = max(int(width * TILE_RATIO), TILE_OVERLAP * 2)
    lines: list[OcrLine] = []
    y = 0
    while True:
        crop = image.crop((0, y, width, min(height, y + tile)))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        h = crop.size[1]
        for text, top, rel_h, left, right in _recognize_tile(buf.getvalue()):
            line = OcrLine(text, y + top * h, rel_h * h, left * width, right * width)
            # Überlappung: dieselbe Zeile nicht doppelt aus zwei Kacheln übernehmen
            if not any(o.text == line.text and abs(o.top - line.top) < line.height for o in lines):
                lines.append(line)
        if y + tile >= height:
            return lines, width
        y += tile - TILE_OVERLAP


def _side_column_start(lines: list[OcrLine], width: float) -> float | None:
    """x-Position einer rechten Seitenspalte (Desktop-Layout) oder None (eine Spalte).

    Gesucht ist die am weitesten rechts liegende Kante, an der eine Spalte mit ≥ 5 Zeilen beginnt
    und die von höchstens 2 Zeilen der linken Spalte überquert wird."""
    tolerance = 0.01 * width
    for start in sorted({ln.left for ln in lines if 0.45 * width <= ln.left <= 0.85 * width}, reverse=True):
        main = [ln for ln in lines if ln.left < start - tolerance]
        side = [ln for ln in lines if ln.left >= start - tolerance]
        crossing = sum(1 for ln in main if ln.right > start + tolerance)
        if len(side) >= 5 and len(main) >= 5 and crossing <= 2:
            return start - tolerance
    return None


def _rows(lines: list[OcrLine], width: float) -> list[str]:
    """Zeilen von oben nach unten; Teile auf gleicher Höhe (Tabellen) zu einer Zeile verbinden,
    umbrochene Absätze wieder zu einer Zeile zusammenfügen."""
    rows: list[list[OcrLine]] = []
    for line in sorted(lines, key=lambda ln: ln.top):
        if rows and abs(rows[-1][0].top - line.top) < 0.5 * min(rows[-1][0].height, line.height):
            rows[-1].append(line)
        else:
            rows.append([line])
    out: list[str] = []
    prev: OcrLine | None = None
    for row in rows:
        row.sort(key=lambda ln: ln.left)
        text = " ".join(ln.text for ln in row)
        first = row[0]
        wrapped = (prev is not None and len(row) == 1 and len(out[-1]) >= 40
                   and abs(first.left - prev.left) < 0.02 * width
                   and first.top - (prev.top + prev.height) < 0.8 * prev.height)
        if wrapped:
            out[-1] = f"{out[-1]} {text}"
        else:
            out.append(text)
        prev = first
    return out


def layout_rows(lines: list[OcrLine], width: float) -> tuple[list[str], list[str]]:
    """(Hauptspalte, Seitenspalte) als lesbare Zeilen."""
    start = _side_column_start(lines, width)
    if start is None:
        return _rows(lines, width), []
    return (_rows([ln for ln in lines if ln.left < start], width),
            _rows([ln for ln in lines if ln.left >= start], width))
