"""Durable, provider-independent image-generation receipts using only the stdlib.

prepare() is read-only and returns a plan, not a task. begin()/claim()/start()
commit a requesting receipt before returning should_call=True. Only that return
value authorizes a caller to dispatch its external tool; this module never does.
A repeated begin, including after process death, never resends a requesting or
unknown task. A definite failed attempt needs retryable=True and retry=True to
be claimed again. Explicit regeneration requires a different intent_id.

Compiled local references use path descriptors or references/refs lists and bind
to file bytes rather than filenames. Destinations, delivery metadata, timestamps
and supplied fingerprints do not affect identity. Backend descriptors must not
contain credentials. Authorization requires approved=True or authorized=True
when claiming, and may additionally bind project_id, slot_id and intent_id.

complete() checks local image structure and hashes, not visual quality or remote
provider provenance. Its receipts are not image-review or delivery approvals.
Claims prevent local automatic resends; they cannot guarantee remote exactly-once
execution. JPEG/WebP checks validate structure without fully decoding pixels.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import re
import stat
import struct
import tempfile
import threading
import time
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 1
_TASK_ID = re.compile(r"gen-[0-9a-f]{64}\Z")
_STATUSES = {"requesting", "unknown", "failed", "completed"}
_VOLATILE = {
    "created_at", "updated_at", "compiled_at", "generated_at", "timestamp",
    "output", "outputs", "output_path", "output_paths", "output_dir",
    "dest", "dest_path", "dest_dir",
    "destination", "destination_path", "delivery", "delivery_id", "delivery_path",
    "delivery_revision", "presentation_id", "project_root", "project_path", "root",
    "fingerprint", "compile_fingerprint", "compiled_fingerprint", "compile_sha256",
}
_REFERENCE_LISTS = {"references", "refs", "reference_images", "input_images"}
_REFERENCE_PATHS = {"path", "image_path", "reference_path"}
_SECRET_FIELDS = {
    "api_key", "apikey", "access_token", "refresh_token", "token", "secret",
    "password", "credentials", "authorization_header",
}
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class TaskError(ValueError):
    """An invalid plan, unsafe path, conflicting receipt, or unsafe transition."""


GenerationTaskError = TaskError


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json_copy(value: Any) -> Any:
    def validate(item: Any) -> None:
        if isinstance(item, dict):
            for field, entry in item.items():
                if not isinstance(field, str):
                    raise TaskError("invalid_json: object keys must be strings")
                normalized = field.lower().replace("-", "_")
                if normalized in _SECRET_FIELDS or (normalized == "authorization" and not isinstance(entry, dict)):
                    raise TaskError("credentials_forbidden: pass descriptors, never secrets")
                validate(entry)
        elif isinstance(item, list):
            for entry in item:
                validate(entry)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise TaskError("invalid_json: use JSON values only")

    validate(value)
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (ValueError, TypeError, RecursionError) as error:
        raise TaskError("invalid_json: use finite JSON values only") from error


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise TaskError(f"invalid_{field}: expected a nonempty, unpadded string")
    return value


def _root(root: str | Path) -> Path:
    try:
        resolved = Path(root).expanduser().resolve(strict=True)
    except (OSError, ValueError, RuntimeError) as error:
        raise TaskError("invalid_root: project directory does not exist") from error
    if not resolved.is_dir():
        raise TaskError("invalid_root: expected a project directory")
    return resolved


def _scoped(root: Path, value: str | Path) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise TaskError("invalid_path: expected an in-project file path")
    path = Path(value)
    if ".." in path.parts:
        raise TaskError("path_outside_project: parent traversal is forbidden")
    candidate = path if path.is_absolute() else root / path
    try:
        if path.is_absolute() and not candidate.is_relative_to(root):
            alias = next((ancestor for ancestor in candidate.parents if ancestor.resolve() == root), None)
            if alias is not None:
                candidate = root / candidate.relative_to(alias)
        relative = candidate.relative_to(root)
        if not relative.parts:
            raise ValueError("project root is not a file")
        current = root
        for component in relative.parts:
            current = current / component
            if current.is_symlink():
                raise TaskError("unsafe_symlink: project files must not be symlinks")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("outside project")
    except TaskError:
        raise
    except (ValueError, OSError, RuntimeError) as error:
        raise TaskError("path_outside_project: file must remain inside the project") from error
    return resolved


def _read_bytes(root: Path, value: str | Path) -> tuple[Path, bytes]:
    path = _scoped(root, value)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise TaskError("invalid_file: expected a regular file")
            content = stream.read()
            after = os.fstat(stream.fileno())
        current = path.stat()
    except OSError as error:
        raise TaskError("file_unavailable: cannot read the in-project file") from error
    signature = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    if signature(before) != signature(after) or signature(after) != signature(current) or len(content) != after.st_size:
        raise TaskError("file_changed: file changed while being hashed")
    return path, content


def _read_json(root: Path, value: str | Path) -> dict[str, Any]:
    _, content = _read_bytes(root, value)
    try:
        data = json.loads(content)
    except (UnicodeError, ValueError) as error:
        raise TaskError("invalid_record: expected a JSON object") from error
    if not isinstance(data, dict):
        raise TaskError("invalid_record: expected a JSON object")
    return data


def _project_id(root: Path, supplied: dict[str, Any] | None = None) -> str:
    project_id = _text(_read_json(root, "project.json").get("project_id"), "project_id")
    if supplied is not None and (not isinstance(supplied, dict) or supplied.get("project_id") != project_id):
        raise TaskError("project_mismatch: plan must belong to this project_id")
    return project_id


def _semantic(root: Path, value: Any, field: str = "") -> Any:
    if isinstance(value, list):
        if field in _REFERENCE_LISTS:
            return [_semantic(root, {"path": entry} if isinstance(entry, str) else entry) for entry in value]
        return [_semantic(root, entry) for entry in value]
    if not isinstance(value, dict):
        return value
    normalized = {name: _semantic(root, entry, name) for name, entry in value.items()
                  if name not in _VOLATILE and name not in _REFERENCE_PATHS}
    paths = [value[name] for name in sorted(_REFERENCE_PATHS) if name in value]
    if paths:
        hashes = {hashlib.sha256(_read_bytes(root, path)[1]).hexdigest() for path in paths}
        if len(hashes) != 1:
            raise TaskError("reference_conflict: reference paths contain different bytes")
        actual = hashes.pop()
        expected = normalized.get("sha256")
        if expected is not None and expected != actual:
            raise TaskError("reference_changed: reference hash does not match its bytes")
        normalized["sha256"] = actual
    return normalized


def _authorization(value: Any, identity: dict[str, Any], required: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TaskError("invalid_authorization: expected an authorization object")
    authorization = _json_copy(value)
    for field in ("project_id", "slot_id", "intent_id", "compile_fingerprint"):
        if field in authorization and authorization[field] != identity[field]:
            raise TaskError("authorization_scope_mismatch: approval belongs to another request")
    flags = [authorization[field] for field in ("approved", "authorized") if field in authorization]
    if required and (not flags or any(flag is not True for flag in flags)):
        raise TaskError("authorization_required: an explicit approval is required before claiming")
    return authorization


def prepare(root: str | Path, project: dict[str, Any], slot_id: str, intent_id: str,
            compiled: dict[str, Any], backend: dict[str, Any], authorization: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic, read-only plan; no lock or tasks directory is made.

    slot_id identifies the stable slot/subject, not a delivery or output filename.
    The same project_id can be copied or moved without changing task identity.
    """
    directory = _root(root)
    project_id = _project_id(directory, project)
    if not isinstance(compiled, dict) or not compiled or not isinstance(backend, dict) or not backend:
        raise TaskError("invalid_request: compiled and backend must be nonempty objects")
    compiled = _json_copy(compiled)
    backend = _json_copy(backend)
    request = {"compiled": _semantic(directory, compiled), "backend": _semantic(directory, backend)}
    if not request["compiled"] or not request["backend"]:
        raise TaskError("invalid_request: semantic compiled and backend descriptors are required")
    identity = {
        "project_id": project_id,
        "slot_id": _text(slot_id, "slot_id"),
        "intent_id": _text(intent_id, "intent_id"),
        "compile_fingerprint": _digest(request["compiled"]),
        "backend": request["backend"],
    }
    key = _digest(identity)
    return {
        "schema_version": SCHEMA_VERSION, "kind": "generation-plan",
        "task_id": "gen-" + key, "idempotency_key": key, "identity": identity,
        "compiled": compiled, "backend": backend, "request": request,
        "request_sha256": _digest(request),
        "authorization": _authorization(authorization, identity),
    }


