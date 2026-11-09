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

RENDER_VERSION = "post-png-2"
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
    finals = state.mapped_scene_finals(project)
    if not finals:
        raise state.ProjectError("没有定稿图；先通过整组图片，或用 project.py final 登记")
    note_type = (project.get("answers") or {}).get("note_type")
    order = state.SEQUENCES.get(note_type)
    if not order:
        raise state.ProjectError("先填写 answers.note_type")
    if project.get("scene_plan"):
        slots = [slot["id"] for slot in state.scene_slots(project)]
        identifiers = [final.get("slot") for final in finals]
        if len(set(identifiers)) != len(identifiers) or any(identifier not in slots for identifier in identifiers):
            raise state.ProjectError("定稿图位重复或不属于当前 scene_plan，请先核对图位")
        ordered = sorted(finals, key=lambda final: slots.index(final["slot"]))
    else:
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


def json_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def render_settings(images: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    from PIL import __version__ as pillow_version

    font = None
    if any(str(entry["overlay"].get(key) or "").strip() for entry in images for key in ("text", "sub")):
        selected = imaging.find_font(args.font)
        if not selected:
            raise imaging.ImagingError(f"没有找到中文字体；用 --font 指定字体文件，或设置环境变量 {imaging.FONT_ENV}")
        path = Path(selected).resolve()
        font = {"path": path.as_posix(), "sha256": file_hash(path)}
    renderer = {"package_post": file_hash(Path(__file__)), "imaging": file_hash(Path(imaging.__file__))}
    return {"render_version": RENDER_VERSION, "renderer_sha256": renderer, "pillow_version": pillow_version,
            "size": list(imaging.parse_size(args.size)), "fit": args.fit, "font": font}


def require_render_inputs(root: Path, plan: dict[str, Any], args: argparse.Namespace) -> None:
    settings = render_settings(plan["images"], args)
    expected = plan.get("render_settings")
    if expected is not None and settings != expected:
        raise state.ProjectError("排版参数或实际字体在计划后变化，停止整理并重新审查")
    for entry in plan["images"]:
        if file_hash(source_path(root, entry["source"])) != entry["source_sha256"]:
            raise state.ProjectError("定稿图在计划后变化，停止整理并重新审查")


def require_package_reviews(root: Path, project: dict[str, Any], paths: list[str], copy_path: Path | None = None) -> None:
    import review_checks

    state.require_reviews(root, project, paths, "scene")
    try:
        for category in ("sequence", "copy"):
            result = review_checks.status(root, project, category, paths, copy_path)
            if result.get("blocking"):
                raise state.ProjectError(f"{category} 审查仍有阻断问题：" + json.dumps(result.get("findings") or result, ensure_ascii=False))
    except review_checks.ReviewError as error:
        raise state.ProjectError(str(error)) from error


def make_plan(root: Path, project: dict[str, Any], copy: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    note_type = (project.get("answers") or {}).get("note_type")
    warnings = validate_copy(copy, note_type)
    finals = ordered_finals(project)
    state.require_physics(root, project, [final["path"] for final in finals])
    copy_path = Path(args.copy).resolve() if args.copy else None
    require_package_reviews(root, project, [final["path"] for final in finals], copy_path)
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
    settings = render_settings(images, args)
    render_inputs = {key: value for key, value in settings.items() if key != "font"}
    render_inputs["font_sha256"] = settings["font"]["sha256"] if settings["font"] else None
    render_inputs["images"] = [{"source_sha256": entry["source_sha256"], "overlay": entry["overlay"]} for entry in images]
    render_signature = json_hash(render_inputs)
    signature_data = {"images": images, "copy": copy, "render_signature": render_signature,
                      "requirements": state.requirement_signature(project), "delivery": (project.get("answers") or {}).get("delivery")}
    signature = json_hash(signature_data)
    return {"images": images, "signature": signature, "package_signature": signature, "render_signature": render_signature,
            "render_inputs": render_inputs, "render_settings": settings, "warnings": warnings, "size": settings["size"]}


def artifact_manifest(plan: dict[str, Any], prepared: bool = False) -> dict[str, Any]:
    return {"schema_version": 1, "prepared_review": prepared, "signature": plan["signature"],
            "package_signature": plan["signature"], "render_signature": plan["render_signature"],
            "render_version": RENDER_VERSION, "render_inputs": plan["render_inputs"],
            "render_settings": plan["render_settings"], "images": plan["images"], "size": plan["size"]}


def prepared_artifacts(root: Path, plan: dict[str, Any], out: Path | None = None) -> tuple[Path, dict[str, Any]] | None:
    parent = root / "reviews/post"
    candidates = sorted(parent.glob("*/manifest.json"), reverse=True)
    if out is not None and (out / "manifest.json").is_file():
        candidates.append(out / "manifest.json")
    for manifest_path in candidates:
        try:
            if not manifest_path.resolve().is_relative_to(root.resolve()):
                continue
            manifest = state.read_json(manifest_path)
            if (manifest.get("schema_version") != 1
                    or (manifest.get("prepared_review") is not True
                        and manifest_path != (out / "manifest.json" if out is not None else None))
                    or manifest.get("render_version") != RENDER_VERSION
                    or manifest.get("render_signature") != plan["render_signature"]
                    or manifest.get("render_inputs") != plan["render_inputs"]):
                continue
            entries = manifest.get("images")
            if not isinstance(entries, list) or len(entries) != len(plan["images"]):
                continue
            for entry, expected in zip(entries, plan["images"]):
                if (not isinstance(entry, dict) or not isinstance(entry.get("file"), str)
                        or Path(entry["file"]).name != entry["file"] or entry.get("fit") not in ("contain", "cover")
                        or entry.get("source_sha256") != expected["source_sha256"] or entry.get("overlay") != expected["overlay"]
                        or file_hash(source_path(root, (manifest_path.parent / entry["file"]).relative_to(root).as_posix())) != entry.get("output_sha256")):
                    break
            else:
                return manifest_path.parent, manifest
        except (OSError, state.ProjectError, AttributeError, KeyError, TypeError, ValueError):
            continue
    return None


def actual_output_slots(root: Path, project: dict[str, Any], paths: list[str]) -> list[dict[str, Any]]:
    """Map each requested path, in its actual order, to its hash-bound source and slot."""
    root = root.resolve()
    if not paths or len(set(paths)) != len(paths):
        raise state.ProjectError("实际输出需要非空且不重复的图片路径")
    finals = state.mapped_scene_finals(project)
    manifests = {}
    result = []
    for relative in paths:
        path = source_path(root, relative)
        output_hash = file_hash(path)
        manifest_path = path.parent / "manifest.json"
        entry = None
        if manifest_path.is_file():
            if manifest_path not in manifests:
                manifests[manifest_path] = state.read_json(manifest_path)
            if not isinstance(manifests[manifest_path], dict):
                raise state.ProjectError(f"实际输出图位清单必须是对象：{relative}")
            entries = manifests[manifest_path].get("images") or []
            if not isinstance(entries, list):
                raise state.ProjectError(f"实际输出图位清单必须包含 images 列表：{relative}")
            matches = [candidate for candidate in entries if isinstance(candidate, dict) and candidate.get("file") == path.name]
            if len(matches) > 1:
                raise state.ProjectError(f"实际输出图位重复：{relative}")
            if matches:
                entry = matches[0]
                if entry.get("output_sha256") != output_hash:
                    raise state.ProjectError(f"实际输出哈希与图位清单不符：{relative}")
                candidates = [final for final in finals if final.get("path") == entry.get("source")
                              and str(final.get("slot") or final.get("role")) == entry.get("slot")
                              and final.get("role") == entry.get("role") and final.get("subject", "") == entry.get("subject", "")]
            else:
                candidates = []
        else:
            candidates = [final for final in finals if source_path(root, final["path"]) == path]
        if len(candidates) != 1:
            raise state.ProjectError(f"无法唯一映射实际输出图位：{relative}")
        final = candidates[0]
        source_hash = file_hash(source_path(root, final["path"]))
        if entry is not None and entry.get("source_sha256") != source_hash:
            raise state.ProjectError(f"实际输出的源图已变化：{relative}")
        result.append({"path": path.relative_to(root).as_posix(), "sha256": output_hash,
                       "source": final["path"], "source_sha256": source_hash,
                       "slot_id": str(final.get("slot") or final.get("role")),
                       "slot": str(final.get("slot") or final.get("role")), "role": final.get("role"),
                       "subject": final.get("subject", "")})
    if len({entry["slot_id"] for entry in result}) != len(result):
        raise state.ProjectError("实际输出图位重复")
    return result


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
    with state.project_lock(root):
        current = state.load_project(root, writable=True)
        if current.get("revision") != project.get("revision"):
            raise state.ProjectError("stale_revision：整理期间项目有变化，请重新校验")
        record = {"path": out.relative_to(root).as_posix(), "signature": plan["signature"], "images": len(plan["images"])}
        if current.get("stage") == "done" and current.get("post") == record:
            return
        project["stage"] = "done"
        project["post"] = record
        state.save_project(root, project)


def render_images(root: Path, plan: dict[str, Any], directory: Path, args: argparse.Namespace) -> list[str]:
    require_render_inputs(root, plan, args)
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
        entry["output_sha256"] = file_hash(directory / entry["file"])
        paths.append((directory / entry["file"]).relative_to(root).as_posix())
    require_render_inputs(root, plan, args)
    return paths


def stage_images(root: Path, plan: dict[str, Any], directory: Path, args: argparse.Namespace,
                 out: Path | None = None, prepared: bool = False) -> tuple[list[str], bool]:
    require_render_inputs(root, plan, args)
    cached = prepared_artifacts(root, plan, out)
    if cached is None:
        paths = render_images(root, plan, directory, args)
    else:
        source_directory, manifest = cached
        paths = []
        for entry, previous in zip(plan["images"], manifest["images"]):
            destination = directory / entry["file"]
            shutil.copyfile(source_directory / previous["file"], destination)
            if file_hash(destination) != previous["output_sha256"]:
                raise state.ProjectError("已审输出在复用期间变化，停止整理并重新审查")
            entry["fit"] = previous["fit"]
            entry["output_sha256"] = previous["output_sha256"]
            paths.append(destination.relative_to(root).as_posix())
    require_render_inputs(root, plan, args)
    state.atomic_json(directory / "manifest.json", artifact_manifest(plan, prepared))
    return paths, cached is not None


def prepare_review(root: Path, plan: dict[str, Any], args: argparse.Namespace,
                   project: dict[str, Any] | None = None) -> dict[str, Any]:
    project = project if project is not None else state.load_project(root)
    parent = root / "reviews/post"
    parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=plan["signature"][:12] + "-", dir=parent))
    try:
        paths, reused = stage_images(root, plan, directory, args, root / getattr(args, "out", "post"), prepared=True)
        output_slots = actual_output_slots(root, project, paths)
        state.atomic_json(directory / "output-slots.json", output_slots)
        return {"ok": True, "prepared_review": True, "images": paths, "output_slots": output_slots,
                "ordered_slots_file": (directory / "output-slots.json").relative_to(root).as_posix(),
                "manifest": (directory / "manifest.json").relative_to(root).as_posix(),
                "signature": plan["signature"], "render_signature": plan["render_signature"], "reused": reused,
                "say": "对实际输出做 G1 publication 审查：contract_review.py prepare --stage package --images 按 images 顺序，--ordered-slots 使用 ordered_slots_file；并用 physics_review.py prepare --images 做物理盲审，通过后再正式打包"}
    except BaseException:
        shutil.rmtree(directory)
        raise


