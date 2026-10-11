"""Swifties Charts X avatar + header, Eras Tour theme (black + gold glitter + 12 era colours)."""
import sys
import numpy as np
from PIL import Image, ImageFilter, ImageDraw

LOGO = r"C:/Users/sfara/Documents/GitHub/tsm-frontend/frontend/public/logo.png"
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
rng = np.random.default_rng(1989)

BG = np.array([4, 4, 5], float)
GOLD = [(255, 255, 255), (252, 252, 253), (244, 244, 247), (232, 232, 237)]  # white/silver foil
# Album order, colours from the redesign tickets / theme palettes
ERAS = ["#7fb38f", "#e8c46a", "#7b4aa6", "#b91c1c", "#90aec4", "#6b6b6b",
        "#f2a7c3", "#a9b0b8", "#b5793f", "#3a4a9a", "#d9d1c7", "#f97316"]


def hexrgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], float)


def blur(a, r):
    im = Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8))
    return np.asarray(im.filter(ImageFilter.GaussianBlur(r)), float) / 255


def glitter(h, w, density=0.06, size=1.0):
    """Fine glitter field 0..1: dense dim grain + sparse bright flecks."""
    k = size / 2  # look was tuned at size=2; keep fleck count/size per area constant at any resolution
    grain = blur(rng.random((h, w)) ** 6 * 0.18, 0.4 * k) if k > 1 else rng.random((h, w)) ** 6 * 0.18
    pts = (rng.random((h, w)) < density * 0.035 / k ** 2) * (rng.random((h, w)) ** 2.2)
    big = (rng.random((h, w)) < density * 0.004 / k ** 2) * rng.uniform(0.7, 1.0, (h, w))
    flecks = blur(pts, 0.42 * size) * 4.5 * k ** 2 + blur(big, 0.75 * size) * 13.0 * k ** 2
    return np.clip(grain + flecks, 0, 1)


