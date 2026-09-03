"""Generate the application icons.

The icons are our own artwork, drawn from primitives so they can be regenerated at any
time: a broom on the accent colour of the interface. Run after changing the colours:

    python tools/generate_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ICON_DIR = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"

ACCENT = (47, 111, 79, 255)
LIGHT = (255, 255, 255, 255)
BRISTLE = (214, 231, 221, 255)

#: Drawn at this size and scaled down — cheap anti-aliasing.
CANVAS = 1024


def draw_broom(size: int, *, background: bool, padding: float) -> Image.Image:
    """A broom on a rounded square. ``padding`` leaves room for maskable safe areas."""
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    if background:
        draw.rounded_rectangle((0, 0, CANVAS - 1, CANVAS - 1), radius=CANVAS // 6, fill=ACCENT)

    layer = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    pen = ImageDraw.Draw(layer)
    centre = CANVAS / 2
    scale = (1 - 2 * padding) * CANVAS / 1024

    def point(x: float, y: float) -> tuple[float, float]:
        return centre + (x - 512) * scale, centre + (y - 512) * scale

    # Handle.
    pen.line([point(512, 170), point(512, 560)], fill=LIGHT, width=int(58 * scale), joint="curve")
    pen.ellipse([*point(483, 150), *point(541, 200)], fill=LIGHT)

    # Head: a trapezoid with a band where the bristles are tied.
    pen.polygon(
        [point(430, 560), point(594, 560), point(668, 858), point(356, 858)],
        fill=LIGHT,
    )
    pen.polygon(
        [point(424, 592), point(600, 592), point(612, 646), point(412, 646)],
        fill=ACCENT if background else BRISTLE,
    )
    for x in (452, 512, 572):
        pen.line(
            [point(x, 660), point(x + (x - 512) * 0.42, 852)],
            fill=ACCENT if background else BRISTLE,
            width=int(14 * scale),
        )

    image.alpha_composite(layer.rotate(28, resample=Image.BICUBIC, center=(centre, centre)))
    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    variants = {
        "icon-192.png": (192, True, 0.16),
        "icon-512.png": (512, True, 0.16),
        # Maskable icons get cropped to a circle by the launcher, so keep more margin.
        "icon-maskable-512.png": (512, True, 0.26),
        "apple-touch-icon.png": (180, True, 0.16),
        "favicon.png": (64, True, 0.12),
    }
    for name, (size, background, padding) in variants.items():
        draw_broom(size, background=background, padding=padding).save(ICON_DIR / name)
        print(f"wrote {name}")


if __name__ == "__main__":
    main()
