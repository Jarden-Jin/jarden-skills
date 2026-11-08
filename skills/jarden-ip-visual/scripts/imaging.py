"""图片脚本共用的 Pillow 工具：读取、取背景色、缩放、字体和画面文字。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
except ImportError:  # pragma: no cover - 取决于运行环境
    Image = None  # type: ignore[assignment]

FONT_ENV = "JARDEN_IP_VISUAL_FONT"
FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
)
DISPLAY_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "C:/Windows/Fonts/ariblk.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
)


class ImagingError(Exception):
    """可以直接告诉 Agent 的图片处理错误。"""


def require_pillow() -> None:
    if Image is None:
        raise ImagingError("需要 Pillow。征得用户同意后运行：<python> -m pip install Pillow")


def open_image(path: Path) -> "Image.Image":
    require_pillow()
    try:
        image = Image.open(path)
        image.load()
    except FileNotFoundError as error:
        raise ImagingError(f"没有找到图片：{path.as_posix()}") from error
    except OSError as error:
        raise ImagingError(f"无法读取图片 {path.as_posix()}：{error}") from error
    return ImageOps.exif_transpose(image)


def parse_color(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        raise ImagingError(f"颜色要写成 #RRGGBB：{value}")
    try:
        return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
    except ValueError as error:
        raise ImagingError(f"颜色要写成 #RRGGBB：{value}") from error


def parse_size(value: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in value.lower().split("x"))
    except ValueError as error:
        raise ImagingError(f"尺寸要写成 宽x高，例如 1080x1440：{value}") from error
    if width < 16 or height < 16:
        raise ImagingError("尺寸太小")
    return width, height


def border_color(image: "Image.Image", strip: int = 4) -> tuple[int, int, int]:
    """取四条边的中位色，作为背景色估计。"""
    rgb = flatten(image).convert("RGB")
    small = rgb.copy()
    small.thumbnail((256, 256))
    width, height = small.size
    pixels = small.load()
    samples = []
    for x in range(width):
        for y in list(range(min(strip, height))) + list(range(max(height - strip, 0), height)):
            samples.append(pixels[x, y])
    for y in range(height):
        for x in list(range(min(strip, width))) + list(range(max(width - strip, 0), width)):
            samples.append(pixels[x, y])
    channels = list(zip(*samples))
    return tuple(sorted(channel)[len(channel) // 2] for channel in channels)  # type: ignore[return-value]


def flatten(image: "Image.Image", background: tuple[int, int, int] = (255, 255, 255)) -> "Image.Image":
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        base = Image.new("RGBA", rgba.size, background + (255,))
        base.alpha_composite(rgba)
        return base.convert("RGB")
    return image.convert("RGB")


def has_alpha(image: "Image.Image") -> bool:
    return image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)


def fit_contain(image: "Image.Image", size: tuple[int, int], background: tuple[int, int, int]) -> "Image.Image":
    rgba = image.convert("RGBA")
    scale = min(size[0] / rgba.width, size[1] / rgba.height)
    resized = rgba.resize((max(1, round(rgba.width * scale)), max(1, round(rgba.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", size, background + (255,))
    canvas.alpha_composite(resized, ((size[0] - resized.width) // 2, (size[1] - resized.height) // 2))
    return canvas.convert("RGB")


def fit_cover(image: "Image.Image", size: tuple[int, int], anchor_y: float = 0.5) -> "Image.Image":
    rgb = flatten(image)
    scale = max(size[0] / rgb.width, size[1] / rgb.height)
    resized = rgb.resize((max(size[0], round(rgb.width * scale)), max(size[1], round(rgb.height * scale))), Image.LANCZOS)
    left = (resized.width - size[0]) // 2
    top = round((resized.height - size[1]) * min(max(anchor_y, 0.0), 1.0))
    return resized.crop((left, top, left + size[0], top + size[1]))


def fit(image: "Image.Image", size: tuple[int, int], mode: str = "auto", tolerance: float = 0.2) -> tuple["Image.Image", str]:
    """auto：比例接近时居中裁切，差得多时用边缘色补边。返回图片和实际方式。"""
    if mode not in ("auto", "cover", "contain"):
        raise ImagingError("fit 只能是 auto、cover、contain")
    target = size[0] / size[1]
    ratio = image.width / image.height
    if mode == "auto":
        mode = "cover" if abs(ratio - target) / target <= tolerance else "contain"
    if mode == "cover":
        return fit_cover(image, size), mode
    return fit_contain(image, size, border_color(image)), mode


def find_font(explicit: str | None = None) -> str | None:
    for candidate in (explicit, os.environ.get(FONT_ENV), *FONT_CANDIDATES):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def load_font(size: int, explicit: str | None = None) -> "ImageFont.FreeTypeFont":
    require_pillow()
    path = find_font(explicit)
    if not path:
        raise ImagingError(f"没有找到中文字体；用 --font 指定字体文件，或设置环境变量 {FONT_ENV}")
    return ImageFont.truetype(path, size)


def load_display_font(size: int, explicit: str | None = None) -> "ImageFont.FreeTypeFont":
    """大号英文字标用的粗体字体；找不到时退回中文字体。"""
    require_pillow()
    for candidate in (explicit, *DISPLAY_FONT_CANDIDATES):
        if candidate and Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return load_font(size)


def luminance(image: "Image.Image", box: tuple[int, int, int, int]) -> float:
    region = image.convert("L").crop(box)
    region.thumbnail((64, 64))
    data = list(region.getdata())
    return sum(data) / max(len(data), 1)


def draw_overlay(image: "Image.Image", spec: dict[str, Any], font_path: str | None = None) -> "Image.Image":
    """在图上居中写一行名字和一行小字，自动选深色或白色。"""
    text = str(spec.get("text") or "").strip()
    sub = str(spec.get("sub") or "").strip()
    if not text and not sub:
        return image
    canvas = image.convert("RGBA")
    width, height = canvas.size
    main_font = load_font(max(24, round(width * 0.058)), font_path)
    sub_font = load_font(max(18, round(width * 0.032)), font_path)
    draw = ImageDraw.Draw(canvas)
    lines = []
    if text:
        lines.append((text, main_font))
    if sub:
        lines.append((sub, sub_font))
    sizes = [draw.textbbox((0, 0), line, font=font) for line, font in lines]
    gap = round(width * 0.012)
    block = sum(box[3] - box[1] for box in sizes) + gap * (len(lines) - 1)
    margin = round(height * 0.05)
    top = margin if spec.get("position") == "top" else height - margin - block
    region = (0, max(top - gap, 0), width, min(top + block + gap, height))
    light = luminance(canvas, region) > 140
    color = (40, 38, 36, 255) if light else (255, 255, 255, 255)
    if spec.get("style") == "band":
        band = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(band).rectangle((0, top - gap * 2, width, top + block + gap * 2), fill=(255, 255, 255, 170) if light else (0, 0, 0, 110))
        canvas.alpha_composite(band)
    y = top
    for (line, font), box in zip(lines, sizes):
        line_width = box[2] - box[0]
        draw.text(((width - line_width) / 2 - box[0], y - box[1]), line, font=font, fill=color)
        y += box[3] - box[1] + gap
    return canvas.convert("RGB")


def save_png(image: "Image.Image", path: Path, force: bool = False) -> None:
    if path.exists() and not force:
        raise ImagingError(f"{path.as_posix()} 已存在；确认要覆盖时加 --force")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".tmp-{path.name}")
    image.save(temp, format="PNG")
    os.replace(temp, path)


def fail(message: str) -> int:
    import json

    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False, indent=2))
    return 2


def python_ok() -> bool:
    return sys.version_info >= (3, 10)
