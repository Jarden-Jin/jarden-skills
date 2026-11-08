#!/usr/bin/env python3
"""生成项目根目录的 gallery.html 对比页。

只读取 project.json、各轮 round.json 和 post/manifest.json，不生成、不修改任何图片。
缺少的图片在页面上用 CSS 占位显示。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import project as state  # noqa: E402
import physics_review

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "gallery.html"
STAGE_LABELS = {
    "concept": "概念图",
    "material": "材质方向",
    "cutout": "透明图",
    "scene": "场景图",
    "package": "发帖素材",
    "done": "已完成",
}
REF_LABELS = {"source": "参考图", "style": "风格参考", "based_on": "在这张上继续"}
METHOD_LABELS = {"generate": "AI 生成", "compose": "后期拼版", "derive": "已有图派生"}
CHOICES = {
    "missing": [
        "文字方向已经可以（推荐）：保留某方向意向；图片补齐并确认之前不进入下一步",
        "要新的方向：沿某个想法补变化，继续保留未生成图片的标记",
        "重新生成：重写本轮方案；生图恢复后再生成图片",
    ],
    "alternatives": [
        "已经可以（推荐）：选定一个方向，例如回复「1 B」，进入下一步",
        "要新的图：在某个方向上继续出变化，或者补几个新方向，例如「2 A 调整主体比例」",
        "重新生成：这一轮全部重来，可以附上想改的地方",
    ],
    "set": [
        "已经可以（推荐）：整组通过，进入下一步",
        "要新的图：只重出不满意的几张，例如「2 第 3 张换个使用场景」",
        "重新生成：整组重来，可以附上想改的地方",
    ],
}


def label_of(options: list[dict[str, Any]], value: Any) -> str:
    for option in options:
        if option["value"] == value:
            return option["label"]
    return str(value) if value else ""


def chips(project: dict[str, Any]) -> list[str]:
    answers = project.get("answers") or {}
    found = []
    if answers.get("product_form"):
        found.append("产品形态：" + label_of(state.PRODUCT_FORMS, answers["product_form"]))
    if answers.get("note_type"):
        found.append("笔记类型：" + label_of(state.note_types_of(project), answers["note_type"]))
    if answers.get("delivery"):
        found.append("交付：" + label_of(state.DELIVERIES, answers["delivery"]))
    subjects = answers.get("subjects") or []
    if subjects:
        found.append("角色：" + "、".join(subjects))
    found.append("当前阶段：" + STAGE_LABELS.get(project.get("stage") or "concept", ""))
    return found


def round_items(root: Path, entry: dict[str, Any], data: dict[str, Any], project: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for item in data.get("items") or []:
        path = f"{entry['dir']}/{item.get('image', '')}"
        exists = (root / path).is_file()
        badge = item.get("id") if data.get("kind") == "alternatives" else item.get("slot_label") or item.get("id")
        method = item.get("method", "generate")
        items.append({
            "id": item.get("id"),
            "badge": badge,
            "title": item.get("title") or "",
            "idea": item.get("idea") or "",
            "prompt": item.get("prompt") or "",
            "prompt_label": "生图提示词" if method == "generate" else "制作说明",
            "method": METHOD_LABELS.get(method, method),
            "sources": item.get("sources") or [],
            "path": path,
            "exists": exists,
            "reason": "" if exists else "这一轮没有可用的生图能力，提示词已保存",
            "picked": entry.get("pick") == item.get("id"),
            "physics": physics_review.status(root, project, path) if entry.get("stage") == "scene" and exists else None,
        })
    return items


def heading(entry: dict[str, Any], data: dict[str, Any]) -> str:
    stage = STAGE_LABELS.get(entry.get("stage"), entry.get("stage"))
    parts = [f"第 {entry['id'][:2].lstrip('0') or '0'} 轮", stage]
    if entry.get("subject"):
        parts.append(entry["subject"])
    if data.get("kind") == "set":
        parts.append("整组")
    return " · ".join(parts)


def build_data(root: Path, project: dict[str, Any]) -> dict[str, Any]:
    rounds = project.get("rounds") or []
    open_entry = state.open_round(project)
    current = None
    history = []
    missing = 0
    for entry in reversed(rounds):
        round_file = root / entry["dir"] / "round.json"
        if not round_file.is_file():
            continue
        data = state.read_json(round_file)
        items = round_items(root, entry, data, project)
        if open_entry and entry["id"] == open_entry["id"]:
            refs = []
            for key, paths in (data.get("refs") or {}).items():
                for path in paths:
                    if (root / path).is_file():
                        refs.append({"path": path, "label": REF_LABELS.get(key, key)})
            missing = sum(1 for item in items if not item["exists"])
            hint = "提示词点开卡片下方查看"
            if missing:
                hint = f"{missing} 张还没有图片，先看方向和提示词"
            pending_physics = sum(bool(item.get("physics")) and item["physics"]["verdict"] != "pass" for item in items)
            if pending_physics:
                hint += f"；{pending_physics} 张未通过独立物理审查，不能选定"
            current = {"heading": heading(entry, data), "hint": hint, "refs": refs, "items": items, "kind": data.get("kind")}
        else:
            history.append({"heading": heading(entry, data) + f"（{entry.get('status')}）", "items": items})
    picks = []
    for stage in ("concept", "material", "cutout", "scene"):
        for key, pick in ((project.get("selections") or {}).get(stage) or {}).items():
            if not isinstance(pick, dict) or "image" not in pick:
                continue
            name = "关键场景风格" if key == "_" else key
            picks.append({
                "id": f"{stage}-{key}",
                "badge": STAGE_LABELS[stage],
                "title": f"{STAGE_LABELS[stage]} · {name}",
                "idea": f"来自 {pick.get('round')} 的方向 {pick.get('item')}",
                "path": pick["image"],
                "exists": (root / pick["image"]).is_file(),
                "physics": physics_review.status(root, project, pick["image"]) if stage == "scene" else None,
            })
    post = None
    post_record = project.get("post") or {}
    post_dir = post_record.get("path") or "post"
    manifest = root / post_dir / "manifest.json"
    if post_record and project.get("stage") == "done" and manifest.is_file():
        info = state.read_json(manifest)
        paths = [final["path"] for final in project.get("finals") or []] + [f"{post_dir}/{image['file']}" for image in info.get("images") or []]
        physics_valid = bool(paths) and all(physics_review.status(root, project, path)["verdict"] == "pass" for path in paths)
        if physics_valid and info.get("signature") == post_record.get("signature") and info.get("requirements") == state.requirement_signature(project):
            post = {
                "images": [{"path": f"{post_dir}/{image['file']}", "role": image.get("role", ""), "label": image.get("label", "")} for image in info.get("images", [])],
                "title": info.get("title", ""),
                "body": info.get("body", ""),
                "tags": info.get("tags", []),
                "hint": "按发布顺序排列，左右滑动查看",
                "notes": "；".join(info.get("checklist", [])),
            }
    if not post and project.get("finals"):
        for final in project["finals"]:
            picks.append({
                "id": final.get("slot") or final["path"],
                "badge": "定稿",
                "title": f"定稿 · {final.get('role')}" + (f" · {final['subject']}" if final.get("subject") else ""),
                "idea": final.get("note", ""),
                "path": final["path"],
                "exists": (root / final["path"]).is_file(),
                "physics": physics_review.status(root, project, final["path"]),
            })
    kind = "missing" if missing else (current or {}).get("kind") or "alternatives"
    return {
        "title": ((project.get("brief") or {}).get("ip_name") or project.get("project") or "IP 项目") + " · 视觉对比页",
        "subtitle": f"项目 {project.get('project')} · 更新于 {project.get('updated_at')}",
        "chips": chips(project),
        "current": current,
        "choices": CHOICES.get(kind, CHOICES["alternatives"]),
        "picks": picks,
        "post": post,
        "history": history,
        "missing": missing,
    }


def render(data: dict[str, Any]) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    if "__GALLERY_DATA__" not in template:
        raise state.ProjectError("assets/gallery.html 缺少数据占位符")
    return template.replace("__GALLERY_DATA__", payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 gallery.html 对比页")
    parser.add_argument("project")
    parser.add_argument("--out", help="默认写到项目目录的 gallery.html")
    args = parser.parse_args(argv)
    root = Path(args.project)
    try:
        project = state.load_project(root)
        data = build_data(root, project)
        out = Path(args.out) if args.out else root / "gallery.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        html = render(data)
        temp = out.with_name(f".tmp-{out.name}")
        temp.write_text(html, encoding="utf-8", newline="\n")
        temp.replace(out)
    except state.ProjectError as error:
        return state.emit({"ok": False, "error": str(error)})
    current = data["current"]
    return state.emit({
        "ok": True,
        "gallery": out.resolve().as_posix(),
        "current": current["heading"] if current else None,
        "missing_images": data["missing"],
        "has_post": data["post"] is not None,
        "say": "把 gallery 路径发给用户，再按页面上的三种选择问一个问题" if current else "把 gallery 路径发给用户",
    })


if __name__ == "__main__":
    sys.exit(main())
