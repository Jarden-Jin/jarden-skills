"""Optional Live Photo sidecar; local receipts never dispatch paid generation.

Helpers mutate only project.live_photo. Persistence and presentation belong to
project.execute_command, not this module. Status views do not write state.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asset_workflow
import generation_tasks


ACTIONS = ("plan", "begin", "record", "reconcile", "package", "attest", "status")
TARGETS = ("macos-photos", "iphone-photos", "publishing-app")
TARGET_STATUS = {"macos-photos": "macos-verified", "iphone-photos": "iphone-verified",
                 "publishing-app": "publishing-app-verified"}
BOUNDARY = "本机校验不等于 Photos 导入；人工／宿主证据仅登记其声明，各目标独立验收；不证明商业分辨率、无水印或发布兼容性。"
MOTION_SCOPE = "保持主体身份、比例、结构和画面构图，仅做轻微自然动态；不增加肢体、道具、文字或运镜。"


class LivePhotoWorkflowError(ValueError):
    """An unsafe path, changed receipt, or unmet Live Photo transition."""


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _state_api() -> Any:
    return asset_workflow._state_api()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LivePhotoWorkflowError(f"{field}_required: supply nonempty text")
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _descriptor(root: Path, value: Any, *, required_hash: bool = False) -> dict[str, Any]:
    if required_hash and (not isinstance(value, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("sha256", "")))):
        raise LivePhotoWorkflowError("descriptor_required: supply an in-project {path, sha256}")
    if isinstance(value, dict) and set(value) - {"path", "sha256", "size_bytes"}:
        raise LivePhotoWorkflowError("invalid_descriptor: only path, sha256 and size_bytes are accepted")
    return asset_workflow._file(root, value)


def _workflow(project: dict[str, Any]) -> dict[str, Any] | None:
    workflow = project.get("live_photo")
    if workflow is None:
        return None
    if not isinstance(workflow, dict) or workflow.get("schema_version") != 1 or not isinstance(workflow.get("jobs"), list):
        raise LivePhotoWorkflowError("invalid_live_schema: preserve the existing sidecar and reconcile its schema")
    return workflow


def _job(project: dict[str, Any], identifier: Any) -> dict[str, Any]:
    workflow = _workflow(project)
    for job in workflow["jobs"] if workflow else []:
        if job.get("id") == identifier:
            return job
    raise LivePhotoWorkflowError("unknown_job: use the id returned by live plan/status")


def _copy_file(root: Path, source: dict[str, Any], target: str) -> dict[str, Any]:
    return asset_workflow._write_artifact(root, source, target)


def _check(root: Path, job: dict[str, Any]) -> None:
    _descriptor(root, job.get("source"), required_hash=True)
    _descriptor(root, job.get("input"), required_hash=True)
    confirmation = job.get("confirmed_source_evidence") or {}
    if (confirmation.get("scope") != "source-only" or confirmation.get("source") != job.get("source")
            or not isinstance(confirmation.get("evidence"), str) or not confirmation["evidence"].strip()):
        raise LivePhotoWorkflowError("source_confirmation_required: source adoption must bind evidence and the source digest")
    if job["source"]["sha256"] != job["input"]["sha256"]:
        raise LivePhotoWorkflowError("source_changed: input snapshot differs from confirmed source")
    if job.get("video"):
        _descriptor(root, job["video"], required_hash=True)
        _descriptor(root, job.get("provider_receipt"), required_hash=True)
    for receipt in job.get("reconciliations") or []:
        _descriptor(root, receipt.get("provider_receipt"), required_hash=True)
        if receipt.get("binding") != {"signature": job["signature"], "claim_id": job.get("claim_id"), "source": job["source"]}:
            raise LivePhotoWorkflowError("reconciliation_binding_changed: receipt belongs to a different claim or source")
    bundle = job.get("package") or {}
    for field in ("photo", "video", "report", "archive", "bundle", "manifest"):
        if bundle.get(field):
            _descriptor(root, bundle[field], required_hash=True)
    for descriptor in bundle.get("pvt_files") or []:
        _descriptor(root, descriptor, required_hash=True)
    if bundle.get("pvt_path"):
        path = asset_workflow._path(root, bundle["pvt_path"])
        if not path.is_dir():
            raise LivePhotoWorkflowError("pvt_changed: the retained PVT directory is missing")


def _acceptance(root: Path | None, job: dict[str, Any], current: bool) -> dict[str, Any]:
    result = {}
    bundle = job.get("package") or {}
    binding = {field: (bundle.get(field) or {}).get("sha256") for field in ("photo", "video")}
    for target in TARGETS:
        receipts = [receipt for receipt in job.get("attestations", []) if receipt.get("target") == target]
        latest = receipts[-1] if receipts else None
        valid = bool(current and latest and latest.get("binding") == binding)
        if valid:
            try:
                for descriptor in latest.get("evidence_files") or []:
                    _descriptor(root, descriptor, required_hash=True)
                for descriptor in (latest.get("roundtrip") or {}).values():
                    _descriptor(root, descriptor, required_hash=True)
            except (ValueError, OSError):
                valid = False
        result[target] = {"verified": valid, "live_badge": bool(valid and latest.get("live_badge") is True),
                          "playback_confirmed": bool(valid and latest.get("playback_confirmed") is True),
                          "status": "recorded-attestation" if valid else "stale" if latest else "not-verified",
                          "evidence": latest.get("evidence") if latest else None,
                          "observer": latest.get("observer") if latest else None,
                          "receipt": copy.deepcopy(latest), "programmatically_observed": False}
    return result


def _job_view(root: Path | None, job: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(job)
    current = False
    reason = None
    if root is not None:
        try:
            _check(root, job)
            current = True
        except (ValueError, OSError) as error:
            reason = str(error)
    result.update(recorded_status=job["status"], stale=bool(root is not None and not current),
                  files_current=current, stale_reason=reason, should_call=False)
    result["acceptance"] = _acceptance(root, job, current)
    if result["stale"]:
        result["status"] = "stale"
    elif job.get("package"):
        result["status"] = "packaged"
        for target in TARGETS:
            if result["acceptance"][target]["verified"]:
                result["status"] = TARGET_STATUS[target]
    result["passed_local_validation"] = bool(current and (job.get("package") or {}).get("verification", {}).get("passed_local_validation") is True)
    return result


def _next(job: dict[str, Any] | None) -> dict[str, Any]:
    if job is None:
        return {"action": "plan", "should_call": False, "say": "可选 Live 尚未启用；不影响静态资产下一步。"}
    status = job["status"]
    action = {"planned": "begin-or-import", "requesting": "reconcile", "unknown": "reconcile",
              "failed": "reconcile", "video-recorded": "package", "motion-review": "review-motion",
              "package-failed": "package", "stale": "reconcile"}.get(status, "attest")
    pending = [target for target in TARGETS if not job["acceptance"][target]["verified"]]
    if action == "attest" and not pending:
        action = "done"
    return {"action": action, "job_id": job["id"], "should_call": False, "pending_targets": pending,
            "say": "只推进可选 Live sidecar；requesting/unknown 不重发，目标验收分别登记。"}


def view(root: Path | None, project: dict[str, Any]) -> dict[str, Any]:
    workflow = _workflow(project)
    directory = Path(root).resolve() if root is not None else None
    jobs = [_job_view(directory, job) for job in workflow["jobs"]] if workflow else []
    latest = jobs[-1] if jobs else None
    return {"enabled": workflow is not None, "schema_version": 1, "jobs": jobs,
            "current": copy.deepcopy(latest), "latest": copy.deepcopy(latest), "next": _next(latest),
            "claim_boundary": BOUNDARY}


def _result(root: Path, job: dict[str, Any], *, unchanged: bool = False, **fields: Any) -> dict[str, Any]:
    return {"ok": True, "job_id": job["id"], "job": _job_view(root, job), "should_call": False,
            "unchanged": unchanged, "claim_boundary": BOUNDARY, **fields}


def plan(root: Path, project: dict[str, Any], source: str, *, prompt: str, model: str,
         evidence: str, provider: str = "libtv") -> dict[str, Any]:
    root = Path(root).resolve()
    prompt = _text(prompt, "prompt")
    model = _text(model, "model")
    provider = _text(provider, "provider")
    evidence = _text(evidence, "confirmed_source_evidence")
    descriptor = _descriptor(root, source)
    suffix = Path(descriptor["path"]).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg"}:
        raise LivePhotoWorkflowError("invalid_source: select an existing project-relative PNG/JPG")
    _, content = generation_tasks._read_bytes(root, descriptor["path"])
    generation_tasks._image_info(content)
    signature = _digest({"source": descriptor, "prompt": prompt, "model": model, "provider": provider,
                         "motion_scope": MOTION_SCOPE})
    workflow = _workflow(project)
    for existing in workflow["jobs"] if workflow else []:
        if existing.get("signature") == signature:
            _check(root, existing)
            return _result(root, existing, unchanged=True)
    identifier = "live-" + signature[:24]
    snapshot = _copy_file(root, descriptor, f"live-photo/{identifier}/input{suffix}")
    stamp = _stamp()
    job = {"id": identifier, "title": Path(descriptor["path"]).stem + " · Live Photo", "status": "planned",
           "signature": signature, "source": descriptor, "source_digest": descriptor["sha256"], "input": snapshot,
           "confirmed_source_evidence": {"scope": "source-only", "evidence": evidence, "source": copy.deepcopy(descriptor)},
           "prompt": prompt, "generation_prompt": prompt + "\n" + MOTION_SCOPE, "model": model, "provider": provider,
           "motion_scope": "preserve-subject-micro-motion", "duration": 3.0, "generation_authorized": False,
           "video": None, "provider_receipt": None, "package": None, "attestations": [],
           "acceptance": {}, "package_attempts": [], "created_at": stamp, "updated_at": stamp}
    if workflow is None:
        workflow = {"schema_version": 1, "jobs": []}
        project["live_photo"] = workflow
    workflow["jobs"].append(job)
    return _result(root, job)


def begin(root: Path, project: dict[str, Any], job_id: str, *, evidence: str) -> dict[str, Any]:
    root = Path(root).resolve()
    evidence = _text(evidence, "service_authorization_evidence")
    job = _job(project, job_id)
    _check(root, job)
    if job["status"] != "planned":
        return _result(root, job, unchanged=True, claimed=False, action="reconcile")
    job.update(status="requesting", generation_authorized=True, updated_at=_stamp(),
               authorization={"evidence": evidence, "provider": job["provider"], "model": job["model"],
                              "signature": job["signature"], "source_sha256": job["source"]["sha256"]},
               claim_id=uuid.uuid4().hex)
    return _result(root, job, should_call=True, claimed=True, action="call",
                   request={"provider": job["provider"], "model": job["model"], "prompt": job["generation_prompt"],
                            "source": copy.deepcopy(job["input"]), "live_duration": job["duration"],
                            "video_duration_policy": "read-current-provider-schema-before-dispatch"})


def record(root: Path, project: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {"job_id", "video", "provider_receipt", "motion_review", "quality", "watermark"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise LivePhotoWorkflowError("invalid_record: supply only job_id, video, provider_receipt, motion_review, quality, watermark")
    root = Path(root).resolve()
    job = _job(project, payload.get("job_id"))
    _check(root, job)
    video = _descriptor(root, payload.get("video"))
    if Path(video["path"]).suffix.lower() not in {".mp4", ".mov", ".m4v"}:
        raise LivePhotoWorkflowError("invalid_video: expected a nonempty MP4/MOV/M4V file")
    receipt = _descriptor(root, payload.get("provider_receipt"), required_hash=True)
    review = payload.get("motion_review")
    if not isinstance(review, dict) or set(review) != {"verdict", "reviewer", "evidence"}:
        raise LivePhotoWorkflowError("motion_review_required: supply verdict, reviewer and evidence")
    if review.get("verdict") not in {"pass", "needs_revision", "uncertain"}:
        raise LivePhotoWorkflowError("invalid_motion_verdict: pass, needs_revision or uncertain")
    _text(review.get("reviewer"), "motion_reviewer")
    _text(review.get("evidence"), "motion_evidence")
    if payload.get("quality") not in {"draft", "final"} or payload.get("watermark") not in {"preserved", "none-with-entitlement", "unknown"}:
        raise LivePhotoWorkflowError("quality_watermark_required: declare draft/final and preserved/none-with-entitlement/unknown")
    signature = _digest({"video": video, "provider_receipt": receipt, "motion_review": review,
                         "quality": payload["quality"], "watermark": payload["watermark"]})
    if job.get("record_signature") == signature:
        return _result(root, job, unchanged=True)
    if job.get("video"):
        if video["sha256"] != job["video"]["sha256"] or receipt["sha256"] != job["provider_receipt"]["sha256"]:
            raise LivePhotoWorkflowError("immutable_video: changed generation needs a new source/prompt plan")
        if job.get("package"):
            raise LivePhotoWorkflowError("immutable_review: packaged motion review cannot be rewritten")
    elif job["status"] not in {"planned", "requesting", "unknown", "failed"}:
        raise LivePhotoWorkflowError("invalid_record_state: cannot attach a generation in this state")
    folder = f"live-photo/{job['id']}/recorded"
    isolated_video = _copy_file(root, video, folder + "/video" + Path(video["path"]).suffix.lower())
    isolated_receipt = _copy_file(root, receipt, folder + "/provider-receipt" + Path(receipt["path"]).suffix)
    imported = job.get("imported", job["status"] == "planned")
    job.update(video=isolated_video, provider_receipt=isolated_receipt,
               recorded_from={"video": video, "provider_receipt": receipt}, imported=imported,
               motion_review=copy.deepcopy(review), quality=payload["quality"], watermark=payload["watermark"],
               record_signature=signature, status="video-recorded" if review["verdict"] == "pass" else "motion-review",
               updated_at=_stamp())
    return _result(root, job, imported=imported)


def reconcile(root: Path, project: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {"job_id", "status", "provider_receipt", "evidence", "claim_id", "signature", "source"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise LivePhotoWorkflowError("invalid_reconciliation: supply job_id, failed/unknown, provider_receipt and evidence")
    root = Path(root).resolve()
    job = _job(project, payload.get("job_id"))
    _check(root, job)
    if job["status"] not in {"requesting", "unknown", "failed"} or job.get("video") or job.get("package"):
        raise LivePhotoWorkflowError("invalid_reconciliation_state: reconcile only an unresolved claim without a video")
    if not job.get("claim_id"):
        raise LivePhotoWorkflowError("claim_required: reconcile the claim returned by begin")
    if payload.get("status") not in {"failed", "unknown"}:
        raise LivePhotoWorkflowError("invalid_reconciliation_status: only failed or unknown can be recorded")
    for field in ("claim_id", "signature"):
        if field in payload and payload[field] != job[field]:
            raise LivePhotoWorkflowError("stale_reconciliation: claim or plan signature no longer matches")
    if "source" in payload and _descriptor(root, payload["source"], required_hash=True) != job["source"]:
        raise LivePhotoWorkflowError("stale_reconciliation: source does not match the current confirmed input")
    evidence = _text(payload.get("evidence"), "reconciliation_evidence")
    receipt = _descriptor(root, payload.get("provider_receipt"), required_hash=True)
    binding = {"signature": job["signature"], "claim_id": job["claim_id"], "source": copy.deepcopy(job["source"])}
    signature = _digest({"job_id": job["id"], "status": payload["status"], "evidence": evidence,
                         "provider_receipt": receipt, "binding": binding})
    if any(entry.get("signature") == signature for entry in job.get("reconciliations") or []):
        return _result(root, job, unchanged=True)
    destination = f"live-photo/{job['id']}/reconciliations/{signature}/provider-receipt" + Path(receipt["path"]).suffix
    isolated = _copy_file(root, receipt, destination)
    _check(root, job)
    entry = {"job_id": job["id"], "status": payload["status"], "evidence": evidence,
             "provider_receipt": isolated, "binding": binding, "signature": signature, "recorded_at": _stamp()}
    job.setdefault("reconciliations", []).append(entry)
    job.update(status=payload["status"], reconciliation=copy.deepcopy(entry), updated_at=_stamp())
    return _result(root, job)


def _native_descriptor(root: Path, value: Any, directory: Path) -> dict[str, Any]:
    value = value.get("path") if isinstance(value, dict) else value
    if not isinstance(value, (str, Path)):
        raise LivePhotoWorkflowError("native_output_required: native package must return real file paths")
    path = Path(value)
    relative = path.relative_to(root).as_posix() if path.is_absolute() and path.is_relative_to(root) else str(value)
    descriptor = _descriptor(root, relative)
    if not asset_workflow._path(root, descriptor["path"]).is_relative_to(directory):
        raise LivePhotoWorkflowError("native_output_outside_package: keep generated files in the unique package directory")
    return descriptor


def _archive(root: Path, directory: Path, bundle: dict[str, Any]) -> dict[str, Any]:
    manifest = directory / "manifest.json"
    with manifest.open("x", encoding="utf-8") as stream:
        json.dump({"schema_version": 1, "photo": bundle["photo"], "video": bundle["video"],
                   "pvt_path": bundle.get("pvt_path"), "pvt_files": bundle.get("pvt_files", []),
                   "verification": bundle["verification"], "claim_boundary": BOUNDARY}, stream,
                  ensure_ascii=False, indent=2)
    bundle["manifest"] = _descriptor(root, manifest.relative_to(root).as_posix())
    descriptors = [bundle[field] for field in ("photo", "video", "report", "manifest") if bundle.get(field)]
    descriptors.extend(bundle.get("pvt_files", []))
    destination = directory / "LIVE.zip"
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for descriptor in descriptors:
            _descriptor(root, descriptor, required_hash=True)
            source = asset_workflow._path(root, descriptor["path"])
            archive.write(source, source.relative_to(directory).as_posix())
    return _descriptor(root, destination.relative_to(root).as_posix())


def package(root: Path, project: dict[str, Any], job_id: str) -> dict[str, Any]:
    root = Path(root).resolve()
    job = _job(project, job_id)
    _check(root, job)
    if not job.get("video") or (job.get("motion_review") or {}).get("verdict") != "pass":
        raise LivePhotoWorkflowError("motion_pass_required: record the real video and a passing motion review before packaging")
    if job.get("package"):
        return _result(root, job, unchanged=True)
    relative = f"live-photo/{job['id']}/packages/{uuid.uuid4().hex}"
    directory = asset_workflow._path(root, relative)
    attempt = {"directory": relative, "status": "packaging", "started_at": _stamp()}
    job.setdefault("package_attempts", []).append(attempt)
    try:
        native = importlib.import_module("live_photo_native")
        generated = native.package(asset_workflow._path(root, job["video"]["path"]), directory,
                                   name="LIVE", duration=job["duration"])
        if not isinstance(generated, dict):
            raise LivePhotoWorkflowError("native_result_required: package must return file descriptors")
        if generated.get("passed_local_validation") is not True:
            raise LivePhotoWorkflowError("native_validation_failed: retain diagnostics and explicitly repeat packaging")
        photo = _native_descriptor(root, generated.get("photo"), directory)
        video = _native_descriptor(root, generated.get("video"), directory)
        verification = native.verify(asset_workflow._path(root, photo["path"]), asset_workflow._path(root, video["path"]))
        if not isinstance(verification, dict) or verification.get("passed_local_validation") is not True:
            raise LivePhotoWorkflowError("local_verification_failed: package retained for diagnosis; no Photos import claimed")
        _check(root, job)
        _descriptor(root, photo, required_hash=True)
        _descriptor(root, video, required_hash=True)
        bundle = {"photo": photo, "video": video, "pvt_path": None,
                  "verification": {"passed_local_validation": True, "scope": "local-files-only"}}
        report = directory / "verification.json"
        if generated.get("report"):
            bundle["report"] = _native_descriptor(root, generated["report"], directory)
        elif report.exists():
            bundle["report"] = _descriptor(root, report.relative_to(root).as_posix())
        value = generated.get("pvt_package") or generated.get("pvt")
        value = value.get("path") if isinstance(value, dict) else value
        if not isinstance(value, (str, Path)) or not str(value):
            raise LivePhotoWorkflowError("pvt_required: native package must produce a complete PVT directory")
        path = Path(value)
        scoped = asset_workflow._path(root, path.relative_to(root).as_posix() if path.is_absolute() and path.is_relative_to(root) else str(value))
        if not scoped.is_relative_to(directory) or not scoped.is_dir():
            raise LivePhotoWorkflowError("invalid_pvt: expected a package-local directory")
        _descriptor(root, (scoped / "metadata.plist").relative_to(root).as_posix())
        for resource in (photo, video):
            copied = _descriptor(root, (scoped / Path(resource["path"]).name).relative_to(root).as_posix())
            if copied["sha256"] != resource["sha256"]:
                raise LivePhotoWorkflowError("pvt_resource_changed: PVT resources must match the verified pair")
        bundle["pvt_path"] = scoped.relative_to(root).as_posix()
        bundle["pvt_files"] = [_descriptor(root, item.relative_to(root).as_posix()) for item in sorted(scoped.rglob("*")) if item.is_file()]
        bundle["archive"] = _archive(root, directory, bundle)
        job.update(package=bundle, status="packaged", updated_at=_stamp())
        attempt.update(status="packaged", finished_at=_stamp())
    except Exception as error:
        attempt.update(status="failed", error=type(error).__name__, finished_at=_stamp())
        job.update(status="package-failed", updated_at=_stamp(),
                   error={"code": "package_failed", "type": type(error).__name__, "directory": relative,
                          "recovery": "Inspect retained local outputs, restore dependencies, then explicitly repeat live package; do not regenerate video."})
        return _result(root, job, ok=False, error=copy.deepcopy(job["error"]))
    job.pop("error", None)
    return _result(root, job)


def attest(root: Path, project: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {"job_id", "target", "evidence", "live_badge", "playback_confirmed", "observer", "evidence_files", "roundtrip"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise LivePhotoWorkflowError("invalid_attestation: supply target, observation text, observer and real evidence_files")
    root = Path(root).resolve()
    job = _job(project, payload.get("job_id"))
    _check(root, job)
    bundle = job.get("package") or {}
    if (bundle.get("verification") or {}).get("passed_local_validation") is not True:
        raise LivePhotoWorkflowError("package_required: attest only a current locally verified pair")
    target = payload.get("target")
    if target not in TARGETS:
        raise LivePhotoWorkflowError("invalid_target: macos-photos, iphone-photos or publishing-app")
    if payload.get("live_badge") is not True or payload.get("playback_confirmed") is not True:
        raise LivePhotoWorkflowError("observation_required: live_badge and playback_confirmed must both be true")
    evidence = _text(payload.get("evidence"), "attestation_evidence")
    observer = _text(payload.get("observer"), "observer")
    files = payload.get("evidence_files")
    if not isinstance(files, list) or not files:
        raise LivePhotoWorkflowError("evidence_files_required: supply at least one real {path, sha256}")
    files = [_descriptor(root, value, required_hash=True) for value in files]
    roundtrip = payload.get("roundtrip")
    if roundtrip is not None:
        if target == "publishing-app" or not isinstance(roundtrip, dict) or set(roundtrip) != {"photo", "video"}:
            raise LivePhotoWorkflowError("invalid_roundtrip: Photos roundtrip requires photo and video descriptors")
        roundtrip = {field: _descriptor(root, value, required_hash=True) for field, value in roundtrip.items()}
        if any(roundtrip[field]["sha256"] != bundle[field]["sha256"] for field in ("photo", "video")):
            raise LivePhotoWorkflowError("roundtrip_changed: exported originals do not match the packaged pair")
    entry = {"target": target, "evidence": evidence, "observer": observer, "live_badge": True,
             "playback_confirmed": True, "evidence_files": files, "roundtrip": roundtrip,
             "binding": {field: bundle[field]["sha256"] for field in ("photo", "video")},
             "kind": "human-or-host-attestation", "programmatically_observed": False}
    signature = _digest(entry)
    if any(item.get("signature") == signature for item in job["attestations"]):
        return _result(root, job, unchanged=True)
    entry.update(signature=signature, recorded_at=_stamp())
    job["attestations"].append(entry)
    job["acceptance"] = _acceptance(root, job, True)
    job["status"] = "packaged"
    for accepted_target in TARGETS:
        if job["acceptance"][accepted_target]["verified"]:
            job["status"] = TARGET_STATUS[accepted_target]
    job["updated_at"] = _stamp()
    return _result(root, job)


def _dispatch(args: argparse.Namespace) -> int:
    if args.action not in ACTIONS:
        raise LivePhotoWorkflowError("action_required: choose plan, begin, record, reconcile, package, attest or status")
    state = _state_api()
    root = Path(args.project).expanduser().resolve()
    if args.action == "status":
        project = state.load_project(root)
        return state.emit({"ok": True, **view(root, project), "revision": project.get("revision", 0)})
    with state.project_lock(root):
        project = state.load_project(root, writable=True)
        if args.action == "plan":
            result = plan(root, project, args.source, prompt=args.prompt, model=args.model,
                          evidence=args.evidence, provider=args.provider)
        elif args.action == "begin":
            result = begin(root, project, args.job, evidence=args.evidence)
        elif args.action == "package":
            result = package(root, project, args.job)
        else:
            if not args.file:
                raise LivePhotoWorkflowError("file_required: supply a project-relative JSON receipt")
            payload = generation_tasks._read_json(root, asset_workflow._path(root, args.file))
            handlers = {"record": record, "reconcile": reconcile, "attest": attest}
            result = handlers[args.action](root, project, payload)
        if not result.get("unchanged"):
            state.save_project(root, project)
        result["revision"] = project.get("revision", 0)
        return state.emit(result)


def add_parser(subparsers: Any) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("live", help="可选 Live Photo 状态、真实视频回执、本机打包和逐目标人工验收")
    parser.add_argument("project")
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("--source")
    parser.add_argument("--prompt")
    parser.add_argument("--model")
    parser.add_argument("--provider", default="libtv")
    parser.add_argument("--evidence")
    parser.add_argument("--job")
    parser.add_argument("--file")
    parser.set_defaults(func=_dispatch)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="可选 Live sidecar；不调用外网，不启用静态流水线")
    add_parser(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    state = _state_api()
    try:
        from runtime_activity import execution_lease
        with execution_lease():
            return state.execute_command(args)
    except (ValueError, OSError, state.ProjectError) as error:
        return state.command_error(error)


if __name__ == "__main__":
    raise SystemExit(main())