def require_current_plan(root: Path, project: dict[str, Any], plan: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    current = state.load_project(root, writable=True)
    if current.get("revision") != project.get("revision"):
        raise state.ProjectError("stale_revision：整理期间项目有变化，旧成品未覆盖")
    latest_copy = state.read_json(Path(args.copy) if args.copy else root / "copy.json")
    if make_plan(root, current, latest_copy, args)["signature"] != plan["signature"]:
        raise state.ProjectError("整理期间文案或素材变化，旧成品未覆盖")
    require_render_inputs(root, plan, args)
    return current


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
        paths, reused = stage_images(root, plan, staging, args, out)
        output_slots = actual_output_slots(root, project, paths)
        reviews = state.require_physics(root, project, paths)
        copy_path = Path(args.copy).resolve() if args.copy else None
        state.require_reviews(root, project, paths, "package", copy_path)
        for entry, review in zip(plan["images"], reviews):
            entry["physics_review"] = {"verdict": review["verdict"], "report": review["report"]}
        (staging / "caption.md").write_text(text, encoding="utf-8")
        if (project.get("answers") or {}).get("delivery") == "grid-9":
            grid_preview([staging / entry["file"] for entry in plan["images"]], staging / "previews" / "grid-preview.png")
        manifest = {
            **artifact_manifest(plan),
            "requirements": state.requirement_signature(project),
            "title": copy["title"].strip(),
            "body": body_with_disclosure,
            "tags": copy["tags"],
            "images": plan["images"],
            "size": plan["size"],
            "checklist": CHECKLIST + ["标题、正文、图片数和话题数采用保守预算；以发布时的平台校验为准"],
            "warnings": plan["warnings"],
            "files": {path.relative_to(staging).as_posix(): file_hash(path) for path in staging.rglob("*")
                      if path.is_file() and path != staging / "manifest.json"},
        }
        state.atomic_json(staging / "manifest.json", manifest)
        backup = None
        with state.project_lock(root):
            current = require_current_plan(root, project, plan, args)
            state.require_physics(root, current, paths)
            state.require_reviews(root, current, paths, "package", copy_path)
            if actual_output_slots(root, current, paths) != output_slots:
                raise state.ProjectError("实际输出在审查后变化，旧成品未覆盖")
            require_render_inputs(root, plan, args)
            if out.exists():
                backup = out.with_name(f"{out.name}-previous-{datetime.now():%Y%m%d-%H%M%S-%f}")
                out.replace(backup)
            try:
                staging.replace(out)
                commit_state(root, project, out, plan)
            except BaseException:
                if out.exists():
                    out.replace(staging)
                if backup:
                    backup.replace(out)
                raise
        result = {"ok": True, "post": out.resolve().as_posix(), "images": len(plan["images"]),
                  "warnings": plan["warnings"], "reused": reused}
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
        project = state.load_project(root, writable=not args.dry_run)
        out = (root / args.out).resolve()
        if out == root or not out.is_relative_to(root):
            raise state.ProjectError("--out 必须是项目内的子目录")
        copy = state.read_json(Path(args.copy) if args.copy else root / "copy.json")
        plan = make_plan(root, project, copy, args)
        if args.dry_run:
            return state.emit({"ok": True, "dry_run": True, "post": out.as_posix(), **plan})
        if args.prepare_review:
            return state.emit(prepare_review(root, plan, args, project))
        if complete_existing(out, plan["signature"]):
            existing = [(out / entry["file"]).relative_to(root).as_posix() for entry in plan["images"]]
            copy_path = Path(args.copy).resolve() if args.copy else None
            with state.project_lock(root):
                current = require_current_plan(root, project, plan, args)
                if not complete_existing(out, plan["signature"]):
                    raise state.ProjectError("实际输出在校验后变化，停止整理并重新审查")
                output_slots = actual_output_slots(root, current, existing)
                state.require_physics(root, current, existing)
                state.require_reviews(root, current, existing, "package", copy_path)
                if not complete_existing(out, plan["signature"]) or actual_output_slots(root, current, existing) != output_slots:
                    raise state.ProjectError("实际输出在审查后变化，停止整理并重新审查")
                require_render_inputs(root, plan, args)
                commit_state(root, project, out, plan)
            return state.emit({"ok": True, "unchanged": True, "post": out.as_posix(), "images": len(plan["images"])})
        if out.exists() and any(out.iterdir()) and not args.force:
            raise state.ProjectError("发帖目录已存在但内容不同或不完整；核对后加 --force 保留上一版再更新")
        return state.emit(execute(root, project, copy, plan, out, args))
    except (state.ProjectError, imaging.ImagingError, OSError) as error:
        return state.emit({"ok": False, "error": str(error)})


if __name__ == "__main__":
    from runtime_activity import execution_lease
    with execution_lease():
        sys.exit(main())
