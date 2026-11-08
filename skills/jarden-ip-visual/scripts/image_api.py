#!/usr/bin/env python3
"""兼容 Images 接口的可选后端。普通设置在用户目录，密钥只从运行时环境读取。"""

from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack, contextmanager
import hashlib
import io
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import imaging
import project as state

KEY_ENV = "JARDEN_IP_VISUAL_IMAGE_API_KEY"
MAX_RESPONSE = 80 * 1024 * 1024


def config_home() -> Path:
    override = os.environ.get("JARDEN_IP_VISUAL_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "jarden-ip-visual"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "jarden-ip-visual"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "jarden-ip-visual"


def clean_url(value: str, base: bool = False) -> str:
    parsed = urllib.parse.urlsplit(value)
    local = parsed.hostname in ("127.0.0.1", "::1", "localhost")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise state.ProjectError("接口和下载地址需要 HTTPS；本机测试接口可以用 HTTP")
    if not parsed.netloc or parsed.username or parsed.password or (base and (parsed.query or parsed.fragment)):
        raise state.ProjectError("接口地址不能含账号、密码、查询参数或片段；凭据通过系统凭据库配置")
    return value.rstrip("/") if base else value


def configuration() -> dict[str, Any]:
    path = config_home() / "settings.json"
    if not path.is_file():
        raise state.ProjectError("尚未设置图片接口；先运行 configure --base-url <地址含/v1> --model <模型>")
    config = state.read_json(path)
    clean_url(config.get("base_url", ""), base=True)
    if not config.get("model"):
        raise state.ProjectError("settings.json 缺少 model")
    return config


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def signature(plan: dict[str, Any]) -> str:
    payload = {key: value for key, value in plan.items() if key != "sha256"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def cmd_configure(args: argparse.Namespace) -> int:
    path = config_home() / "settings.json"
    config = state.read_json(path) if path.is_file() else {}
    config.update({"schema_version": 1, "base_url": clean_url(args.base_url, base=True), "model": args.model})
    if not re.fullmatch(r"[A-Za-z0-9._:/-]+", args.model):
        raise state.ProjectError("model 只能含模型标识字符")
    if args.dry_run:
        return state.emit({"ok": True, "dry_run": True, "settings": config, "path": path.as_posix()})
    state.write_json(path, config)
    return state.emit({"ok": True, "path": path.as_posix(), "settings": config})


def cmd_status(args: argparse.Namespace) -> int:
    config = configuration()
    return state.emit({"ok": True, "settings": config, "key_from_environment": bool(os.environ.get(KEY_ENV)), "say": "系统凭据库的状态用凭据组件的 status image-api 查询"})


def cmd_plan(args: argparse.Namespace) -> int:
    config = configuration()
    prompt_file = Path(args.prompt_file)
    prompt = prompt_file.read_text(encoding="utf-8").strip()
    if len(prompt) < 40:
        raise state.ProjectError("提示词至少 40 字；写清主体、材质、场景、构图和参考图约束")
    if not 1 <= args.n <= 5:
        raise state.ProjectError("--n 必须是 1 到 5；不同方向用不同提示词和单独计划")
    references = []
    for value in args.reference or []:
        path = Path(value).resolve()
        imaging.open_image(path)
        references.append({"path": path.as_posix(), "sha256": digest(path)})
    out = Path(args.out).resolve()
    if out.suffix.lower() != ".png":
        raise state.ProjectError("--out 必须是 .png")
    outputs = [out.as_posix()] if args.n == 1 else [out.with_name(f"{out.stem}-{index + 1}{out.suffix}").as_posix() for index in range(args.n)]
    plan = {
        "schema_version": 1,
        "endpoint": config["base_url"] + ("/images/edits" if references else "/images/generations"),
        "model": config["model"],
        "prompt": prompt,
        "references": references,
        "size": args.size,
        "quality": args.quality,
        "background": args.background,
        "n": args.n,
        "outputs": outputs,
    }
    plan["sha256"] = signature(plan)
    path = Path(args.plan)
    if path.exists():
        previous = state.read_json(path)
        if previous != plan:
            raise state.ProjectError("计划文件已存在且不同；换一个计划文件名，保留旧计划")
    else:
        state.write_json(path, plan)
    return state.emit({"ok": True, "plan": path.resolve().as_posix(), "sha256": plan["sha256"], "endpoint": plan["endpoint"], "model": plan["model"], "outputs": outputs, "references": len(references), "say": "先 run --plan <计划> --dry-run 校验；实际 run 会把提示词和参考图发给该服务，可能收费"})


def validate_plan(plan: dict[str, Any], resume: bool = False) -> None:
    if plan.get("schema_version") != 1 or plan.get("sha256") != signature(plan):
        raise state.ProjectError("计划被修改或版本不支持；重新创建计划并核对")
    clean_url(plan["endpoint"], base=True)
    if not isinstance(plan.get("n"), int) or not 1 <= plan["n"] <= 5 or len(plan.get("outputs") or []) != plan["n"]:
        raise state.ProjectError("计划里的图片数量不合法")
    outputs = plan["outputs"]
    if not all(isinstance(value, str) and Path(value).is_absolute() and Path(value).suffix.lower() == ".png" for value in outputs):
        raise state.ProjectError("计划里的输出必须是 PNG 的绝对路径")
    if len({os.path.normcase(str(Path(value).resolve())) for value in outputs}) != len(outputs):
        raise state.ProjectError("计划里的输出路径不能重复")
    if resume:
        return
    for reference in plan.get("references") or []:
        path = Path(reference["path"])
        if not path.is_file() or digest(path) != reference["sha256"]:
            raise state.ProjectError("参考图已变化或缺失；重新创建计划")
    for output in plan["outputs"]:
        if Path(output).exists():
            raise state.ProjectError(f"输出已存在，未再次调用付费接口：{output}")


def receipt_path(plan_file: Path) -> Path:
    return plan_file.with_name(plan_file.stem + ".receipt.json")


def response_path(plan_file: Path) -> Path:
    return plan_file.with_name(plan_file.stem + ".response.json")


def output_claim(output: str) -> Path:
    path = Path(output).resolve()
    return path.with_name(f".{path.name}.image-api.claim.json")


def save_progress(path: Path, data: dict[str, Any]) -> None:
    state.write_json(path, data)
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


@contextmanager
def exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "r+b") as stream:
        try:
            if sys.platform == "win32":
                import msvcrt

                if not os.fstat(stream.fileno()).st_size:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise state.ProjectError("同一请求或输出正在被其他进程处理；本次没有发送生成请求") from None
        try:
            yield
        finally:
            if sys.platform == "win32":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def operation_locks(plan_file: Path, plan: dict[str, Any]):
    request = {name: plan.get(name) for name in ("endpoint", "model", "prompt", "size", "quality", "background", "n")}
    request["references"] = [reference["sha256"] for reference in plan.get("references") or []]
    request_hash = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    paths = [config_home() / "locks" / f"image-request-{request_hash}.lock"]
    paths.append(plan_file.with_name(f".{plan_file.name}.lock"))
    paths.extend(output_claim(output).with_suffix(".lock") for output in plan["outputs"])
    with ExitStack() as stack:
        for path in sorted(set(paths), key=lambda value: os.path.normcase(str(value.resolve()))):
            stack.enter_context(exclusive_lock(path))
        yield


def claim_outputs(plan_file: Path, plan: dict[str, Any]) -> None:
    if receipt_path(plan_file).exists() or response_path(plan_file).exists():
        raise state.ProjectError("已有调用回执或生成结果；用 resume --plan <计划> 仅恢复图片，不要重复生成")
    for output in plan["outputs"]:
        if output_claim(output).exists():
            raise state.ProjectError("输出已被其他计划认领；先核对原计划回执，用原计划 resume，未再次发送生成请求")
    for output in plan["outputs"]:
        claim = output_claim(output)
        descriptor = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"plan_sha256": plan["sha256"], "receipt": str(receipt_path(plan_file))}, stream)
            stream.flush()
            os.fsync(stream.fileno())


