"""Inserat hochladen: gespeicherte Seite, Text, PDF, Screenshot (OCR) und Ordner – synthetische Daten."""

import io
import json

import pytest
from PIL import Image, ImageDraw, ImageFont

from scamguard.data import discovery, ocr
from scamguard.data.listing_import import import_listing, listing_from_html, parse_rows
from scamguard.data.loaders import load_spec
from scamguard.data.ocr import OcrLine, layout_rows

# Aufbau wie eine echte Kleinanzeigen-Seite (Stand 10/2026), Inhalte erfunden
HTML = """<html><head>
<link rel="canonical" href="https://www.kleinanzeigen.de/s-anzeige/ps5/123-279-1">
</head><body>
<div id="vap-brdcrmb"><a class="breadcrump-link">Kleinanzeigen Mannheim</a>
  <a class="breadcrump-link">Elektronik</a><a class="breadcrump-link">Konsolen</a></div>
<h1 id="viewad-title"><span>Reserviert •</span> PlayStation 5 Disc</h1>
<h2 id="viewad-price">350 € VB</h2>
<span id="viewad-locality">68159 Mannheim - Innenstadt</span>
<p id="viewad-description-text">Zahlung nur per <b>PayPal Freunde und Familie</b>.<br>Versand per DHL.</p>
<div id="viewad-contact"><span class="userprofile-vip">Max Muster</span>
  <span class="userprofile-vip-details-text">Aktiv seit 01.01.2020</span></div>
</body></html>"""

PASTED = """Zum Inhalt springen
Kleinanzeigen Mannheim › Elektronik › Haushaltsgeräte
Reserviert • Waschmaschine Bosch 8 kg
1.200 € VB
Musterstraße 1, 68159 Mannheim - Innenstadt
Beschreibung
Neuwertig, kaum benutzt.
Bezahlung vorab per Überweisung, Abholung nicht möglich.
Nachricht schreiben
Erika Beispiel
Privater Nutzer
Aktiv seit 15.03.2021
Das könnte dich auch interessieren
Kühlschrank 50 €"""


def test_saved_kleinanzeigen_page():
    fields = listing_from_html(HTML)
    assert fields["title"] == "PlayStation 5 Disc"            # ohne „Reserviert“-Abzeichen
    assert fields["price"] == 350.0 and fields["category"] == "elektronik"
    assert fields["location"] == "68159 Mannheim - Innenstadt"
    assert fields["description"].splitlines() == ["Zahlung nur per PayPal Freunde und Familie.",
                                                  "Versand per DHL."]
    assert fields["seller_account_age_days"] > 2000
    assert fields["url"].endswith("/s-anzeige/ps5/123-279-1")
    assert "Max" not in json.dumps(fields)                    # Anbietername wird nicht übernommen


def test_unknown_html_is_read_as_text():
    fields = listing_from_html("<html><body><script>x()</script><h1>iPhone 15</h1><p>99 €</p></body></html>")
    assert fields["title"] == "iPhone 15" and fields["price"] == 99.0


def test_pasted_page_text():
    fields = parse_rows(PASTED.splitlines())
    assert fields["title"] == "Waschmaschine Bosch 8 kg"
    assert fields["price"] == 1200.0 and fields["category"] == "haushaltsgeraete"
    assert fields["location"] == "68159 Mannheim - Innenstadt"   # ohne Straße
    assert fields["description"] == ("Neuwertig, kaum benutzt.\n"
                                     "Bezahlung vorab per Überweisung, Abholung nicht möglich.")
    assert fields["seller_account_age_days"] > 1000


def test_mileage_or_amount_is_not_a_postcode():
    fields = parse_rows(["VW Golf 7 Kilometerstand 12000 Km", "9.500 €", "68159 Mannheim"])
    assert fields["location"] == "68159 Mannheim"


def test_text_without_description_heading_skips_seller_name_and_other_listings():
    rows = ["iPhone 15 Pro", "800 €", "Akku 100 %, keine Kratzer, mit OVP", "Erika Beispiel",
            "Privater Nutzer", "Ähnliche Anzeigen", "Samsung S24 Ultra wie neu"]
    fields = parse_rows(rows)
    assert fields["title"] == "iPhone 15 Pro"
    assert fields["description"] == "Akku 100 %, keine Kratzer, mit OVP"


def _minimal_pdf(text: str) -> bytes:
    """Einseitiges PDF mit Text (Standardschrift) – ohne zusätzliche Bibliothek."""
    lines = text.splitlines()
    ops = "BT /F1 12 Tf 50 750 Td 14 TL " + " ".join(f"({ln}) Tj T*" for ln in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
         "/Resources << /Font << /F1 5 0 R >> >> >>"),
        f"<< /Length {len(ops)} >>\nstream\n{ops}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, obj in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


def test_pdf_and_chat_text():
    pdf = _minimal_pdf("Fahrrad Damen 28 Zoll\n90 EUR\nBeschreibung\nNur Vorkasse, kein Versand.")
    result = import_listing([("anzeige.pdf", pdf)], chat_text="Hier der Link: https://paypa1-sicher.de")
    assert result.listing.title == "Fahrrad Damen 28 Zoll" and result.listing.price == 90.0
    assert "Vorkasse" in result.listing.description
    assert result.listing.messages == ["Hier der Link: https://paypa1-sicher.de"]
    assert "PDF-Text gelesen" in result.notes[0]


def test_unsupported_and_empty_inputs():
    result = import_listing([("notizen.docx", b"xx")])
    assert result.empty and "nicht unterstützt" in result.notes[0]


