#!/usr/bin/env python3
"""用已定稿的图拼版和加字。画面上的文字都由这里来写，不交给生图模型。

子命令：
  grid     多张图拼成宫格，例如全家福封面
  lineup   透明图同底、同比例排成一排，例如配色款平铺、三视图
  poster   透明图放在超大英文字标前面，例如设计手稿的首图
  info     用户确认的信息卡：图、品名、已知字段；不默认加提示性声明
  overlay  在图上写名字、副标或编号
所有输出都是 PNG，已存在时不覆盖，除非加 --force。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import imaging  # noqa: E402

DEFAULT_SIZE = "1080x1440"
DEFAULT_NOTE = ""


def subject_of(image: Any) -> Any:
    """透明图裁到主体范围；不透明的图原样返回。"""
    if not imaging.has_alpha(image):
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    box = rgba.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox()
    return rgba.crop(box) if box else rgba


def scaled(image: Any, factor: float) -> Any:
    from PIL import Image

    return image.resize((max(1, round(image.width * factor)), max(1, round(image.height * factor))), Image.LANCZOS)


def shadow(canvas: Any, center_x: float, base_y: float, width: float, strength: int = 70) -> None:
    """在主体脚下画一块柔和的接触阴影。"""
    from PIL import Image, ImageDraw, ImageFilter

    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    half_w = width * 0.42
    half_h = max(4.0, width * 0.06)
    ImageDraw.Draw(layer).ellipse((center_x - half_w, base_y - half_h, center_x + half_w, base_y + half_h), fill=(0, 0, 0, strength))
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(max(3, width * 0.04))))


def place(canvas: Any, image: Any, box: tuple[int, int, int, int], fill: float = 0.82, with_shadow: bool = False) -> None:
    """把图放进 box：透明图按比例放在中间，不透明的图铺满裁切。"""
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    if imaging.has_alpha(image):
        subject = subject_of(image)
        factor = min(width * fill / subject.width, height * fill / subject.height)
        piece = scaled(subject, factor)
        x = left + (width - piece.width) // 2
        y = top + (height - piece.height) // 2
        if with_shadow:
            shadow(canvas, x + piece.width / 2, y + piece.height, piece.width)
        canvas.alpha_composite(piece, (x, y))
    else:
        canvas.alpha_composite(imaging.fit_cover(image, (width, height)).convert("RGBA"), (left, top))


def label(canvas: Any, text: str, center_x: float, top: float, size: int, color: tuple[int, int, int], font: str | None) -> None:
    from PIL import ImageDraw

    draw = ImageDraw.Draw(canvas)
    face = imaging.load_font(size, font)
    box = draw.textbbox((0, 0), text, font=face)
    draw.text((center_x - (box[2] - box[0]) / 2 - box[0], top - box[1]), text, font=face, fill=color + (255,))


def ink_for(background: tuple[int, int, int]) -> tuple[int, int, int]:
    light = (background[0] * 299 + background[1] * 587 + background[2] * 114) / 1000 > 140
    return (40, 38, 36) if light else (250, 250, 248)


def background_of(args: argparse.Namespace, images: list[Any]) -> tuple[int, int, int]:
    if args.bg:
        return imaging.parse_color(args.bg)
    opaque = [image for image in images if not imaging.has_alpha(image)]
    return imaging.border_color(opaque[0]) if opaque else (238, 236, 232)


def load_all(paths: list[str]) -> list[Any]:
    if not paths:
        raise imaging.ImagingError("至少给一张图")
    return [imaging.open_image(Path(path)) for path in paths]


def finish(canvas: Any, args: argparse.Namespace) -> Any:
    if getattr(args, "title", None) or getattr(args, "sub", None):
        spec = {"text": args.title or "", "sub": args.sub or "", "position": args.position, "style": "plain"}
        return imaging.draw_overlay(canvas.convert("RGB"), spec, args.font)
    return canvas.convert("RGB")


def cmd_grid(args: argparse.Namespace) -> Any:
    from PIL import Image

    images = load_all(args.images)
    size = imaging.parse_size(args.size)
    count = len(images)
    cols = args.cols or (1 if count == 1 else 2 if count in (2, 4) else 3)
    rows = math.ceil(count / cols)
    background = background_of(args, images)
    canvas = Image.new("RGBA", size, background + (255,))
    margin, gap = args.margin, args.gap
    top_space = round(size[1] * 0.16) if args.title and args.position == "top" else 0
    bottom_space = round(size[1] * 0.16) if args.title and args.position == "bottom" else 0
    cell_w = (size[0] - margin * 2 - gap * (cols - 1)) // cols
    cell_h = (size[1] - margin * 2 - top_space - bottom_space - gap * (rows - 1)) // rows
    if cell_w < 32 or cell_h < 32:
        raise imaging.ImagingError("格子太小：减少图片数量、--margin 或 --gap")
    names = args.labels or []
    label_h = round(cell_h * 0.12) if names else 0
    for index, image in enumerate(images):
        row, col = divmod(index, cols)
        in_row = min(cols, count - row * cols)
        offset = (cols - in_row) * (cell_w + gap) // 2
        left = margin + offset + col * (cell_w + gap)
        top = margin + top_space + row * (cell_h + gap)
        place(canvas, image, (left, top, left + cell_w, top + cell_h - label_h), with_shadow=args.shadow)
        if index < len(names) and names[index]:
            label(canvas, names[index], left + cell_w / 2, top + cell_h - label_h * 0.95, max(16, round(label_h * 0.55)), ink_for(background), args.font)
    return finish(canvas, args)


def cmd_lineup(args: argparse.Namespace) -> Any:
    from PIL import Image

    images = [subject_of(image) for image in load_all(args.images)]
    if any(not imaging.has_alpha(image) for image in images):
        raise imaging.ImagingError("lineup 需要透明背景的图；先用 remove_bg.py 去底")
    size = imaging.parse_size(args.size)
    background = background_of(args, [])
    if args.bg:
        background = imaging.parse_color(args.bg)
    canvas = Image.new("RGBA", size, background + (255,))
    count = len(images)
    rows = 1 if count <= args.per_row else math.ceil(count / args.per_row)
    per_row = math.ceil(count / rows)
    names = args.labels or []
    margin = round(size[0] * 0.06)
    title_space = round(size[1] * 0.14) if args.title else 0
    usable_h = size[1] - margin * 2 - title_space
    row_h = usable_h / rows
    label_h = row_h * 0.12 if names else 0
    slot_w = (size[0] - margin * 2) / per_row
    tallest = max(image.height for image in images)
    factor = min((row_h - label_h) * 0.86 / tallest, min(slot_w * 0.9 / image.width for image in images))
    for index, image in enumerate(images):
        row, col = divmod(index, per_row)
        in_row = min(per_row, count - row * per_row)
        row_left = margin + (per_row - in_row) * slot_w / 2
        center_x = row_left + slot_w * (col + 0.5)
        base_y = margin + title_space + row_h * (row + 1) - label_h - row_h * 0.04
        piece = scaled(image, factor)
        if args.shadow:
            shadow(canvas, center_x, base_y, piece.width)
        canvas.alpha_composite(piece, (round(center_x - piece.width / 2), round(base_y - piece.height)))
        if index < len(names) and names[index]:
            label(canvas, names[index], center_x, base_y + label_h * 0.25, max(16, round(label_h * 0.5)), ink_for(background), args.font)
    if args.title:
        args.position = "top"
    return finish(canvas, args)


def cmd_poster(args: argparse.Namespace) -> Any:
    from PIL import Image, ImageDraw

    image = subject_of(imaging.open_image(Path(args.subject)))
    if not imaging.has_alpha(image):
        raise imaging.ImagingError("poster 需要透明背景的主体图；先用 remove_bg.py 去底")
    size = imaging.parse_size(args.size)
    background = imaging.parse_color(args.bg)
    canvas = Image.new("RGBA", size, background + (255,))
    word = args.word.strip().upper()
    if not word:
        raise imaging.ImagingError("--word 不能为空")
    draw = ImageDraw.Draw(canvas)
    font_size = round(size[1] * 0.3)
    face = imaging.load_display_font(font_size, args.word_font)
    box = draw.textbbox((0, 0), word, font=face)
    target_w = size[0] * 0.92
    face = imaging.load_display_font(max(24, round(font_size * target_w / max(box[2] - box[0], 1))), args.word_font)
    box = draw.textbbox((0, 0), word, font=face)
    word_color = imaging.parse_color(args.word_color) if args.word_color else tuple(min(255, value + 14) for value in background)
    word_top = size[1] * 0.2
    draw.text(((size[0] - (box[2] - box[0])) / 2 - box[0], word_top - box[1]), word, font=face, fill=tuple(word_color) + (255,))
    factor = min(size[1] * args.subject_height / image.height, size[0] * 0.86 / image.width)
    piece = scaled(image, factor)
    base_y = size[1] * 0.86
    shadow(canvas, size[0] / 2, base_y, piece.width, strength=80)
    canvas.alpha_composite(piece, (round((size[0] - piece.width) / 2), round(base_y - piece.height)))
    ink = ink_for(background)
    if args.caption:
        label(canvas, args.caption, size[0] / 2, size[1] * 0.91, max(18, round(size[0] * 0.026)), ink, args.font)
    if args.corner:
        small = imaging.load_font(max(16, round(size[0] * 0.022)), args.font)
        ImageDraw.Draw(canvas).text((size[0] * 0.06, size[1] * 0.045), args.corner, font=small, fill=ink + (255,))
    return canvas.convert("RGB")


def cmd_info(args: argparse.Namespace) -> Any:
    from PIL import Image, ImageDraw

    image = imaging.open_image(Path(args.image))
    size = imaging.parse_size(args.size)
    background = imaging.parse_color(args.bg)
    canvas = Image.new("RGBA", size, background + (255,))
    ink = ink_for(background)
    image_box = (round(size[0] * 0.08), round(size[1] * 0.05), round(size[0] * 0.92), round(size[1] * 0.58))
    place(canvas, image, image_box, fill=0.9, with_shadow=imaging.has_alpha(image))
    draw = ImageDraw.Draw(canvas)
    left = size[0] * 0.1
    y = size[1] * 0.62
    title_font = imaging.load_font(max(24, round(size[0] * 0.05)), args.font)
    draw.text((left, y), args.title, font=title_font, fill=ink + (255,))
    y += size[0] * 0.085
    draw.line((left, y, size[0] * 0.9, y), fill=ink + (90,), width=max(1, round(size[0] * 0.002)))
    y += size[0] * 0.03
    field_font = imaging.load_font(max(18, round(size[0] * 0.032)), args.font)
    for field in args.field or []:
        name, _, value = field.partition("=")
        if not value:
            raise imaging.ImagingError(f"--field 要写成 名称=内容：{field}")
        draw.text((left, y), name.strip(), font=field_font, fill=ink + (150,))
        draw.text((left + size[0] * 0.2, y), value.strip(), font=field_font, fill=ink + (255,))
        y += size[0] * 0.058
        if y > size[1] * 0.9:
            raise imaging.ImagingError("字段太多，放不下；减少 --field")
    if args.note:
        note_font = imaging.load_font(max(14, round(size[0] * 0.022)), args.font)
        draw.text((left, size[1] * 0.94), args.note, font=note_font, fill=ink + (140,))
    return canvas.convert("RGB")


def cmd_overlay(args: argparse.Namespace) -> Any:
    image = imaging.open_image(Path(args.input))
    spec = {"text": args.text or "", "sub": args.sub or "", "position": args.position, "style": args.style}
    if not spec["text"] and not spec["sub"]:
        raise imaging.ImagingError("--text 和 --sub 至少写一个")
    return imaging.draw_overlay(imaging.flatten(image), spec, args.font)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="拼版和加字")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser, sized: bool = True) -> None:
        command.add_argument("--out", required=True, help="输出 PNG 路径")
        command.add_argument("--font", help="中文字体文件")
        command.add_argument("--force", action="store_true", help="输出已存在时覆盖")
        command.add_argument("--image-text", choices=("none", "user-approved"), default="none", help="默认不写图字；用户明确要时用 user-approved")
        if sized:
            command.add_argument("--size", default=DEFAULT_SIZE, help="画布尺寸，默认 1080x1440")

    grid = sub.add_parser("grid", help="多张图拼成宫格")
    grid.add_argument("--images", nargs="+", required=True)
    grid.add_argument("--cols", type=int)
    grid.add_argument("--labels", nargs="+", help="每格下方的名字，顺序和图片一致")
    grid.add_argument("--bg", help="底色 #RRGGBB")
    grid.add_argument("--gap", type=int, default=12)
    grid.add_argument("--margin", type=int, default=0)
    grid.add_argument("--shadow", action="store_true", help="透明图下加接触阴影")
    grid.add_argument("--title")
    grid.add_argument("--sub")
    grid.add_argument("--position", choices=("top", "bottom"), default="top")
    common(grid)
    grid.set_defaults(func=cmd_grid)

    lineup = sub.add_parser("lineup", help="透明图同底同比例排成一排")
    lineup.add_argument("--images", nargs="+", required=True)
    lineup.add_argument("--labels", nargs="+")
    lineup.add_argument("--bg", default="#EEEDEA")
    lineup.add_argument("--per-row", type=int, default=4)
    lineup.add_argument("--no-shadow", dest="shadow", action="store_false")
    lineup.add_argument("--title")
    lineup.add_argument("--sub")
    lineup.set_defaults(position="top")
    common(lineup)
    lineup.set_defaults(func=cmd_lineup)

    poster = sub.add_parser("poster", help="主体放在超大英文字标前面")
    poster.add_argument("--subject", required=True, help="透明背景的主体图")
    poster.add_argument("--word", required=True, help="背后的英文字标，通常是 IP 的英文名")
    poster.add_argument("--word-color", help="字标颜色，默认比底色略浅")
    poster.add_argument("--word-font", help="字标字体文件")
    poster.add_argument("--bg", default="#E6E6E3")
    poster.add_argument("--subject-height", type=float, default=0.66, help="主体占画面高度的比例")
    poster.add_argument("--caption", help="底部小字，例如 DESIGN 2026 · 署名")
    poster.add_argument("--corner", help="左上角小字")
    common(poster)
    poster.set_defaults(func=cmd_poster)

    info = sub.add_parser("info", help="产品信息卡")
    info.add_argument("--image", required=True)
    info.add_argument("--title", required=True, help="品名")
    info.add_argument("--field", action="append", help="名称=内容，可重复，例如 材质=短毛绒")
    info.add_argument("--note", default=DEFAULT_NOTE, help="仅用户明确要写进图的附注，默认无；真实性声明放文案和HTML")
    info.add_argument("--bg", default="#F3F1EC")
    common(info)
    info.set_defaults(func=cmd_info)

    overlay = sub.add_parser("overlay", help="在图上写名字、副标或编号")
    overlay.add_argument("input")
    overlay.add_argument("--text")
    overlay.add_argument("--sub")
    overlay.add_argument("--position", choices=("top", "bottom"), default="bottom")
    overlay.add_argument("--style", choices=("plain", "band"), default="plain")
    common(overlay, sized=False)
    overlay.set_defaults(func=cmd_overlay)
    return parser


def main(argv: list[str] | None = None) -> int:
    if not imaging.python_ok():
        return imaging.fail("需要 Python 3.10 或更高版本")
    args = build_parser().parse_args(argv)
    out = Path(args.out)
    try:
        text_fields = ("title", "sub", "word", "caption", "corner", "note", "text", "labels", "field")
        if args.image_text != "user-approved" and any(getattr(args, name, None) for name in text_fields):
            raise imaging.ImagingError("成图默认不加文字；只有用户明确要图字时加 --image-text user-approved，声明放文案和HTML")
        if out.suffix.lower() != ".png":
            raise imaging.ImagingError("--out 必须是 .png")
        if out.exists() and not args.force:
            raise imaging.ImagingError(f"{out.as_posix()} 已存在；确认要覆盖时加 --force")
        result = args.func(args)
        imaging.save_png(result, out, force=args.force)
    except imaging.ImagingError as error:
        return imaging.fail(str(error))
    print(json.dumps({"ok": True, "command": args.command, "output": out.as_posix(), "size": list(result.size)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    from runtime_activity import execution_lease
    with execution_lease():
        sys.exit(main())