def saved_response(raw: bytes, plan: dict[str, Any], key: str) -> dict[str, Any]:
    try:
        response = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise state.ProjectError("返回内容不是 JSON；核对服务端记录，不自动重复调用") from None
    items = response.get("data") if isinstance(response, dict) else None
    if not isinstance(items, list) or len(items) != plan["n"]:
        raise state.ProjectError("接口未返回预期数量的 data 图片；异步任务型接口需要专用适配器，先保留调用回执并核对服务端任务")
    results = []
    for item in items:
        if not isinstance(item, dict):
            raise state.ProjectError("接口返回的图片条目不是对象；核对服务端记录，不自动重复调用")
        field = "b64_json" if item.get("b64_json") else "url"
        value = item.get(field)
        if not isinstance(value, str) or not value:
            raise state.ProjectError("接口返回图片缺少 b64_json 或 url")
        if key and key in urllib.parse.unquote(value):
            raise state.ProjectError("生成结果含运行时凭据，未保存敏感字段；先核对服务端记录，不自动重复调用")
        results.append({field: value})
    result = {"schema_version": 1, "plan_sha256": plan["sha256"], "received_at": state.now(), "data": results}
    identifier = response.get("id")
    if isinstance(identifier, (str, int)) and key not in str(identifier):
        result["request_id"] = identifier
    return result


