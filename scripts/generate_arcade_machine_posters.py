from __future__ import annotations

from collections import deque
import io
from pathlib import Path
import subprocess
from typing import Iterable

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageOps


REPO_ROOT = Path(__file__).resolve().parents[1]
IMG_DIR = REPO_ROOT / "maquinas-medellin-frontend" / "static" / "img"
OUTPUT_SIZE = (1400, 1050)


PALETTES = {
    "basketball.jpg": {
        "bg_top": "#08152e",
        "bg_bottom": "#112f5c",
        "glow_a": "#ff7b2f",
        "glow_b": "#56c8ff",
        "spark": "#ffd166",
    },
    "basketball 2.jpg": {
        "bg_top": "#101d3c",
        "bg_bottom": "#274d84",
        "glow_a": "#ff9f45",
        "glow_b": "#5de0ff",
        "spark": "#fff1a8",
    },
    "simulador1.jpg": {
        "bg_top": "#100713",
        "bg_bottom": "#451629",
        "glow_a": "#ff4d4d",
        "glow_b": "#31d7ff",
        "spark": "#ffd6a5",
    },
    "simulador2.jpg": {
        "bg_top": "#0f0915",
        "bg_bottom": "#39205f",
        "glow_a": "#ff4d92",
        "glow_b": "#55d6ff",
        "spark": "#fff3b0",
    },
    "simulador pk.jpg": {
        "bg_top": "#130811",
        "bg_bottom": "#4e1745",
        "glow_a": "#ff4aa2",
        "glow_b": "#7df9ff",
        "spark": "#ffd9a0",
    },
    "peluches1.jpg": {
        "bg_top": "#19081f",
        "bg_bottom": "#5a1f58",
        "glow_a": "#ff65c3",
        "glow_b": "#ffd166",
        "spark": "#fff3b0",
    },
    "peluches2.jpg": {
        "bg_top": "#13081f",
        "bg_bottom": "#4b1d66",
        "glow_a": "#ff71ce",
        "glow_b": "#ffd166",
        "spark": "#fff1a8",
    },
    "caballo.jpg": {
        "bg_top": "#22110b",
        "bg_bottom": "#7d2915",
        "glow_a": "#ff7f51",
        "glow_b": "#ffd166",
        "spark": "#fff4c2",
    },
    "tren.jpg": {
        "bg_top": "#0b1a1f",
        "bg_bottom": "#1c5f6c",
        "glow_a": "#ff8a3d",
        "glow_b": "#62f0ff",
        "spark": "#fff1c1",
    },
    "mcqueen.jpg": {
        "bg_top": "#180607",
        "bg_bottom": "#6d0f14",
        "glow_a": "#ff4747",
        "glow_b": "#ffc857",
        "spark": "#ffe7aa",
    },
    "pelea.jpg": {
        "bg_top": "#0c130d",
        "bg_bottom": "#1f5f37",
        "glow_a": "#ff6b3d",
        "glow_b": "#9ef01a",
        "spark": "#fefae0",
    },
    "disco hockey.jpg": {
        "bg_top": "#07141d",
        "bg_bottom": "#114b74",
        "glow_a": "#49d6ff",
        "glow_b": "#7dffb3",
        "spark": "#f1faee",
    },
    "disco air hockey.jpg": {
        "bg_top": "#071523",
        "bg_bottom": "#0d4b68",
        "glow_a": "#3ddcff",
        "glow_b": "#7ae582",
        "spark": "#f1faee",
    },
    "sillas de masajes.jpg": {
        "bg_top": "#090d17",
        "bg_bottom": "#263b65",
        "glow_a": "#77a1ff",
        "glow_b": "#ae67fa",
        "spark": "#f8edeb",
    },
    "default.jpg": {
        "bg_top": "#08152e",
        "bg_bottom": "#1c4678",
        "glow_a": "#58d5ff",
        "glow_b": "#ff7b7b",
        "spark": "#fff1a8",
    },
}


SCALE_OVERRIDES = {
    "basketball.jpg": 0.84,
    "basketball 2.jpg": 0.84,
    "caballo.jpg": 0.9,
    "tren.jpg": 0.92,
    "disco hockey.jpg": 0.92,
    "disco air hockey.jpg": 0.92,
    "peluches1.jpg": 0.9,
    "peluches2.jpg": 0.9,
}


