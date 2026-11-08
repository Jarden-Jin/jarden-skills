#!/usr/bin/env python3
"""把纯色背景的图去成透明背景的 PNG。

从画面四边开始，找和背景色相近并且连通的区域，把它变成透明；主体内部和背景同色、
但不和四边连通的部分默认保留。边缘按颜色距离做半透明过渡，并去掉背景色的溢色。
保存后按 check_images.py --alpha 的标准自检：通过时退出码为 0，没通过为 1。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_images  # noqa: E402
import imaging  # noqa: E402

WORK_SIDE = 640


def distance_map(rgb: Any, color: tuple[int, int, int]) -> Any:
    """每个像素和背景色在 R、G、B 上的最大差值。"""
    from PIL import Image, ImageChops

    solid = Image.new("RGB", rgb.size, color)
    red, green, blue = ImageChops.difference(rgb, solid).split()
    return ImageChops.lighter(ImageChops.lighter(red, green), blue)


def border_values(gray: Any, strip: int = 3) -> list[int]:
    width, height = gray.size
    pixels = gray.load()
    values = []
    for x in range(width):
        for y in range(min(strip, height)):
            values += [pixels[x, y], pixels[x, height - 1 - y]]
    for y in range(height):
        for x in range(min(strip, width)):
            values += [pixels[x, y], pixels[width - 1 - x, y]]
    return values


def flood(data: bytearray, width: int, height: int, seeds: list[int], seen: bytearray) -> list[int]:
    """从 seeds 出发，标记 data 里非零且四邻连通的像素，返回这次标记的像素。"""
    found = []
    queue = deque()
    for index in seeds:
        if data[index] and not seen[index]:
            seen[index] = 1
            queue.append(index)
    last_row = (height - 1) * width
    while queue:
        index = queue.popleft()
        found.append(index)
        x = index % width
        for neighbor, inside in ((index - 1, x > 0), (index + 1, x < width - 1), (index - width, index >= width), (index + width, index < last_row)):
            if inside and data[neighbor] and not seen[neighbor]:
                seen[neighbor] = 1
                queue.append(neighbor)
    return found


def background_region(mask: Any, holes: bool, min_hole: float) -> Any:
    """mask 里 255 表示像背景。返回和四边连通的背景；holes 为真时加上足够大的封闭空隙。"""
    from PIL import Image

    width, height = mask.size
    data = bytearray(mask.tobytes())
    seen = bytearray(width * height)
    seeds = [x for x in range(width)] + [(height - 1) * width + x for x in range(width)]
    seeds += [y * width for y in range(height)] + [y * width + width - 1 for y in range(height)]
    flood(data, width, height, seeds, seen)
    if holes:
        limit = max(4, round(width * height * min_hole))
        trial = bytearray(seen)
        for index in range(width * height):
            if data[index] and not trial[index]:
                found = flood(data, width, height, [index], trial)
                if len(found) >= limit:
                    for pixel in found:
                        seen[pixel] = 1
    return Image.frombytes("L", (width, height), bytes(seen)).point(lambda value: 255 if value else 0)


def decontaminate(rgba: Any, color: tuple[int, int, int]) -> None:
    """半透明边缘按 alpha 反推主体颜色，去掉背景色的溢色。"""
    alpha = rgba.getchannel("A").tobytes()
    rgb = bytearray(rgba.convert("RGB").tobytes())
    for index, value in enumerate(alpha):
        if 0 < value < 255:
            base = index * 3
            for channel in range(3):
                mixed = rgb[base + channel]
                restored = color[channel] + (mixed - color[channel]) * 255 / value
                rgb[base + channel] = max(0, min(255, round(restored)))
    from PIL import Image

    clean = Image.frombytes("RGB", rgba.size, bytes(rgb)).convert("RGBA")
    clean.putalpha(rgba.getchannel("A"))
    rgba.paste(clean)


def remove_background(image: Any, args: argparse.Namespace) -> tuple[Any, dict[str, Any]]:
    from PIL import Image, ImageChops, ImageFilter

    rgb = imaging.flatten(image)
    color = imaging.parse_color(args.color) if args.color else imaging.border_color(rgb)
    dist = distance_map(rgb, color)
    border = sorted(border_values(dist))
    tolerance = args.tolerance if args.tolerance is not None else max(16, min(64, border[int(len(border) * 0.75)] + 14))
    soft = args.soft if args.soft is not None else max(10, tolerance // 2)
    off_color = sum(1 for value in border if value > tolerance) / max(len(border), 1)
    hard = dist.point(lambda value: 255 if value <= tolerance else 0)
    scale = min(1.0, WORK_SIDE / max(rgb.size))
    small_size = (max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale)))
    small = hard.resize(small_size, Image.BOX).point(lambda value: 255 if value >= 128 else 0)
    region = background_region(small, args.holes, args.min_hole).resize(rgb.size, Image.NEAREST)
    kernel = 2 * max(1, round(1 / scale)) + 1
    zone = region.filter(ImageFilter.MaxFilter(kernel))
    core = region.filter(ImageFilter.MinFilter(kernel))
    ramp = dist.point(lambda value: 0 if value <= tolerance else 255 if value >= tolerance + soft else round((value - tolerance) * 255 / soft))
    alpha = ImageChops.lighter(ramp, ImageChops.invert(zone))
    alpha = ImageChops.darker(alpha, ImageChops.invert(core))
    if args.erode:
        alpha = alpha.filter(ImageFilter.MinFilter(args.erode * 2 + 1))
    if args.feather > 0:
        alpha = alpha.filter(ImageFilter.GaussianBlur(args.feather))
    result = rgb.convert("RGBA")
    result.putalpha(alpha)
    decontaminate(result, color)
    info = {
        "background": "#%02X%02X%02X" % color,
        "tolerance": tolerance,
        "soft": soft,
        "border_off_color": round(off_color, 3),
    }
    return result, info


def corners_clear(image: Any) -> bool:
    if not imaging.has_alpha(image):
        return False
    alpha = image.convert("RGBA").getchannel("A")
    width, height = alpha.size
    return all(max(alpha.crop(box).getdata()) <= 8 for box in check_images.corner_boxes(width, height, 8))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把纯色背景去成透明背景的 PNG")
    parser.add_argument("input", help="纯色背景的原图")
    parser.add_argument("output", help="输出的 PNG 路径")
    parser.add_argument("--color", help="背景色 #RRGGBB；默认取四边的中位色")
    parser.add_argument("--tolerance", type=int, help="和背景色的最大差值，默认按四边自动估计")
    parser.add_argument("--soft", type=int, help="边缘半透明过渡的宽度，默认是容差的一半")
    parser.add_argument("--holes", action="store_true", help="同时去掉主体围出来的封闭空隙，例如挂环中间；背景色在主体上没有出现时才用")
    parser.add_argument("--min-hole", type=float, default=0.0008, help="封闭空隙至少占画面的比例")
    parser.add_argument("--erode", type=int, default=0, help="把边缘向内收几个像素，去掉白边或色边")
    parser.add_argument("--feather", type=float, default=0.0, help="边缘模糊半径")
    parser.add_argument("--force", action="store_true", help="输出已存在时覆盖")
    args = parser.parse_args(argv)
    if not imaging.python_ok():
        return imaging.fail("需要 Python 3.10 或更高版本")
    source = Path(args.input)
    target = Path(args.output)
    try:
        if target.suffix.lower() != ".png":
            raise imaging.ImagingError("输出必须是 .png，其他格式不能保存透明背景")
        if source.resolve() == target.resolve():
            raise imaging.ImagingError("输出不能覆盖原图，换一个文件名")
        image = imaging.open_image(source)
        if corners_clear(image):
            result, info = image.convert("RGBA"), {"method": "already-transparent"}
        else:
            result, info = remove_background(image, args)
            info["method"] = "flood-from-border"
        imaging.save_png(result, target, force=args.force)
        check = check_images.inspect(target, argparse.Namespace(alpha=True, ratio=None, tolerance=0.02, min_side=0))
    except imaging.ImagingError as error:
        return imaging.fail(str(error))
    hints = []
    if info.get("border_off_color", 0) > 0.25:
        hints.append("四边有不少像素和背景色差得远：背景可能不是纯色，或者主体贴到了边缘。建议用纯色背景重新生成，或用 --color 指定背景色")
    if not check["ok"]:
        hints.append("自检没通过：先看图，再调 --tolerance，或者用和主体反差更大的纯色背景重新生成")
    payload = {"ok": check["ok"], "output": target.as_posix(), **info, "check": check}
    if hints:
        payload["hints"] = hints
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if check["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