def save_outputs(plan_file: Path, plan: dict[str, Any], receipt: dict[str, Any], response: dict[str, Any], timeout: int) -> int:
    from PIL import Image

    receipt_file = receipt_path(plan_file)
    images = receipt.setdefault("images", [{"path": output, "state": "pending"} for output in plan["outputs"]])
    if not isinstance(images, list) or len(images) != plan["n"] or any(
        not isinstance(image, dict) or image.get("path") != output for image, output in zip(images, plan["outputs"])
    ):
        raise state.ProjectError("回执中的输出与计划不一致，未恢复或生成图片")
    receipt.update(state="saving", response_sha256=digest(response_path(plan_file)))
    save_progress(receipt_file, receipt)
    try:
        for record, item in zip(images, response["data"]):
            out = Path(record["path"])
            if out.exists():
                if not record.get("sha256") or digest(out) != record["sha256"]:
                    raise state.ProjectError("已有输出与回执不一致；保留文件并先核对，未覆盖或重新生成")
                imaging.open_image(out)
            else:
                if item.get("b64_json"):
                    try:
                        image_bytes = base64.b64decode(item["b64_json"], validate=True)
                    except ValueError:
                        raise state.ProjectError("接口返回的图片不是有效的 base64") from None
                else:
                    url = clean_url(item["url"])
                    image_bytes = request_bytes(urllib.request.Request(url, method="GET"), timeout)
                try:
                    image = Image.open(io.BytesIO(image_bytes))
                    image.load()
                except OSError:
                    raise state.ProjectError("接口返回的文件不是可读取的图片") from None
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                record.update(state="ready", sha256=hashlib.sha256(buffer.getvalue()).hexdigest(), size=list(image.size))
                save_progress(receipt_file, receipt)
                imaging.save_png(image, out)
                if digest(out) != record["sha256"]:
                    raise state.ProjectError("保存结果与预期图片不一致；保留文件并先核对")
            record["state"] = "saved"
            receipt["outputs"] = [{name: value[name] for name in ("path", "sha256", "size")} for value in images if value.get("state") == "saved"]
            save_progress(receipt_file, receipt)
    except (state.ProjectError, imaging.ImagingError, OSError) as error:
        receipt["state"] = "partial"
        save_progress(receipt_file, receipt)
        raise state.ProjectError(f"{error}；成功响应已保留，用 resume --plan <计划> 仅恢复图片，不会重新 POST") from None
    receipt.update(state="completed", completed_at=state.now())
    save_progress(receipt_file, receipt)
    return state.emit({"ok": True, "receipt": receipt_file.as_posix(), "outputs": receipt["outputs"]})


def multipart(plan: dict[str, Any]) -> tuple[bytes, str]:
    boundary = "jarden-" + uuid.uuid4().hex
    chunks = []
    fields = {key: plan[key] for key in ("model", "prompt", "size", "quality", "background", "n") if plan.get(key) is not None}
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8"))
    for index, reference in enumerate(plan.get("references") or []):
        path = Path(reference["path"])
        suffix = path.suffix.lower() or ".png"
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"image[]\"; filename=\"ref-{index + 1}{suffix}\"\r\nContent-Type: {content_type}\r\n\r\n".encode("utf-8"))
        chunks += [path.read_bytes(), b"\r\n"]
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: Any, response: Any, code: int, message: str, headers: Any, new_url: str) -> None:
        raise state.ProjectError("服务返回重定向，已停止；核对接口地址后重新创建计划")


def request_bytes(request: urllib.request.Request, timeout: int) -> bytes:
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
            data = response.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as error:
        raise state.ProjectError(f"接口返回 HTTP {error.code}，未自动重试。检查权限、配额和接口兼容性；响应正文不输出，以免泄露凭据") from None
    except (urllib.error.URLError, TimeoutError):
        raise state.ProjectError("网络失败或超时，未自动重试；请求可能已被服务接受，先在服务端核对任务和费用") from None
    if len(data) > MAX_RESPONSE:
        raise state.ProjectError("响应超过 80 MB，已停止；缩小数量或尺寸")
    return data


