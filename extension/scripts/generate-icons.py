#!/usr/bin/env python3
"""Generate PaperForge extension icons in recommended sizes.

Usage:
    python scripts/generate-icons.py

Outputs PNG icons to extension/icons/:
    icon16.png, icon32.png, icon48.png, icon128.png

The script is intentionally dependency-light: it only requires Pillow.
"""
from __future__ import annotations

from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "Pillow is required to generate icons. "
        "Install it with: pip install Pillow"
    ) from exc


SIZES = (16, 32, 48, 128)
BACKGROUND_COLOR = (37, 99, 235)  # matches extension primary blue #2563eb
TEXT_COLOR = (255, 255, 255)
TEXT = "PF"


def _pick_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Try to load a system font; fall back to Pillow's default bitmap font."""
    candidates = [
        "Arial.ttf",
        "arial.ttf",
        "Helvetica.ttf",
        "DejaVuSans-Bold.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, int(size * 0.45))
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def generate_icon(size: int, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> Image:
    """Render a single square icon at ``size`` x ``size`` pixels."""
    img = Image.new("RGBA", (size, size), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(img)

    bbox = draw.textbbox((0, 0), TEXT, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    x = (size - text_width) // 2
    # Slight upward nudge so the letters look visually centered.
    y = (size - text_height) // 2 - int(size * 0.05)

    draw.text((x, y), TEXT, font=font, fill=TEXT_COLOR)
    return img


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    icons_dir = root / "icons"
    icons_dir.mkdir(exist_ok=True)

    # Remove old generated icons first so that stale sizes do not linger.
    for stale in icons_dir.glob("icon*.png"):
        stale.unlink()
        print(f"[icons] removed stale {stale.name}")

    for size in SIZES:
        font = _pick_font(size)
        img = generate_icon(size, font)
        out_path = icons_dir / f"icon{size}.png"
        img.save(out_path)
        print(f"[icons] generated {out_path} ({size}x{size})")


if __name__ == "__main__":  # pragma: no cover
    main()