def soft_noise(h, w, scale):
    n = rng.random((h // scale + 2, w // scale + 2))
    im = Image.fromarray((n * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    return np.asarray(im.filter(ImageFilter.GaussianBlur(scale / 3)), float) / 255


def bokeh(img, n, rmin, rmax, colors, alpha=(0.15, 0.55)):
    h, w, _ = img.shape
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(n):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        r = rng.uniform(rmin, rmax)
        c = colors[rng.integers(len(colors))]
        a = int(255 * rng.uniform(*alpha))
        d.ellipse([x - r, y - r, x + r, y + r], fill=(*[int(v) for v in c], a))
    layer = layer.filter(ImageFilter.GaussianBlur(rmax * 0.35))
    arr = np.asarray(layer, float) / 255
    a = arr[..., 3:4]
    return img * (1 - a) + arr[..., :3] * 255 * a


def sparkles(img, n, rmin, rmax, color=(255, 240, 205)):
    """4-point star flares (screen blend)."""
    h, w, _ = img.shape
    layer = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(layer)
    pts = []
    for _ in range(n):
        x, y, r = rng.uniform(0, w), rng.uniform(0, h), rng.uniform(rmin, rmax)
        pts.append((x, y, r))
        for k in range(1, 6):
            t = max(1, r * 0.06 / k)
            d.line([x - r / k, y, x + r / k, y], fill=int(60 * k), width=int(t))
            d.line([x, y - r / k, x, y + r / k], fill=int(60 * k), width=int(t))
        d.ellipse([x - r * .12, y - r * .12, x + r * .12, y + r * .12], fill=255)
    m = np.asarray(layer.filter(ImageFilter.GaussianBlur(rmin * 0.08 + 0.6)), float)[..., None] / 255
    return 255 - (255 - img) * (1 - m * np.array(color) / 255)


def background(h, w, era_wash):
    img = np.tile(BG, (h, w, 1))
    yy, xx = np.mgrid[0:h, 0:w]
    # warm vignette glow from centre
    cx, cy = w / 2, h / 2
    dist = np.sqrt(((xx - cx) / (w * .6)) ** 2 + ((yy - cy) / (h * .75)) ** 2)
    glow = np.clip(1 - dist, 0, 1)[..., None] ** 1.6
    img = img + glow * np.array([7, 7, 8])
    if era_wash is not None:
        img = img * (1 - era_wash[1]) + era_wash[0] * era_wash[1]
    # glitter tinted by local colour (gold-ish over dark, era hue over era wash)
    cloud = soft_noise(h, w, max(h, w) // 6)[..., None]
    g = glitter(h, w, density=1.0, size=max(1, w / 1500))[..., None] * (0.35 + 1.1 * cloud)
    tint = np.clip(img * 2.4 + np.array([78, 78, 84]), 0, 255)
    img = img + g * (tint - img)
    return img


def gold_foil(mask, h, w):
    """Gold metallic gradient + glitter, masked."""
    yy, xx = np.mgrid[0:h, 0:w]
    t = ((xx / w) * 0.45 + (yy / h) * 0.75)
    wave = (np.sin(t * 9.0) * 0.5 + 0.5)
    stops = np.array(GOLD, float)
    pos = np.clip(wave * 0.75 + t * 0.25, 0, 1) * (len(stops) - 1)
    i = np.clip(pos.astype(int), 0, len(stops) - 2)
    f = (pos - i)[..., None]
    col = stops[i] * (1 - f) + stops[i + 1] * f
    g = glitter(h, w, density=1.6, size=max(1, w / 800))[..., None]
    col = col * (0.93 + 0.12 * g)
    col = np.minimum(col + (g > 0.8) * 60, 255)
    return col, mask[..., None]


def logo_mask(size):
    lg = Image.open(LOGO).convert("RGBA")
    lg = lg.crop(lg.getbbox())
    s = size / max(lg.size)
    lg = lg.resize((round(lg.width * s), round(lg.height * s)), Image.LANCZOS)
    a = lg.getchannel("A").filter(ImageFilter.GaussianBlur(max(0.5, s * 0.25)))
    # re-sharpen the upscaled edges (smoothstep around 50%) so the line art stays crisp
    x = np.clip((np.asarray(a, float) / 255 - 0.38) / 0.24, 0, 1)
    return Image.fromarray((x * x * (3 - 2 * x) * 255).astype(np.uint8))


def paste_logo(img, cx, cy, height):
    h, w, _ = img.shape
    m = logo_mask(height)
    full = Image.new("L", (w, h), 0)
    full.paste(m, (int(cx - m.width / 2), int(cy - m.height / 2)))
    mask = np.asarray(full, float) / 255
    # soft dark shadow + gold halo for legibility
    shadow = blur(mask, height * 0.012)[..., None]
    img = img * (1 - shadow * 0.85)
    halo = blur(mask, height * 0.05)[..., None]
    img = img + halo * np.array([255, 255, 255]) * 0.12
    col, a = gold_foil(mask, h, w)
    return img * (1 - a) + col * a


def era_ring(img, cx, cy, r, width):
    """12 era-coloured glitter arcs (Eras Tour), album order clockwise from top."""
    h, w, _ = img.shape
    yy, xx = np.mgrid[0:h, 0:w]
    ang = (np.degrees(np.arctan2(xx - cx, -(yy - cy))) + 360) % 360
    rad = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    band = np.clip(1 - np.abs(rad - r) / (width / 2), 0, 1)
    band = np.clip((band - 0.04) * 14, 0, 1)
    idx = (ang // 30).astype(int)
    gap = np.minimum(ang % 30, 30 - ang % 30) < 1.6  # thin gaps between eras
    band = band * (~gap)
    cols = np.array([hexrgb(c) for c in ERAS])[idx]
    g = glitter(h, w, density=1.4, size=max(1, w / 800))[..., None]
    cols = np.clip(cols * (0.8 + 0.6 * g) + (g > 0.8) * 90, 0, 255)
    a = blur(band, w / 1600)[..., None]
    return img * (1 - a) + cols * a


def save(img, path, size):
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    im.resize(size, Image.LANCZOS).save(path, quality=95)


# ---------- avatar (hi-res 2400 + X 800) ----------
S = 2400
img = background(S, S, None)
img = era_ring(img, S / 2, S / 2, S * 0.448, S * 0.05)
img = paste_logo(img, S / 2, S / 2, S * 0.70)
save(img, f"{OUT}/x_avatar_2400.png", (S, S))
save(img, f"{OUT}/x_avatar_800.png", (800, 800))

# ---------- header (hi-res 4500x1500 + X 1500x500) ----------
W, H = 4500, 1500
xx = np.mgrid[0:H, 0:W][1]
# 6 era panels each side (debut..rep left, Lover..Showgirl right), dark centre for the logo
SIDE = 0.36  # fraction of width per side
u = xx / W
left, right = u < SIDE, u > 1 - SIDE
pos = np.where(left, u / SIDE * 6, (u - (1 - SIDE)) / SIDE * 6)
panel = np.where(left, pos.astype(int).clip(0, 5), 6 + pos.astype(int).clip(0, 5))
cols = np.array([hexrgb(c) for c in ERAS])[panel]
inner = np.where(left, u / SIDE, 1 - (u - (1 - SIDE)) / SIDE)  # 0 outer edge -> 1 centre side
wash = np.where(left | right, 0.84 * np.clip(1 - (inner - 0.93) / 0.07 * 0.5, 0.5, 1), 0)[..., None]
seam = ((np.minimum(pos % 1, 1 - pos % 1) < 0.03) & (left | right))[..., None]
wash = wash * (~seam)
img = background(H, W, (cols * 0.62, wash))
img = paste_logo(img, W / 2, H / 2, H * 0.74)
save(img, f"{OUT}/x_header_4500x1500.png", (W, H))
save(img, f"{OUT}/x_header_1500x500.png", (1500, 500))
print("ok")