def rgb(hex_color: str) -> tuple[int, int, int]:
    return ImageColor.getrgb(hex_color)


def blend(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def color_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return sum(abs(a[i] - b[i]) for i in range(3))


def is_background_candidate(pixel: tuple[int, int, int]) -> bool:
    brightness = sum(pixel) / 3
    spread = max(pixel) - min(pixel)
    return brightness > 214 and spread < 64


def border_connected_background_mask(image: Image.Image) -> Image.Image:
    rgb_img = image.convert("RGB")
    width, height = rgb_img.size
    px = rgb_img.load()
    mask = Image.new("L", (width, height), 255)
    mask_px = mask.load()
    visited = set()
    queue: deque[tuple[int, int]] = deque()

    def enqueue(x: int, y: int) -> None:
        if (x, y) in visited:
            return
        pixel = px[x, y]
        if is_background_candidate(pixel):
            visited.add((x, y))
            queue.append((x, y))

    for x in range(width):
        enqueue(x, 0)
        enqueue(x, height - 1)
    for y in range(height):
        enqueue(0, y)
        enqueue(width - 1, y)

    while queue:
        x, y = queue.popleft()
        current = px[x, y]
        mask_px[x, y] = 0
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            if (nx, ny) in visited:
                continue
            neighbor = px[nx, ny]
            if is_background_candidate(neighbor) and color_distance(current, neighbor) < 72:
                visited.add((nx, ny))
                queue.append((nx, ny))

    return mask.filter(ImageFilter.GaussianBlur(1.4))


def alpha_cutout(image: Image.Image) -> Image.Image:
    image = image.convert("RGBA")
    cutout = image.copy()
    mask = border_connected_background_mask(image)
    cutout.putalpha(mask)
    bbox = cutout.getbbox()
    if not bbox:
        return cutout

    cropped = cutout.crop(bbox)
    alpha = cropped.getchannel("A")
    alpha = ImageEnhance.Contrast(alpha).enhance(1.25).filter(ImageFilter.GaussianBlur(0.8))
    cropped.putalpha(alpha)
    return cropped


def make_vertical_gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size)
    draw = ImageDraw.Draw(layer)
    for y in range(height):
        t = y / max(height - 1, 1)
        draw.line((0, y, width, y), fill=blend(top, bottom, t) + (255,))
    return layer


def glow_ellipse(size: tuple[int, int], box: tuple[int, int, int, int], color: tuple[int, int, int], blur: int, alpha: int) -> Image.Image:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.ellipse(box, fill=color + (alpha,))
    return layer.filter(ImageFilter.GaussianBlur(blur))


