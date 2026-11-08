#!/usr/bin/env python3
"""把定稿图和 copy.json 整理成可直接发布的素材目录。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import imaging
import project as state

TAG_RANGES = {
    "series-collection": (8, 10),
    "design-manuscript": (8, 10),
    "single-multiview": (3, 7),
    "multi-scene": (6, 10),
}
CHECKLIST = [
    "发布时开启平台的 AI 内容声明，并保留文案里的效果图说明",
    "图片为概念或材质效果图；未打样的尺寸、材质、价格和发售时间不写成已确定事实",
    "核对角色的一致性、拼写和画面边缘；自行检查是否和现有 IP 过度相似",
    "不添加未授权的水印、联系方式或站外导流信息",
]


def count_chars(text: str) -> int:
    return sum(1 for char in text if char not in ("\ufe0f", "\u200d"))


def validate_copy(copy: dict[str, Any], note_type: str) -> list[str]:
    title, body, tags = copy.get("title"), copy.get("body"), copy.get("tags")
    if not isinstance(title, str) or not title.strip() or count_chars(title) > 20:
        raise state.ProjectError("title 必须是 1 到 20 字的标题")
    if not isinstance(body, str) or not body.strip() or count_chars(body) > 1000:
        raise state.ProjectError("body 必须是 1 到 1000 字的正文（本 Skill 的保守交付预算，平台当前限制请在发布页核对）")
    if not isinstance(tags, list) or not 3 <= len(tags) <= 10:
        raise state.ProjectError("tags 必须是 3 到 10 个话题的列表")
    if not all(isinstance(tag, str) and re.fullmatch(r"#[^\s#]+", tag) for tag in tags):
        raise state.ProjectError("每个 tag 都要以 # 开头，不含空格或第二个 #")
    if len({tag.casefold() for tag in tags}) != len(tags):
        raise state.ProjectError("tags 不能重复，大小写不同也算重复")
    overlays = copy.get("overlays") or {}
    if not isinstance(overlays, dict) or not all(isinstance(value, dict) for value in overlays.values()):
        raise state.ProjectError("overlays 要写成 {图位: {text, sub, position}}")
    image_text = copy.get("image_text", "none")
    if image_text not in ("none", "user-approved"):
        raise state.ProjectError("image_text 只能是 none 或 user-approved")
    if image_text != "user-approved" and any(str(value.get(key) or "").strip() for value in overlays.values() for key in ("text", "sub")):
        raise state.ProjectError("成图默认不加文字；说明放正文或HTML，只有用户明确要图字时才设置 image_text=user-approved")
    low, high = TAG_RANGES.get(note_type, (3, 10))
    return [] if low <= len(tags) <= high else [f"这一类笔记通常用 {low} 到 {high} 个 tag；当前为 {len(tags)} 个，请确认是否符合内容"]


def ordered_finals(project: dict[str, Any]) -> list[dict[str, Any]]:
    finals = project.get("finals") or []
    if not finals:
        raise state.ProjectError("没有定稿图；先通过整组图片，或用 project.py final 登记")
    note_type = (project.get("answers") or {}).get("note_type")
    order = state.SEQUENCES.get(note_type)
    if not order:
        raise state.ProjectError("先填写 answers.note_type")
    result = sorted(enumerate(finals), key=lambda pair: (order.index(pair[1].get("role")) if pair[1].get("role") in order else len(order), pair[0]))
    ordered = [entry for _, entry in result]
    if ordered[0].get("role") != "cover":
        raise state.ProjectError("缺少 cover 封面；封面不能用随机一张图代替")
    missing = state.missing_scene_slots(project)
    if missing:
        raise state.ProjectError("当前笔记还缺少已通过图位：" + ", ".join(missing))
    delivery = (project.get("answers") or {}).get("delivery")
    if delivery == "grid-9" and len(ordered) != 9:
        raise state.ProjectError(f"九宫格交付需要正好 9 张定稿图，当前有 {len(ordered)} 张；先补齐或让用户改交付形式")
    if len(ordered) > 18:
        raise state.ProjectError("这套图片超过 18 张；先和用户拆成多篇笔记")
    return ordered


def source_path(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise state.ProjectError(f"定稿图必须在项目目录里并且存在：{value}")
    return path


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_plan(root: Path, project: dict[str, Any], copy: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    note_type = (project.get("answers") or {}).get("note_type")
    warnings = validate_copy(copy, note_type)
    finals = ordered_finals(project)
    state.require_physics(root, project, [final["path"] for final in finals])
    images = []
    for index, final in enumerate(finals, 1):
        path = source_path(root, final["path"])
        role = str(final.get("role") or "image")
        slot = str(final.get("slot") or role)
        image = imaging.open_image(path)
        safe_role = re.sub(r"[^a-zA-Z0-9_-]+", "-", role)
        entry = {
            "file": f"{index:02d}-{safe_role}.png",
            "source": final["path"],
            "role": role,
            "slot": slot,
            "subject": final.get("subject", ""),
            "label": final.get("subject") or final.get("note") or slot,
            "source_sha256": file_hash(path),
            "source_size": list(image.size),
            "overlay": (copy.get("overlays") or {}).get(slot) or {},
        }
        images.append(entry)
    signature_data = {"images": images, "copy": copy, "size": args.size, "fit": args.fit, "font": args.font,
                      "requirements": state.requirement_signature(project), "delivery": (project.get("answers") or {}).get("delivery")}
    signature = hashlib.sha256(json.dumps(signature_data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"images": images, "signature": signature, "warnings": warnings, "size": list(imaging.parse_size(args.size))}


def complete_existing(out: Path, signature: str) -> bool:
    path = out / "manifest.json"
    if not path.is_file():
        return False
    try:
        manifest = state.read_json(path)
        if manifest.get("signature") != signature:
            return False
        files = manifest.get("files") or {}
        return bool(files) and all((out / name).is_file() and file_hash(out / name) == sha for name, sha in files.items())
    except (OSError, state.ProjectError):
        return False


def caption(copy: dict[str, Any], disclosure: str) -> str:
    body = copy["body"].strip()
    if disclosure and disclosure not in body:
        body += "\n\n" + disclosure
    return f"{copy['title'].strip()}\n\n{body}\n\n" + " ".join(copy["tags"]) + "\n"


def grid_preview(paths: list[Path], out: Path) -> None:
    from PIL import Image

    tile = (270, 360)
    canvas = Image.new("RGB", (tile[0] * 3, tile[1] * 3), (245, 243, 238))
    for index, path in enumerate(paths):
        image = imaging.open_image(path).resize(tile, Image.LANCZOS)
        canvas.paste(image, ((index % 3) * tile[0], (index // 3) * tile[1]))
    imaging.save_png(canvas, out)


def commit_state(root: Path, project: dict[str, Any], out: Path, plan: dict[str, Any]) -> None:
    project["stage"] = "done"
    project["post"] = {"path": out.relative_to(root).as_posix(), "signature": plan["signature"], "images": len(plan["images"])}
    state.save_project(root, project)


def render_images(root: Path, plan: dict[str, Any], directory: Path, args: argparse.Namespace) -> list[str]:
    paths = []
    for entry in plan["images"]:
        source = source_path(root, entry["source"])
        if file_hash(source) != entry["source_sha256"]:
            raise state.ProjectError("定稿图在计划后变化，停止整理并重新审查")
        image = imaging.open_image(source)
        result, mode = imaging.fit(image, tuple(plan["size"]), args.fit)
        result = imaging.draw_overlay(result, entry["overlay"], args.font)
        imaging.save_png(result, directory / entry["file"])
        entry["fit"] = mode
        paths.append((directory / entry["file"]).relative_to(root).as_posix())
    return paths


def prepare_review(root: Path, plan: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    parent = root / "reviews/post"
    parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=plan["signature"][:12] + "-", dir=parent))
    paths = render_images(root, plan, directory, args)
    return {"ok": True, "prepared_review": True, "images": paths, "signature": plan["signature"],
            "say": "对以上实际输出用 physics_review.py prepare --images 送零上下文审查；通过后再正式打包，不改已有post"}


def execute(root: Path, project: dict[str, Any], copy: dict[str, Any], plan: dict[str, Any], out: Path, args: argparse.Namespace) -> dict[str, Any]:
    disclosure_value = copy.get("ai_disclosure", "AI 生成的设计效果图，实物以打样为准。")
    disclosure = disclosure_value if isinstance(disclosure_value, str) else ""
    text = caption(copy, disclosure)
    body_with_disclosure = copy["body"].strip() + ("\n\n" + disclosure if disclosure and disclosure not in copy["body"] else "")
    if count_chars(body_with_disclosure) > 1000:
        raise state.ProjectError("正文加 AI 声明超过 1000 字，请缩短正文后再整理")
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".post-", dir=out.parent))
    try:
        paths = render_images(root, plan, staging, args)
        reviews = state.require_physics(root, project, paths)
        for entry, review in zip(plan["images"], reviews):
            entry["physics_review"] = {"verdict": review["verdict"], "report": review["report"]}
        (staging / "caption.md").write_text(text, encoding="utf-8")
        if (project.get("answers") or {}).get("delivery") == "grid-9":
            grid_preview([staging / entry["file"] for entry in plan["images"]], staging / "previews" / "grid-preview.png")
        manifest = {
            "schema_version": 1,
            "signature": plan["signature"],
            "requirements": state.requirement_signature(project),
            "title": copy["title"].strip(),
            "body": body_with_disclosure,
            "tags": copy["tags"],
            "images": plan["images"],
            "size": plan["size"],
            "checklist": CHECKLIST + ["标题、正文、图片数和话题数采用保守预算；以发布时的平台校验为准"],
            "warnings": plan["warnings"],
            "files": {path.relative_to(staging).as_posix(): file_hash(path) for path in staging.rglob("*") if path.is_file()},
        }
        state.write_json(staging / "manifest.json", manifest)
        backup = None
        if out.exists():
            backup = out.with_name(f"{out.name}-previous-{datetime.now():%Y%m%d-%H%M%S-%f}")
            out.replace(backup)
        try:
            staging.replace(out)
        except OSError:
            if backup:
                backup.replace(out)
            raise
        commit_state(root, project, out, plan)
        result = {"ok": True, "post": out.resolve().as_posix(), "images": len(plan["images"]), "warnings": plan["warnings"]}
        if backup:
            result["previous"] = backup.resolve().as_posix()
        return result
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按笔记类型整理发布图片、标题、正文和 tag")
    parser.add_argument("project")
    parser.add_argument("--copy", help="文案 JSON，默认 <项目>/copy.json")
    parser.add_argument("--out", default="post", help="项目内的输出子目录，默认 post")
    parser.add_argument("--size", default="1080x1440")
    parser.add_argument("--fit", choices=("auto", "contain", "cover"), default="auto")
    parser.add_argument("--font", help="中文字体路径")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只校验源图和文案并输出计划，不代表最终图已审")
    mode.add_argument("--prepare-review", action="store_true", help="输出排版后待审图，不提交post或项目状态")
    parser.add_argument("--force", action="store_true", help="输出不同或损坏时，保留上一版后重新整理")
    args = parser.parse_args(argv)
    if not imaging.python_ok():
        return imaging.fail("需要 Python 3.10 或更高版本")
    root = Path(args.project).resolve()
    try:
        project = state.load_project(root)
        out = (root / args.out).resolve()
        if out == root or not out.is_relative_to(root):
            raise state.ProjectError("--out 必须是项目内的子目录")
        copy = state.read_json(Path(args.copy) if args.copy else root / "copy.json")
        plan = make_plan(root, project, copy, args)
        if args.dry_run:
            return state.emit({"ok": True, "dry_run": True, "post": out.as_posix(), **plan})
        if args.prepare_review:
            return state.emit(prepare_review(root, plan, args))
        if complete_existing(out, plan["signature"]):
            state.require_physics(root, project, [(out / entry["file"]).relative_to(root).as_posix() for entry in plan["images"]])
            commit_state(root, project, out, plan)
            return state.emit({"ok": True, "unchanged": True, "post": out.as_posix(), "images": len(plan["images"])})
        if out.exists() and any(out.iterdir()) and not args.force:
            raise state.ProjectError("发帖目录已存在但内容不同或不完整；核对后加 --force 保留上一版再更新")
        return state.emit(execute(root, project, copy, plan, out, args))
    except (state.ProjectError, imaging.ImagingError, OSError) as error:
        return state.emit({"ok": False, "error": str(error)})


if __name__ == "__main__":
    sys.exit(main())
