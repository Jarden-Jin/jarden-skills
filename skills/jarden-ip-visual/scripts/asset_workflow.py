"""Finite asset workflow; no generation dispatch and no implicit legacy stages.

Helpers mutate the supplied project. The CLI persists through project transactions.
submit leaves authentication, delivery checks, persistence and receipts to its caller.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asset_plan
import generation_provenance
import generation_tasks


SOURCE_KINDS = {"user-input", "reference-derived", "builtin-generation", "local-derive", "local-compose", "local-authoring"}
CONTENT_KINDS = {"flat", "illustration", "product", "scene", "document"}
VERDICTS = {"pass", "needs_revision", "uncertain"}
MANIFEST = "assets-output/manifest.json"
BOUNDARY = "程序验证本地文件、摘要、报告声明及适用范围；不证明实际独立看图、外部生图真实性、视觉质量或制造可用性。"
PRESETS = {
    "series-collection": ["07", "12", "16", "18"],
    "design-manuscript": ["01", "02", "05", "07", "08", "09"],
    "single-multiview": ["07", "09", "12", "14", "15"],
    "multi-scene": ["07", "12", "16", "18"],
}


class AssetWorkflowError(ValueError):
    """An unmet asset contract or unsafe state transition."""


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _state_api() -> Any:
    running = sys.modules.get("__main__")
    location = getattr(running, "__file__", None)
    if location and Path(location).resolve() == Path(__file__).with_name("project.py").resolve():
        return running
    import project
    return project


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssetWorkflowError(f"{field}_required: supply nonempty text")
    return value.strip()


def initialize(project: dict[str, Any], enabled: bool = True) -> None:
    if not isinstance(project.get("asset_workflow"), dict):
        project["asset_workflow"] = {
            "schema_version": 1, "enabled": bool(enabled), "selected": [], "subjects": [],
            "plan_revision": 0, "requirements": {}, "instances": [], "rounds": [],
            "choices": [], "intents": [], "exports": [],
        }
    else:
        project["asset_workflow"].setdefault("enabled", bool(enabled))


def is_enabled(project: dict[str, Any]) -> bool:
    workflow = project.get("asset_workflow")
    return isinstance(workflow, dict) and workflow.get("enabled") is True


def _workflow(project: dict[str, Any]) -> dict[str, Any]:
    if not is_enabled(project):
        raise AssetWorkflowError("assets_disabled: explicitly choose assets in a migrated schema-4 project first")
    return project["asset_workflow"]


def _path(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise AssetWorkflowError("invalid_path: use a project-relative regular file")
    try:
        return generation_tasks._scoped(Path(root).resolve(), relative)
    except generation_tasks.TaskError as error:
        raise AssetWorkflowError(str(error)) from error


def _file(root: Path, value: Any) -> dict[str, Any]:
    descriptor = value if isinstance(value, dict) else {"path": value}
    path = _path(root, descriptor.get("path"))
    try:
        _, content = generation_tasks._read_bytes(Path(root).resolve(), path)
    except generation_tasks.TaskError as error:
        raise AssetWorkflowError(str(error)) from error
    if not content:
        raise AssetWorkflowError("empty_file: an asset must contain real bytes")
    result = {"path": path.relative_to(Path(root).resolve()).as_posix(),
              "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}
    for field in ("sha256", "size_bytes"):
        if field in descriptor and descriptor[field] != result[field]:
            raise AssetWorkflowError(f"file_changed: {result['path']} no longer matches {field}")
    return result


def _json_evidence(root: Path, descriptor: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(descriptor, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(descriptor.get("sha256", ""))):
        raise AssetWorkflowError("report_descriptor_required: supply {path, sha256}, not an approval flag")
    actual = _file(root, descriptor)
    try:
        data = json.loads(_path(root, actual["path"]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AssetWorkflowError("invalid_report: expected a UTF-8 JSON report") from error
    if not isinstance(data, dict) or _file(root, actual) != actual:
        raise AssetWorkflowError("invalid_report: report changed or is not an object")
    return actual, data


def _key(asset_id: str, subject: str) -> str:
    return asset_plan.digest([asset_id, subject])


def _instance(project: dict[str, Any], identifier: Any) -> dict[str, Any]:
    for instance in _workflow(project)["instances"]:
        if instance["id"] == identifier:
            return instance
    raise AssetWorkflowError("unknown_instance: use instance_id from assets status/new")


def _signature(project: dict[str, Any], asset_id: str, subject: str) -> str:
    workflow = _workflow(project)
    brief = project.get("brief") or {}
    return asset_plan.digest({
        "definition": asset_plan.definition(asset_id), "subject": subject,
        "identity": {field: brief.get(field) for field in ("ip_name", "bible", "identity_features", "identity_revision", "keep")},
        "requirements": workflow["requirements"].get(_key(asset_id, subject), {}),
    })


def choose(root: Path, project: dict[str, Any], asset_ids: Any, *, evidence: str,
           append: bool = False, subjects: list[str] | None = None,
           requirements: dict[str, Any] | None = None, preset: str | None = None) -> dict[str, Any]:
    if not is_enabled(project):
        if project.get("schema_version") != 4:
            raise AssetWorkflowError("migration_required: explicitly migrate before enabling assets")
        initialize(project)
    workflow = _workflow(project)
    if preset is not None and preset not in PRESETS:
        raise AssetWorkflowError("unknown_preset: choose a visible asset combination")
    if type(append) is not bool:
        raise AssetWorkflowError("invalid_append: expected a boolean")
    chosen = asset_plan.normalize_ids(asset_ids or PRESETS.get(preset, []))
    evidence = _text(evidence, "selection_evidence")
    if subjects is None:
        subjects = workflow.get("subjects") or (project.get("answers") or {}).get("subjects")
        subjects = subjects or [str((project.get("brief") or {}).get("ip_name") or "主体")]
    if not isinstance(subjects, list) or not subjects or any(not isinstance(subject, str) or not subject.strip() for subject in subjects):
        raise AssetWorkflowError("invalid_subjects: supply a nonempty list of names")
    subjects = list(dict.fromkeys(subject.strip() for subject in subjects))
    selected = list(dict.fromkeys([*workflow["selected"], *chosen])) if append else chosen
    updated = copy.deepcopy(workflow["requirements"])
    if requirements is not None:
        if not isinstance(requirements, dict):
            raise AssetWorkflowError("invalid_requirements: expected asset-ID to object mapping")
        for identifier, values in requirements.items():
            identifier = asset_plan.normalize_ids([identifier])[0]
            if not isinstance(values, dict):
                raise AssetWorkflowError("invalid_requirements: per-asset requirements must be objects")
            for subject in subjects:
                updated[_key(identifier, subject)] = copy.deepcopy(values)
    asset_plan.digest(updated)
    if selected == workflow["selected"] and subjects == workflow["subjects"] and updated == workflow["requirements"]:
        return {"ok": True, "unchanged": True, "selected": selected}
    workflow.update(selected=selected, subjects=subjects, requirements=updated,
                    plan_revision=workflow["plan_revision"] + 1)
    workflow["choices"].append({"selected": selected[:], "subjects": subjects[:], "append": append,
                                "preset": preset, "evidence": evidence, "created_at": _stamp(),
                                "plan_revision": workflow["plan_revision"]})
    return {"ok": True, "selected": selected[:], "plan": asset_plan.compile_plan(project, _approved(root, project))}


def _decode(root: Path, descriptor: dict[str, Any], alpha: bool) -> dict[str, Any]:
    try:
        from PIL import Image
    except ImportError as error:
        raise AssetWorkflowError("pillow_required: image decoding unavailable; Markdown delivery still works") from error
    path = _path(root, descriptor["path"])
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image.load()
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise AssetWorkflowError("unsupported_image_format: use PNG, JPEG or WebP")
                expected = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}.get(path.suffix.lower())
                if image.format != expected:
                    raise AssetWorkflowError("image_format_mismatch: extension does not match decoded bytes")
                result = {"width": image.width, "height": image.height, "format": image.format,
                          "mode": image.mode, "decoded": True, "alpha_required": alpha}
        if alpha:
            import check_images
            inspection = check_images.inspect(path, argparse.Namespace(alpha=True, ratio=None, min_side=0, tolerance=0.02))
            if not inspection["ok"]:
                raise AssetWorkflowError("alpha_check_failed: " + "; ".join(inspection["problems"]))
            result["alpha"] = inspection
    except AssetWorkflowError:
        raise
    except (OSError, ValueError, SyntaxError, Warning, Image.DecompressionBombError) as error:
        raise AssetWorkflowError("image_decode_failed: " + str(error)) from error
    _file(root, descriptor)
    return result


def _files(root: Path, instance: dict[str, Any], files: Any) -> list[dict[str, Any]]:
    definition = asset_plan.definition(instance["asset_id"])
    if not isinstance(files, list) or not files:
        raise AssetWorkflowError("files_required: supply actual project-relative files")
    if definition["kind"] in {"image", "document"} and len(files) != 1:
        raise AssetWorkflowError("single_file_required: each candidate is one asset; use bundle for a library")
    actual = []
    for value in files:
        descriptor = _file(root, value)
        suffix = Path(descriptor["path"]).suffix.lower().lstrip(".")
        if suffix == "jpeg":
            suffix = "jpg"
        if suffix not in definition["delivery"]:
            raise AssetWorkflowError("unsupported_delivery_format: file does not match this asset's contract")
        if definition["kind"] == "document":
            try:
                text = _path(root, descriptor["path"]).read_text(encoding="utf-8")
            except UnicodeError as error:
                raise AssetWorkflowError("invalid_document: use readable UTF-8 Markdown") from error
            if not text.strip() or "\x00" in text:
                raise AssetWorkflowError("invalid_document: document must contain readable text")
            descriptor["inspection"] = {"format": "markdown", "characters": len(text), "decoded": True}
        else:
            descriptor["inspection"] = _decode(root, descriptor, definition["alpha_required"] or instance.get("alpha_required", False))
        if any(previous["path"] == descriptor["path"] for previous in actual):
            raise AssetWorkflowError("duplicate_file: bundle cannot list a path twice")
        actual.append(descriptor)
    return actual


def _file_binding(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{field: descriptor[field] for field in ("path", "sha256", "size_bytes")} for descriptor in files]


def _source(root: Path, project: dict[str, Any], instance: dict[str, Any], source: Any,
            files: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(source, dict) or source.get("kind") != instance["source_kind"]:
        raise AssetWorkflowError("source_kind_mismatch: distinguish input, reference, generation and derivation")
    result = copy.deepcopy(source)
    result["evidence"] = _text(source.get("evidence"), "source_evidence")
    result["producer"] = _text(source.get("producer"), "producer")
    kind = result["kind"]
    if kind == "local-authoring":
        if asset_plan.definition(instance["asset_id"])["kind"] != "document":
            raise AssetWorkflowError("invalid_source_kind: local authoring is for text documents only")
        result["receipts"] = [{"scope": "local-authored-document", "external_generation_verified": False}]
    elif kind == "user-input":
        inputs = {item.get("path"): item for item in project.get("inputs") or [] if isinstance(item, dict)}
        if any(descriptor["path"] not in inputs for descriptor in files):
            raise AssetWorkflowError("unregistered_input: import genuine user files as project inputs first")
        for descriptor in files:
            declared = inputs[descriptor["path"]]
            if declared.get("sha256") and declared["sha256"] != descriptor["sha256"]:
                raise AssetWorkflowError("input_changed: imported input no longer matches registered bytes")
        result["receipts"] = [{"scope": "registered-user-input", "input_kind": inputs[file["path"]].get("kind"),
                                **file} for file in _file_binding(files)]
    else:
        sources = source.get("sources") or []
        if kind != "builtin-generation":
            if not isinstance(sources, list) or not sources:
                raise AssetWorkflowError("sources_required: derived/composed files need real local sources")
            result["sources"] = [_file(root, descriptor) for descriptor in sources]
        receipts = []
        for descriptor in files:
            path = Path(descriptor["path"])
            if path.parent == Path("."):
                raise AssetWorkflowError("asset_directory_required: generated/derived files need a subdirectory")
            item = {"id": instance["id"], "slot_id": instance["id"], "image": path.name,
                    "method": "generate" if kind == "builtin-generation" else "compose" if kind == "local-compose" else "derive"}
            if kind == "builtin-generation":
                task_id = source.get("generation_task") or instance.get("generation_task")
                if task_id != instance.get("generation_task"):
                    raise AssetWorkflowError("generation_task_mismatch: retain this instance's planned task")
                item.update(generation_task=task_id, prompt=instance["compiled"]["prompt"], compiled=instance["compiled"])
            else:
                item["sources"] = result["sources"]
            try:
                receipts.append(generation_provenance.verify_generated(root, project, {"dir": path.parent.as_posix()}, item))
            except generation_provenance.ProvenanceError as error:
                raise AssetWorkflowError("provenance_blocked: " + str(error)) from error
        result["receipts"] = receipts
    return result


def review_template(project: dict[str, Any], instance: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1, "kind": "asset-review", "project_id": project["project_id"],
        "instance_id": instance["id"], "asset_id": instance["asset_id"],
        "requirements_signature": instance["requirements_signature"], "upstream_signature": instance["upstream_signature"],
        "files": _file_binding(instance.get("files") or []),
        "reviewer": {"id": "", "independent": False, "viewed_files": False}, "verdict": "uncertain",
        "checks": {criterion: {"verdict": "uncertain", "evidence": ""}
                   for criterion in asset_plan.definition(instance["asset_id"])["acceptance"]},
        "physical_applicability": {"required": instance.get("requires_physics", False),
                                   "content_kind": instance["content_kind"], "reason": ""},
        "claim_boundary": BOUNDARY,
    }


def _design_report(root: Path, project: dict[str, Any], instance: dict[str, Any], descriptor: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    actual, report = _json_evidence(root, descriptor)
    expected = review_template(project, instance)
    for field in ("schema_version", "kind", "project_id", "instance_id", "asset_id", "requirements_signature", "upstream_signature", "files"):
        if report.get(field) != expected[field]:
            raise AssetWorkflowError("review_scope_mismatch: report must bind this instance, bytes and upstream contract")
    reviewer = report.get("reviewer")
    if not isinstance(reviewer, dict) or reviewer.get("independent") is not True or reviewer.get("viewed_files") is not True:
        raise AssetWorkflowError("independent_review_required: independently inspect the actual files")
    if _text(reviewer.get("id"), "reviewer_id") == instance["source"]["producer"]:
        raise AssetWorkflowError("independent_review_required: producer cannot approve its own output")
    if report.get("verdict") not in VERDICTS or not isinstance(report.get("checks"), dict):
        raise AssetWorkflowError("invalid_review: verdict and criterion checks required")
    for criterion in asset_plan.definition(instance["asset_id"])["acceptance"]:
        check = report["checks"].get(criterion)
        if not isinstance(check, dict) or check.get("verdict") not in VERDICTS or len(_text(check.get("evidence"), "review_evidence")) < 8:
            raise AssetWorkflowError("review_checks_required: each acceptance criterion needs observable evidence")
        if report["verdict"] == "pass" and check["verdict"] != "pass":
            raise AssetWorkflowError("review_contradiction: failing or uncertain checks are not pass")
    applicability = report.get("physical_applicability")
    if not isinstance(applicability, dict) or type(applicability.get("required")) is not bool:
        raise AssetWorkflowError("review_applicability_required: independently explain whether G1/G2 apply")
    if applicability.get("content_kind") not in CONTENT_KINDS or len(_text(applicability.get("reason"), "applicability_reason")) < 8:
        raise AssetWorkflowError("review_applicability_required: describe actual content, not just a CLI flag")
    if (applicability["content_kind"] in {"product", "scene"} or instance["requires_physics"]) and not applicability["required"]:
        raise AssetWorkflowError("review_contradiction: real product/scene content cannot waive G1/G2")
    return actual, report


def _negative_pixels(root: Path, project: dict[str, Any], hashes: set[str]) -> None:
    for previous in _workflow(project)["instances"]:
        if not hashes.intersection(file["sha256"] for file in previous.get("files") or []):
            continue
        for record in previous.get("review_history") or []:
            _json_evidence(root, record["report"])
            if record["verdict"] != "pass":
                raise AssetWorkflowError("prior_review_failed: identical bytes retain failed/uncertain evidence; renaming/retyping is not a repair")


def _gates(root: Path, project: dict[str, Any], instance: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    image_paths = [file["path"] for file in instance["files"]] if instance["kind"] != "document" else []
    if image_paths:
        import review_checks
        for category in ("identity", "creative"):
            try:
                gate = review_checks.status(root, project, category, image_paths)
            except review_checks.ReviewError as error:
                raise AssetWorkflowError("existing_review_unavailable: " + str(error)) from error
            if gate.get("blocking"):
                raise AssetWorkflowError("existing_review_failed: " + json.dumps(gate, ensure_ascii=False))
    required = instance["requires_physics"] or report["physical_applicability"]["required"]
    source_files = instance["files"] + (instance["source"].get("sources") or [])
    hashes = {file["sha256"] for file in source_files}
    inputs = {item.get("path"): item for item in project.get("inputs") or [] if isinstance(item, dict)}
    required = required or any(inputs.get(file["path"], {}).get("kind") == "photo" for file in source_files)
    for previous in _workflow(project)["instances"]:
        if previous.get("requires_physics") and hashes.intersection(file["sha256"] for file in previous.get("files") or []):
            required = True
    if not required:
        return {"physical": "not_applicable", "reason": report["physical_applicability"]["reason"]}
    if not image_paths:
        raise AssetWorkflowError("physical_contract_blocked: real product claims need actual reviewed imagery")
    import contract_review
    import physics_review
    mappings = instance.get("review_slots")
    if not isinstance(mappings, list) or len(mappings) != len(image_paths):
        raise AssetWorkflowError("g1_binding_required: bind each product/scene file to an existing contract slot")
    g1 = contract_review.status(root, project, image_paths, stage="scene", ordered_slots=mappings)
    if g1.get("applicable") is not True or g1.get("blocking") is not False or g1.get("verdict") != "pass":
        raise AssetWorkflowError("g1_blocked: current applicable G1 contract required; unsupported contracts stay blocked")
    g2 = [{"path": path, **physics_review.status(root, project, path)} for path in image_paths]
    if any(row.get("verdict") != "pass" for row in g2):
        raise AssetWorkflowError("g2_blocked: independent current physical review required")
    g1_reviewers = {row.get("reviewer_id") for row in g1.get("paths") or []}
    if None in g1_reviewers or not g1_reviewers or any(row.get("reviewer_id") in g1_reviewers for row in g2):
        raise AssetWorkflowError("reviewer_separation_required: G1 and G2 need different independent reviewers")
    return {"physical": "pass", "g1": g1, "g2": g2}


def eligibility(root: Path | None, project: dict[str, Any], instance: dict[str, Any], *,
                reviews: dict[str, Any] | None = None, cache: dict[str, Any] | None = None,
                ancestry: set[str] | None = None) -> dict[str, Any]:
    cache = {} if cache is None else cache
    ancestry = set() if ancestry is None else ancestry
    if reviews is None and instance["id"] in cache:
        return cache[instance["id"]]
    result = {"selectable": False, "approvable": False, "approved": False, "reasons": [], "gates": {}}
    try:
        if root is None:
            raise AssetWorkflowError("root_required: files and reviews are not assessed in state-only views")
        if instance["id"] in ancestry:
            raise AssetWorkflowError("upstream_cycle: invalid dependency evidence")
        ancestry = ancestry | {instance["id"]}
        definition = asset_plan.definition(instance["asset_id"])
        if definition["support"] in {"unsupported", "unverified"}:
            raise AssetWorkflowError("unsupported_asset: " + definition.get("blocked_reason", "capability not verified"))
        if instance["requirements_signature"] != _signature(project, instance["asset_id"], instance["subject"]):
            raise AssetWorkflowError("requirements_changed: applicable requirements changed")
        if not instance.get("files"):
            raise AssetWorkflowError("files_missing: record actual files before intent or approval")
        if instance["upstream_signature"] != asset_plan.digest(instance["upstream"]):
            raise AssetWorkflowError("upstream_signature_mismatch: upstream binding changed")
        actual = _files(root, instance, instance["files"])
        _source(root, project, instance, instance.get("source"), actual)
        _negative_pixels(root, project, {file["sha256"] for file in actual})
        for upstream in instance["upstream"]:
            previous = _instance(project, upstream["instance_id"])
            valid = eligibility(root, project, previous, cache=cache, ancestry=ancestry)
            if not valid["approved"] or upstream["files"] != _file_binding(previous.get("files") or []):
                raise AssetWorkflowError("upstream_changed: necessary upstream bytes or approval changed")
            if upstream["requirements_signature"] != previous["requirements_signature"]:
                raise AssetWorkflowError("upstream_changed: applicable requirements changed")
        result["selectable"] = True
        descriptor = (reviews or {}).get("design") or instance.get("design_review")
        if not descriptor:
            raise AssetWorkflowError("design_review_required: direction intent is not formal approval")
        _, report = _design_report(root, project, instance, descriptor)
        if report["verdict"] != "pass":
            raise AssetWorkflowError("design_review_failed: failed/uncertain candidate cannot be adopted")
        result["gates"] = _gates(root, project, instance, report)
        result["approvable"] = True
        approval = instance.get("approval") or {}
        result["approved"] = (instance.get("status") == "approved"
                              and approval.get("files") == _file_binding(actual)
                              and approval.get("design_review") == descriptor
                              and approval.get("requirements_signature") == instance["requirements_signature"]
                              and approval.get("upstream_signature") == instance["upstream_signature"])
    except (AssetWorkflowError, asset_plan.AssetPlanError, generation_tasks.TaskError, OSError, ValueError, KeyError, TypeError) as error:
        result["reasons"].append(str(error))
    result["disabled_reason"] = "; ".join(result["reasons"])
    if reviews is None:
        cache[instance["id"]] = result
    return result


def _approved(root: Path | None, project: dict[str, Any]) -> dict[str, dict[str, Any]]:
    approved = {}
    cache: dict[str, Any] = {}
    for instance in _workflow(project)["instances"]:
        if instance.get("status") == "approved" and eligibility(root, project, instance, cache=cache)["approved"]:
            approved[_key(instance["asset_id"], instance["subject"])] = instance
    return approved


def _task_status(root: Path, instance: dict[str, Any]) -> dict[str, Any] | None:
    if not instance.get("generation_task"):
        return None
    try:
        return generation_tasks.resume_status(root, instance["generation_task"])
    except generation_tasks.TaskError as error:
        raise AssetWorkflowError("generation_reconcile_required: " + str(error)) from error


def new(root: Path, project: dict[str, Any], asset_id: str, *, subject: str | None = None,
        quality: str = "exploration", count: int | None = None, source_kind: str | None = None,
        prompt: str = "", intent_id: str | None = None, evidence: str = "",
        content_kind: str | None = None, alpha_required: bool = False,
        authorization: dict[str, Any] | None = None) -> dict[str, Any]:
    workflow = _workflow(project)
    if not workflow["selected"]:
        raise AssetWorkflowError("choose_assets_first: select this request's asset scope before generation")
    asset_id = asset_plan.normalize_ids([asset_id])[0]
    definition = asset_plan.definition(asset_id)
    if definition["support"] in {"unsupported", "unverified"}:
        raise AssetWorkflowError("unsupported_asset: " + definition.get("blocked_reason", "not verified"))
    allowed = {row["asset_id"] for row in asset_plan.compile_plan(project)["execution"]}
    if asset_id not in allowed:
        raise AssetWorkflowError("asset_outside_scope: choose/append the asset before planning")
    subjects = workflow["subjects"]
    if subject is None and len(subjects) != 1:
        raise AssetWorkflowError("subject_required: choose one subject from the saved scope")
    subject = subject or subjects[0]
    if subject not in subjects:
        raise AssetWorkflowError("subject_outside_scope: choose/append the subject before planning")
    if quality not in {"exploration", "final"}:
        raise AssetWorkflowError("invalid_quality: expected exploration or final")
    if type(alpha_required) is not bool:
        raise AssetWorkflowError("invalid_alpha_required: expected a boolean")
    source_kind = source_kind or ("local-authoring" if definition["kind"] == "document" else "builtin-generation")
    if source_kind not in SOURCE_KINDS or (source_kind not in definition["source_kinds"] and source_kind != "local-authoring"):
        raise AssetWorkflowError("unsupported_source_kind: source not supported for this asset")
    if source_kind == "local-authoring" and definition["kind"] != "document":
        raise AssetWorkflowError("invalid_source_kind: local authoring cannot stand in for image generation")
    count = count if count is not None else 1 if quality == "final" or definition["kind"] == "document" or source_kind == "user-input" else 2
    if type(count) is not int or not 1 <= count <= 5:
        raise AssetWorkflowError("invalid_count: choose between one and five candidates")
    content_kind = content_kind or ("document" if definition["kind"] == "document" else "product" if asset_id == "12" else "flat")
    if content_kind not in CONTENT_KINDS or (definition["kind"] == "document") != (content_kind == "document"):
        raise AssetWorkflowError("invalid_content_kind: content must match the asset kind")
    if asset_id == "12" and content_kind not in {"product", "scene"}:
        raise AssetWorkflowError("physical_contract_required: product imagery cannot be relabeled flat")
    signature = _signature(project, asset_id, subject)
    previous = [item for item in workflow["instances"] if item["asset_id"] == asset_id and item["subject"] == subject]
    for item in previous:
        task = _task_status(root, item)
        if task and task.get("status") in {"requesting", "unknown"}:
            return {"ok": True, "unchanged": True, "action": "reconcile-generation", "instance": copy.deepcopy(item),
                    "task": task, "should_call": False}
    same_intent = [entry for entry in workflow["rounds"] if entry["asset_id"] == asset_id and entry["subject"] == subject
                   and intent_id and entry["intent_id"] == intent_id]
    matching = [entry for entry in workflow["rounds"] if entry["asset_id"] == asset_id and entry["subject"] == subject
                and entry["requirements_signature"] == signature]
    recovered = same_intent[-1] if same_intent else matching[-1] if matching and not intent_id else None
    if recovered:
        return {"ok": True, "unchanged": True, "round": copy.deepcopy(recovered),
                "instances": [copy.deepcopy(_instance(project, identifier)) for identifier in recovered["instances"]],
                "should_call": False}
    if matching:
        _text(evidence, "new_intent_evidence")
    approved = _approved(root, project)
    upstream = []
    for prerequisite in definition["dependencies"]:
        dependency = approved.get(_key(prerequisite, subject))
        if not dependency:
            raise AssetWorkflowError(f"dependency_required: asset {asset_id} needs approved {prerequisite}; see the explicit plan")
        upstream.append({"asset_id": prerequisite, "instance_id": dependency["id"],
                         "requirements_signature": dependency["requirements_signature"], "files": _file_binding(dependency["files"])})
    if source_kind == "builtin-generation":
        prompt = _text(prompt, "prompt")
    if authorization is not None and (not isinstance(authorization, dict) or
            ((authorization.get("approved") or authorization.get("authorized")) and not authorization.get("evidence"))):
        raise AssetWorkflowError("generation_authorization_evidence_required: retain the user's actual authorized scope")
    round_id = "assets-" + uuid.uuid4().hex[:12]
    intent_id = intent_id or round_id
    instances = []
    plans = []
    references = [file for dependency in upstream for file in dependency["files"]]
    for index in range(count):
        identifier = round_id + "-" + "ABCDE"[index]
        instance = {
            "id": identifier, "round_id": round_id, "candidate": "ABCDE"[index], "asset_id": asset_id,
            "subject": subject, "kind": definition["kind"], "quality": quality, "status": "planned",
            "source_kind": source_kind, "content_kind": content_kind, "alpha_required": bool(alpha_required),
            "requires_physics": content_kind in {"product", "scene"} or definition.get("real_product") is True
                                or any(approved[_key(dependency["asset_id"], subject)].get("requires_physics") for dependency in upstream),
            "requirements_signature": signature, "upstream": copy.deepcopy(upstream),
            "upstream_signature": asset_plan.digest(upstream), "files": [], "created_at": _stamp(),
        }
        if source_kind == "builtin-generation":
            instance["compiled"] = {"prompt": prompt, "references": references, "asset_instance": identifier,
                                    "asset_id": asset_id, "asset_signature": signature,
                                    "upstream_signature": instance["upstream_signature"], "quality": quality,
                                    "content_kind": content_kind, "subject": subject}
            plan = generation_tasks.prepare(root, project, identifier, intent_id, instance["compiled"],
                {"kind": "builtin", "tool": "image_gen.imagegen"}, authorization or {"approved": False})
            instance["generation_task"] = plan["task_id"]
            instance["generation_plan"] = "asset-plans/" + plan["task_id"] + ".json"
            plans.append((instance["generation_plan"], plan))
        instances.append(instance)
    entry = {"id": round_id, "asset_id": asset_id, "subject": subject, "kind": "alternatives" if count > 1 else "set",
             "requirements_signature": signature, "intent_id": intent_id, "evidence": evidence,
             "instances": [instance["id"] for instance in instances], "created_at": _stamp()}
    if plans:
        state = _state_api()
        for relative, plan in plans:
            state.write_json(_path(root, relative), plan)
    workflow["instances"].extend(instances)
    workflow["rounds"].append(entry)
    return {"ok": True, "round": copy.deepcopy(entry), "instances": copy.deepcopy(instances), "should_call": False,
            "say": "仅建计划，未领取或调用；生图前用现有 task begin，unknown/requesting 只对账不重发。"}


def validate_generation_plan(root: Path, project: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    compiled = plan.get("compiled") or {}
    if not compiled.get("asset_instance"):
        return {"applicable": False}
    instance = _instance(project, compiled["asset_instance"])
    if plan.get("task_id") != instance.get("generation_task") or compiled != instance.get("compiled"):
        raise AssetWorkflowError("stale_asset_plan: task differs from the saved instance request")
    if instance["subject"] not in _workflow(project)["subjects"]:
        raise AssetWorkflowError("subject_outside_scope: subject was removed before a new claim")
    if instance["asset_id"] not in {row["asset_id"] for row in asset_plan.compile_plan(project)["execution"]}:
        raise AssetWorkflowError("asset_outside_scope: asset removed from selected dependency closure")
    if instance["requirements_signature"] != _signature(project, instance["asset_id"], instance["subject"]):
        raise AssetWorkflowError("stale_asset_plan: requirements changed before a new claim")
    for upstream in instance["upstream"]:
        dependency = _instance(project, upstream["instance_id"])
        if not eligibility(root, project, dependency)["approved"] or _file_binding(dependency["files"]) != upstream["files"]:
            raise AssetWorkflowError("stale_asset_plan: upstream bytes or approval changed before a new claim")
    return {"applicable": True, "valid": True, "instance_id": instance["id"]}


def record(root: Path, project: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {"instance_id", "files", "source", "reviews", "review_slots"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise AssetWorkflowError("invalid_record_payload: supply actual files/source and optional review descriptors")
    if "reviews" in payload and (not isinstance(payload["reviews"], dict) or set(payload["reviews"]) - {"design"}):
        raise AssetWorkflowError("invalid_reviews: record uses a design report; G1/G2 use their existing registered gates")
    instance = _instance(project, payload.get("instance_id"))
    if asset_plan.definition(instance["asset_id"])["support"] in {"unsupported", "unverified"}:
        raise AssetWorkflowError("unsupported_asset: files cannot manufacture an unsupported capability")
    updated = copy.deepcopy(instance)
    if "files" in payload:
        actual = _files(root, instance, payload["files"])
        if instance.get("files") and _file_binding(actual) != _file_binding(instance["files"]):
            raise AssetWorkflowError("immutable_record: keep old candidate; changed bytes need an explicit new intent")
        updated["files"] = actual
        updated["source"] = _source(root, project, updated, payload.get("source", instance.get("source")), actual)
        if instance.get("source") and instance["source"] != updated["source"]:
            raise AssetWorkflowError("immutable_source: retained origin cannot be rewritten after recording")
        if payload.get("review_slots") is not None:
            updated["review_slots"] = copy.deepcopy(payload["review_slots"])
        inputs = {item.get("path"): item for item in project.get("inputs") or [] if isinstance(item, dict)}
        source_paths = [file["path"] for file in actual] + [file["path"] for file in updated["source"].get("sources") or []]
        if any(inputs.get(path, {}).get("kind") == "photo" for path in source_paths):
            updated["requires_physics"] = True
        updated["recorded_at"] = _stamp()
        updated["status"] = instance["status"] if instance.get("status") == "approved" else "recorded"
    if not updated.get("files"):
        raise AssetWorkflowError("files_required: register actual files before a review")
    descriptor = (payload.get("reviews") or {}).get("design")
    if descriptor:
        actual, report = _design_report(root, project, updated, descriptor)
        updated.setdefault("review_history", []).append({"report": actual, "verdict": report["verdict"], "recorded_at": _stamp()})
        updated["design_review"] = actual
        if report["verdict"] != "pass":
            updated["status"] = "needs_revision"
    instance.clear()
    instance.update(updated)
    return {"ok": True, "instance": copy.deepcopy(instance), "eligibility": eligibility(root, project, instance),
            "review_template": review_template(project, instance), "claim_boundary": BOUNDARY}


def approve(root: Path, project: dict[str, Any], instance_id: str, *, evidence: str,
            reviews: dict[str, Any] | None = None, origin: str = "user-confirmed") -> dict[str, Any]:
    instance = _instance(project, instance_id)
    evidence = _text(evidence, "adoption_evidence")
    if origin != "user-confirmed":
        raise AssetWorkflowError("user_adoption_required: inferred direction is not formal user adoption")
    if not isinstance(reviews or {}, dict) or set(reviews or {}) - {"design", "g1", "g2"}:
        raise AssetWorkflowError("invalid_reviews: expected design and optional existing G1/G2 descriptors")
    for category in ("g1", "g2"):
        if (reviews or {}).get(category):
            _json_evidence(root, reviews[category])
    permitted = eligibility(root, project, instance, reviews=reviews)
    if not permitted["approvable"]:
        raise AssetWorkflowError("asset_not_approvable: " + permitted["disabled_reason"])
    actual, report = _design_report(root, project, instance, (reviews or {}).get("design") or instance.get("design_review"))
    instance["design_review"] = actual
    instance.setdefault("review_history", []).append({"report": actual, "verdict": report["verdict"], "recorded_at": _stamp()})
    instance["approval"] = {"evidence": evidence, "origin": origin, "created_at": _stamp(),
                            "files": _file_binding(instance["files"]), "design_review": actual,
                            "requirements_signature": instance["requirements_signature"],
                            "upstream_signature": instance["upstream_signature"], "gates": permitted["gates"]}
    instance["status"] = "approved"
    for sibling in _workflow(project)["instances"]:
        if sibling["id"] != instance_id and sibling["asset_id"] == instance["asset_id"] and sibling["subject"] == instance["subject"]:
            if sibling.get("status") == "approved":
                sibling["status"] = "superseded"
            elif sibling["round_id"] == instance["round_id"] and sibling.get("status") in {"planned", "recorded"}:
                sibling["disposition"] = "not-selected"
    return {"ok": True, "instance_id": instance_id, "status": "approved", "claim_boundary": BOUNDARY}


def _write_artifact(root: Path, source: dict[str, Any], target: str) -> dict[str, Any]:
    actual = _file(root, source)
    destination = _path(root, target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        existing = _file(root, target)
        if existing["sha256"] != actual["sha256"]:
            raise AssetWorkflowError("artifact_conflict: refusing to overwrite different exported bytes")
        return existing
    _, content = generation_tasks._read_bytes(Path(root).resolve(), source["path"])
    handle, temporary = tempfile.mkstemp(prefix=".asset-", dir=destination.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _file(root, actual)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    exported = _file(root, target)
    if exported["sha256"] != source["sha256"]:
        raise AssetWorkflowError("artifact_changed: exported bytes differ from reviewed source")
    return exported


def _selected_approved(root: Path | None, project: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    plan = asset_plan.compile_plan(project, _approved(root, project))
    return plan, [row for row in plan["deliverables"] if not row["reuse_instance"]]


def export(root: Path, project: dict[str, Any]) -> dict[str, Any]:
    workflow = _workflow(project)
    plan, missing = _selected_approved(root, project)
    if not workflow["selected"] or missing:
        raise AssetWorkflowError("assets_incomplete: selected assets need current qualified approvals; unsupported requests stay incomplete")
    artifacts = []
    for row in plan["deliverables"]:
        instance = _instance(project, row["reuse_instance"])
        files = []
        for index, source in enumerate(instance["files"], 1):
            suffix = Path(source["path"]).suffix.lower()
            relative = f"assets-output/files/{instance['id']}-{index}-{source['sha256']}{suffix}"
            files.append({**_write_artifact(root, source, relative), "source": source["path"]})
        artifacts.append({"asset_id": instance["asset_id"], "instance_id": instance["id"], "subject": instance["subject"],
                          "kind": instance["kind"], "quality": instance["quality"], "files": files,
                          "source": copy.deepcopy(instance["source"]), "upstream": copy.deepcopy(instance["upstream"]),
                          "requirements_signature": instance["requirements_signature"],
                          "upstream_signature": instance["upstream_signature"], "approval": copy.deepcopy(instance["approval"]),
                          "support": asset_plan.definition(instance["asset_id"])["support"],
                          "manufacturing_ready": False, "claim_boundary": BOUNDARY})
    manifest = {"schema_version": 1, "kind": "asset-export", "project_id": project["project_id"],
                "selected": workflow["selected"][:], "plan_signature": plan["signature"],
                "artifacts": artifacts, "created_at": _stamp(), "claim_boundary": BOUNDARY,
                "production_boundary": "13–15 仅设计参考；所有效果图不是实际样品、工程或量产证明。"}
    for row in plan["deliverables"]:
        if not eligibility(root, project, _instance(project, row["reuse_instance"]))["approved"]:
            raise AssetWorkflowError("export_scope_changed: review/source changed during export; manifest not committed")
    state = _state_api()
    state.write_json(_path(root, MANIFEST), manifest)
    workflow["exports"].append({"manifest": MANIFEST, "plan_signature": plan["signature"],
                                 "manifest_signature": asset_plan.digest(manifest),
                                 "instance_ids": [artifact["instance_id"] for artifact in artifacts], "created_at": manifest["created_at"]})
    return {"ok": True, "manifest": MANIFEST, "artifacts": artifacts, "claim_boundary": BOUNDARY}


def _export_current(root: Path | None, project: dict[str, Any], plan: dict[str, Any]) -> bool:
    if root is None:
        return False
    state = _state_api()
    try:
        manifest = state.read_json(_path(root, MANIFEST))
        exports = _workflow(project)["exports"]
        if not exports or exports[-1].get("manifest_signature") != asset_plan.digest(manifest):
            return False
        if manifest.get("project_id") != project["project_id"] or manifest.get("plan_signature") != plan["signature"]:
            return False
        if {artifact["instance_id"] for artifact in manifest["artifacts"]} != {row["reuse_instance"] for row in plan["deliverables"]}:
            return False
        for artifact in manifest["artifacts"]:
            instance = _instance(project, artifact["instance_id"])
            if [file["sha256"] for file in artifact["files"]] != [file["sha256"] for file in instance["files"]]:
                return False
            for descriptor in artifact["files"]:
                _file(root, descriptor)
        return True
    except (ValueError, OSError, KeyError, TypeError, state.ProjectError):
        return False


def next_action(root: Path | None, project: dict[str, Any]) -> dict[str, Any]:
    workflow = _workflow(project)
    if not workflow["selected"]:
        return {"action": "choose-assets", "say": "先展示四组 21 项目录及能力状态，请用户选择本轮资产，不先开商品流水线。"}
    if root is None:
        return {"action": "verify-assets", "say": "纯状态视图未检查文件、来源和审查；提供项目目录再核验。"}
    plan, missing = _selected_approved(root, project)
    if not missing:
        if _export_current(root, project, plan):
            return {"action": "done", "manifest": MANIFEST, "say": "所选资产已按合同导出；未选宣发不补场景或发帖。"}
        return {"action": "export-assets", "say": "所选资产已具备当前单项批准；导出到 assets-output/manifest.json。"}
    active_keys = {_key(row["asset_id"], row["subject"]) for row in plan["execution"] if not row["reuse_instance"]}
    for instance in reversed(workflow["instances"]):
        if _key(instance["asset_id"], instance["subject"]) not in active_keys or instance.get("disposition") == "not-selected":
            continue
        try:
            task = _task_status(root, instance)
        except AssetWorkflowError as error:
            return {"action": "reconcile-generation", "instance_id": instance["id"], "should_call": False, "reason": str(error)}
        if task and task.get("status") in {"requesting", "unknown"}:
            return {"action": "reconcile-generation", "instance_id": instance["id"], "task_id": task["task_id"],
                    "should_call": False, "say": "只核对已有调用回执，不能自动重发或用新 intent 绕过 unknown。"}
    for row in plan["execution"]:
        if row["reuse_instance"] or row["blocked_reason"] or row["missing_dependencies"]:
            continue
        candidates = [instance for instance in workflow["instances"] if instance["asset_id"] == row["asset_id"]
                      and instance["subject"] == row["subject"] and instance.get("disposition") != "not-selected"
                      and instance["requirements_signature"] == _signature(project, row["asset_id"], row["subject"])]
        evaluated = [(instance, eligibility(root, project, instance)) for instance in candidates]
        qualified = [(instance, gate) for instance, gate in evaluated if gate["approvable"]]
        if qualified:
            return {"action": "approve-asset", "asset_id": row["asset_id"], "subject": row["subject"],
                    "instances": [instance["id"] for instance, _ in qualified], "say": "逐候选验资格，等待用户采用；其他候选失败不连带阻断。"}
        recorded = [(instance, gate) for instance, gate in evaluated if instance.get("files")]
        if recorded:
            return {"action": "review-asset", "asset_id": row["asset_id"], "subject": row["subject"],
                    "instances": [{"instance_id": instance["id"], **gate} for instance, gate in recorded],
                    "say": "按候选独立审查实际文件；意向不替代审查，失败只阻断对应候选。"}
        if candidates:
            return {"action": "record-asset", "asset_id": row["asset_id"], "subject": row["subject"],
                    "instances": [instance["id"] for instance in candidates], "say": "登记实际文件及来源，生成仍按原账本领取／恢复。"}
        return {"action": "new-asset", "asset_id": row["asset_id"], "subject": row["subject"], "internal": row["internal"],
                "say": "补明示的最小依赖；探索默认两候选可覆盖，不加无关材质、去底或场景。"}
    return {"action": "blocked-assets", "blocked": plan["blocked"], "missing": missing,
            "say": "未支持或依赖缺项仍未完成；保留原需求，需明确改范围、延期或交接，不能自动替代。"}


def view(root: Path | None, project: dict[str, Any]) -> dict[str, Any]:
    workflow = _workflow(project)
    catalog = asset_plan.catalog()
    plan, missing = _selected_approved(root, project)
    cache: dict[str, Any] = {}
    instances = [{**copy.deepcopy(instance), "eligibility": eligibility(root, project, instance, cache=cache)}
                 for instance in workflow["instances"]]
    current = None
    if workflow["rounds"]:
        entry = workflow["rounds"][-1]
        by_id = {instance["id"]: instance for instance in instances}
        items = []
        for identifier in entry["instances"]:
            instance = by_id[identifier]
            files = instance.get("files") or []
            path = files[0]["path"] if files else None
            gate = instance["eligibility"]
            exists = False
            if root is not None and path:
                try:
                    exists = _path(root, path).is_file()
                except AssetWorkflowError:
                    pass
            items.append({"id": identifier, "instance_id": identifier, "asset_id": instance["asset_id"],
                          "round_id": entry["id"], "stage": "assets", "subject": instance["subject"], "kind": entry["kind"],
                          "badge": instance["candidate"], "title": asset_plan.definition(instance["asset_id"])["name"],
                          "path": path, "original_path": path, "display_path": path if exists and instance["kind"] != "document" else None,
                          "exists": exists, "image_sha256": files[0]["sha256"] if files else None,
                          "selectable": gate["approvable"], "intent_selectable": gate["selectable"],
                          "disabled_reason": gate["disabled_reason"], "picked": gate["approved"],
                          "status": instance["status"], "asset_kind": instance["kind"], "quality": instance["quality"],
                          "files": files, "source": instance.get("source"), "review_template": review_template(project, instance)})
        current = {"kind": entry["kind"], "items": items, "round_id": entry["id"], "stage": "assets",
                   "asset_id": entry["asset_id"], "subject": entry["subject"],
                   "heading": asset_plan.definition(entry["asset_id"])["name"] + " · " + entry["subject"]}
    exported = bool(workflow["selected"] and not missing and _export_current(root, project, plan))
    return {"catalog": catalog["assets"], "groups": catalog["groups"], "presets": copy.deepcopy(PRESETS),
            "selected": workflow["selected"][:], "plan": plan, "instances": instances, "current": current,
            "next": next_action(root, project), "intents": copy.deepcopy(workflow["intents"]),
            "submit_actions": ["choose-assets", "approve-asset", "asset-intent"],
            "completion": {"complete": exported, "ready_to_export": bool(workflow["selected"] and not missing),
                           "selected": len(plan["deliverables"]), "approved": len(plan["deliverables"]) - len(missing),
                           "missing": missing, "exported": exported, "manifest": MANIFEST if exported else None,
                           "verification": "current-files" if root is not None else "not_assessed",
                           "capability_validation": "可测文件和流程门禁；真实生图、视觉、耗时和制造能力未由程序证明"},
            "claim_boundary": BOUNDARY}


def submit(root: Path, project: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise AssetWorkflowError("invalid_request: expected schema_version=1 decision")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", str(payload.get("request_id", ""))):
        raise AssetWorkflowError("invalid_request_id: use a stable 8–128 character ID")
    if payload.get("project_id") != project.get("project_id"):
        raise AssetWorkflowError("wrong_project: decision belongs to another project")
    if type(payload.get("expected_revision")) is not int or payload["expected_revision"] != project.get("revision", 0):
        raise AssetWorkflowError("stale_revision: refresh before submitting")
    common = {"schema_version", "request_id", "project_id", "expected_revision", "delivery_id", "round_id", "action", "note", "keep", "origin", "items"}
    action = payload.get("action")
    fields = {"choose-assets": {"asset_ids", "append", "evidence", "subjects", "requirements", "preset"},
              "approve-asset": {"asset_id", "instance_id", "evidence", "reviews"},
              "asset-intent": {"instance_id", "asset_id", "intent"}}
    if not isinstance(action, str) or action not in fields or set(payload) - common - fields[action]:
        raise AssetWorkflowError("invalid_action_payload: use documented fields, not an approval flag")
    if action == "choose-assets":
        if payload.get("items") not in (None, []):
            raise AssetWorkflowError("invalid_choice_items: asset scope selection is not candidate adoption")
        return choose(root, project, payload.get("asset_ids"),
                      evidence=payload.get("evidence") or f"工作台资产选择 request_id={payload['request_id']}；备注：{payload.get('note', '')}",
                      append=payload.get("append", False), subjects=payload.get("subjects"),
                      requirements=payload.get("requirements"), preset=payload.get("preset"))
    _workflow(project)
    selected_instance = _instance(project, payload["instance_id"]) if payload.get("instance_id") else None
    if selected_instance is not None:
        if payload.get("asset_id") not in (None, selected_instance["asset_id"]):
            raise AssetWorkflowError("asset_instance_mismatch: instance belongs to a different numbered asset")
        if payload.get("round_id") not in (None, selected_instance["round_id"]):
            raise AssetWorkflowError("stale_asset_round: instance differs from the submitted round")
        if "items" in payload:
            expected_items = [{"id": selected_instance["id"], "sha256": selected_instance["files"][0]["sha256"]}] if selected_instance.get("files") else []
            if payload["items"] != expected_items:
                raise AssetWorkflowError("changed_asset: candidate ID or bytes differ from the submission")
    if action == "approve-asset":
        return approve(root, project, payload.get("instance_id"),
                       evidence=payload.get("evidence") or f"工作台正式采用 request_id={payload['request_id']}；备注：{payload.get('note', '')}",
                       reviews=payload.get("reviews"), origin=payload.get("origin", "user-confirmed"))
    intent = payload.get("intent")
    if intent not in {"prefer-direction", "request-variation", "request-slots", "request-redo", "defer-unsupported", "accept-design-reference"}:
        raise AssetWorkflowError("invalid_asset_intent: unknown intent")
    if not isinstance(payload.get("note", ""), str):
        raise AssetWorkflowError("invalid_intent_note: expected text")
    note = payload.get("note", "").strip() or f"工作台用户意向 {intent} request_id={payload['request_id']}"
    identifier = payload.get("instance_id")
    instance = selected_instance
    asset_id = instance["asset_id"] if instance else asset_plan.normalize_ids([payload.get("asset_id")])[0]
    if asset_id not in _workflow(project)["selected"] and instance is None:
        raise AssetWorkflowError("asset_outside_scope: intent must belong to this request")
    if intent == "prefer-direction" and (instance is None or not eligibility(root, project, instance)["selectable"]):
        raise AssetWorkflowError("intent_not_eligible: direction intent still needs valid files, upstream and true source")
    entry = {"request_id": payload["request_id"], "instance_id": identifier, "asset_id": asset_id,
             "intent": intent, "note": note, "status": "pending", "created_at": _stamp(),
             "generation_authorized": False, "formal_approval": False}
    _workflow(project)["intents"].append(entry)
    return {"ok": True, "intent": entry, "say": "仅登记意向，未生图、未正式批准，原需求未完成。"}


def _dispatch(args: argparse.Namespace) -> int:
    state = _state_api()
    root = Path(args.project).expanduser().resolve()
    if args.action == "catalog":
        return state.emit({"ok": True, **asset_plan.catalog(), "presets": PRESETS})
    with state.project_lock(root):
        project = state.load_project(root, writable=args.action not in {"status", "next"})
        payload = state.read_json(Path(args.file)) if args.file else {}
        if args.action == "choose":
            result = choose(root, project, payload.get("asset_ids", args.asset_ids),
                            evidence=payload.get("evidence", args.evidence), append=payload.get("append", args.append),
                            subjects=payload.get("subjects"), requirements=payload.get("requirements"), preset=payload.get("preset", args.preset))
        elif args.action == "new":
            allowed = {"asset_id", "subject", "count", "quality", "source_kind", "prompt", "intent_id", "evidence", "content_kind", "alpha_required", "authorization"}
            if set(payload) - allowed:
                raise AssetWorkflowError("invalid_new_payload: unknown planning options")
            options = {"asset_id": args.asset, "subject": args.subject, "count": args.count, "quality": args.quality,
                       "source_kind": args.source_kind, "prompt": args.prompt, "intent_id": args.intent,
                       "evidence": args.evidence, "content_kind": args.content_kind, **payload}
            result = new(root, project, **options)
        elif args.action == "record":
            result = record(root, project, {"instance_id": args.instance, **payload})
        elif args.action == "approve":
            result = approve(root, project, payload.get("instance_id", args.instance),
                             evidence=payload.get("evidence", args.evidence), reviews=payload.get("reviews"),
                             origin=payload.get("origin", "user-confirmed"))
        elif args.action == "export":
            result = export(root, project)
        elif args.action == "next":
            result = {"ok": True, "next": next_action(root, project)}
        else:
            result = {"ok": True, **view(root, project)}
        if args.action not in {"status", "next"} and not result.get("unchanged"):
            state.save_project(root, project)
        result["revision"] = project.get("revision", 0)
        return state.emit(result)


def add_parser(subparsers: Any) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("assets", help="编号资产选择、文件登记、独立审查采用和按需导出")
    parser.add_argument("project")
    parser.add_argument("action", choices=("catalog", "choose", "next", "new", "record", "approve", "export", "status"))
    parser.add_argument("asset_ids", nargs="*")
    parser.add_argument("--file")
    parser.add_argument("--asset")
    parser.add_argument("--instance")
    parser.add_argument("--subject")
    parser.add_argument("--quality", choices=("exploration", "final"), default="exploration")
    parser.add_argument("--count", type=int)
    parser.add_argument("--source-kind", choices=sorted(SOURCE_KINDS))
    parser.add_argument("--content-kind", choices=sorted(CONTENT_KINDS))
    parser.add_argument("--prompt", default="")
    parser.add_argument("--intent")
    parser.add_argument("--evidence", default="")
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--preset", choices=sorted(PRESETS))
    parser.set_defaults(func=_dispatch)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="资产工作流独立入口，亦可注册到 project.py")
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