def build_background(size: tuple[int, int], palette: dict[str, str]) -> Image.Image:
    width, height = size
    bg = make_vertical_gradient(size, rgb(palette["bg_top"]), rgb(palette["bg_bottom"]))
    draw = ImageDraw.Draw(bg)

    # Radial glows
    for box, key, blur, alpha in (
        ((-120, -80, 620, 520), "glow_a", 90, 120),
        ((width - 520, 40, width + 120, 620), "glow_b", 110, 115),
        ((260, height - 330, width - 260, height + 160), "glow_b", 130, 90),
    ):
        bg = Image.alpha_composite(bg, glow_ellipse(size, box, rgb(palette[key]), blur, alpha))

    # Perspective floor.
    floor = Image.new("RGBA", size, (0, 0, 0, 0))
    floor_draw = ImageDraw.Draw(floor)
    horizon_y = int(height * 0.69)
    for i in range(16):
        y = horizon_y + i * 24
        alpha = max(18 - i, 4) * 5
        floor_draw.line((0, y, width, y), fill=(255, 255, 255, alpha), width=1)
    center_x = width // 2
    for offset in range(-10, 11):
        x = center_x + offset * 74
        floor_draw.line((center_x, horizon_y, x, height), fill=(255, 255, 255, 26), width=1)
    bg = Image.alpha_composite(bg, floor)

    # Accent streaks and stars.
    accent = Image.new("RGBA", size, (0, 0, 0, 0))
    accent_draw = ImageDraw.Draw(accent)
    for idx in range(7):
        x1 = 140 + idx * 160
        accent_draw.line((x1, 90, x1 + 170, 10), fill=rgb(palette["spark"]) + (42,), width=3)
    for idx in range(40):
        x = 40 + (idx * 97) % width
        y = 20 + (idx * 149) % int(height * 0.55)
        radius = 1 + (idx % 3)
        accent_draw.ellipse((x, y, x + radius, y + radius), fill=rgb(palette["spark"]) + (110,))
    bg = Image.alpha_composite(bg, accent.filter(ImageFilter.GaussianBlur(0.4)))

    # Frame and vignette.
    frame = Image.new("RGBA", size, (0, 0, 0, 0))
    frame_draw = ImageDraw.Draw(frame)
    margin = 34
    frame_draw.rounded_rectangle(
        (margin, margin, width - margin, height - margin),
        radius=28,
        outline=(255, 255, 255, 38),
        width=2,
    )
    vignette = Image.new("L", size, 0)
    vignette_draw = ImageDraw.Draw(vignette)
    vignette_draw.ellipse((-180, -120, width + 180, height + 220), fill=220)
    vignette = ImageChops.invert(vignette).filter(ImageFilter.GaussianBlur(80))
    bg.putalpha(255)
    bg = Image.alpha_composite(bg, frame)
    bg = Image.composite(bg, Image.new("RGBA", size, (3, 8, 18, 255)), vignette)
    return bg


def add_shadow(canvas: Image.Image, alpha: Image.Image, anchor: tuple[int, int], palette: dict[str, str]) -> Image.Image:
    width, height = canvas.size
    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow_alpha = alpha.resize(
        (max(int(alpha.width * 1.08), 1), max(int(alpha.height * 0.18), 1)),
        Image.Resampling.LANCZOS,
    ).filter(ImageFilter.GaussianBlur(16))
    shadow_img = Image.new("RGBA", shadow_alpha.size, rgb(palette["glow_a"]) + (0,))
    shadow_img.putalpha(ImageEnhance.Brightness(shadow_alpha).enhance(0.72))
    shadow.paste(shadow_img, (anchor[0] - shadow_alpha.width // 2, anchor[1] - shadow_alpha.height // 2), shadow_img)
    return Image.alpha_composite(canvas, shadow)


def add_glow(canvas: Image.Image, alpha: Image.Image, position: tuple[int, int], palette: dict[str, str]) -> Image.Image:
    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    for color_key, expand, blur, opacity in (
        ("glow_a", 46, 24, 110),
        ("glow_b", 20, 14, 90),
    ):
        layer = Image.new("RGBA", alpha.size, rgb(palette[color_key]) + (0,))
        expanded = alpha.filter(ImageFilter.MaxFilter(expand if expand % 2 == 1 else expand + 1))
        expanded = expanded.filter(ImageFilter.GaussianBlur(blur))
        layer.putalpha(ImageEnhance.Brightness(expanded).enhance(opacity / 255))
        glow.paste(layer, position, layer)
    return Image.alpha_composite(canvas, glow)


def add_reflection(canvas: Image.Image, image: Image.Image, position: tuple[int, int]) -> Image.Image:
    reflection = ImageOps.flip(image).resize(
        (max(int(image.width * 0.9), 1), max(int(image.height * 0.3), 1)),
        Image.Resampling.LANCZOS,
    )
    alpha = reflection.getchannel("A")
    fade = Image.linear_gradient("L").rotate(90, expand=True).resize(reflection.size)
    alpha = ImageChops.multiply(alpha, ImageEnhance.Brightness(fade).enhance(0.65))
    reflection.putalpha(alpha.filter(ImageFilter.GaussianBlur(2.2)))
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    layer.paste(reflection, (position[0] + 16, position[1] + image.height - 8), reflection)
    return Image.alpha_composite(canvas, layer)


def prepare_subject(image: Image.Image, filename: str) -> Image.Image:
    cutout = alpha_cutout(image)
    cutout = ImageEnhance.Color(cutout).enhance(1.08)
    cutout = ImageEnhance.Contrast(cutout).enhance(1.05)
    cutout = ImageEnhance.Sharpness(cutout).enhance(1.2)

    max_width = int(OUTPUT_SIZE[0] * SCALE_OVERRIDES.get(filename, 0.76))
    max_height = int(OUTPUT_SIZE[1] * 0.62)
    ratio = min(max_width / cutout.width, max_height / cutout.height)
    target = (
        max(int(cutout.width * ratio), 1),
        max(int(cutout.height * ratio), 1),
    )
    return cutout.resize(target, Image.Resampling.LANCZOS)


def load_source_image(asset: Path) -> Image.Image:
    rel_path = asset.relative_to(REPO_ROOT).as_posix()
    try:
        blob = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "show", f"HEAD:{rel_path}"],
            stderr=subprocess.DEVNULL,
        )
        with Image.open(io.BytesIO(blob)) as original:
            return original.copy()
    except Exception:
        with Image.open(asset) as original:
            return original.copy()


