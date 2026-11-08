#!/usr/bin/env python3
"""检查图片尺寸、比例和透明背景。

--alpha 要求真透明：有 alpha 通道、四角透明、主体面积合理；
画出来的灰白棋盘格不算透明。所有图片通过时退出码为 0。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import imaging  # noqa: E402

SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def parse_ratio(value: str) -> float:
    try:
        width, height = (float(part) for part in value.split(":"))
        return width / height
    except (ValueError, ZeroDivisionError) as error:
        raise imaging.ImagingError(f"比例要写成 宽:高，例如 3:4：{value}") from error


def corner_boxes(width: int, height: int, size: int) -> list[tuple[int, int, int, int]]:
    size = max(2, min(size, width // 4, height // 4))
    return [
        (0, 0, size, size),
        (width - size, 0, width, size),
        (0, height - size, size, height),
        (width - size, height - size, width, height),
    ]


def looks_like_checkerboard(image: Any) -> bool:
    """角落是两种接近灰白的颜色交替出现，通常是模型画出来的假透明。"""
    rgb = imaging.flatten(image)
    width, height = rgb.size
    hits = 0
    for box in corner_boxes(width, height, max(24, width // 20)):
        region = rgb.crop(box).convert("L")
        values = list(region.getdata())
        low = [value for value in values if value < 235]
        high = [value for value in values if value >= 235]
        if not low or not high:
            continue
        share = len(low) / len(values)
        low_mean = sum(low) / len(low)
        if 0.25 <= share <= 0.75 and 170 <= low_mean <= 230:
            hits += 1
    return hits >= 3


def inspect(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    image = imaging.open_image(path)
    width, height = image.size
    result: dict[str, Any] = {"file": path.as_posix(), "size": [width, height], "mode": image.mode, "problems": [], "notes": []}
    alpha_present = imaging.has_alpha(image)
    result["has_alpha"] = alpha_present
    if args.min_side and min(width, height) < args.min_side:
        result["problems"].append(f"短边 {min(width, height)} 小于 {args.min_side}")
    if args.ratio:
        target = parse_ratio(args.ratio)
        actual = width / height
        result["ratio"] = round(actual, 4)
        if abs(actual - target) / target > args.tolerance:
            result["problems"].append(f"比例 {width}:{height} 不是 {args.ratio}")
    if args.alpha:
        if not alpha_present:
            result["problems"].append("没有透明通道")
            if looks_like_checkerboard(image):
                result["problems"].append("背景像画出来的棋盘格，不是真透明；改用纯色底生成再用 remove_bg.py 去底")
        else:
            alpha = image.convert("RGBA").getchannel("A")
            corners = [max(alpha.crop(box).getdata()) for box in corner_boxes(width, height, 8)]
            result["corner_alpha"] = corners
            if any(value > 8 for value in corners):
                result["problems"].append("四个角不全是透明的")
                if looks_like_checkerboard(image):
                    result["problems"].append("背景像画出来的棋盘格，不是真透明")
            small = alpha.copy()
            small.thumbnail((256, 256))
            values = list(small.getdata())
            opaque = sum(1 for value in values if value > 16) / max(len(values), 1)
            result["subject_coverage"] = round(opaque, 3)
            if opaque < 0.03:
                result["problems"].append("几乎全透明，主体可能被一起去掉了")
            elif opaque > 0.92:
                result["problems"].append("几乎没有透明区域，背景没有去干净")
            box = alpha.point(lambda value: 255 if value > 16 else 0).getbbox()
            if box:
                result["subject_box"] = list(box)
                if box[0] <= 1 or box[1] <= 1 or box[2] >= width - 1 or box[3] >= height - 1:
                    result["notes"].append("主体贴到画面边缘，可能被裁掉了一部分")
    result["ok"] = not result["problems"]
    return result


def collect(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for value in paths:
        path = Path(value)
        if path.is_dir():
            files.extend(sorted(item for item in path.iterdir() if item.suffix.lower() in SUFFIXES and not item.name.startswith(".")))
        else:
            files.append(path)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查图片尺寸、比例和透明背景")
    parser.add_argument("paths", nargs="+", help="图片或目录")
    parser.add_argument("--alpha", action="store_true", help="要求真透明背景")
    parser.add_argument("--ratio", help="要求的比例，例如 3:4")
    parser.add_argument("--tolerance", type=float, default=0.02, help="比例允许的相对误差")
    parser.add_argument("--min-side", type=int, default=0, help="短边最少像素")
    args = parser.parse_args(argv)
    try:
        files = collect(args.paths)
        if not files:
            raise imaging.ImagingError("没有找到图片")
        results = [inspect(path, args) for path in files]
    except imaging.ImagingError as error:
        return imaging.fail(str(error))
    passed = all(item["ok"] for item in results)
    print(json.dumps({"ok": passed, "checked": len(results), "results": results}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    from runtime_activity import execution_lease
    with execution_lease():
        sys.exit(main())
