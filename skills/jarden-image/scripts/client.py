import argparse
import csv
import getpass
import hashlib
import json
import mimetypes
import os
import platform
from pathlib import Path
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import warnings


class Client:
    def __init__(self, base_url, token):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("BASE_URL_INVALID")
        if parsed.scheme != "https" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("HTTPS_REQUIRED_OUTSIDE_LOCALHOST")
        self.base = base_url.rstrip("/")
        self.token = token.strip()
        handlers = [urllib.request.HTTPSHandler(context=ssl.create_default_context()), NoRedirect()]
        if parsed.hostname in ("127.0.0.1", "localhost", "::1"):
            handlers.insert(0, urllib.request.ProxyHandler({}))
        self.opener = urllib.request.build_opener(*handlers)

    def request(self, path, payload=None, content_type=None, destination=None):
        headers = {
            "Authorization": "Bearer " + self.token,
            "User-Agent": "HomeInferenceClient/1.0",
        }
        data = None
        if isinstance(payload, dict):
            data = json.dumps(payload, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        elif payload is not None:
            data = payload
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self.base + path, data=data, headers=headers)
        with self.opener.open(request, timeout=60) as response:
            if destination:
                temporary = destination.with_suffix(destination.suffix + ".partial")
                with temporary.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                temporary.replace(destination)
                return {"file": str(destination)}
            return json.load(response)

    def download(self, job, folder):
        if job["status"] != "succeeded":
            raise RuntimeError("JOB_NOT_SUCCEEDED: " + job["status"])
        folder.mkdir(parents=True, exist_ok=True)
        files = []
        if not job["job_id"].startswith("job_") or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in job["job_id"]):
            raise ValueError("JOB_ID_INVALID")
        for output in job["outputs"]:
            suffix = ".png" if output["media_type"] == "image/png" else ".mp4"
            target = folder / (job["job_id"] + "-" + output["output_id"] + suffix)
            if not target.resolve().is_relative_to(folder.resolve()):
                raise ValueError("OUTPUT_PATH_INVALID")
            path = output["download_path"]
            if not path.startswith("/v1/jobs/") or "://" in path:
                raise ValueError("OUTPUT_URL_INVALID")
            if target.is_file() and output.get("sha256") and file_digest(target) == output["sha256"]:
                files.append({"file": str(target), "already_verified": True})
                continue
            files.append(self.request(path, destination=target))
            digest = hashlib.sha256()
            with target.open("rb") as incoming:
                while chunk := incoming.read(1024 * 1024):
                    digest.update(chunk)
            if output.get("sha256") and digest.hexdigest() != output["sha256"]:
                target.unlink(missing_ok=True)
                raise ValueError("OUTPUT_CHECKSUM_MISMATCH")
        return files


def _canonical_base(base_url):
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("BASE_URL_INVALID")
    if parsed.scheme != "https" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("HTTPS_REQUIRED_OUTSIDE_LOCALHOST")
    host = parsed.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    port = parsed.port
    if port is not None and not ((parsed.scheme == "https" and port == 443) or (parsed.scheme == "http" and port == 80)):
        host += ":" + str(port)
    return parsed.scheme + "://" + host + parsed.path.rstrip("/")


def _credential_dir(base_url):
    namespace = hashlib.sha256(_canonical_base(base_url).encode()).hexdigest()[:24]
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return root / "JardenAI" / "hosts" / namespace
    root = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return root / "jarden-ai" / "hosts" / namespace


def _secret_path(value, directory, fallback_name):
    if value:
        return Path(value).expanduser().resolve()
    return directory / fallback_name


def _read_secret(filename):
    try:
        value = filename.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        return None
    if not value:
        raise ValueError("CREDENTIAL_FILE_EMPTY: " + str(filename))
    return value


_WINDOWS_SID = None


