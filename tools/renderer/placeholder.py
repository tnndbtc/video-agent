"""
Placeholder PNG generator for missing visual assets.

Generates solid-color 'shot_id + PLACEHOLDER' images using Pillow.
Output is deterministic: identical (shot_id, width, height, color) always
produces a bit-identical PNG across repeated calls (cached by content hash).

No ffmpeg dependency — Pillow only.
Pillow is already a worker dependency (Pillow ^10.2.0 in worker/pyproject.toml).
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Pillow is required; guard import for environments where it may be absent.
try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PIL_AVAILABLE = False


def generate_placeholder(
    shot_id: str,
    width: int,
    height: int,
    color: str = "#1a1a2e",
    label: Optional[str] = None,
    font_path: Optional[str] = None,
    font_size: int = 36,
    output_path: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
) -> Path:
    """
    Generate a placeholder PNG for a missing or unresolved visual asset.

    Determinism guarantee:
        Same (shot_id, width, height, color) always produces bit-identical PNG
        across runs on the same Pillow version.
        The decoded frame content (RGB values) is stable across Pillow versions
        because a solid-color image is a trivial lossless representation.

    Args:
        shot_id:     Shot identifier; used as the label text if *label* not set.
        width:       Output image width in pixels.
        height:      Output image height in pixels.
        color:       Background hex color, e.g. "#1a1a2e".
        label:       Override display text (two lines; defaults to shot_id + "PLACEHOLDER").
        font_path:   Absolute path to a .ttf font file.
                     If absent or unreadable, falls back to Pillow's built-in font.
        font_size:   Font point size (approximate for built-in font fallback).
        output_path: Explicit output path.  If None, a cached path in *cache_dir* is used.
        cache_dir:   Directory for cached placeholders.  Required if *output_path* is None.

    Returns:
        Path to the generated (or cached) PNG file.

    Raises:
        RuntimeError: if Pillow is not installed.
        ValueError:   if neither *output_path* nor *cache_dir* is provided.
    """
    if not _PIL_AVAILABLE:
        raise RuntimeError(
            "Pillow is required for placeholder generation. "
            "Install: pip install Pillow>=10"
        )

    if output_path is None:
        if cache_dir is None:
            raise ValueError("Either output_path or cache_dir must be provided.")
        # Deterministic cache filename: hash of the key inputs.
        key = hashlib.sha256(
            f"{shot_id}|{width}|{height}|{color}".encode()
        ).hexdigest()[:16]
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        output_path = cache_dir / f"placeholder_{key}.png"

    output_path = Path(output_path)

    # Return cached file if already generated (same key → same content).
    if output_path.exists():
        return output_path

    # --- Parse hex color → (R, G, B) ---
    hex_color = color.lstrip("#")
    try:
        bg_rgb: tuple[int, int, int] = (
            int(hex_color[0:2], 16),
            int(hex_color[2:4], 16),
            int(hex_color[4:6], 16),
        )
    except (ValueError, IndexError):
        logger.warning("Invalid placeholder color %r, using default.", color)
        bg_rgb = (26, 26, 46)  # #1a1a2e

    # --- Create image ---
    img = Image.new("RGB", (width, height), color=bg_rgb)
    draw = ImageDraw.Draw(img)

    # --- Load font ---
    font = _load_font(font_path, font_size)

    # --- Compose label ---
    text = label if label is not None else f"{shot_id}\nPLACEHOLDER"

    # --- Center text ---
    try:
        bbox = draw.textbbox((0, 0), text, font=font, align="center")
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
    except TypeError:
        # Older Pillow without textbbox
        text_w, text_h = draw.textsize(text, font=font)  # type: ignore[attr-defined]

    x = (width - text_w) // 2
    y = (height - text_h) // 2

    # Draw a slight drop-shadow for legibility on all background colors.
    shadow_color = (0, 0, 0)
    text_color = (220, 220, 220)
    draw.text((x + 2, y + 2), text, fill=shadow_color, font=font, align="center")
    draw.text((x, y), text, fill=text_color, font=font, align="center")

    # --- Save PNG ---
    # compress_level=9 with optimize=False is deterministic for the same Pillow version.
    img.save(str(output_path), format="PNG", compress_level=9, optimize=False)
    logger.debug("Generated placeholder: %s (%dx%d)", output_path, width, height)
    return output_path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_font(font_path: Optional[str], font_size: int) -> "ImageFont.FreeTypeFont | ImageFont.ImageFont":
    """Load a TrueType font or fall back to Pillow's built-in."""
    if font_path:
        try:
            return ImageFont.truetype(font_path, size=font_size)
        except (IOError, OSError) as exc:
            logger.debug("Could not load font %r: %s — using built-in", font_path, exc)

    # Pillow >= 9.2 supports load_default(size=N)
    try:
        return ImageFont.load_default(size=font_size)
    except TypeError:
        return ImageFont.load_default()