def cmd_run(args: argparse.Namespace) -> int:
    plan_file = Path(args.plan).resolve()
    plan = state.read_json(plan_file)
    validate_plan(plan)
    key = os.environ.get(KEY_ENV, "").strip()
    if args.dry_run:
        return state.emit({"ok": True, "dry_run": True, "sha256": plan["sha256"], "endpoint": plan["endpoint"], "model": plan["model"], "references": len(plan["references"]), "outputs": plan["outputs"], "key_from_environment": bool(key), "say": "dry-run 未联网、未读取系统密钥、未生成图片"})
    if not key:
        raise state.ProjectError(f"缺少运行时凭据；通过 credential-ui 的 run image-api 包装器执行，或在受信运行时注入 {KEY_ENV}")
    imaging.require_pillow()
    if plan.get("references"):
        data, content_type = multipart(plan)
    else:
        payload = {key_name: plan[key_name] for key_name in ("model", "prompt", "size", "quality", "background", "n") if plan.get(key_name) is not None}
        data, content_type = json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json"
    request = urllib.request.Request(plan["endpoint"], data=data, headers={"Authorization": "Bearer " + key, "Content-Type": content_type}, method="POST")
    with operation_locks(plan_file, plan):
        validate_plan(plan)
        claim_outputs(plan_file, plan)
        receipt = {"schema_version": 1, "state": "requesting", "plan_sha256": plan["sha256"], "started_at": state.now()}
        save_progress(receipt_path(plan_file), receipt)
        response = saved_response(request_bytes(request, args.timeout), plan, key)
        save_progress(response_path(plan_file), response)
        receipt.update(state="received", received_at=response["received_at"])
        return save_outputs(plan_file, plan, receipt, response, args.timeout)


def cmd_resume(args: argparse.Namespace) -> int:
    plan_file = Path(args.plan).resolve()
    plan = state.read_json(plan_file)
    validate_plan(plan, resume=True)
    imaging.require_pillow()
    with operation_locks(plan_file, plan):
        receipt = state.read_json(receipt_path(plan_file))
        if receipt.get("plan_sha256") != plan["sha256"]:
            raise state.ProjectError("回执与计划不一致，未恢复或生成图片")
        for output in plan["outputs"]:
            claim = state.read_json(output_claim(output))
            if claim.get("plan_sha256") != plan["sha256"] or claim.get("receipt") != str(receipt_path(plan_file)):
                raise state.ProjectError("输出属于其他请求，未恢复或生成图片")
        saved = response_path(plan_file)
        if not saved.is_file():
            raise state.ProjectError("没有已保存的成功响应；请求可能已被接受，先核对服务端记录；resume 不会重新 POST")
        if receipt.get("response_sha256") and digest(saved) != receipt["response_sha256"]:
            raise state.ProjectError("已保存的生成结果发生变化，未恢复或生成图片")
        response = state.read_json(saved)
        items = response.get("data")
        if response.get("plan_sha256") != plan["sha256"] or not isinstance(items, list) or len(items) != plan["n"] or any(
            not isinstance(item, dict) or not any(isinstance(item.get(field), str) and item[field] for field in ("b64_json", "url")) for item in items
        ):
            raise state.ProjectError("已保存的生成结果与计划不一致，未恢复或生成图片")
        return save_outputs(plan_file, plan, receipt, response, args.timeout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="可选的兼容图片接口：configure → plan → run --dry-run → run")
    sub = parser.add_subparsers(dest="command", required=True)
    configure = sub.add_parser("configure", help="保存普通设置，不接收 API Key")
    configure.add_argument("--base-url", required=True, help="API 根地址，通常以 /v1 结尾")
    configure.add_argument("--model", required=True)
    configure.add_argument("--dry-run", action="store_true")
    configure.set_defaults(func=cmd_configure)
    status = sub.add_parser("status")
    status.set_defaults(func=cmd_status)
    plan = sub.add_parser("plan", help="写入不含密钥的图片请求计划")
    plan.add_argument("--prompt-file", required=True)
    plan.add_argument("--reference", action="append")
    plan.add_argument("--out", required=True)
    plan.add_argument("--plan", required=True)
    plan.add_argument("--size", default="1024x1536")
    plan.add_argument("--quality", default="high")
    plan.add_argument("--background", choices=("transparent", "opaque", "auto"), default="auto")
    plan.add_argument("--n", type=int, default=1)
    plan.set_defaults(func=cmd_plan)
    run = sub.add_parser("run", help="先 dry-run，再通过凭据包装器实际调用")
    run.add_argument("--plan", required=True)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--timeout", type=int, default=300)
    run.set_defaults(func=cmd_run)
    resume = sub.add_parser("resume", help="仅从已保存响应恢复解码、下载和保存；无需 Key，绝不重新 POST")
    resume.add_argument("--plan", required=True)
    resume.add_argument("--timeout", type=int, default=300)
    resume.set_defaults(func=cmd_resume)
    args = parser.parse_args(argv)
    if not imaging.python_ok():
        return imaging.fail("需要 Python 3.10 或更高版本")
    try:
        return args.func(args)
    except (state.ProjectError, imaging.ImagingError, OSError) as error:
        message = str(error)
        key = os.environ.get(KEY_ENV)
        if key:
            message = message.replace(key, "[已脱敏]")
        return state.emit({"ok": False, "error": message})


if __name__ == "__main__":
    sys.exit(main())
