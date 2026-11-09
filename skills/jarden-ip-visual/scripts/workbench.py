#!/usr/bin/env python3
"""Resume local state and immutable presentations without choosing or generating."""

from __future__ import annotations

import argparse
import contextlib
import copy
import http.client
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit
import uuid

import build_gallery as gallery
import generation_tasks
import present_round as presentation
import project as state
import runtime_activity


SERVER_READY_TIMEOUT = 8.0
SERVER_TTL = 1800
HEALTH_TIMEOUT = 1.5
MAX_HEALTH_BYTES = 1024 * 1024
MAX_SERVER_ERROR_BYTES = 8192


def redact_server_error(detail: str) -> str:
    return re.sub(r"(?i)(token(?:=|%3d)|Bearer\s+)[A-Za-z0-9_%+-]+", r"\1[redacted]", detail)


def server_failure_detail(path: Path) -> str:
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - MAX_SERVER_ERROR_BYTES))
        detail = stream.read(MAX_SERVER_ERROR_BYTES).decode("utf-8", errors="replace")
    return " ".join(redact_server_error(detail).split())


def workbench_directory(root: Path) -> Path:
    directory = root / ".workbench"
    if directory.is_symlink():
        raise state.ProjectError("unsafe_workbench：工作台目录不能是符号链接")
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory


def source_hashes(root: Path, project: dict[str, Any]) -> dict[str, str | None]:
    paths: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"path", "image"} and isinstance(child, str) and child:
                    paths.add(child)
                elif key in {"sources", "refs", "references"}:
                    references(child)
                else:
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    def references(value: Any) -> None:
        if isinstance(value, str) and value:
            paths.add(value)
        elif isinstance(value, list):
            for child in value:
                references(child)
        elif isinstance(value, dict):
            if "path" in value:
                collect(value)
            else:
                for child in value.values():
                    references(child)

    collect({key: value for key, value in project.items() if key not in {"presentations", "deliveries", "decision_receipts"}})
    for entry in project.get("rounds") or []:
        relative = entry["dir"] + "/round.json"
        paths.add(relative)
        round_file = state.project_path(root, relative)
        if round_file.is_file():
            plan = state.read_json(round_file)
            for item in plan.get("items") or []:
                paths.add(entry["dir"] + "/" + item["image"])
                collect({key: value for key, value in item.items() if key != "image"})
            references(plan.get("refs") or {})
    base = state.project_path(root, "reviews")
    paths.update(path.relative_to(root).as_posix() for path in base.rglob("*.json"))
    post = project.get("post") or {}
    if post:
        paths.add((post.get("path") or "post") + "/manifest.json")
        paths.add("copy.json")
    return {relative: gallery.file_sha256(path) if path.is_file() else None
            for relative in sorted(paths) for path in [state.project_path(root, relative)]}


def snapshots_intact(root: Path, value: Any) -> bool:
    if isinstance(value, list):
        return all(snapshots_intact(root, child) for child in value)
    if not isinstance(value, dict):
        return True
    display = value.get("display_path")
    if display:
        path = state.project_path(root, display)
        if not path.is_file() or gallery.file_sha256(path) != value.get("image_sha256"):
            return False
    return all(snapshots_intact(root, child) for child in value.values())


def ensure_presentation(root: Path) -> dict[str, Any]:
    with state.project_lock(root):
        project = state.load_project(root)
        directory = workbench_directory(root)
        cache_file = directory / "presentation.json"
        if cache_file.is_symlink():
            raise state.ProjectError("unsafe_workbench：交付恢复记录不能是符号链接")
        fingerprint = presentation.fingerprint(project)
        sources = source_hashes(root, project)
        latest = (project.get("presentations") or {}).get("latest_delivery_id")
        reusable = False
        try:
            cached = state.read_json(cache_file)
            manifest = gallery.load_manifest(root, latest)
            manifest_file = state.project_path(root, f"deliveries/{latest}/manifest.json")
            snapshot = state.project_path(root, f"deliveries/{latest}/index.html")
            reusable = (
                manifest.get("project_id") == project["project_id"]
                and manifest.get("project_revision") == project.get("revision", 0)
                and cached.get("project_id") == project["project_id"]
                and cached.get("delivery_id") == latest
                and cached.get("project_revision") == manifest["project_revision"]
                and cached.get("project_fingerprint") == fingerprint
                and cached.get("source_hashes") == sources
                and cached.get("manifest_sha256") == gallery.file_sha256(manifest_file)
                and snapshot.is_file()
                and cached.get("snapshot_sha256") == gallery.file_sha256(snapshot)
                and manifest.get("next") == gallery.gallery_next(root, copy.deepcopy(project))
                and snapshots_intact(root, manifest.get("view"))
            )
        except (state.ProjectError, OSError, ValueError):
            pass
        if reusable:
            gallery.build_gallery(root, delivery_id=latest)
            return {"delivery_id": latest, "project_revision": manifest["project_revision"],
                    "gallery": str(root / "gallery.html"), "manifest": str(manifest_file),
                    "snapshot": str(snapshot), "reused": True}
        result = presentation.present_round(root, open_browser=False)
        current = state.load_project(root)
        if presentation.fingerprint(current) != fingerprint or source_hashes(root, current) != sources:
            raise state.ProjectError("presentation_changed：项目或源文件变化，请恢复工作台")
        state.write_json(cache_file, {
            "schema_version": 1, "project_id": current["project_id"], "delivery_id": result["delivery_id"],
            "project_revision": current.get("revision", 0), "project_fingerprint": fingerprint,
            "source_hashes": sources, "manifest_sha256": gallery.file_sha256(Path(result["manifest"])),
            "snapshot_sha256": gallery.file_sha256(Path(result["snapshot"])),
        })
        return {**result, "reused": False}


