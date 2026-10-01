"""Erzeugt die Extension-Icons (Schild mit Lupe) in 16/32/48/128 px.

Aufruf:  python scripts/make_extension_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "extension" / "icons"
BASE = 512  # groß zeichnen, dann verkleinern → glatte Kanten
BLUE = (23, 92, 211, 255)
WHITE = (255, 255, 255, 255)


def draw_icon() -> Image.Image:
    img = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Schild: oben gerade, unten spitz zulaufend
    shield = [(256, 24), (464, 96), (448, 280), (256, 488), (64, 280), (48, 96)]
    d.polygon(shield, fill=BLUE)
    # Lupe
    d.ellipse((150, 140, 330, 320), outline=WHITE, width=36)
    d.line((305, 295, 385, 375), fill=WHITE, width=48)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    icon = draw_icon()
    for size in (16, 32, 48, 128):
        icon.resize((size, size), Image.Resampling.LANCZOS).save(OUT / f"icon{size}.png")
        print(f"{OUT / f'icon{size}.png'}")


if __name__ == "__main__":
    main()
