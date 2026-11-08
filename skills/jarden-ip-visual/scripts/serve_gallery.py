#!/usr/bin/env python3
"""仅在本机提供注册交付资源与项目核心选择接口。"""

from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import project as state


def resources(root: Path) -> set[str]:
    allowed = {"gallery.html"}
    project = state.load_project(root)
    for entry in project.get("deliveries") or []:
        delivery_id = entry if isinstance(entry, str) else entry.get("delivery_id") or entry.get("id")
        if not isinstance(delivery_id, str) or not re.fullmatch(r"D\d+", delivery_id):
            continue
        directory = root / "deliveries" / delivery_id
        if not (directory / "manifest.json").is_file():
            continue
        allowed.add(f"deliveries/{delivery_id}/index.html")
        manifest = state.read_json(directory / "manifest.json")
        def collect(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"display_path", "path"} and isinstance(child, str) and child.startswith("artifacts/sha256/"):
                        allowed.add(child)
                    else:
                        collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        collect(manifest)
    return allowed


def server_for(root: Path, token: str, port: int = 0) -> ThreadingHTTPServer:
    root = root.resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format_value, *args):
            return

        def json_response(self, status: int, payload: dict) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(encoded)

        def host_valid(self) -> bool:
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def authorized(self) -> bool:
            expected = "Bearer " + token
            origin = self.headers.get("Origin")
            expected_origin = f"http://127.0.0.1:{self.server.server_port}"
            return self.host_valid() and hmac.compare_digest(self.headers.get("Authorization", ""), expected) and (origin is None or origin == expected_origin) and self.headers.get("Sec-Fetch-Site", "same-origin") in {"same-origin", "none"}

        def do_GET(self) -> None:
            if not self.host_valid():
                self.json_response(403, {"ok": False, "error": "invalid_host"})
                return
            route = urlsplit(self.path).path
            if route.startswith("/api/"):
                if not self.authorized():
                    self.json_response(403, {"ok": False, "error": "unauthorized_origin_or_token"})
                    return
                try:
                    with state.project_lock(root):
                        if route == "/api/state":
                            project = state.load_project(root)
                            latest = (project.get("presentations") or {}).get("latest_delivery_id")
                            manifest = state.read_json(root / "deliveries" / latest / "manifest.json") if latest else None
                            payload = state.status_payload(project, root=root)
                            payload.update(latest_delivery_id=latest, delivery_id=latest, manifest=manifest, receipts=project.get("decision_receipts") or {})
                        elif route.startswith("/api/receipt/"):
                            request_id = route.removeprefix("/api/receipt/")
                            if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request_id):
                                raise state.ProjectError("invalid_request_id")
                            result = state.receipt(root, request_id)
                            payload = result or {"ok": False, "committed": False, "error": "receipt_not_found"}
                        else:
                            self.json_response(404, {"ok": False, "error": "unknown_endpoint"})
                            return
                    self.json_response(200 if payload.get("ok") else 404, payload)
                except (state.ProjectError, OSError, ValueError) as error:
                    self.json_response(409, {"ok": False, "error": str(error)})
                return
            relative = unquote(route).lstrip("/") or "gallery.html"
            try:
                if "\\" in relative or relative not in resources(root):
                    raise state.ProjectError("unregistered_resource")
                target = state.project_path(root, relative)
                data = target.read_bytes()
            except (state.ProjectError, OSError, ValueError):
                self.json_response(404, {"ok": False, "error": "resource_not_found"})
                return
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self' data:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            if not self.authorized():
                self.json_response(403, {"ok": False, "error": "unauthorized_origin_or_token"})
                return
            if urlsplit(self.path).path != "/api/decision":
                self.json_response(404, {"ok": False, "error": "unknown_endpoint"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536 or self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                    raise state.ProjectError("invalid_body")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                result = state.submit_decision(root, payload)
                if state.receipt(root, result["request_id"]) != result:
                    raise state.ProjectError("receipt_readback_failed")
                self.json_response(200, result)
            except (state.ProjectError, OSError, ValueError) as error:
                self.json_response(409, {"ok": False, "committed": False, "error": str(error)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="启动本机工作台，或有界等待真实选择")
    parser.add_argument("project")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ready-file")
    parser.add_argument("--idle-timeout", type=float, default=1800)
    parser.add_argument("--wait-choice", action="store_true")
    parser.add_argument("--after-revision", type=int)
    parser.add_argument("--timeout", type=float, default=45)
    args = parser.parse_args()
    root = Path(args.project).resolve()
    try:
        from runtime_activity import execution_lease
        with execution_lease():
            project = state.load_project(root)
            if args.wait_choice:
                revision = args.after_revision if args.after_revision is not None else project.get("revision", 0)
                deadline = time.monotonic() + max(0, min(args.timeout, 45))
                while time.monotonic() < deadline:
                    current = state.load_project(root)
                    if current.get("revision", 0) > revision:
                        return state.emit({"ok": True, "status": "changed", **state.status_payload(current, root=root)})
                    time.sleep(0.2)
                return state.emit({"ok": True, "status": "awaiting-user", "revision": revision})
            token = secrets.token_urlsafe(32)
            server = server_for(root, token, args.port)
            info = {"ok": True, "pid": __import__("os").getpid(), "url": f"http://127.0.0.1:{server.server_port}/gallery.html#token={token}", "origin": f"http://127.0.0.1:{server.server_port}", "project": str(root), "automatic_generation": False}
            if args.ready_file:
                ready = Path(args.ready_file)
                state.atomic_json(ready, info)
                ready.chmod(0o600)
            print(json.dumps(info, ensure_ascii=False), flush=True)
            timer = threading.Timer(max(1, args.idle_timeout), server.shutdown)
            timer.daemon = True
            timer.start()
            try:
                server.serve_forever(poll_interval=0.2)
            except KeyboardInterrupt:
                pass
            finally:
                timer.cancel()
                server.server_close()
            return 0
    except (state.ProjectError, OSError, ValueError) as error:
        return state.emit({"ok": False, "error": str(error)})


if __name__ == "__main__":
    raise SystemExit(main())
