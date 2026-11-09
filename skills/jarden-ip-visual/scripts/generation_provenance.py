"""Read-only local provenance checks for record, select, and final callers.

verify_generated returns a fresh JSON descriptor, never an approval. A completed
local ledger binds a request to current local bytes; it cannot prove an external
tool actually ran, that a claim preceded a remote call, or that pixels are good.
Call begin before dispatching new work; never backfill claims for existing art.

generation_task may be a task ID or a descriptor containing task_id. Without it,
discovery matches actual output path, hash, and size, then verifies the request.
Keep the original compiled descriptor on the item when it contains local refs:
the ledger's semantic request preserves hashes but intentionally omits paths.
Derived/composed descriptors establish only the presence of their local files.
Re-run at every gate; a stored descriptor is not authority for later bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

import generation_tasks


class ProvenanceError(ValueError):
    """Missing, stale, mismatched, or unreadable local provenance."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ProvenanceError(f"invalid_{field}: expected nonempty, unpadded text")
    return value


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class _Files:
    def __init__(self, root: str | Path):
        supplied = Path(root).expanduser().absolute()
        if ".." in supplied.parts or any(path.is_symlink() for path in (supplied, *supplied.parents)):
            raise ProvenanceError("unsafe_root: traversal and symlinks are forbidden")
        self.root = supplied.resolve(strict=True)
        info = self.root.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise ProvenanceError("invalid_root: expected a project directory")
        self.root_identity = info.st_dev, info.st_ino
        self.observed: dict[str, tuple[Any, ...]] = {}

    def relative(self, value: Any, *, absolute: bool = False) -> str:
        if isinstance(value, Path):
            value = str(value)
        path = Path(_text(value, "path"))
        if ".." in path.parts or not path.parts or path.is_absolute() and not absolute:
            raise ProvenanceError("unsafe_path: use an in-project path without traversal")
        if path.is_absolute():
            try:
                path = path.relative_to(self.root)
            except ValueError as error:
                raise ProvenanceError("unsafe_path: file is outside this project") from error
        if not path.parts:
            raise ProvenanceError("unsafe_path: expected a file, not the project root")
        return path.as_posix()

    def directory(self, relative: str | None = None) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(self.root, flags)
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != self.root_identity:
                raise ProvenanceError("project_changed: project directory was replaced")
            for component in Path(relative).parts if relative else ():
                child = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def read(self, relative: str) -> bytes:
        path = Path(relative)
        parent = self.directory(path.parent.as_posix())
        try:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ProvenanceError("invalid_file: expected a regular project file")
                content = stream.read()
                after = os.fstat(stream.fileno())
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        finally:
            os.close(parent)
        if _signature(before) != _signature(after) or _signature(after) != _signature(current) or len(content) != after.st_size:
            raise ProvenanceError("file_changed: file changed during verification")
        token = (_signature(after), hashlib.sha256(content).hexdigest())
        previous = self.observed.setdefault(relative, token)
        if previous != token:
            raise ProvenanceError("file_changed: previously checked evidence changed")
        return content

    def json(self, relative: str) -> dict[str, Any]:
        value = json.loads(self.read(relative))
        if not isinstance(value, dict):
            raise ProvenanceError("invalid_record: expected a JSON object")
        return value

    def file(self, value: Any, *, absolute: bool = False) -> dict[str, Any]:
        supplied = value if isinstance(value, dict) else {"path": value}
        relative = self.relative(supplied.get("path"), absolute=absolute)
        content = self.read(relative)
        result = {"path": relative, "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}
        for field in ("sha256", "size_bytes"):
            if field in supplied and (type(supplied[field]) is not type(result[field]) or supplied[field] != result[field]):
                raise ProvenanceError("file_changed: source descriptor does not match current bytes")
        return result

    def finish(self) -> None:
        for relative in list(self.observed):
            self.read(relative)
        descriptor = self.directory()
        os.close(descriptor)


def _references(files: _Files, value: Any, field: str = "") -> None:
    reference_lists = {"bindings", "references", "refs", "reference_images", "input_images"}
    path_fields = {"path", "image_path", "reference_path"}
    if field in reference_lists:
        if not isinstance(value, list):
            raise ProvenanceError("invalid_references: compiled references must be a list")
        for reference in value:
            if isinstance(reference, str) and field != "bindings":
                files.file(reference, absolute=True)
            elif isinstance(reference, dict) and path_fields.intersection(reference):
                _references(files, reference)
            else:
                raise ProvenanceError("reference_paths_required: keep original compiled local reference paths on the item")
    elif isinstance(value, list):
        for child in value:
            _references(files, child)
    elif isinstance(value, dict):
        for name, child in value.items():
            if name in path_fields:
                files.file(child, absolute=True)
            else:
                _references(files, child, name)


def _task_id(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("task_id")
    if not isinstance(value, str) or re.fullmatch(r"gen-[0-9a-f]{64}", value) is None:
        raise ProvenanceError("invalid_generation_task: expected a task ID or task_id descriptor")
    return value


def _verify_task(files: _Files, project: dict[str, Any], item: dict[str, Any],
                 task_id: str, output: dict[str, Any]) -> dict[str, Any]:
    task = files.json(f"tasks/{_task_id(task_id)}.json")
    current = generation_tasks.resume_status(files.root, task_id)
    if any(current.get(field) != value for field, value in task.items()):
        raise ProvenanceError("task_changed: ledger changed while it was being read")
    if current.get("status") != "completed" or current.get("outputs_valid") is not True:
        raise ProvenanceError("task_incomplete: requires completed ledger and outputs_valid=true")
    identity = current["identity"]
    if identity["project_id"] != project["project_id"]:
        raise ProvenanceError("project_mismatch: task belongs to another project")
    slots = {item[field] for field in ("id", "slot_id", "review_slot_id")
             if isinstance(item.get(field), str) and item[field].strip()}
    if identity["slot_id"] not in slots:
        raise ProvenanceError("slot_mismatch: task must match item id, slot_id, or review_slot_id")
    if not any(all(record.get(field) == value for field, value in output.items()) for record in current["outputs"]):
        raise ProvenanceError("output_mismatch: task does not bind this round image path, sha256, and size")
    request_id = _text(current.get("tool_request_id"), "tool_request_id")
    compiled = item.get("compiled", current["request"]["compiled"])
    prompt = item.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or not isinstance(compiled, dict) or compiled.get("prompt") != prompt:
        raise ProvenanceError("prompt_mismatch: actual compiled prompt must equal item.prompt")
    if current["request"]["compiled"].get("prompt") != item["prompt"]:
        raise ProvenanceError("prompt_mismatch: ledger prompt must equal item.prompt")
    _references(files, compiled)
    checked = generation_tasks.prepare(files.root, project, identity["slot_id"], identity["intent_id"],
                                       compiled, current["request"]["backend"], current["authorization"])
    for field in ("task_id", "identity", "idempotency_key", "request", "request_sha256"):
        if checked[field] != current[field]:
            raise ProvenanceError("request_mismatch: compiled content or current reference bytes changed")
    return {"scope": "local-task-ledger", "task_id": task_id, "slot_id": identity["slot_id"],
            "claim_id": current["claim_id"], "tool_request_id": request_id,
            "receipt_kind": (current.get("metadata") or {}).get("receipt_kind", "tool-returned-identifier"),
            "server_request_id": (current.get("metadata") or {}).get("server_request_id"),
            "request_sha256": current["request_sha256"], "outputs_valid": True}


def _discover(files: _Files, project: dict[str, Any], item: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    directory = files.directory("tasks")
    try:
        names = sorted(os.listdir(directory))
    finally:
        os.close(directory)
    matches = []
    failures = []
    for name in names:
        if re.fullmatch(r"gen-[0-9a-f]{64}\.json", name) is None:
            continue
        task = files.json("tasks/" + name)
        outputs = task.get("outputs")
        if not isinstance(outputs, list) or not any(isinstance(record, dict) and all(record.get(field) == value
                for field, value in output.items()) for record in outputs):
            continue
        try:
            matches.append(_verify_task(files, project, item, name[:-5], output))
        except (ProvenanceError, generation_tasks.TaskError) as error:
            failures.append(str(error))
    if len(matches) > 1:
        raise ProvenanceError("ambiguous_generation_task: set item.generation_task explicitly")
    if not matches:
        detail = "; ".join(failures) if failures else "no ledger matches the actual output path, sha256, and size"
        raise ProvenanceError("generation_task_not_found: " + detail)
    return matches[0]


def verify_generated(root: str | Path, project: dict[str, Any], entry: dict[str, Any],
                     item: dict[str, Any]) -> dict[str, Any]:
    """Verify current local evidence and return a JSON receipt, or ProvenanceError.

    entry.dir and item.image are project/round-relative paths respectively.
    generate requires a completed generation_tasks ledger. derive/compose require
    nonempty sources (project-relative paths or path/hash/size descriptors), and
    return source descriptors without asserting generation or visual approval.
    No files, approvals, claims, network requests, or cached passes are written.
    """
    try:
        if not all(isinstance(value, dict) for value in (project, entry, item)):
            raise ProvenanceError("invalid_arguments: project, entry, and item must be objects")
        files = _Files(root)
        project_id = _text(project.get("project_id"), "project_id")
        if files.json("project.json").get("project_id") != project_id:
            raise ProvenanceError("project_mismatch: supplied project_id differs from current project.json")
        directory = files.relative(entry.get("dir"))
        image = files.relative(item.get("image"))
        output = files.file(directory + "/" + image)
        method = item.get("method", "generate")
        receipt = {"schema_version": 1, "kind": "generation-provenance", "method": method,
                   "project_id": project_id, **output,
                   "external_generation_verified": False, "visual_quality_verified": False}
        if method == "generate":
            if "generation_task" in item:
                evidence = _verify_task(files, project, item, _task_id(item["generation_task"]), output)
            else:
                evidence = _discover(files, project, item, output)
        elif method in ("derive", "compose"):
            sources = item.get("sources")
            if not isinstance(sources, list) or not sources:
                raise ProvenanceError("sources_required: derive/compose need real project-local source files")
            evidence = {"scope": "local-files", "sources": [files.file(source) for source in sources]}
        else:
            raise ProvenanceError("invalid_method: expected generate, derive, or compose")
        files.finish()
        return {**receipt, **evidence}
    except ProvenanceError:
        raise
    except (generation_tasks.TaskError, OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
        raise ProvenanceError(f"provenance_unavailable: {error}") from error