def _restrict_secret(filename):
    global _WINDOWS_SID
    if os.name != "nt":
        os.chmod(filename, 0o600)
        return
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if _WINDOWS_SID is None:
            result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                                    capture_output=True, text=True, check=True, timeout=10, creationflags=flags)
            rows = list(csv.reader(result.stdout.splitlines()))
            sid = rows[-1][-1] if rows else ""
            if not sid.startswith("S-1-"):
                raise ValueError("SID_UNAVAILABLE")
            _WINDOWS_SID = sid
        subprocess.run(["icacls", str(filename), "/inheritance:r", "/grant:r", "*" + _WINDOWS_SID + ":F"],
                       capture_output=True, check=True, timeout=10, creationflags=flags)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise RuntimeError("CREDENTIAL_PERMISSIONS_FAILED: " + str(filename)) from None


def _write_private(filename, value):
    filename.parent.mkdir(parents=True, exist_ok=True)
    temporary = filename.with_name(filename.name + ".tmp-" + uuid.uuid4().hex)
    descriptor = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        _restrict_secret(temporary)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = None
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(filename)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _write_secret(filename, value):
    _write_private(filename, value.strip() + "\n")


def _device_label(value):
    label = (value or os.environ.get("HOME_INFERENCE_DEVICE_LABEL") or
             (platform.node() or "computer") + "-JardenAI").strip()
    if not label:
        label = "computer-JardenAI"
    if len(label) > 80:
        label = label[:80]
    return label


def _authentication(client):
    try:
        identity = client.request("/v1/auth")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        client.request("/v1/models")
        return {"user_id": None, "key_id": None, "key_kind": "legacy", "legacy_server": True}
    if (not isinstance(identity, dict) or not isinstance(identity.get("user_id"), str)
            or not identity["user_id"] or identity.get("key_kind") not in ("personal", "device", "legacy", "admin")):
        raise RuntimeError("AUTH_RESPONSE_INVALID")
    return identity


