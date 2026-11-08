#!/usr/bin/env python3
"""Render immutable deliveries or a read-only legacy project without generating images."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import project as state  # noqa: E402
import physics_review

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "gallery.html"
ASSETS = TEMPLATE.parent
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
    eligibility = state.round_eligibility(root, project, entry)
    for item in data.get("items") or []:
        path = f"{entry['dir']}/{item.get('image', '')}"
        image_path = project_path(root, path)
        exists = bool(item.get("image")) and image_path.is_file()
        permitted = eligibility.get(item.get("id")) or {}
        missing_reason = ""
        if not exists:
            detail = item.get("missing_reason") or item.get("error") or data.get("missing_reason") or data.get("blocked_reason") or (project.get("brief") or {}).get("execution_status")
            if not isinstance(detail, str):
                detail = json.dumps(detail, ensure_ascii=False) if detail else "未记录具体原因"
            prompt_state = "提示词已保存" if str(item.get("prompt") or "").strip() else "提示词尚未填写"
            missing_reason = f"{'图片尚未生成' if item.get('status') == 'planned' or entry.get('status') == 'planned' else '图片文件缺失'}；{detail}；{prompt_state}"
        reason = missing_reason or permitted.get("disabled_reason") or ""
        if project.get("schema_version") == 1:
            reason = "旧项目只读；明确迁移后才能提交"
        badge = item.get("id") if data.get("kind") == "alternatives" else item.get("slot_label") or item.get("id")
        method = item.get("method", "generate")
        items.append({
            "id": item.get("id"),
            "slot": item.get("slot") or (item.get("id") if data.get("kind") == "set" else None),
            "round_id": entry["id"],
            "stage": entry.get("stage"),
            "subject": item.get("subject") or entry.get("subject"),
            "kind": data.get("kind"),
            "badge": badge,
            "title": item.get("title") or "",
            "idea": item.get("idea") or "",
            "prompt": item.get("prompt") or "",
            "prompt_label": "生图提示词" if method == "generate" else "制作说明",
            "method": METHOD_LABELS.get(method, method),
            "method_code": method,
            "sources": item.get("sources") or [],
            "path": path,
            "original_path": path,
            "display_path": path if exists else None,
            "image_sha256": permitted.get("image_sha256"),
            "selectable": bool(permitted.get("selectable")) and not reason and exists,
            "disabled_reason": reason,
            "exists": exists,
            "reason": missing_reason,
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


def build_data(root: Path, project: dict[str, Any], *, prefer_latest: bool = True) -> dict[str, Any]:
    root = Path(root).resolve()
    latest_id = (project.get("presentations") or {}).get("latest_delivery_id")
    if prefer_latest and latest_id:
        return delivery_data(root, project, load_manifest(root, latest_id))
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
                hint = f"{missing} 张还没有图片；查看各项实际原因与已填写内容"
            pending_physics = sum(bool(item.get("physics")) and item["physics"]["verdict"] != "pass" for item in items)
            if pending_physics:
                hint += f"；{pending_physics} 张未通过独立物理审查，不能选定"
            current = {"heading": heading(entry, data), "hint": hint, "refs": refs, "items": items, "kind": data.get("kind"),
                       "round_id": entry["id"], "subject": entry.get("subject"), "stage": entry.get("stage"),
                       "status": entry.get("status"), "based_on": data.get("based_on"), "note": data.get("note", ""),
                       "feedback": data.get("feedback") or [], "keep": data.get("keep") or []}
        else:
            history.append({"heading": heading(entry, data) + f"（{entry.get('status')}）", "items": items,
                            "round_id": entry["id"], "stage": entry.get("stage"), "subject": entry.get("subject"), "kind": data.get("kind")})
    picks = []
    for stage in ("concept", "material", "cutout", "scene"):
        for key, pick in ((project.get("selections") or {}).get(stage) or {}).items():
            if not isinstance(pick, dict) or "image" not in pick:
                continue
            name = "关键场景风格" if key == "_" else key
            picks.append({
                "id": f"{stage}-{key}",
                "round_id": pick.get("round"),
                "source_item_id": pick.get("item"),
                "stage": stage,
                "subject": key if stage in state.PER_SUBJECT_STAGES or stage == "cutout" else None,
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
                "slot": final.get("slot"),
                "stage": "scene",
                "subject": final.get("subject"),
                "kind": "set",
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
        "project_id": project.get("project_id"),
        "project_revision": project.get("revision", 0),
        "stage": project.get("stage"),
        "snapshot_at": state.now(),
        "readonly": project.get("schema_version") != 2,
        "next": state.next_action(copy.deepcopy(project), root),
        "answers": project.get("answers") or {},
        "decisions": (project.get("brief") or {}).get("decisions") or [],
        "bible": (project.get("brief") or {}).get("bible") or {},
        "feedback": (current or {}).get("feedback") or project.get("feedback") or [],
        "previous_delivery": None,
        "comparison": None,
        "delivery_history": [],
    }


def project_path(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise state.ProjectError(f"资源路径越出项目：{value}")
    return path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(root: Path, delivery_id: str) -> dict[str, Any]:
    if not isinstance(delivery_id, str) or not delivery_id.startswith("D") or not delivery_id[1:].isdigit():
        raise state.ProjectError("交付编号无效")
    manifest = state.read_json(project_path(root, f"deliveries/{delivery_id}/manifest.json"))
    if manifest.get("schema_version") != 1 or manifest.get("delivery_id") != delivery_id:
        raise state.ProjectError(f"交付 {delivery_id} 已损坏，未使用当前图片替代")
    if not isinstance(manifest.get("items"), list):
        raise state.ProjectError(f"交付 {delivery_id} 缺少图片列表；未使用当前图片替代")
    if not isinstance(manifest.get("view"), dict):
        raise state.ProjectError(f"交付 {delivery_id} 缺少冻结画面数据；未使用当前图片替代")
    return manifest


def decision_receipts(root: Path, delivery_id: str) -> list[dict[str, Any]]:
    receipts = []
    for path in sorted((root / "decisions").glob("*.json")):
        try:
            record = state.read_json(path)
            request = record.get("request") or record.get("payload") or record
            if request.get("delivery_id") == delivery_id:
                receipts.append(record.get("receipt") or record)
        except (OSError, state.ProjectError):
            continue
    return receipts


def frozen_items(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    view = manifest.get("view") or {}
    groups = [("items", manifest.get("items") or [], manifest),
              ("view/current/items", (view.get("current") or {}).get("items") or [], view.get("current") or {})]
    for index, entry in enumerate(view.get("history") or []):
        groups.append((f"view/history/{index}/items", entry.get("items") or [], entry))
    groups.extend([("view/picks", view.get("picks") or [], {}), ("view/post/images", (view.get("post") or {}).get("images") or [], {"stage": "scene", "kind": "set"})])
    result = []
    known_paths = {}
    for source, images, context in groups:
        for index, image in enumerate(images):
            item = copy.deepcopy(image)
            original = item.get("original_path") or ""
            known = known_paths.get(original) or {}
            parts = Path(original).parts
            inferred_round = parts[1] if len(parts) >= 3 and parts[0] == "rounds" else None
            round_id = item.get("round_id") or context.get("round_id") or known.get("round_id") or inferred_round
            inferred_stage = (round_id or "").split("-")[1:2]
            stage = item.get("stage") or context.get("stage") or known.get("stage") or (inferred_stage[0] if inferred_stage and inferred_stage[0] in STAGE_LABELS else None)
            subject = item.get("subject") or context.get("subject") or known.get("subject")
            if not subject and stage in state.PER_SUBJECT_STAGES and round_id:
                suffix = round_id.rsplit("-s", 1)[-1]
                subjects = (view.get("answers") or {}).get("subjects") or []
                if suffix.isdigit() and 0 < int(suffix) <= len(subjects):
                    subject = subjects[int(suffix) - 1]
            identifier = item.get("source_item_id") or known.get("id") or item.get("id")
            item.update(id=identifier, round_id=round_id, stage=stage, subject=subject,
                        kind=item.get("kind") or context.get("kind") or known.get("kind") or ("set" if item.get("slot") else None),
                        snapshot_source=f"{source}/{index}")
            result.append(item)
            if original and original not in known_paths:
                known_paths[original] = item
    return result


def verify_frozen_item(root: Path, item: dict[str, Any]) -> dict[str, Any]:
    item = copy.deepcopy(item)
    display = item.get("display_path")
    exists = bool(display) and project_path(root, display).is_file()
    intact = exists and file_sha256(project_path(root, display)) == item.get("image_sha256")
    item["exists"] = bool(intact)
    item["path"] = display if intact else None
    if not intact:
        item["disabled_reason"] = "历史快照缺失或摘要不符；没有用当前图片替换" if display or item.get("image_sha256") else item.get("disabled_reason") or item.get("reason") or "图片尚未生成；没有可展示的快照"
    return item


def delivery_summary(root: Path, manifest: dict[str, Any], *, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = {key: copy.deepcopy(manifest.get(key)) for key in
               ("delivery_id", "project_revision", "stage", "subject", "round_id", "kind", "items", "created_at", "feedback", "decisions", "status")}
    summary["path"] = f"deliveries/{manifest['delivery_id']}/index.html"
    summary["receipts"] = decision_receipts(root, manifest["delivery_id"])
    candidates = frozen_items(manifest)
    if baseline:
        sources = {match["baseline_snapshot_source"] for match in baseline.get("matches") or [] if match.get("baseline_snapshot_source")}
        candidates = [item for item in candidates if item["snapshot_source"] in sources] if sources else [item for item in candidates if item["snapshot_source"].startswith("items/")]
        summary.update({key: baseline.get(key) for key in ("round_id", "stage", "subject") if key in baseline})
    elif manifest.get("items"):
        candidates = [item for item in candidates if item["snapshot_source"].startswith("items/")]
    else:
        primary = next((prefix for prefix in ("view/picks/", "view/post/images/", "view/history/") if any(item["snapshot_source"].startswith(prefix) for item in candidates)), "")
        candidates = [item for item in candidates if primary and item["snapshot_source"].startswith(primary)]
    summary["items"] = [verify_frozen_item(root, item) for item in candidates]
    return summary


def safe_summary(root: Path, delivery_id: str, *, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return delivery_summary(root, load_manifest(root, delivery_id), baseline=baseline)
    except (state.ProjectError, OSError) as error:
        return {"delivery_id": delivery_id, "items": [], "error": str(error)}


def delivery_data(root: Path, project: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(manifest.get("view") or {})
    data.update({key: copy.deepcopy(manifest.get(key)) for key in
                 ("project_id", "delivery_id", "project_revision", "stage", "subject", "round_id", "kind", "next", "decisions", "feedback")})
    data["snapshot_at"] = manifest.get("created_at")
    data["readonly"] = project.get("schema_version") != 2
    data["stale"] = project.get("revision", 0) != manifest.get("project_revision") or (project.get("presentations") or {}).get("latest_delivery_id", manifest["delivery_id"]) != manifest["delivery_id"]
    data["live_revision"] = project.get("revision", 0)
    data["receipts"] = decision_receipts(root, manifest["delivery_id"])
    verified = delivery_summary(root, manifest)
    if data.get("current"):
        for item, frozen in zip(data["current"]["items"], verified["items"]):
            item.update({key: frozen.get(key) for key in ("exists", "path", "display_path", "disabled_reason")})
            if not item["exists"]:
                item["selectable"] = False
            original = item.get("original_path")
            if original and item.get("image_sha256"):
                original_file = project_path(root, original)
                if not original_file.is_file() or file_sha256(original_file) != item["image_sha256"]:
                    item["selectable"] = False
                    item["disabled_reason"] = "源文件已被替换或移除；仍展示旧快照，请重新交付后再采用"
    last_id = manifest.get("last_delivery_id")
    baseline_id = (manifest.get("comparison_baseline") or {}).get("delivery_id")
    data["previous_delivery"] = safe_summary(root, last_id) if last_id else None
    data["comparison"] = safe_summary(root, baseline_id, baseline=manifest["comparison_baseline"]) if baseline_id else None
    if data["comparison"]:
        data["comparison"]["basis"] = manifest["comparison_baseline"]
    data["delivery_history"] = []
    directories = sorted((root / "deliveries").glob("D*"), key=lambda path: int(path.name[1:]) if path.name[1:].isdigit() else -1, reverse=True)
    for directory in directories:
        if directory.name != manifest["delivery_id"] and directory.name[1:].isdigit() and int(directory.name[1:]) < int(manifest["delivery_id"][1:]):
            summary = safe_summary(root, directory.name)
            data["delivery_history"].append({key: value for key, value in summary.items() if key != "items"})
    if data.get("current"):
        selected = next((value.get("selected") for value in reversed(data["receipts"]) if value.get("committed")), []) or []
        for item in data["current"]["items"]:
            if item["id"] in selected:
                item["picked"] = True
    return data


def rebase_data(data: dict[str, Any], root: Path, output: Path) -> dict[str, Any]:
    def visit(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {name: visit(child, name) for name, child in value.items()}
        if isinstance(value, list):
            return [visit(child) for child in value]
        if key in {"path", "display_path"} and isinstance(value, str) and value:
            return Path(os.path.relpath(project_path(root, value), output.resolve().parent)).as_posix()
        return value
    return visit(data)


def atomic_html(output: Path, content: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".tmp-gallery-", suffix=".html", dir=output.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)


def render(data: dict[str, Any]) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    if "__GALLERY_DATA__" not in template:
        raise state.ProjectError("assets/gallery.html 缺少数据占位符")
    return template.replace("__GALLERY_CSS__", (ASSETS / "gallery.css").read_text(encoding="utf-8")).replace("__GALLERY_JS__", (ASSETS / "gallery.js").read_text(encoding="utf-8")).replace("__GALLERY_DATA__", payload)


def build_gallery(root: Path, output: Path | None = None, *, delivery_id: str | None = None) -> dict[str, Any]:
    root = root.resolve()
    output = (output or root / "gallery.html").resolve()
    if output.is_relative_to(root / "deliveries") or output == root / "project.json":
        raise state.ProjectError("build_gallery 不得覆盖不可变交付或项目状态")
    if output.suffix.lower() not in {".html", ".htm"}:
        raise state.ProjectError("画廊输出必须是 HTML，不能覆盖源图片或 JSON")
    project = state.load_project(root, writable=False)
    data = delivery_data(root, project, load_manifest(root, delivery_id)) if delivery_id else build_data(root, project)
    atomic_html(output, render(rebase_data(data, root, output)))
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 gallery.html 对比页")
    parser.add_argument("project")
    parser.add_argument("--out", help="默认写到项目目录的 gallery.html")
    parser.add_argument("--delivery", help="渲染指定交付；不创建或改写交付")
    args = parser.parse_args(argv)
    root = Path(args.project)
    try:
        from runtime_activity import execution_lease
        with execution_lease():
            out = Path(args.out) if args.out else root / "gallery.html"
            data = build_gallery(root, out, delivery_id=args.delivery)
            current = data["current"]
            return state.emit({
                "ok": True,
                "gallery": out.resolve().as_posix(),
                "current": current["heading"] if current else None,
                "missing_images": data["missing"],
                "has_post": data["post"] is not None,
                "delivery_id": data.get("delivery_id"),
                "readonly": data.get("readonly"),
                "say": "把 gallery 路径发给用户，再按页面上的三种选择问一个问题" if current else "把 gallery 路径发给用户",
            })
    except (state.ProjectError, OSError, ValueError) as error:
        return state.emit({"ok": False, "error": str(error)})


if __name__ == "__main__":
    sys.exit(main())