def _checked_plan(root: Path, plan: dict[str, Any], authorization: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(plan, dict) or plan.get("kind") != "generation-plan" or plan.get("schema_version") != SCHEMA_VERSION:
        raise TaskError("invalid_plan: use prepare() before beginning a task")
    try:
        identity = plan["identity"]
        checked = prepare(root, {"project_id": identity["project_id"]}, identity["slot_id"], identity["intent_id"],
                          plan["compiled"], plan["backend"], plan["authorization"] if authorization is None else authorization)
    except (KeyError, TypeError) as error:
        raise TaskError("invalid_plan: missing plan fields") from error
    for field in ("identity", "task_id", "idempotency_key", "request", "request_sha256"):
        if plan.get(field) != checked[field]:
            raise TaskError("plan_conflict: plan content does not match its stable key")
    return checked


def _task_path(root: Path, task_id: str) -> Path:
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise TaskError("invalid_task_id: expected the identifier returned by prepare()")
    return _scoped(root, f"tasks/{task_id}.json")


def _atomic_json(root: Path, path: Path, data: dict[str, Any]) -> None:
    path = _scoped(root, path)
    descriptor, temporary = tempfile.mkstemp(prefix=".generation-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextlib.contextmanager
def task_lock(root: str | Path, timeout: float = 10.0) -> Iterator[Path]:
    """Serialize receipts independently of, and safely inside, project transactions."""
    directory = _root(root)
    with _LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(str(directory), threading.RLock())
    if not thread_lock.acquire(timeout=max(0.0, timeout)):
        raise TaskError("tasks_busy: another generation receipt is being committed")
    acquired = False
    stream = None
    try:
        tasks = _scoped(directory, "tasks")
        tasks.mkdir(exist_ok=True)
        lock_path = _scoped(directory, "tasks/.generation.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        stream = os.fdopen(descriptor, "r+b")
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise TaskError("unsafe_lock: lock must be a regular file")
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + max(0.0, timeout)
        while not acquired:
            try:
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise TaskError("tasks_busy: another process holds the task lock") from error
                time.sleep(0.025)
        yield directory
    finally:
        if stream:
            try:
                if acquired:
                    if os.name == "nt":
                        import msvcrt
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()
        thread_lock.release()


def _load_task(root: Path, task_id: str) -> dict[str, Any] | None:
    path = _task_path(root, task_id)
    if not path.exists():
        return None
    task = _read_json(root, path)
    try:
        identity = task["identity"]
        request = task["request"]
        valid = (
            task["schema_version"] == SCHEMA_VERSION and task["kind"] == "generation-task"
            and task["task_id"] == task_id and task_id == "gen-" + _digest(identity)
            and task["idempotency_key"] == _digest(identity)
            and identity["project_id"] == _project_id(root)
            and identity["compile_fingerprint"] == _digest(request["compiled"])
            and identity["backend"] == request["backend"]
            and task["request_sha256"] == _digest(request)
            and task["status"] in _STATUSES
            and type(task["attempt"]) is int and task["attempt"] > 0
            and isinstance(task["claim_id"], str) and bool(task["claim_id"])
        )
    except (KeyError, TypeError, ValueError) as error:
        raise TaskError("task_conflict: receipt identity or request is invalid") from error
    if not valid:
        raise TaskError("task_conflict: receipt identity or request no longer matches its key")
    return task


def _png_size(content: bytes) -> tuple[int, int]:
    offset = 8
    width = height = 0
    compressed = bytearray()
    ended = False
    palette = False
    data_ended = False
    while offset + 12 <= len(content):
        length = struct.unpack_from(">I", content, offset)[0]
        chunk_type = content[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(content):
            raise TaskError("invalid_image: truncated PNG chunk")
        payload = content[offset + 8:end - 4]
        checksum = struct.unpack_from(">I", content, end - 4)[0]
        if zlib.crc32(chunk_type + payload) & 0xffffffff != checksum:
            raise TaskError("invalid_image: PNG checksum mismatch")
        if offset == 8:
            if chunk_type != b"IHDR" or length != 13:
                raise TaskError("invalid_image: missing PNG header")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
            if not width or not height or depth not in depths.get(color, set()) or compression or filtering or interlace not in (0, 1):
                raise TaskError("invalid_image: invalid PNG dimensions or encoding")
        elif chunk_type == b"IHDR":
            raise TaskError("invalid_image: duplicate PNG header")
        if chunk_type == b"PLTE":
            if palette or compressed or color in (0, 4) or not length or length % 3 or length > 768 or (color == 3 and length // 3 > 2 ** depth):
                raise TaskError("invalid_image: invalid PNG palette")
            palette = True
        if chunk_type == b"IDAT":
            if data_ended or (color == 3 and not palette):
                raise TaskError("invalid_image: invalid PNG image-data order")
            compressed.extend(payload)
        elif compressed:
            data_ended = True
        if len(chunk_type) != 4 or any(not (65 <= byte <= 90 or 97 <= byte <= 122) for byte in chunk_type) or chunk_type[2] & 32:
            raise TaskError("invalid_image: invalid PNG chunk type")
        if not chunk_type[0] & 32 and chunk_type not in (b"IHDR", b"PLTE", b"IDAT", b"IEND"):
            raise TaskError("invalid_image: unsupported critical PNG chunk")
        if chunk_type == b"IEND":
            ended = length == 0 and end == len(content)
            break
        offset = end
    if not ended or not compressed:
        raise TaskError("invalid_image: incomplete PNG image")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    passes = [(0, 0, 1, 1)] if not interlace else [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
    expected = 0
    row_layout = []
    for start_x, start_y, step_x, step_y in passes:
        columns = max(0, (width - start_x + step_x - 1) // step_x)
        rows = max(0, (height - start_y + step_y - 1) // step_y)
        if columns and rows:
            row_size = 1 + (columns * channels * depth + 7) // 8
            row_layout.append((expected, rows, row_size))
            expected += rows * row_size
    if expected > 512 * 1024 * 1024:
        raise TaskError("invalid_image: PNG decoded data exceeds the local validation limit")
    filters = (start + row * row_size for start, rows, row_size in row_layout for row in range(rows))
    next_filter = next(filters, None)
    decoder = zlib.decompressobj()
    remaining = bytes(compressed)
    decoded_size = 0
    try:
        while remaining:
            block = decoder.decompress(remaining, 1024 * 1024)
            while next_filter is not None and next_filter < decoded_size + len(block):
                if block[next_filter - decoded_size] > 4:
                    raise TaskError("invalid_image: invalid PNG row filter")
                next_filter = next(filters, None)
            decoded_size += len(block)
            remaining = decoder.unconsumed_tail
            if decoded_size > expected:
                raise TaskError("invalid_image: excessive PNG pixel data")
    except zlib.error as error:
        raise TaskError("invalid_image: corrupt PNG image data") from error
    if not decoder.eof or decoder.unused_data or decoded_size != expected:
        raise TaskError("invalid_image: incomplete PNG pixel data")
    return width, height


def _image_info(content: bytes) -> dict[str, Any]:
    width = height = 0
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = _png_size(content)
        image_format = "png"
    elif content.startswith(b"\xff\xd8") and content.endswith(b"\xff\xd9"):
        offset = 2
        scanned = False
        tables = set()
        components = set()
        ended = False
        while offset + 4 <= len(content):
            if content[offset] != 255:
                break
            while offset < len(content) and content[offset] == 255:
                offset += 1
            if offset + 3 > len(content):
                break
            marker = content[offset]
            offset += 1
            if marker == 217:
                ended = offset == len(content)
                break
            length = int.from_bytes(content[offset:offset + 2], "big")
            if length < 2 or offset + length > len(content):
                break
            payload = content[offset + 2:offset + length]
            if marker in {192, 193, 194}:
                if components or length < 11 or payload[0] != 8 or length != 8 + 3 * payload[5]:
                    raise TaskError("invalid_image: invalid JPEG frame")
                height, width = struct.unpack_from(">HH", content, offset + 3)
                components = set(payload[6::3])
                if not components or len(components) != payload[5] or any(not 1 <= value >> 4 <= 4 or not 1 <= value & 15 <= 4 for value in payload[7::3]):
                    raise TaskError("invalid_image: invalid JPEG components")
            if marker == 219:
                cursor = 0
                while cursor < len(payload):
                    descriptor = payload[cursor]
                    if descriptor >> 4 not in (0, 1) or descriptor & 15 > 3:
                        raise TaskError("invalid_image: invalid JPEG quantization table")
                    cursor += 1 + 64 * (1 + (descriptor >> 4))
                if not payload or cursor != len(payload):
                    raise TaskError("invalid_image: truncated JPEG quantization table")
                tables.add(marker)
            if marker == 196:
                cursor = 0
                while cursor < len(payload):
                    if cursor + 17 > len(payload) or payload[cursor] >> 4 not in (0, 1) or payload[cursor] & 15 > 3:
                        raise TaskError("invalid_image: invalid JPEG Huffman table")
                    count = sum(payload[cursor + 1:cursor + 17])
                    if not 1 <= count <= 256:
                        raise TaskError("invalid_image: empty JPEG Huffman table")
                    cursor += 17 + count
                if not payload or cursor != len(payload):
                    raise TaskError("invalid_image: truncated JPEG Huffman table")
                tables.add(marker)
            if marker == 218:
                if not components or tables != {196, 219} or not payload or not 1 <= payload[0] <= len(components) or length != 6 + 2 * payload[0]:
                    raise TaskError("invalid_image: incomplete JPEG scan header")
                if not set(payload[1:1 + 2 * payload[0]:2]) <= components:
                    raise TaskError("invalid_image: unknown JPEG scan component")
                scan_start = offset + length
                boundary = re.search(b"\xff+(?![\x00\xd0-\xd7\xff])", content[scan_start:])
                if boundary is None or boundary.start() == 0:
                    raise TaskError("invalid_image: missing JPEG scan data")
                offset = scan_start + boundary.start()
                scanned = True
                continue
            offset += length
        if offset == len(content) - 2 and content[offset:] == b"\xff\xd9":
            ended = True
        if not scanned or not ended:
            raise TaskError("invalid_image: incomplete JPEG image")
        image_format = "jpeg"
    elif len(content) >= 20 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        if int.from_bytes(content[4:8], "little") + 8 != len(content):
            raise TaskError("invalid_image: incomplete WebP container")
        offset = 12
        image_chunks = 0
        while offset + 8 <= len(content):
            chunk_type = content[offset:offset + 4]
            length = int.from_bytes(content[offset + 4:offset + 8], "little")
            end = offset + 8 + length
            if end > len(content):
                raise TaskError("invalid_image: truncated WebP chunk")
            payload = content[offset + 8:end]
            if chunk_type == b"VP8 ":
                frame = int.from_bytes(payload[:3], "little")
                if length <= 10 or frame & 1 or (frame >> 1) & 7 > 3 or not frame & 16 or not 0 < frame >> 5 < length - 10 or payload[3:6] != b"\x9d\x01\x2a":
                    raise TaskError("invalid_image: incomplete WebP key frame")
                width = int.from_bytes(payload[6:8], "little") & 0x3fff
                height = int.from_bytes(payload[8:10], "little") & 0x3fff
                image_chunks += 1
            elif chunk_type == b"VP8L":
                if length < 10 or payload[0] != 47 or payload[4] >> 5:
                    raise TaskError("invalid_image: incomplete WebP lossless frame")
                dimensions = int.from_bytes(payload[1:5], "little")
                width, height = (dimensions & 0x3fff) + 1, ((dimensions >> 14) & 0x3fff) + 1
                image_chunks += 1
            elif chunk_type == b"VP8X":
                if offset != 12 or length != 10 or payload[0] & 0xc3 or any(payload[1:4]):
                    raise TaskError("invalid_image: invalid WebP extended header")
            if length % 2 and (end >= len(content) or content[end] != 0):
                raise TaskError("invalid_image: invalid WebP chunk padding")
            offset = end + length % 2
        if offset != len(content) or image_chunks != 1:
            raise TaskError("invalid_image: malformed WebP chunks")
        image_format = "webp"
    else:
        raise TaskError("invalid_image: expected real PNG, JPEG, or WebP bytes")
    if not width or not height:
        raise TaskError("invalid_image: missing positive image dimensions")
    return {"format": image_format, "width": width, "height": height}


def _outputs(root: Path, outputs: Any) -> list[dict[str, Any]]:
    if isinstance(outputs, (str, Path, dict)):
        outputs = [outputs]
    if not isinstance(outputs, (list, tuple)) or not outputs:
        raise TaskError("outputs_required: a tool name or request ID is not an image receipt")
    result = []
    seen = set()
    for output in outputs:
        descriptor = output if isinstance(output, dict) else {"path": output}
        path, content = _read_bytes(root, descriptor.get("path"))
        relative = path.relative_to(root).as_posix()
        if path.relative_to(root).parts[0] == "tasks" or relative in seen:
            raise TaskError("invalid_output: duplicate or reserved task-store path")
        seen.add(relative)
        actual = hashlib.sha256(content).hexdigest()
        if descriptor.get("sha256", actual) != actual or descriptor.get("size_bytes", len(content)) != len(content):
            raise TaskError("output_changed: output bytes do not match the supplied receipt")
        result.append({"path": relative, "sha256": actual, "size_bytes": len(content), **_image_info(content)})
    return sorted(result, key=lambda output: output["path"])


def _view(root: Path, task: dict[str, Any], claimed: bool = False, reused: bool = True) -> dict[str, Any]:
    result = copy.deepcopy(task)
    status = task["status"]
    result.update(claimed=claimed, should_call=claimed, reused=False, resume_status=status)
    if status == "completed":
        try:
            if _outputs(root, task.get("outputs")) != task["outputs"]:
                raise TaskError("output_changed: recorded image metadata differs")
        except TaskError as error:
            result.update(action="reconcile", resume_status="output_invalid", outputs_valid=False, error=str(error))
        else:
            result.update(action="reused" if reused else "completed", reused=reused, outputs_valid=True)
    elif claimed:
        result["action"] = "call"
    elif status in {"requesting", "unknown"}:
        result.update(action="reconcile", resume_status="unknown", retryable=False)
    else:
        result["action"] = "retry_available" if task.get("retryable") is True else "stop"
    return result


def begin(root: str | Path, plan: dict[str, Any], *, authorization: dict[str, Any] | None = None,
          retry: bool = False, tool_request_id: str | None = None) -> dict[str, Any]:
    """Atomically claim a task; call a tool only when this returns should_call=True.

    A claim is fsynced independently even inside a project transaction. Retrying a
    requesting/unknown task is never allowed, irrespective of its PID or age.
    """
    directory = _root(root)
    if type(retry) is not bool:
        raise TaskError("invalid_retry: expected a boolean")
    checked = _checked_plan(directory, plan, authorization)
    if tool_request_id is not None:
        _text(tool_request_id, "tool_request_id")
    with task_lock(directory):
        checked = _checked_plan(directory, checked, None)
        task = _load_task(directory, checked["task_id"])
        if task is not None:
            if task["identity"] != checked["identity"] or task["request"] != checked["request"] or task["request_sha256"] != checked["request_sha256"]:
                raise TaskError("task_conflict: the same stable key has different request content")
            result = _view(directory, task)
            if result.get("resume_status") == "output_invalid":
                raise TaskError("output_invalid: restore or reconcile the completed output; do not resend")
            if task["status"] != "failed" or not retry:
                return result
            if task.get("retryable") is not True:
                raise TaskError("retry_forbidden: failure is not known to be safe to retry")
        approval = _authorization(checked["authorization"], checked["identity"], required=True)
        stamp = _stamp()
        history = [] if task is None else task.get("previous_attempts", []) + [{
            field: task.get(field) for field in ("attempt", "claim_id", "status", "reason", "tool_request_id", "started_at", "updated_at")
        }]
        task = {
            "schema_version": SCHEMA_VERSION, "kind": "generation-task",
            "task_id": checked["task_id"], "idempotency_key": checked["idempotency_key"],
            "identity": checked["identity"], "request": checked["request"],
            "request_sha256": checked["request_sha256"], "authorization": approval,
            "status": "requesting", "attempt": 1 if task is None else task["attempt"] + 1,
            "claim_id": uuid.uuid4().hex, "pid": os.getpid(), "tool_request_id": tool_request_id,
            "created_at": stamp if task is None else task["created_at"],
            "started_at": stamp, "updated_at": stamp, "previous_attempts": history,
        }
        _atomic_json(directory, _task_path(directory, task["task_id"]), task)
        return _view(directory, task, claimed=True)


claim = begin
start = begin


def _existing(root: Path, task_id: str, claim_id: str | None, tool_request_id: str | None) -> dict[str, Any]:
    task = _load_task(root, task_id)
    if task is None:
        raise TaskError("claim_required: begin() must persist a claim before any external call")
    if claim_id is not None and claim_id != task["claim_id"]:
        raise TaskError("stale_claim: receipt belongs to another attempt")
    if claim_id is None and task["attempt"] > 1:
        raise TaskError("claim_required: claim_id is required after a retry")
    if tool_request_id is not None:
        _text(tool_request_id, "tool_request_id")
        if task.get("tool_request_id") not in (None, tool_request_id):
            raise TaskError("tool_request_conflict: request ID is already bound to this attempt")
    return task


def complete(root: str | Path, task_id: str, outputs: Any, *, claim_id: str | None = None,
             tool_request_id: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record actual in-project images; repeat completion reuses unchanged bytes.

    outputs accepts a path, a path list, or descriptors with path and optional
    sha256/size_bytes. A recovered result may complete an unknown task without
    dispatching another request. claim_id is mandatory after an explicit retry.
    """
    directory = _root(root)
    _task_path(directory, task_id)
    details = _json_copy({} if metadata is None else metadata)
    if not isinstance(details, dict):
        raise TaskError("invalid_metadata: expected an object")
    with task_lock(directory):
        task = _existing(directory, task_id, claim_id, tool_request_id)
        receipts = _outputs(directory, outputs)
        if task["status"] == "completed":
            if receipts != task["outputs"] or (metadata is not None and details != task.get("metadata", {})):
                raise TaskError("completion_conflict: completed receipts are immutable")
            return _view(directory, task)
        if task["status"] not in {"requesting", "unknown"}:
            raise TaskError("invalid_transition: only a requesting or unknown task can complete")
        task.update(status="completed", outputs=receipts, metadata=details, completed_at=_stamp(), updated_at=_stamp())
        if tool_request_id is not None:
            task["tool_request_id"] = tool_request_id
        _atomic_json(directory, _task_path(directory, task_id), task)
        return _view(directory, task, reused=False)


def _finish(root: str | Path, task_id: str, status: str, reason: str, retryable: bool,
            claim_id: str | None, tool_request_id: str | None) -> dict[str, Any]:
    directory = _root(root)
    _task_path(directory, task_id)
    reason = _text(reason, "reason")
    if type(retryable) is not bool:
        raise TaskError("invalid_retryable: expected a boolean")
    with task_lock(directory):
        task = _existing(directory, task_id, claim_id, tool_request_id)
        if task["status"] == status:
            if task.get("reason") != reason or task.get("retryable") != retryable:
                raise TaskError("task_conflict: failure receipt is already recorded differently")
            return _view(directory, task)
        if task["status"] != "requesting":
            raise TaskError("invalid_transition: unknown/completed outcomes cannot be made retryable")
        task.update(status=status, reason=reason, retryable=retryable, updated_at=_stamp())
        if tool_request_id is not None:
            task["tool_request_id"] = tool_request_id
        _atomic_json(directory, _task_path(directory, task_id), task)
        return _view(directory, task)


def unknown(root: str | Path, task_id: str, reason: str, *, claim_id: str | None = None,
            tool_request_id: str | None = None) -> dict[str, Any]:
    """Persist uncertain outcome; reconcile local/remote evidence, never resend."""
    return _finish(root, task_id, "unknown", reason, False, claim_id, tool_request_id)


def failed(root: str | Path, task_id: str, reason: str, *, retryable: bool = False,
           claim_id: str | None = None, tool_request_id: str | None = None) -> dict[str, Any]:
    """Record a definite failure; retryable=True asserts no ambiguous remote work.

    Timeouts, lost responses and killed processes must use unknown(), not this
    assertion. Even a retryable failure is not dispatched without retry=True.
    """
    return _finish(root, task_id, "failed", reason, retryable, claim_id, tool_request_id)


def resume_status(root: str | Path, task_id: str | dict[str, Any]) -> dict[str, Any]:
    """Read without mutations or dispatch authority; requesting resumes as unknown."""
    directory = _root(root)
    _project_id(directory)
    if isinstance(task_id, dict):
        task_id = _checked_plan(directory, task_id, None)["task_id"]
    task = _load_task(directory, task_id)
    if task is None:
        return {"task_id": task_id, "status": "not_started", "resume_status": "not_started",
                "action": "prepare_or_begin", "claimed": False, "should_call": False, "reused": False}
    return _view(directory, task)