def read_server_info(path: Path) -> dict[str, Any]:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise state.ProjectError("unsafe_server_record")
    return state.read_json(path)


def healthy_server(root: Path, project_id: str, info: dict[str, Any], *,
                   timeout: float = HEALTH_TIMEOUT, expected_pid: int | None = None) -> bool:
    connection = None
    try:
        process_id = info.get("pid")
        if type(process_id) is not int or process_id <= 0 or expected_pid is not None and process_id != expected_pid:
            return False
        if info.get("project") != str(root) or info.get("project_id", project_id) != project_id:
            return False
        expires = info.get("expires_at")
        if expires is not None and (type(expires) not in (int, float) or not time.time() < expires):
            return False
        parsed = urlsplit(info["url"])
        fragment = parse_qs(parsed.fragment, strict_parsing=True)
        token_values = fragment.get("token") or []
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
                or parsed.username is not None or parsed.password is not None
                or parsed.path != "/gallery.html" or parsed.query
                or set(fragment) != {"token"} or len(token_values) != 1
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", token_values[0])):
            return False
        origin = f"http://127.0.0.1:{parsed.port}"
        if parsed.netloc != f"127.0.0.1:{parsed.port}" or info.get("origin", origin) != origin:
            return False
        os.kill(process_id, 0)
        connection = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=timeout)
        connection.request("GET", "/api/state", headers={"Authorization": "Bearer " + token_values[0]})
        response = connection.getresponse()
        if response.status != 200:
            return False
        encoded = response.read(MAX_HEALTH_BYTES + 1)
        if len(encoded) > MAX_HEALTH_BYTES:
            return False
        payload = json.loads(encoded)
        return isinstance(payload, dict) and payload.get("ok") is True and payload.get("project_id") == project_id
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        return False
    finally:
        if connection is not None:
            connection.close()


@contextlib.contextmanager
def server_lock(directory: Path):
    path = directory / "server.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "r+b") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise state.ProjectError("unsafe_server_lock")
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        runtime_activity.acquire_gate(stream, HEALTH_TIMEOUT)
        try:
            yield
        finally:
            runtime_activity.release_gate(stream)


def stop_created_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=0.1)


def ensure_server(root: Path, project_id: str) -> dict[str, Any]:
    directory = workbench_directory(root)
    with server_lock(directory):
        cache = directory / "server.json"
        try:
            existing = read_server_info(cache)
            if healthy_server(root, project_id, existing):
                return {**existing, "status": "ready", "reused": True, "created": False}
        except (state.ProjectError, OSError, ValueError):
            pass
        if cache.is_symlink():
            raise state.ProjectError("unsafe_server_record")
        ready_file = directory / f".ready-{uuid.uuid4().hex}.json"
        stderr_file = directory / f".server-{uuid.uuid4().hex}.stderr.log"
        process = None
        keep = False
        deadline = time.monotonic() + SERVER_READY_TIMEOUT
        try:
            descriptor = os.open(stderr_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(descriptor, "wb") as stderr:
                process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).with_name("serve_gallery.py")), str(root),
                     "--ready-file", str(ready_file), "--idle-timeout", str(SERVER_TTL)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr,
                    close_fds=True,
                )
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    info = read_server_info(ready_file)
                    remaining = deadline - time.monotonic()
                    if remaining > 0 and healthy_server(root, project_id, info, timeout=min(HEALTH_TIMEOUT, remaining), expected_pid=process.pid):
                        info.update(project_id=project_id, expires_at=time.time() + SERVER_TTL,
                                    idle_timeout=SERVER_TTL, stderr_log=str(stderr_file))
                        state.atomic_json(cache, info)
                        cache.chmod(0o600)
                        keep = True
                        return {**info, "status": "ready", "reused": False, "created": True}
                except (state.ProjectError, OSError, ValueError):
                    pass
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(0.04, remaining))
            returncode = process.poll()
            reason = f"child_exit={returncode}" if returncode is not None else "startup_deadline"
            detail = server_failure_detail(stderr_file)
            raise state.ProjectError(f"server_not_ready：{reason}; ready_file={ready_file.exists()}; stderr_log={stderr_file}; {detail}")
        finally:
            if process is not None and not keep:
                stop_created_process(process)
            ready_file.unlink(missing_ok=True)