def _read_metadata(filename):
    try:
        value = json.loads(filename.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def resolve_client(base_url, token_file=None, personal_key_file=None,
                   device_key_file=None, device_label=None):
    """Return a client, automatically bootstrapping a persistent device key.

    --token-file remains the explicit legacy path and bypasses bootstrap. Without
    it, a personal key is read from the configured local file (or prompted once),
    verified, then used to create/recover a per-device key saved separately.
    """
    explicit = token_file or os.environ.get("HOME_INFERENCE_TOKEN_FILE")
    if explicit:
        token = _read_secret(Path(explicit).expanduser().resolve())
        if not token:
            raise ValueError("TOKEN_FILE_MISSING: " + str(explicit))
        return Client(base_url, token)

    base_url = _canonical_base(base_url)
    directory = _credential_dir(base_url)
    cache_personal_path = directory / "personal.key"
    supplied_personal_path = personal_key_file or os.environ.get("HOME_INFERENCE_PERSONAL_KEY_FILE")
    personal_path = _secret_path(supplied_personal_path, directory, "personal.key")
    personal = _read_secret(personal_path)
    if personal is None:
        if supplied_personal_path:
            raise RuntimeError("PERSONAL_KEY_FILE_MISSING: " + str(personal_path))
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                personal = getpass.getpass("Personal API Key (saved locally for next time): ").strip()
        except getpass.GetPassWarning:
            raise RuntimeError("PERSONAL_KEY_REQUIRED: no hidden-input terminal; provide --personal-key-file") from None
        except (EOFError, KeyboardInterrupt):
            raise RuntimeError("PERSONAL_KEY_REQUIRED: provide --personal-key-file or HOME_INFERENCE_PERSONAL_KEY_FILE") from None
        if not personal:
            raise RuntimeError("PERSONAL_KEY_REQUIRED: no key entered")

    personal_client = Client(base_url, personal)
    try:
        identity = _authentication(personal_client)
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise RuntimeError("PERSONAL_KEY_INVALID: replace the saved personal key") from None
        raise
    if identity["key_kind"] not in ("personal", "legacy", "admin"):
        raise RuntimeError("PERSONAL_KEY_REQUIRED: this file contains a device key")
    if identity.get("legacy_server"):
        # Older servers do not reveal account identity. Scope their cache to the
        # validated personal credential locally; never print this identifier.
        identity["user_id"] = "legacy-local-" + hashlib.sha256(personal.encode()).hexdigest()
        identity["key_id"] = identity["user_id"]
    try:
        cached_personal = _read_secret(cache_personal_path)
    except ValueError:
        cached_personal = None
    if cached_personal != personal:
        _write_secret(cache_personal_path, personal)

    device_path = _secret_path(
        device_key_file or os.environ.get("HOME_INFERENCE_DEVICE_KEY_FILE"), directory, "device.key"
    )
    metadata_path = directory / "metadata.json"
    metadata = _read_metadata(metadata_path)
    try:
        device = _read_secret(device_path)
    except ValueError:
        device = None
    if device:
        device_client = Client(base_url, device)
        try:
            device_identity = _authentication(device_client)
            same_account = device_identity.get("user_id") == identity["user_id"]
            if device_identity.get("legacy_server"):
                same_account = (metadata.get("base_url") == base_url and metadata.get("user_id") == identity["user_id"]
                                and metadata.get("personal_key_id") == identity.get("key_id"))
            if same_account and device_identity["key_kind"] in ("device", "legacy"):
                return device_client
        except urllib.error.HTTPError as error:
            if error.code != 401:
                raise

    try:
        created = personal_client.request("/v1/keys", {"label": _device_label(device_label)})
    except urllib.error.HTTPError as error:
        if error.code == 429:
            raise RuntimeError("DEVICE_KEY_LIMIT: remove an old device key in the web UI") from None
        raise
    device = created.get("key") if isinstance(created, dict) else None
    if not isinstance(device, str) or not device.strip():
        raise RuntimeError("DEVICE_KEY_CREATE_FAILED: server returned no key")
    device_client = Client(base_url, device)
    device_identity = _authentication(device_client)
    if not device_identity.get("legacy_server") and (device_identity.get("user_id") != identity["user_id"]
            or device_identity["key_kind"] not in ("device", "legacy")):
        raise RuntimeError("DEVICE_KEY_ACCOUNT_MISMATCH")
    try:
        _write_secret(device_path, device)
        _write_private(metadata_path, json.dumps({
            "version": 1, "base_url": base_url, "user_id": identity["user_id"],
            "personal_key_id": identity.get("key_id"),
            "device_key_id": device_identity.get("key_id") or created.get("id"),
            "device_key_path": str(device_path),
        }, indent=2) + "\n")
    except BaseException:
        if created.get("id"):
            try:
                personal_client.request("/v1/keys/" + urllib.parse.quote(created["id"], safe="") + "/revoke", {})
            except Exception:
                pass
        raise
    print("Created or refreshed a local device key at " + str(device_path), file=sys.stderr)
    return device_client


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise urllib.error.HTTPError(request.full_url, code, "REDIRECT_REFUSED_CHECK_API_HOSTNAME", headers, response)


def file_digest(filename):
    digest = hashlib.sha256()
    with filename.open("rb") as incoming:
        while chunk := incoming.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def add_generation_options(parser):
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--steps", type=int)
    parser.add_argument("--cfg", type=float)
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument("--render-mode", choices=("native", "upscale"))
    parser.add_argument("--sampling-profile", choices=("standard", "turbo-4step"))
    parser.add_argument("--attention-backend", choices=("pytorch", "comfy-kitchen"))


def generation_body(arguments, model):
    body = {"model": model, "prompt": arguments.prompt, "seed": arguments.seed,
            "preset": ("square-4mp" if model == "image-h3-t2i" else "square-1mp")
            if model.startswith("image-") else "landscape-480p-5s"}
    for field in ("width", "height", "steps", "cfg", "duration_seconds", "render_mode", "sampling_profile"):
        value = getattr(arguments, field, None)
        if value is not None:
            body[field] = value
    if model.startswith("image-") and ("duration_seconds" in body or "render_mode" in body):
        raise ValueError("DURATION_AND_RENDER_MODE_ARE_VIDEO_ONLY")
    if model.startswith("video-") and "cfg" in body:
        raise ValueError("CFG_IS_IMAGE_ONLY")
    if body.get("sampling_profile") == "turbo-4step":
        if not model.startswith("video-"):
            raise ValueError("TURBO_PROFILE_IS_VIDEO_ONLY")
        if body.get("steps") not in (None, 4):
            raise ValueError("TURBO_PROFILE_REQUIRES_4_STEPS")
        body["steps"] = 4
    attention_backend = getattr(arguments, "attention_backend", None)
    if attention_backend is not None:
        if model.startswith("image-") and attention_backend != "pytorch":
            raise ValueError("ATTENTION_BACKEND_SELECTION_IS_VIDEO_ONLY")
        if model.startswith("video-"):
            body["attention_backend"] = attention_backend
    return body


def save_job(job, folder):
    (folder / (job["job_id"] + ".json")).write_text(
        json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def wait_for_job(client, job, folder):
    while job["status"] in ("queued", "submitting", "running", "submission_unknown"):
        if job["status"] == "submission_unknown":
            raise RuntimeError("SUBMISSION_UNKNOWN: 联系机主对账，不要重复提交")
        time.sleep(5)
        try:
            job = client.request("/v1/jobs/" + job["job_id"])
        except urllib.error.HTTPError as error:
            if error.code == 429:
                time.sleep(int(error.headers.get("Retry-After", "10")))
                continue
            raise
        save_job(job, folder)
        print(json.dumps({"job_id": job["job_id"], "status": job["status"]}), flush=True)
    if job["status"] != "succeeded":
        raise RuntimeError("JOB_FAILED: " + str(job.get("error_code")))
    print(json.dumps(client.download(job, folder), ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("HOME_INFERENCE_BASE_URL", "https://ai.comark.work"))
    parser.add_argument("--token-file", default=os.environ.get("HOME_INFERENCE_TOKEN_FILE"))
    parser.add_argument("--personal-key-file", default=os.environ.get("HOME_INFERENCE_PERSONAL_KEY_FILE"),
                        help="Personal key file used once to verify access and create/recover a device key")
    parser.add_argument("--device-key-file", default=os.environ.get("HOME_INFERENCE_DEVICE_KEY_FILE"),
                        help="Persistent per-device key file; defaults to the user's local JardenAI config directory")
    parser.add_argument("--device-label", default=os.environ.get("HOME_INFERENCE_DEVICE_LABEL"),
                        help="Label used when creating a device key")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health")
    commands.add_parser("models")
    upload = commands.add_parser("upload")
    upload.add_argument("--file", required=True)
    for action in ("image", "video"):
        command = commands.add_parser(action)
        command.add_argument("--prompt", required=True)
        command.add_argument("--seed", type=int, default=12345)
        command.add_argument("--key", default=None)
        command.add_argument("--first-frame", default=None)
        if action == "image":
            command.add_argument("--model", choices=("moss", "h3"), default="moss")
        command.add_argument("--wait", action="store_true")
        command.add_argument("--out", default="outputs")
        add_generation_options(command)
    estimate = commands.add_parser("estimate")
    estimate.add_argument("--kind", choices=("image", "video", "i2v"), default="image")
    estimate.add_argument("--prompt", default="")
    estimate.add_argument("--seed", type=int, default=12345)
    estimate.add_argument("--model", choices=("moss", "h3"), default="moss")
    add_generation_options(estimate)
    resume = commands.add_parser("resume-request")
    resume.add_argument("--file", required=True)
    resume.add_argument("--wait", action="store_true")
    resume.add_argument("--out", default="outputs")
    for action in ("status", "download", "cancel"):
        command = commands.add_parser(action)
        command.add_argument("job_id")
        command.add_argument("--out", default="outputs")
        if action == "download":
            command.add_argument("--wait", action="store_true")
    arguments = parser.parse_args()
    client = resolve_client(
        arguments.base_url, arguments.token_file, arguments.personal_key_file,
        arguments.device_key_file, arguments.device_label,
    )
    if arguments.command == "estimate":
        model = {"image": "image-h3-t2i" if arguments.model == "h3" else "image-moss-sft", "video": "video-h3-t2v", "i2v": "video-h3-i2v"}[arguments.kind]
        print(json.dumps(client.request("/v1/estimates", generation_body(arguments, model)), ensure_ascii=False, indent=2))
        return
    if arguments.command in ("health", "models"):
        path = "/health/ready" if arguments.command == "health" else "/v1/models"
        print(json.dumps(client.request(path), ensure_ascii=False, indent=2))
        return
    if arguments.command == "upload":
        filename = Path(arguments.file)
        if filename.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("IMAGE_TOO_LARGE")
        media_type = mimetypes.guess_type(filename.name)[0]
        if media_type not in ("image/png", "image/jpeg", "image/webp"):
            raise ValueError("IMAGE_TYPE_NOT_ALLOWED")
        print(json.dumps(client.request("/v1/assets", filename.read_bytes(), media_type)))
        return
    if arguments.command == "resume-request":
        body = json.loads(Path(arguments.file).read_text(encoding="utf-8-sig"))
        if not isinstance(body, dict) or not body.get("idempotency_key") or not body.get("model"):
            raise ValueError("REQUEST_FILE_MISSING_MODEL_OR_IDEMPOTENCY_KEY")
        record_dir = Path(arguments.out)
        record_dir.mkdir(parents=True, exist_ok=True)
        job = client.request("/v1/jobs", body)
        save_job(job, record_dir)
        print(json.dumps(job, ensure_ascii=False), flush=True)
        if arguments.wait:
            wait_for_job(client, job, record_dir)
        return
    if arguments.command in ("image", "video"):
        model = ("image-h3-t2i" if arguments.model == "h3" else "image-moss-sft") if arguments.command == "image" else (
            "video-h3-i2v" if arguments.first_frame else "video-h3-t2v"
        )
        body = generation_body(arguments, model)
        body["idempotency_key"] = arguments.key or uuid.uuid4().hex
        if arguments.first_frame:
            body["first_frame_asset_id"] = arguments.first_frame
        record_dir = Path(arguments.out)
        record_dir.mkdir(parents=True, exist_ok=True)
        intent = record_dir / ("request-" + uuid.uuid4().hex + ".json")
        intent.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        print("REQUEST_SAVED " + str(intent), flush=True)
        job = client.request("/v1/jobs", body)
        save_job(job, record_dir)
        print(json.dumps(job, ensure_ascii=False), flush=True)
        if not arguments.wait:
            return
        wait_for_job(client, job, record_dir)
        return
    path = "/v1/jobs/" + urllib.parse.quote(arguments.job_id, safe="")
    if arguments.command == "cancel":
        print(json.dumps(client.request(path + "/cancel", {}), ensure_ascii=False))
    else:
        job = client.request(path)
        if arguments.command == "download" and arguments.wait:
            folder = Path(arguments.out)
            folder.mkdir(parents=True, exist_ok=True)
            save_job(job, folder)
            wait_for_job(client, job, folder)
            return
        result = client.download(job, Path(arguments.out)) if arguments.command == "download" else job
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        print("HTTP_ERROR " + str(error.code))
        print("检查 Base URL、密钥和任务状态；不要把密钥粘贴到聊天中。")
        raise SystemExit(1)
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error))
        raise SystemExit(1)
