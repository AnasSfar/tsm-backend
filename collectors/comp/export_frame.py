from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


EXPORT_MARGIN_CSS_PX = 36
EXPORT_BACKGROUND = "#f3f5f7"
EXPORT_CORNER_RADIUS_CSS_PX = 18
EXPORT_TINT_STRENGTH = 0.10


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return mask


def _edge_tinted_background(src: Image.Image, base_hex: str, strength: float) -> tuple[int, int, int]:
    """Blend the base frame color with the card's own edge colors, so the
    margin reads as the base color subtly tinted by the card's accent."""
    try:
        rgb = src.convert("RGB")
        w, h = rgb.size
        strip = 6
        edges = Image.new("RGB", (w, strip * 2 + h), (0, 0, 0))
        edges.paste(rgb.crop((0, 0, w, strip)), (0, 0))
        edges.paste(rgb.crop((0, h - strip, w, h)), (0, strip))
        edges.paste(rgb.crop((0, 0, strip, h)).resize((strip, strip)), (0, strip * 2))
        edges.paste(rgb.crop((w - strip, 0, w, h)).resize((strip, strip)), (strip, strip * 2))
        sample = edges.resize((16, 16), Image.LANCZOS)
        pixels = list(sample.getdata())
        r = sum(p[0] for p in pixels) / len(pixels)
        g = sum(p[1] for p in pixels) / len(pixels)
        b = sum(p[2] for p in pixels) / len(pixels)
        base_r, base_g, base_b = _hex_to_rgb(base_hex)
        return (
            int(base_r * (1 - strength) + r * strength),
            int(base_g * (1 - strength) + g * strength),
            int(base_b * (1 - strength) + b * strength),
        )
    except Exception:
        return _hex_to_rgb(base_hex)


def add_export_frame(
    path: Path,
    *,
    margin_css_px: int = EXPORT_MARGIN_CSS_PX,
    device_scale_factor: int = 2,
    background: str = EXPORT_BACKGROUND,
    corner_radius_css_px: int = EXPORT_CORNER_RADIUS_CSS_PX,
    tint_strength: float = EXPORT_TINT_STRENGTH,
) -> Path:
    margin = max(0, int(margin_css_px) * int(device_scale_factor))
    radius = max(0, int(corner_radius_css_px) * int(device_scale_factor))
    if margin <= 0:
        return path

    with Image.open(path) as img:
        src = img.convert("RGBA")
        tint = _edge_tinted_background(src, background, tint_strength)
        framed = Image.new("RGBA", (src.width + margin * 2, src.height + margin * 2), tint + (255,))
        if radius > 0:
            mask = _rounded_mask(src.size, radius)
            framed.paste(src, (margin, margin), mask)
        else:
            framed.paste(src, (margin, margin), src)
        framed.save(path)
    return path
