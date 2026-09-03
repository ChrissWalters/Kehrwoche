"""Draw the screenshot placeholders.

The README shows four screenshots. Until the real ones are taken these files keep the
page from displaying broken images, and they say plainly what belongs in each slot so
nobody mistakes them for the finished article.

    python tools/make_placeholders.py

Do not run this again once the real screenshots are in place — it overwrites them.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

TARGET = Path(__file__).resolve().parent.parent / "docs" / "images"

#: The accent colour of the interface, and a grey that reads as "not finished".
ACCENT = (47, 111, 79)
BACKGROUND = (238, 238, 236)
LINE = (170, 170, 166)
TEXT = (90, 90, 88)

PLACEHOLDERS = [
    ("mobile-chores.png", 360, 640, "Chores"),
    ("mobile-shopping.png", 360, 640, "Shopping list"),
    ("mobile-expenses.png", 360, 640, "Balances"),
    ("desktop-feed.png", 1280, 800, "Pinboard (desktop)"),
]


def draw(name: str, width: int, height: int, caption: str) -> None:
    image = Image.new("RGB", (width, height), BACKGROUND)
    pen = ImageDraw.Draw(image)

    # A header bar in the accent colour, so the shape reads as an application.
    pen.rectangle((0, 0, width, height // 12), fill=ACCENT)

    # A dashed frame: unmistakably a placeholder rather than a rendering.
    inset = width // 20
    for x in range(inset, width - inset, 16):
        pen.line((x, inset * 2, x + 8, inset * 2), fill=LINE, width=2)
        pen.line((x, height - inset, x + 8, height - inset), fill=LINE, width=2)
    for y in range(inset * 2, height - inset, 16):
        pen.line((inset, y, inset, y + 8), fill=LINE, width=2)
        pen.line((width - inset, y, width - inset, y + 8), fill=LINE, width=2)

    lines = [caption, "", "screenshot placeholder", f"{width} x {height}"]
    box = pen.multiline_textbbox((0, 0), "\n".join(lines), align="center", spacing=8)
    pen.multiline_text(
        ((width - box[2]) / 2, (height - box[3]) / 2),
        "\n".join(lines),
        fill=TEXT,
        align="center",
        spacing=8,
    )

    image.save(TARGET / name, format="PNG", optimize=True)
    print(f"{name}: {width}x{height}")


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    for name, width, height, caption in PLACEHOLDERS:
        draw(name, width, height, caption)


if __name__ == "__main__":
    main()
