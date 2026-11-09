#!/usr/bin/env python3
"""Freeze each visible delivery without advancing the business revision."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_gallery as gallery
import project as state

OPEN_STATUSES = {"open_dispatched", "opened_verified", "unavailable", "open_failed"}


def fingerprint(project: dict[str, Any]) -> str:
    business = {key: value for key, value in project.items() if key not in {"presentations", "deliveries"}}
    return hashlib.sha256(json.dumps(business, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def snapshot_image(root: Path, original: str) -> tuple[str | None, str | None]:
    source = gallery.project_path(root, original)
    if not source.is_file():
        return None, None
    directory = gallery.project_path(root, "artifacts/sha256")
    directory.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=directory)
    digest = hashlib.sha256()
    try:
        with os.fdopen(handle, "wb") as target, source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                target.write(chunk)
        image_hash = digest.hexdigest()
        suffix = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"} else ".img"
        relative = f"artifacts/sha256/{image_hash}{suffix}"
        destination = gallery.project_path(root, relative)
        if destination.exists():
            if gallery.file_sha256(destination) != image_hash:
                raise state.ProjectError(f"内容寻址对象损坏：{relative}；没有覆盖历史")
        else:
            os.replace(temporary, destination)
        return relative, image_hash
    finally:
        Path(temporary).unlink(missing_ok=True)


def freeze_view(root: Path, data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    captured: dict[str, tuple[str | None, str | None]] = {}

    def visit(value: Any) -> Any:
        if isinstance(value, list):
            return [visit(child) for child in value]
        if not isinstance(value, dict):
            return value
        frozen = {key: visit(child) for key, child in value.items()}
        original = value.get("path")
        if isinstance(original, str) and original and not original.endswith(".html"):
            if original not in captured:
                captured[original] = snapshot_image(root, original)
            display, digest = captured[original]
            if value.get("image_sha256") and digest != value["image_sha256"]:
                raise state.ProjectError("图片在生成交付时发生变化，请重新 present")
            frozen.update(original_path=original, path=display, display_path=display, image_sha256=digest, exists=bool(display))
            if not display:
                frozen.update(selectable=False, disabled_reason=value.get("disabled_reason") or "图片缺失，不能采用")
        return frozen

    return visit(data), {path: digest for path, (_, digest) in captured.items() if digest}


def comparable_baseline(root: Path, current: dict[str, Any] | None, before: str | None = None) -> dict[str, Any] | None:
    if not current:
        return None
    base_round, _, base_item = (current.get("based_on") or "").partition(":")
    directories = sorted((root / "deliveries").glob("D*"), key=lambda path: int(path.name[1:]) if path.name[1:].isdigit() else -1, reverse=True)
    for directory in directories:
        if not directory.name[1:].isdigit() or (before is not None and int(directory.name[1:]) >= int(before[1:])):
            continue
        try:
            manifest = gallery.load_manifest(root, directory.name)
            items = [gallery.verify_frozen_item(root, item) for item in gallery.frozen_items(manifest)]
            items = [item for item in items if item.get("exists") and item.get("image_sha256") and item.get("stage") == current.get("stage")]
            base = next((item for item in items if base_round and item.get("round_id") == base_round and item.get("id") == base_item), None)
            explicit = bool(base)
            if base_round and not explicit:
                continue
            matches = []
            for item in current.get("items") or []:
                subject = item.get("subject") or current.get("subject")
                if explicit:
                    if base.get("subject") != subject or (current.get("kind") == "set" and (item.get("slot") or item.get("id")) != (base.get("slot") or base.get("id"))):
                        continue
                    match = base
                else:
                    key = item.get("slot") or item.get("id")
                    match = next((prior for prior in items if prior.get("subject") == subject and prior.get("kind") == current.get("kind") and (prior.get("slot") or prior.get("id")) == key), None)
                if match:
                    matches.append({"item_id": item["id"], "baseline_item_id": match["id"], "baseline_round_id": match.get("round_id"),
                                    "baseline_snapshot_source": match["snapshot_source"], "image_sha256": match["image_sha256"]})
            if matches:
                return {"delivery_id": manifest["delivery_id"], "round_id": matches[0]["baseline_round_id"], "stage": current.get("stage"), "subject": current.get("subject"),
                        "reason": "明确的 based-on" if explicit else "同主体、同阶段、同图位的最近有效交付", "matches": matches, "explicit": explicit}
        except (OSError, state.ProjectError):
            continue
    return None


def record_open_result(root: Path, delivery_id: str, status: str, *, target: str = "", error: str = "", evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    if status not in OPEN_STATUSES:
        raise state.ProjectError("未知打开状态")
    if status == "opened_verified" and (not evidence or evidence.get("delivery_id") != delivery_id or evidence.get("images_decoded") is not True or not evidence.get("inspected_by")):
        raise state.ProjectError("opened_verified 必须有宿主核对 delivery_id 与图片解码的证据")
    receipt = {"status": status, "delivery_id": delivery_id, "updated_at": state.now(), "target": target}
    if error:
        receipt["error"] = error
    if evidence:
        receipt["evidence"] = evidence
    with state.project_lock(root):
        gallery.load_manifest(root, delivery_id)
        path = gallery.project_path(root, f"deliveries/{delivery_id}/receipts.json")
        record = state.read_json(path) if path.exists() else {"schema_version": 1, "delivery_id": delivery_id, "events": []}
        record.setdefault("events", []).append(receipt)
        record["latest"] = receipt
        state.write_json(path, record)
    return receipt


def dispatch_open(path: Path) -> dict[str, str]:
    try:
        if sys.platform == "darwin":
            command = ["open", str(path)]
        elif os.name == "nt":
            os.startfile(str(path))
            return {"status": "open_dispatched"}
        elif not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return {"status": "unavailable", "error": "没有图形会话；请使用 HTML 路径"}
        elif shutil.which("xdg-open"):
            command = ["xdg-open", str(path)]
        else:
            return {"status": "unavailable", "error": "没有可用的系统打开命令"}
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        if result.returncode:
            return {"status": "open_failed", "error": result.stderr.strip() or f"系统打开退出码 {result.returncode}"}
        return {"status": "open_dispatched"}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"status": "open_failed", "error": str(error)}


def present_round(root: Path, *, open_browser: bool = True, output: Path | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    output = (output or root / "gallery.html").resolve()
    if output.is_relative_to(root / "deliveries") or output == root / "project.json":
        raise state.ProjectError("当前入口不能覆盖不可变交付或项目状态")
    if output.suffix.lower() not in {".html", ".htm"}:
        raise state.ProjectError("交付输出必须是 HTML，不能覆盖源图片或 JSON")
    initial = state.load_project(root, writable=False)
    if initial.get("schema_version") != state.SCHEMA_VERSION:
        raise state.ProjectError("旧项目只读；先明确迁移，再创建交付。可用 build_gallery 只读查看")
    with state.project_lock(root):
        project = state.load_project(root, writable=False)
        if not project.get("project_id"):
            raise state.ProjectError("当前项目缺少 project_id")
        started = fingerprint(project)
    view, hashes = freeze_view(root, gallery.build_data(root, copy.deepcopy(project), prefer_latest=False))
    baseline = comparable_baseline(root, view.get("current"))
    with state.project_lock(root):
        latest = state.load_project(root, writable=False)
        if fingerprint(latest) != started or any(gallery.file_sha256(gallery.project_path(root, path)) != digest for path, digest in hashes.items()):
            raise state.ProjectError("项目或图片在生成交付时已变化；未发布混合版本，请重新 present")
        numbers = [int(path.name[1:]) for path in (root / "deliveries").glob("D*") if path.name[1:].isdigit()]
        for entry in latest.get("deliveries") or []:
            identifier = entry if isinstance(entry, str) else entry.get("delivery_id", "")
            if identifier.startswith("D") and identifier[1:].isdigit():
                numbers.append(int(identifier[1:]))
        delivery_id = f"D{max(numbers, default=0) + 1:04d}"
        directory = gallery.project_path(root, f"deliveries/{delivery_id}")
        directory.mkdir(parents=True, exist_ok=False)
        current = view.get("current") or {}
        items = []
        for item in current.get("items") or []:
            frozen = copy.deepcopy(item)
            frozen["method"] = item.get("method_code", "generate")
            items.append(frozen)
        manifest = {"schema_version": 1, "project_id": latest["project_id"], "delivery_id": delivery_id,
                    "project_revision": latest.get("revision", 0), "stage": current.get("stage") or latest.get("stage"),
                    "subject": current.get("subject"), "round_id": current.get("round_id"),
                    "kind": current.get("kind") or ("post" if view.get("post") else "summary"),
                    "created_at": state.now(), "items": items, "last_delivery_id": (latest.get("presentations") or {}).get("latest_delivery_id"),
                    "comparison_baseline": baseline, "next": view.get("next"),
                    "decisions": view.get("decisions") or [], "feedback": view.get("feedback") or [],
                    "keep": current.get("keep") or [], "status": "missing" if view["missing"] else current.get("status") or latest.get("stage"), "view": view}
        latest.setdefault("presentations", {})["latest_delivery_id"] = delivery_id
        data = gallery.delivery_data(root, latest, manifest)
        manifest["view"].update({key: copy.deepcopy(data[key]) for key in ("previous_delivery", "comparison", "delivery_history")})
        state.atomic_json(directory / "manifest.json", manifest)
        gallery.atomic_html(directory / "index.html", gallery.render(gallery.rebase_data(data, root, directory / "index.html")))
        index = {"delivery_id": delivery_id, "path": f"deliveries/{delivery_id}/manifest.json", "project_revision": manifest["project_revision"], "created_at": manifest["created_at"]}
        if isinstance(latest.get("deliveries"), dict):
            latest["deliveries"][delivery_id] = index
        else:
            latest.setdefault("deliveries", []).append(index)
        state.write_json(root / "project.json", latest)
        gallery.atomic_html(output, gallery.render(gallery.rebase_data(data, root, output)))
    opened = dispatch_open(output) if open_browser else {"status": "unavailable", "error": "未请求系统打开；页面与路径已保留"}
    receipt = record_open_result(root, delivery_id, opened["status"], target=str(output), error=opened.get("error", ""))
    return {"ok": True, "delivery_id": delivery_id, "project_revision": manifest["project_revision"], "gallery": str(output),
            "manifest": str(directory / "manifest.json"), "snapshot": str(directory / "index.html"), "open": receipt,
            "missing_images": view["missing"], "say": "打开工作台核对本轮图片；页面确认后回聊天回复「继续」"}


present = present_round


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="保存不可变交付、渲染工作台并尝试系统打开")
    parser.add_argument("project")
    parser.add_argument("--out")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    try:
        from runtime_activity import execution_lease
        with execution_lease():
            return state.emit(present_round(Path(args.project), open_browser=not args.no_open, output=Path(args.out) if args.out else None))
    except (state.ProjectError, OSError, ValueError) as error:
        return state.emit({"ok": False, "error": str(error)})


if __name__ == "__main__":
    sys.exit(main())