# --------------------------------------------------------------------------- Layout der Texterkennung

def _line(text, top, left, right, height=20):
    return OcrLine(text, top, height, left, right)


def test_desktop_side_column_is_kept_out_of_the_description():
    width = 1280
    main = [_line("WG Zimmer Mannheim", 100, 160, 400), _line("450 €", 130, 160, 220),
            _line("Beschreibung", 200, 160, 300),
            _line("Helles Zimmer mit Balkon, 14 m², ab sofort frei, Nebenkosten inklusive.", 230, 160, 790),
            _line("Besichtigung jederzeit nach Absprache möglich.", 290, 160, 600)]  # neuer Absatz
    side = [_line(t, 100 + 30 * i, 830, 1100) for i, t in enumerate(
        ["Erika Beispiel", "Privater Nutzer", "Aktiv seit 01.02.2022", "Anzeige melden", "Folgen"])]
    rows, side_rows = layout_rows(main + side, width)
    assert "Erika Beispiel" in side_rows and not any("Erika" in r for r in rows)
    fields = parse_rows(rows, side_rows)
    assert fields["title"] == "WG Zimmer Mannheim" and fields["price"] == 450.0
    assert fields["description"] == ("Helles Zimmer mit Balkon, 14 m², ab sofort frei, Nebenkosten "
                                     "inklusive.\nBesichtigung jederzeit nach Absprache möglich.")
    assert fields["seller_account_age_days"] > 0          # Kontoalter kommt aus der Seitenspalte


def test_wrapped_lines_and_table_rows_are_joined():
    lines = [_line("Verkaufe meine Spülmaschine, sie läuft einwandfrei und ist", 100, 50, 900),
             _line("erst zwei Jahre alt.", 122, 50, 300),
             _line("Marke", 200, 50, 150), _line("Bosch", 200, 600, 700)]
    rows, side = layout_rows(lines, 1000)
    assert rows == ["Verkaufe meine Spülmaschine, sie läuft einwandfrei und ist erst zwei Jahre alt.",
                    "Marke Bosch"] and side == []


# --------------------------------------------------------------------------- echte Texterkennung (Mac)

def _screenshot(lines: list[tuple[str, int]]) -> bytes:
    img = Image.new("RGB", (900, 120 + 60 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    y = 40
    for text, size in lines:
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
        except OSError:
            font = ImageFont.load_default(size)
        draw.text((40, y), text, font=font, fill="black")
        y += size + 30
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.skipif(not ocr.available(), reason="Texterkennung nur auf dem Mac (Apple Vision)")
def test_screenshot_and_photo_are_told_apart():
    shot = _screenshot([("PlayStation 5 Disc Edition", 40), ("120 € VB", 40), ("Beschreibung", 24),
                        ("Zahlung nur über PayPal Freunde und Familie.", 24),
                        ("Ich bin im Ausland, Versand per DHL.", 24)])
    photo = io.BytesIO()
    Image.new("RGB", (400, 300), (120, 160, 200)).save(photo, format="JPEG")
    result = import_listing([("screen.png", shot), ("foto.jpg", photo.getvalue())])
    listing = result.listing
    assert listing.title == "PlayStation 5 Disc Edition" and listing.price == 120.0
    assert "Freunde und Familie" in listing.description
    assert [name for name, _ in result.photos] == ["foto.jpg"]


def test_without_ocr_images_become_photos(monkeypatch):
    def unavailable(data):
        raise ocr.OcrUnavailable(ocr.UNAVAILABLE_HINT)

    monkeypatch.setattr(ocr, "recognize", unavailable)
    result = import_listing([("screen.png", b"egal")], text="Titel\n10 €\nBeschreibung\nText hier")
    assert result.photos and "nur auf dem Mac" in result.notes[0]
    assert result.listing.description == "Text hier"


# --------------------------------------------------------------------------- Ordner

def test_listing_folder_is_a_dataset(tmp_path, monkeypatch):
    root = tmp_path / "datensatz_fuellen_inserate"
    (root / "betrug" / "ps5_fall").mkdir(parents=True)
    (root / "serioes").mkdir()
    (root / "betrug" / "inserat1.txt").write_text(PASTED, encoding="utf-8")
    (root / "betrug" / "ps5_fall" / "seite.html").write_text(HTML, encoding="utf-8")
    Image.new("RGB", (40, 40), "red").save(root / "betrug" / "ps5_fall" / "foto.jpg")
    (root / "betrug" / "_entwurf.txt").write_text("ignorieren", encoding="utf-8")
    monkeypatch.setattr(discovery, "LISTING_DIR", str(root))
    monkeypatch.setattr(discovery, "TEXT_DIR", str(tmp_path / "leer"))
    monkeypatch.setattr(discovery, "IMAGE_DIR", str(tmp_path / "leer"))
    monkeypatch.setattr(ocr, "recognize", lambda data: ([], 40))  # Foto ohne Text

    specs = {s.name: s for s in discovery.discover_specs()}
    assert specs["inserate:betrug"].enabled and "2 Inserate" in specs["inserate:betrug"].notes
    assert not specs["inserate:serioes"].enabled
    report = load_spec(specs["inserate:betrug"])
    assert report.error is None and len(report.listings) == 2
    by_title = {l.title: l for l in report.listings}
    assert by_title["PlayStation 5 Disc"].label == 1
    assert by_title["PlayStation 5 Disc"].image_paths[0].endswith("foto.jpg")  # Foto bleibt, wo es liegt
    assert by_title["Waschmaschine Bosch 8 kg"].price == 1200.0