def task_statuses(root: Path) -> list[dict[str, Any]]:
    results = []
    for path in sorted((root / "tasks").glob("gen-*.json")):
        result = generation_tasks.resume_status(root, path.stem)
        results.append({key: result.get(key) for key in
                        ("task_id", "status", "resume_status", "action", "should_call", "outputs_valid")})
    return results


def _run_workbench(root: Path, *, serve: bool, open_browser: bool, record_ready: bool) -> dict[str, Any]:
    root = root.expanduser().resolve()
    initial = state.load_project(root)
    legacy = initial.get("schema_version") != state.SCHEMA_VERSION
    recorded = False
    if legacy:
        data = gallery.build_gallery(root)
        presented = {"gallery": str(root / "gallery.html"), "delivery_id": None, "reused": False, "readonly": True}
        current = initial
        tasks = []
    else:
        with state.project_lock(root):
            current = state.load_project(root, writable=True)
            action = state.next_action(copy.deepcopy(current), root)
            if record_ready and action.get("action") == "record-round":
                state._invoke(state.cmd_record_round, argparse.Namespace(
                    project=str(root), round=action["round"], allow_missing=False,
                ))
                recorded = True
        presented = ensure_presentation(root)
        with state.project_lock(root):
            current = state.load_project(root)
            data = state.status_payload(copy.deepcopy(current), root=root)
            tasks = task_statuses(root)
    next_step = state.next_action(copy.deepcopy(current), root) if legacy else data["next"]
    project_next = copy.deepcopy(next_step)
    uncertain = [task["task_id"] for task in tasks if task["resume_status"] in {"unknown", "output_invalid"}]
    if uncertain and next_step.get("action") == "generate":
        next_step = {"action": "reconcile-task", "task_ids": uncertain,
                     "say": "原生生图结果尚未确认；先核对现有工具回执和实际文件，不再次调用生图"}
    server = {"status": "not_requested", "reused": False, "created": False}
    target = presented["gallery"]
    if serve and not legacy:
        try:
            server = ensure_server(root, current["project_id"])
            target = server["url"]
        except (state.ProjectError, OSError, ValueError) as error:
            server = {"status": "unavailable", "reused": False, "created": False,
                      "error": "server_unavailable：已保留静态工作台；新选择尚未提交",
                      "reason": redact_server_error(str(error))}
    elif serve:
        server["status"] = "readonly"
    opened = presentation.dispatch_open(target) if open_browser else {
        "status": "unavailable", "error": "未请求系统打开；页面入口已返回",
    }
    if opened.get("status") == "opened_verified":
        opened = {"status": "open_dispatched"}
    if open_browser and presented.get("delivery_id"):
        opened = presentation.record_open_result(root, presented["delivery_id"], opened["status"],
                                                 target=target, error=opened.get("error", ""))
    receipts = {key: value for key, value in (current.get("decision_receipts") or {}).items()
                if value.get("committed") is True}
    last_receipt = max(receipts.values(), key=lambda receipt: (receipt.get("new_revision", -1), receipt.get("request_id", "")), default=None)
    interactive = server["status"] == "ready"
    return {"ok": True, "project": str(root), "project_id": current.get("project_id"),
            "schema_version": current["schema_version"], "revision": current.get("revision", 0),
            "next": next_step, "project_next": project_next, "recorded": recorded,
            "presentation": presented, "delivery_id": presented.get("delivery_id"), "gallery": presented["gallery"],
            "url": server.get("url"), "server": server, "open": {**opened, "target": target},
            "interaction": {"mode": "interactive" if interactive else "static",
                            "status": "ready" if interactive else "not_submitted",
                            "say": "新选择需提交并读取 committed 回执" if interactive else "静态页面的新选择尚未提交；请回聊天明确确认"},
            "committed_receipt": last_receipt, "receipts": receipts,
            "generation_tasks": tasks, "automatic_generation": False, "should_call": False}


def run_workbench(root: Path, *, serve: bool = False, open_browser: bool = True,
                  record_ready: bool = False) -> dict[str, Any]:
    """Resume one project under an execution lease, returning a JSON-compatible result."""
    with runtime_activity.execution_lease():
        return _run_workbench(Path(root), serve=serve, open_browser=open_browser, record_ready=record_ready)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="恢复已有工作台、交付与回执；不自动选择或生图")
    parser.add_argument("project")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--record-ready", action="store_true")
    args = parser.parse_args(argv)
    try:
        with runtime_activity.execution_lease():
            try:
                result = _run_workbench(Path(args.project), serve=args.serve,
                                        open_browser=not args.no_open, record_ready=args.record_ready)
            except (state.ProjectError, OSError, ValueError) as error:
                result = {"ok": False, "error": str(error), "automatic_generation": False, "should_call": False}
            return state.emit(result)
    except (state.ProjectError, OSError, ValueError) as error:
        return state.emit({"ok": False, "error": str(error), "automatic_generation": False, "should_call": False})


if __name__ == "__main__":
    raise SystemExit(main())