def compose_poster(source: Path, target: Path, palette: dict[str, str]) -> None:
    original = load_source_image(source)
    subject = prepare_subject(original, source.name)

    canvas = build_background(OUTPUT_SIZE, palette)
    pos_x = (OUTPUT_SIZE[0] - subject.width) // 2
    pos_y = int(OUTPUT_SIZE[1] * 0.18 + max(0, (OUTPUT_SIZE[1] * 0.46 - subject.height) / 2))
    position = (pos_x, pos_y)
    alpha = subject.getchannel("A")

    canvas = add_shadow(canvas, alpha, (OUTPUT_SIZE[0] // 2, int(OUTPUT_SIZE[1] * 0.8)), palette)
    canvas = add_glow(canvas, alpha, position, palette)
    canvas.paste(subject, position, subject)
    canvas = add_reflection(canvas, subject, position)

    # Final polish.
    final_image = ImageEnhance.Contrast(canvas.convert("RGB")).enhance(1.04)
    final_image = ImageEnhance.Color(final_image).enhance(1.08)
    final_image.save(target, quality=93)


def ensure_default_asset() -> None:
    palette = PALETTES["default.jpg"]
    bg = build_background(OUTPUT_SIZE, palette)
    badge = Image.new("RGBA", OUTPUT_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    box = (OUTPUT_SIZE[0] // 2 - 170, OUTPUT_SIZE[1] // 2 - 120, OUTPUT_SIZE[0] // 2 + 170, OUTPUT_SIZE[1] // 2 + 120)
    draw.rounded_rectangle(box, radius=34, outline=(255, 255, 255, 70), width=4, fill=(8, 20, 40, 110))
    draw.ellipse((OUTPUT_SIZE[0] // 2 - 36, OUTPUT_SIZE[1] // 2 - 26, OUTPUT_SIZE[0] // 2 + 36, OUTPUT_SIZE[1] // 2 + 46), fill=rgb(palette["glow_b"]) + (230,))
    draw.rectangle((OUTPUT_SIZE[0] // 2 - 58, OUTPUT_SIZE[1] // 2 - 76, OUTPUT_SIZE[0] // 2 + 58, OUTPUT_SIZE[1] // 2 + 6), fill=rgb(palette["glow_a"]) + (240,))
    draw.rectangle((OUTPUT_SIZE[0] // 2 - 16, OUTPUT_SIZE[1] // 2 + 6, OUTPUT_SIZE[0] // 2 + 16, OUTPUT_SIZE[1] // 2 + 70), fill=(255, 255, 255, 210))
    result = Image.alpha_composite(bg, badge)
    result.convert("RGB").save(IMG_DIR / "default.jpg", quality=92)


def asset_files() -> Iterable[Path]:
    return sorted(
        path for path in IMG_DIR.glob("*.jpg")
        if path.name.lower() not in {"logo.jpg", "default.jpg"}
    )


def main() -> None:
    for asset in asset_files():
        palette = PALETTES.get(asset.name, PALETTES["default.jpg"])
        compose_poster(asset, asset, palette)
        print(f"updated {asset.name}")
    ensure_default_asset()
    print("updated default.jpg")


if __name__ == "__main__":
    main()
