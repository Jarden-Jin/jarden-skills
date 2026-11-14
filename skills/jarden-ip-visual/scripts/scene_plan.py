"""Pure scene planning; no filesystem, generation, approval, or project revision writes.

apply_patch returns (updated_project, impact). expected_revision addresses the scene
plan revision, not the outer project revision. baseline_hashes is a caller-supplied
snapshot of current image digests; this module checks declarations, never pixels.
spec_ref/spec_refs are JSON pointers into the supplied project. Existing refs stay
unchanged; compile_slot produces separate typed bindings and per-slot signatures.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any


SCHEMA_VERSION = 1
VIEWS = ("front", "three-quarter", "left", "right", "back", "detail")
BINDING_KINDS = ("identity", "product", "view", "environment", "style", "baseline")
SLOT_FIELDS = {
    "id", "role", "subject", "subjects", "spec_ref", "spec_refs", "view", "yaw",
    "elevation", "pose", "framing", "reference_bindings", "scale_anchor", "keep",
    "description", "refs", "constraint_policy",
}
CONSTRAINT_FIELDS = {"framing", "yaw", "elevation", "pose", "scale_anchor", "description"}
CONSTRAINT_ORIGINS = ("user-confirmed", "agent-inferred", "reference-derived")
REFERENCE_ASPECTS = {
    "identity": ["identity", "color_regions", "signature_accessories"],
    "product": ["shape", "material", "connections"],
    "view": ["shape", "hidden_surfaces"],
    "environment": ["environment", "lighting", "atmosphere"],
    "style": ["palette", "material_language", "lighting"],
    "baseline": ["identity", "unchanged_regions"],
}
VIEW_LABELS = {
    "front": "正面", "three-quarter": "四分之三视角", "left": "左侧",
    "right": "右侧", "back": "背面", "detail": "局部细节",
}
NOTE_TYPES = ("series-collection", "design-manuscript", "single-multiview", "multi-scene")


class ScenePlanError(ValueError):
    """Invalid plans, stale preconditions, or unsafe patch scope."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ScenePlanError(message)


def _hash(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ScenePlanError("invalid_json: values must be finite JSON data") from error
    return hashlib.sha256(encoded).hexdigest()


def _text(value: Any, field: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), f"invalid_{field}: expected nonempty text")
    return value


def _strings(value: Any, field: str) -> list[str]:
    _require(isinstance(value, list), f"invalid_{field}: expected a list")
    _require(all(isinstance(item, str) and item.strip() for item in value),
             f"invalid_{field}: expected nonempty strings")
    _require(len(value) == len(set(value)), f"invalid_{field}: duplicate values")
    return list(value)


def _pointer(value: Any) -> str:
    _text(value, "spec_ref")
    _require(value.startswith("/") and not re.search(r"~(?![01])", value),
             "invalid_spec_ref: use a project JSON pointer")
    return value


def _resolve(project: dict[str, Any], pointer: str) -> Any:
    current: Any = project
    for token in _pointer(pointer).split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdecimal() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise ScenePlanError(f"missing_spec_ref: {pointer}")
    return current


def _digest(value: Any) -> str:
    _require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
             "invalid_hash: expected lowercase sha256")
    return value


def _binding(value: Any) -> dict[str, Any]:
    _require(isinstance(value, dict), "invalid_binding: expected an object")
    kind = value.get("kind")
    _require(isinstance(kind, str) and kind in BINDING_KINDS, "invalid_binding_kind")
    result = {"kind": kind, "path": _text(value.get("path"), "reference_path")}
    for field in ("id", "subject", "slot_id", "spec_id", "origin"):
        if value.get(field) is not None:
            result[field] = _text(value[field], "binding_" + field)
    if value.get("sha256") is not None:
        result["sha256"] = _digest(value["sha256"])
    if kind == "view" and value.get("view") is not None:
        _require(value["view"] in VIEWS, "invalid_reference_view")
        result["view"] = value["view"]
    if kind == "view" and isinstance(value.get("confirmation"), dict):
        result["confirmation"] = {"status": value["confirmation"].get("status")}
    result["allowed_aspects"] = list(REFERENCE_ASPECTS[kind])
    result["copy_camera"] = False
    result["copy_pose"] = False
    return result


def _slot(value: Any) -> dict[str, Any]:
    _require(isinstance(value, dict) and not set(value) - SLOT_FIELDS, "invalid_slot_fields")
    result = copy.deepcopy(value)
    identifier = _text(result.get("id"), "slot_id")
    _require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", identifier) is not None,
             "invalid_slot_id: use a stable identifier, not an index or path")
    _text(result.get("role"), "role")
    _require(isinstance(result.get("view"), str) and result["view"] in VIEWS, "invalid_view")
    result.setdefault("subject", None)
    if result["subject"] is not None:
        _text(result["subject"], "subject")
    if "subjects" in result:
        result["subjects"] = _strings(result["subjects"], "subjects")
    result.setdefault("spec_ref", None)
    if result["spec_ref"] is not None:
        _pointer(result["spec_ref"])
    if "spec_refs" in result:
        for pointer in _strings(result["spec_refs"], "spec_refs"):
            _pointer(pointer)
    for field, lower, upper in (("yaw", -180, 180), ("elevation", -90, 90)):
        result.setdefault(field, None)
        number = result[field]
        if number is not None:
            _require(isinstance(number, (int, float)) and not isinstance(number, bool)
                     and lower <= number <= upper and math.isfinite(number),
                     f"invalid_{field}: expected a finite number in [{lower}, {upper}]")
    for field, default in (("pose", "保持已确认的产品姿态"), ("framing", "完整主体，避免裁切")):
        result.setdefault(field, default)
        _text(result[field], field)
    if "description" in result:
        _text(result["description"], "description")
    result["keep"] = _strings(result.get("keep", []), "keep")
    bindings = result.get("reference_bindings", [])
    _require(isinstance(bindings, list), "invalid_reference_bindings")
    result["reference_bindings"] = [_binding(item) for item in bindings]
    result.setdefault("scale_anchor", None)
    _require(result["scale_anchor"] is None or isinstance(result["scale_anchor"], (str, dict)),
             "invalid_scale_anchor: expected text, an object, or null")
    if "constraint_policy" in result:
        policy = result["constraint_policy"]
        _require(isinstance(policy, dict) and not set(policy) - CONSTRAINT_FIELDS,
                 "invalid_constraint_policy: unsupported fields")
        for rule in policy.values():
            _require(isinstance(rule, dict) and set(rule) == {"level", "origin", "evidence"},
                     "invalid_constraint_policy_rule")
            _require(rule["level"] in ("required", "advisory"), "invalid_constraint_policy_level")
            _require(rule["origin"] in CONSTRAINT_ORIGINS, "invalid_constraint_policy_origin")
            _text(rule["evidence"], "constraint_policy_evidence")
            _require(rule["level"] != "advisory" or rule["origin"] == "agent-inferred",
                     "invalid_constraint_policy_advisory_origin")
    _hash(result)
    return result


def validate_plan(plan: dict[str, Any], project: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a normalized deep copy or raise; missing view coverage is not approval."""
    _require(isinstance(plan, dict) and not set(plan) - {"schema_version", "slots", "coverage", "revision", "default_profile"},
             "invalid_plan_fields")
    _require(type(plan.get("schema_version")) is int and plan["schema_version"] == SCHEMA_VERSION,
             "unsupported_scene_plan_schema")
    _require(type(plan.get("revision")) is int and plan["revision"] >= 0, "invalid_plan_revision")
    _require(isinstance(plan.get("slots"), list), "invalid_slots")
    slots = [_slot(item) for item in plan["slots"]]
    identifiers = [item["id"] for item in slots]
    _require(len(identifiers) == len(set(identifiers)), "duplicate_slot_id")
    coverage = plan.get("coverage", {})
    _require(isinstance(coverage, dict) and not set(coverage) - {"required_views", "preserve_slots"},
             "invalid_coverage")
    required = _strings(coverage.get("required_views", []), "required_views")
    _require(all(view in VIEWS for view in required), "invalid_required_view")
    preserve = _strings(coverage.get("preserve_slots", []), "preserve_slots")
    _require(set(preserve) <= set(identifiers), "unknown_preserved_slot")
    if project is not None:
        for slot in slots:
            for pointer in _spec_refs(slot):
                _resolve(project, pointer)
        if (project.get("answers") or {}).get("delivery") == "grid-9":
            _require(len(slots) <= 9, "grid_capacity: never silently drop or replace slots")
    result = {"schema_version": SCHEMA_VERSION, "slots": slots,
              "coverage": {"required_views": required, "preserve_slots": preserve}, "revision": plan["revision"]}
    if "default_profile" in plan:
        result["default_profile"] = _validated_profile(plan["default_profile"])
    return result


def _spec_refs(slot: dict[str, Any]) -> list[str]:
    pointers = ([slot["spec_ref"]] if slot.get("spec_ref") else []) + slot.get("spec_refs", [])
    return list(dict.fromkeys(pointers))


def _subject_map(project: dict[str, Any]) -> dict[str, Any]:
    subjects = (project.get("product_contract") or {}).get("subjects")
    return subjects if isinstance(subjects, dict) else {}


def _subject_id(project: dict[str, Any], subject: str) -> str:
    subjects = _subject_map(project)
    if subject in subjects:
        return subject
    return next((identifier for identifier, item in subjects.items()
                 if isinstance(item, dict) and item.get("name") == subject
                 and item.get("active", True)), subject)


def _contract_refs(project: dict[str, Any], subjects: list[str], pointers: list[str]) -> list[str]:
    contract = project.get("product_contract")
    if not isinstance(contract, dict):
        return pointers
    specs = contract.get("specs") or {}
    _require(isinstance(specs, dict), "invalid_contract_specs")
    selected = []
    covered = set()
    for pointer in pointers:
        if not pointer.startswith("/product_contract/specs/"):
            continue
        spec = _resolve(project, pointer)
        _require(isinstance(spec, dict) and spec.get("subject_id") in subjects,
                 "subject_spec_mismatch")
        selected.append(pointer)
        covered.add(spec["subject_id"])
    for subject in subjects:
        if subject in covered:
            continue
        identifier = next((identifier for identifier, spec in specs.items()
                           if isinstance(spec, dict) and spec.get("subject_id") == subject), None)
        _require(identifier is not None, "missing_subject_spec: " + subject)
        token = identifier.replace("~", "~0").replace("/", "~1")
        selected.append("/product_contract/specs/" + token)
    return list(dict.fromkeys(selected))


def default_plan(project: dict[str, Any], base_slots: list[dict[str, Any]], *,
                 coverage: dict[str, Any] | None = None) -> dict[str, Any]:
    """Project old note slots without changing their IDs or inventing three-view requirements."""
    if project.get("scene_plan") is not None:
        _require(coverage is None, "existing_plan: change its contract explicitly")
        return validate_plan(project["scene_plan"], project)
    _require(isinstance(base_slots, list), "invalid_base_slots")
    subjects = (project.get("answers") or {}).get("subjects") or []
    pointer = "/product_spec" if isinstance(project.get("product_spec"), dict) else None
    if pointer is None and isinstance((project.get("brief") or {}).get("product_spec"), dict):
        pointer = "/brief/product_spec"
    slots = []
    for base in base_slots:
        _require(isinstance(base, dict), "invalid_base_slot")
        slot = copy.deepcopy(base)
        role = slot.get("role")
        slot.setdefault("subject", subjects[0] if len(subjects) == 1 else None)
        slot.setdefault("spec_ref", pointer)
        if isinstance(project.get("product_contract"), dict):
            actual_subjects = _subjects(project, slot)
            pointers = _contract_refs(project, actual_subjects, _spec_refs(slot))
            if len(actual_subjects) == 1:
                slot["subject"] = actual_subjects[0]
                slot["spec_ref"] = pointers[0]
                if "spec_refs" in slot or len(pointers) > 1:
                    slot["spec_refs"] = pointers
            else:
                slot["subject"] = None
                slot["spec_ref"] = None
                slot["spec_refs"] = pointers
            if "subjects" in slot or len(actual_subjects) != 1:
                slot["subjects"] = actual_subjects
        slot.setdefault("view", "detail" if role == "detail" else "three-quarter" if role == "angle" else "front")
        if role == "detail":
            slot.setdefault("framing", "局部近景，清楚展示材质与工艺")
        slots.append(slot)
    result = validate_plan({"schema_version": SCHEMA_VERSION, "revision": 0, "slots": slots,
                            "coverage": copy.deepcopy(coverage) if coverage is not None else {}}, project)
    if (project.get("answers") or {}).get("note_type") in NOTE_TYPES:
        result["default_profile"] = _make_profile(project, result["slots"])
    return result


def _profile_subjects(project: dict[str, Any]) -> list[str]:
    names = [name.strip() for name in (project.get("answers") or {}).get("subjects") or []
             if isinstance(name, str) and name.strip()]
    if not names:
        names = [((project.get("brief") or {}).get("ip_name") or "").strip() or "主体"]
    count = (project.get("answers") or {}).get("concept_count")
    counter = len(names) + 1
    while type(count) is int and len(names) < count:
        name = f"角色{counter}"
        counter += 1
        if name not in names:
            names.append(name)
    return names


def _profile_base_slots(project: dict[str, Any]) -> list[dict[str, Any]]:
    answers = project.get("answers") or {}
    note_type = answers.get("note_type")
    _require(note_type in NOTE_TYPES, "missing_note_type: choose a supported default profile")
    subjects = _profile_subjects(project)
    slots = [{"id": "cover", "role": "cover"}]
    if note_type == "series-collection":
        slots.extend({"id": f"character-{index}", "role": "character", "subject": name}
                     for index, name in enumerate(subjects, 1))
        slots.extend({"id": f"scene-{index}", "role": "scene"} for index in (1, 2))
    elif note_type == "design-manuscript":
        if len(subjects) > 1:
            slots.append({"id": "lineup", "role": "lineup"})
        slots.extend({"id": role, "role": role} for role in ("turnaround", "manuscript"))
        slots.extend([{"id": "scene-1", "role": "scene"}, {"id": "extension-1", "role": "extension"}])
    elif note_type == "multi-scene":
        slots.extend({"id": role, "role": role} for role in ("group", "home", "theme", "outing"))
    else:
        slots.extend({"id": role, "role": role} for role in ("scale", "detail", "angle"))
        slots.extend([{"id": "scene-1", "role": "scene"}, {"id": "info", "role": "info"}])
    if answers.get("delivery") == "grid-9":
        _require(note_type != "series-collection" or len(subjects) <= 7,
                 "grid_capacity: default profile cannot silently drop subjects")
        if len(slots) > 9:
            slots = [slot for slot in slots if slot["id"] != "scene-2"]
        extra = 1
        while len(slots) < 9:
            role = "extension" if extra % 2 else "scene"
            slots.append({"id": f"{role}-x{extra}", "role": role})
            extra += 1
    return slots


def _make_profile(project: dict[str, Any], slots: list[dict[str, Any]]) -> dict[str, Any]:
    answers = project.get("answers") or {}
    result = {"schema_version": 1, "note_type": answers.get("note_type"),
              "delivery": answers.get("delivery"), "subjects": _profile_subjects(project),
              "slots": copy.deepcopy(slots)}
    result["signature"] = _hash(result)
    return result


def _validated_profile(value: Any) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == {
        "schema_version", "note_type", "delivery", "subjects", "slots", "signature"}, "invalid_default_profile")
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1,
             "unsupported_default_profile_schema")
    _require(isinstance(value["note_type"], str) and value["note_type"] in NOTE_TYPES,
             "invalid_default_profile_note_type")
    _require(value["delivery"] in (None, "full-post", "grid-9"), "invalid_default_profile_delivery")
    _strings(value["subjects"], "profile_subjects")
    _require(isinstance(value["slots"], list), "invalid_default_profile_slots")
    normalized = [_slot(slot) for slot in value["slots"]]
    _require(normalized == value["slots"] and len({slot["id"] for slot in normalized}) == len(normalized),
             "invalid_default_profile_slots")
    unsigned = {field: copy.deepcopy(entry) for field, entry in value.items() if field != "signature"}
    _require(value["signature"] == _hash(unsigned), "invalid_default_profile_signature")
    return copy.deepcopy(value)


def default_profile_binding(project: dict[str, Any], base_slots: list[dict[str, Any]] | None = None, *,
                            note_type: str | None = None) -> dict[str, Any]:
    """Return the default template snapshot, independent of existing plan edits.

    Both project schema 3 and 4 use the same scene schema.  The optional base
    slots let a router provide its canonical template without this module
    depending on the router or touching the filesystem.
    """
    source = copy.deepcopy(project)
    source.pop("scene_plan", None)
    if note_type is not None:
        source.setdefault("answers", {})["note_type"] = note_type
    _require((source.get("answers") or {}).get("note_type") in NOTE_TYPES, "missing_note_type")
    bases = _profile_base_slots(source) if base_slots is None else base_slots
    return default_plan(source, bases)["default_profile"]


def bind_default_profile(project: dict[str, Any], base_slots: list[dict[str, Any]] | None = None
                         ) -> dict[str, Any]:
    """Bind an unbound legacy plan before changing note/delivery answers.

    The snapshot describes old defaults, not a new assertion that current
    custom camera values were defaults.  No image, approval, outer revision,
    or slot is changed.  Existing bindings are never silently replaced.
    """
    updated = copy.deepcopy(project)
    if updated.get("scene_plan") is not None:
        plan = validate_plan(updated["scene_plan"])
        if "default_profile" not in plan:
            plan["default_profile"] = default_profile_binding(updated, base_slots)
        updated["scene_plan"] = plan
    return updated


def _inferred_profile(project: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any] | None:
    current = {slot["id"]: slot for slot in plan["slots"]}
    candidates = []
    for note_type in NOTE_TYPES:
        try:
            profile = default_profile_binding(project, note_type=note_type)
        except ScenePlanError:
            continue
        defaults = {slot["id"]: slot for slot in profile["slots"]}
        shared = set(current) & set(defaults)
        matches = sum(current[identifier]["role"] == defaults[identifier]["role"] for identifier in shared)
        distance = len(set(current) ^ set(defaults)) + 2 * (len(shared) - matches)
        candidates.append((distance, -matches, profile))
    candidates.sort(key=lambda item: item[:2])
    if not candidates or len(candidates) > 1 and candidates[0][:2] == candidates[1][:2]:
        return None
    return candidates[0][2]


def _retention_reasons(project: dict[str, Any], plan: dict[str, Any]) -> dict[str, list[str]]:
    slots = {slot["id"]: slot for slot in plan["slots"]}
    reasons: dict[str, list[str]] = {identifier: [] for identifier in slots}

    def retain(identifier: Any, reason: str) -> None:
        if isinstance(identifier, str) and identifier in reasons and reason not in reasons[identifier]:
            reasons[identifier].append(reason)

    for identifier in plan["coverage"]["preserve_slots"]:
        retain(identifier, "preserve_slot")
    for slot in slots.values():
        if slot["keep"]:
            retain(slot["id"], "keep")
        if slot["reference_bindings"] or slot.get("refs"):
            retain(slot["id"], "reference_binding")
        if slot.get("constraint_policy"):
            retain(slot["id"], "constraint_policy")
    required = set(plan["coverage"]["required_views"])
    for slot in slots.values():
        if slot["view"] in required:
            retain(slot["id"], "required_view")
    for final in project.get("finals") or []:
        if not isinstance(final, dict):
            continue
        identifier = final.get("slot") or final.get("slot_id")
        if identifier:
            retain(identifier, "existing_image")
        elif final.get("role"):
            for slot in slots.values():
                if slot["role"] == final["role"]:
                    retain(slot["id"], "existing_image")
    for entry in project.get("rounds") or []:
        if not isinstance(entry, dict) or entry.get("stage") != "scene":
            continue
        for slot in entry.get("review_slots") or entry.get("items") or []:
            if isinstance(slot, dict):
                retain(slot.get("slot_id") or slot.get("id"), "historical_round")
        for identifier in entry.get("review_paths") or {}:
            retain(identifier, "historical_round")
    for feedback in project.get("feedback") or []:
        if isinstance(feedback, dict) and feedback.get("status") not in ("cancelled", "superseded"):
            for identifier in (feedback.get("scope") or {}).get("slots") or []:
                retain(identifier, "scoped_feedback")
    return reasons


def profile_diff(project: dict[str, Any], *, base_slots: list[dict[str, Any]] | None = None
                 ) -> dict[str, Any]:
    """Describe a note-profile transition without changing any project data."""
    target = default_profile_binding(project, base_slots)
    raw_plan = project.get("scene_plan")
    if raw_plan is None:
        return {"status": "missing_plan", "needs_coordination": True, "added_slots": [slot["id"] for slot in target["slots"]],
                "removed_slots": [], "reused_slots": [], "retained_slots": [], "customized_slots": [],
                "current_profile": None, "target_profile": target, "plan_hash": None,
                "plan_revision": None, "conflicts": [], "binding_source": "missing", "retention_reasons": {}}
    plan = validate_plan(raw_plan)
    current_profile = plan.get("default_profile") or _inferred_profile(project, plan)
    source = "bound" if "default_profile" in plan else "inferred" if current_profile else "unknown"
    slots = {slot["id"]: slot for slot in plan["slots"]}
    defaults = {slot["id"]: slot for slot in (current_profile or {}).get("slots", [])}
    wanted = {slot["id"]: slot for slot in target["slots"]}
    customized = [identifier for identifier in slots if identifier not in defaults or slots[identifier] != defaults[identifier]]
    reasons = _retention_reasons(project, plan)
    for identifier in customized:
        reasons[identifier].append("customized_slot" if identifier in defaults else "custom_slot")
    changed = current_profile is None or current_profile["signature"] != target["signature"]
    removed = [identifier for identifier in slots if changed and identifier not in wanted
               and identifier in defaults and not reasons[identifier]]
    added = [identifier for identifier in wanted if changed and identifier not in slots]
    retained = [identifier for identifier in slots if identifier not in wanted and identifier not in removed]
    reused = [identifier for identifier in wanted if identifier in slots]
    conflicts = ["slot_role_conflict:" + identifier for identifier in reused
                 if slots[identifier]["role"] != wanted[identifier]["role"]]
    if (project.get("answers") or {}).get("delivery") == "grid-9" and len(slots) - len(removed) + len(added) > 9:
        conflicts.append("grid_capacity: retained custom or historical slots need an explicit capacity decision")
    return {"status": "pending" if changed else "current", "needs_coordination": changed,
            "added_slots": added, "removed_slots": removed, "reused_slots": reused,
            "retained_slots": retained, "customized_slots": customized,
            "current_profile": current_profile, "target_profile": target,
            "plan_hash": plan_hash(plan), "plan_revision": plan["revision"],
            "conflicts": conflicts, "binding_source": source,
            "retention_reasons": {identifier: values for identifier, values in reasons.items() if values}}


def coordinate_profile(project: dict[str, Any], *, base_slots: list[dict[str, Any]] | None = None,
                       expected_revision: int | None = None, expected_hash: str | None = None
                       ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Explicitly apply a profile diff to a copy, preserving customized work.

    The router retains its existing project lock/transaction and persists the
    result.  Preconditions refer to the scene plan.  Removed unmodified defaults
    are archived in scene_profile_history; finals, rounds, feedback, references,
    approvals, and unknown outer fields remain untouched.  Repeating the same
    current-profile operation is a no-op and never creates a generation task.
    """
    plan = validate_plan(project.get("scene_plan"))
    _require(expected_revision is not None or expected_hash is not None, "missing_precondition")
    if expected_revision is not None:
        _require(type(expected_revision) is int and expected_revision == plan["revision"], "stale_plan_revision")
    before_hash = plan_hash(plan)
    if expected_hash is not None:
        _require(_digest(expected_hash) == before_hash, "stale_plan_hash")
    diff = profile_diff(project, base_slots=base_slots)
    _require(not diff["conflicts"], "profile_coordination_conflict: " + "; ".join(diff["conflicts"]))
    updated = copy.deepcopy(project)
    if not diff["needs_coordination"]:
        return updated, {**diff, "committed": False, "unchanged": True, "invalidated_slots": [],
                         "preserved_slots": [slot["id"] for slot in plan["slots"]],
                         "old_revision": plan["revision"], "new_revision": plan["revision"],
                         "before_hash": before_hash, "after_hash": before_hash}
    slots = {slot["id"]: slot for slot in plan["slots"]}
    removed = set(diff["removed_slots"])
    candidate = copy.deepcopy(plan)
    candidate["slots"] = [copy.deepcopy(slots.get(slot["id"], slot)) for slot in diff["target_profile"]["slots"]]
    candidate["slots"].extend(copy.deepcopy(slot) for slot in plan["slots"]
                              if slot["id"] in diff["retained_slots"])
    candidate["default_profile"] = copy.deepcopy(diff["target_profile"])
    candidate["revision"] += 1
    candidate = validate_plan(candidate, project)
    _require(set(coverage_status(candidate)["missing_views"]) <= set(coverage_status(plan)["missing_views"]),
             "coverage_regression: cannot remove required planned views")
    updated["scene_plan"] = candidate
    updated.setdefault("scene_profile_history", []).append({
        "profile": copy.deepcopy(diff["current_profile"]), "before_hash": before_hash,
        "after_hash": plan_hash(candidate), "old_revision": plan["revision"],
        "new_revision": candidate["revision"],
        "removed_slots": [copy.deepcopy(slot) for slot in plan["slots"] if slot["id"] in removed]})
    return updated, {**diff, "committed": True, "unchanged": False,
                     "invalidated_slots": [], "preserved_slots": [identifier for identifier in slots if identifier not in removed],
                     "old_revision": plan["revision"], "new_revision": candidate["revision"],
                     "before_hash": before_hash, "after_hash": plan_hash(candidate),
                     "requires_visual_review": bool(diff["added_slots"])}


def plan_hash(plan: dict[str, Any]) -> str:
    return _hash(validate_plan(plan))


def slot_hash(slot: dict[str, Any]) -> str:
    return _hash(_slot(slot))


def coverage_status(plan: dict[str, Any]) -> dict[str, Any]:
    """Describe planned coverage only; never certify an image's angle or scale."""
    normalized = validate_plan(plan)
    views = list(dict.fromkeys(slot["view"] for slot in normalized["slots"]))
    missing = [view for view in normalized["coverage"]["required_views"] if view not in views]
    return {"complete": not missing, "planned_views": views, "missing_views": missing,
            "preserve_slots": list(normalized["coverage"]["preserve_slots"]),
            "evidence": "plan-only", "visual_verified": False}


def _baseline_snapshot(project: dict[str, Any], known: set[str],
                       supplied: dict[str, str] | None) -> tuple[dict[str, str], set[str]]:
    hashes = {}
    finalized = set()
    for final in project.get("finals") or []:
        _require(isinstance(final, dict), "invalid_final: expected an object")
        identifier = final.get("slot") or final.get("role")
        _require(identifier is None or isinstance(identifier, str), "invalid_final_slot")
        if identifier in known:
            finalized.add(identifier)
            digest = final.get("sha256") or final.get("image_sha256")
            if digest is not None:
                digest = _digest(digest)
                _require(identifier not in hashes or hashes[identifier] == digest, "conflicting_baseline_hash")
                hashes[identifier] = digest
    if supplied is not None:
        _require(isinstance(supplied, dict) and set(supplied) <= known, "invalid_baseline_slots")
        for identifier, digest in supplied.items():
            digest = _digest(digest)
            _require(identifier not in hashes or hashes[identifier] == digest, "conflicting_baseline_hash")
            hashes[identifier] = digest
    return hashes, finalized


def apply_patch(project: dict[str, Any], patch: dict[str, Any], expected_revision: int | None = None,
                expected_hash: str | None = None, *, baseline_hashes: dict[str, str] | None = None
                ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply one scoped append/edit/replace/reorder atomically to a deep copy.

    append uses slots=[...]; edit uses slot_id and changes={...}; replace uses
    slot_id and slot={...}; reorder uses order=[all IDs]. Every operation requires
    scope.slots to name exactly its targets. preserve_slots protects image content;
    an edit of constraint_policy alone may recalibrate its review contract.
    baseline_hashes in patch declares expected bytes for finalized targets and
    protected finals; the keyword argument supplies current digests from the host.
    """
    plan = validate_plan(project.get("scene_plan"), project)
    _require(isinstance(patch, dict), "invalid_patch")
    operation = patch.get("op")
    fields = {"append": {"slots"}, "edit": {"slot_id", "changes"},
              "replace": {"slot_id", "slot"}, "reorder": {"order"}}
    _require(isinstance(operation, str) and operation in fields, "invalid_patch_operation")
    common = {"schema_version", "op", "scope", "preserve_slots", "baseline_hashes", "request_id",
              "expected_revision", "expected_hash"}
    _require(not set(patch) - common - fields[operation], "invalid_patch_fields: no whole-plan overwrite")
    _require(type(patch.get("schema_version")) is int and patch["schema_version"] == 1,
             "unsupported_patch_schema")
    if patch.get("request_id") is not None:
        _require(isinstance(patch["request_id"], str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", patch["request_id"]) is not None,
                 "invalid_patch_request_id")
    for parameter, embedded, field in ((expected_revision, patch.get("expected_revision"), "revision"),
                                       (expected_hash, patch.get("expected_hash"), "hash")):
        _require(parameter is None or embedded is None or parameter == embedded, "conflicting_expected_" + field)
    revision = expected_revision if expected_revision is not None else patch.get("expected_revision")
    signature = expected_hash if expected_hash is not None else patch.get("expected_hash")
    _require(revision is not None or signature is not None, "missing_precondition")
    if revision is not None:
        _require(type(revision) is int and revision == plan["revision"], "stale_plan_revision")
    before_hash = plan_hash(plan)
    if signature is not None:
        _require(_digest(signature) == before_hash, "stale_plan_hash")
    current = {slot["id"]: slot for slot in plan["slots"]}
    candidate = copy.deepcopy(plan)
    policy_only = False
    if operation == "append":
        _require(isinstance(patch.get("slots"), list) and bool(patch["slots"]), "missing_append_slots")
        additions = [_slot(slot) for slot in patch["slots"]]
        targets = [slot["id"] for slot in additions]
        _require(len(targets) == len(set(targets)) and not set(targets) & set(current), "existing_slot_id")
        candidate["slots"].extend(additions)
    elif operation in {"edit", "replace"}:
        identifier = patch.get("slot_id")
        _require(isinstance(identifier, str) and identifier in current, "unknown_target_slot")
        targets = [identifier]
        if operation == "edit":
            changes = patch.get("changes")
            _require(isinstance(changes, dict) and bool(changes) and "id" not in changes, "invalid_slot_changes")
            replacement = _slot({**current[identifier], **copy.deepcopy(changes)})
            policy_only = set(changes) == {"constraint_policy"}
        else:
            replacement = _slot(patch.get("slot"))
            _require(replacement["id"] == identifier, "immutable_slot_id")
        _require(set(current[identifier]["keep"]) <= set(replacement["keep"]), "keep_conflict")
        for field, rule in current[identifier].get("constraint_policy", {}).items():
            if rule["level"] == "required" and rule["origin"] in {"user-confirmed", "reference-derived"}:
                retained = replacement.get("constraint_policy", {}).get(field, {})
                _require(retained.get("level") == "required" and retained.get("origin") == rule["origin"],
                         "constraint_policy_provenance_conflict: cannot weaken, remove, or relabel required sources")
        candidate["slots"] = [replacement if slot["id"] == identifier else slot for slot in candidate["slots"]]
    else:
        targets = _strings(patch.get("order"), "order")
        _require(set(targets) == set(current), "invalid_reorder: require every existing slot exactly once")
        candidate["slots"] = [copy.deepcopy(current[identifier]) for identifier in targets]
    scope = patch.get("scope")
    _require(isinstance(scope, dict) and not set(scope) - {"slots", "subjects"}, "invalid_patch_scope")
    scoped = _strings(scope.get("slots"), "scope_slots")
    _require(set(scoped) == set(targets), "scope_mismatch: targets must be explicit")
    if "subjects" in scope:
        scoped_subjects = _strings(scope["subjects"], "scope_subjects")
        affected_subjects = set()
        for slot in plan["slots"] + candidate["slots"]:
            if slot["id"] in targets:
                affected_subjects.update(_subjects(project, slot))
        _require(affected_subjects <= {_subject_id(project, subject) for subject in scoped_subjects},
                 "subject_scope_mismatch")
    protected = set(plan["coverage"]["preserve_slots"])
    protected.update(_strings(patch.get("preserve_slots", []), "preserve_slots"))
    _require(protected <= set(current), "unknown_preserved_slot")
    _require(operation == "reorder" or policy_only or not protected & set(targets), "preserved_slot_conflict")
    hashes, finalized = _baseline_snapshot(project, set(current), baseline_hashes)
    declared = patch.get("baseline_hashes", {})
    _require(isinstance(declared, dict) and set(declared) <= set(current), "invalid_baseline_declaration")
    for identifier, digest in declared.items():
        _require(identifier in hashes and hashes[identifier] == _digest(digest), "stale_baseline_hash")
    required = (protected | (set(targets) if operation in {"edit", "replace"} else set())) & finalized
    _require(required <= set(hashes), "missing_baseline_hash: host must supply current image digests")
    _require(required <= set(declared), "missing_baseline_declaration")
    candidate["revision"] += 1
    candidate = validate_plan(candidate, project)
    previous_missing = set(coverage_status(plan)["missing_views"])
    missing = set(coverage_status(candidate)["missing_views"])
    _require(missing <= previous_missing, "coverage_regression: cannot remove required planned views")
    added = targets if operation == "append" else []
    changed = targets if operation in {"edit", "replace"} else []
    invalidated = [] if policy_only else changed
    unchanged = [identifier for identifier in current if identifier not in invalidated]
    updated = copy.deepcopy(project)
    updated["scene_plan"] = candidate
    impact = {"op": operation, "added_slots": list(added), "changed_slots": list(changed),
              "invalidated_slots": list(invalidated), "preserved_slots": unchanged,
              "sequence_changed": True,
              "old_revision": plan["revision"], "new_revision": candidate["revision"],
              "before_hash": before_hash, "after_hash": plan_hash(candidate),
              "baseline_hashes": copy.deepcopy(hashes),
              "preserved_hashes": {identifier: hashes[identifier] for identifier in unchanged if identifier in hashes},
              "baseline_evidence": "caller-declared-digests", "scope": copy.deepcopy(scope),
              "coverage": coverage_status(candidate), "requires_visual_review": True}
    return updated, impact


def _subjects(project: dict[str, Any], slot: dict[str, Any]) -> list[str]:
    if slot.get("subjects"):
        subjects = list(slot["subjects"])
    elif slot.get("subject"):
        subjects = [slot["subject"]]
    else:
        subjects = list((project.get("answers") or {}).get("subjects") or [])
        if not subjects:
            subjects = [identifier for identifier, item in _subject_map(project).items()
                        if isinstance(item, dict) and item.get("active", True)]
    identifiers = list(dict.fromkeys(_subject_id(project, subject) for subject in subjects))
    if isinstance(project.get("product_contract"), dict):
        catalog = _subject_map(project)
        _require(all(identifier in catalog and isinstance(catalog[identifier], dict)
                     and catalog[identifier].get("active", True) for identifier in identifiers),
                 "unknown_subject")
    return identifiers


def _reference_list(refs: Any) -> list[dict[str, Any]]:
    if isinstance(refs, list):
        return [_binding(value) for value in refs]
    _require(isinstance(refs, dict), "invalid_refs: expected legacy refs or typed bindings")
    _require(not set(refs) - {"source", "style", "based_on", "bindings"}, "invalid_refs_fields")
    result = []
    for field, kind in (("source", "product"), ("style", "style"), ("based_on", "baseline")):
        for path in _strings(refs.get(field, []), "refs_" + field):
            result.append(_binding({"kind": kind, "path": path}))
    bindings = refs.get("bindings", [])
    _require(isinstance(bindings, list), "invalid_bindings")
    return result + [_binding(value) for value in bindings]


def _selected_references(project: dict[str, Any], subjects: list[str]) -> list[dict[str, Any]]:
    chosen = project.get("selections") or {}
    result = []
    for subject in subjects:
        name = (_subject_map(project).get(subject) or {}).get("name", subject)
        for stage in ("cutout", "material", "concept"):
            reference = (chosen.get(stage) or {}).get(subject) or (chosen.get(stage) or {}).get(name)
            if isinstance(reference, dict) and reference.get("image"):
                binding = {"kind": "product", "path": reference["image"], "subject": subject}
                if reference.get("sha256"):
                    binding["sha256"] = reference["sha256"]
                result.append(_binding(binding))
                break
    environment = (chosen.get("scene") or {}).get("_")
    if isinstance(environment, dict) and environment.get("image"):
        result.append(_binding({"kind": "environment", "path": environment["image"]}))
    return result


def _view_references(project: dict[str, Any], slot: dict[str, Any],
                     subjects: list[str], pointers: list[str]) -> list[dict[str, Any]]:
    contract = project.get("product_contract")
    if not isinstance(contract, dict):
        return []
    specs = contract.get("specs") or {}
    selected_specs = {_resolve(project, pointer)["subject_id"]: set() for pointer in pointers}
    for pointer in pointers:
        token = pointer.rsplit("/", 1)[1].replace("~1", "/").replace("~0", "~")
        selected_specs[_resolve(project, pointer)["subject_id"]].add(token)
    assets = contract.get("view_assets") or {}
    _require(isinstance(assets, dict), "invalid_view_assets")
    result = []
    for identifier, asset in assets.items():
        if not isinstance(asset, dict) or asset.get("subject_id") not in subjects:
            continue
        if asset.get("spec_id") not in selected_specs.get(asset["subject_id"], set()):
            continue
        confirmation = asset.get("confirmation")
        if not isinstance(confirmation, dict) or confirmation.get("status") != "confirmed":
            continue
        if asset.get("spec_signature") is not None and asset["spec_signature"] != _hash(specs[asset["spec_id"]]):
            continue
        if asset.get("view") != slot["view"] and not (slot["view"] == "three-quarter" and asset.get("view") == "front"):
            continue
        result.append(_binding({"kind": "view", "id": identifier, "subject": asset["subject_id"],
                                "spec_id": asset["spec_id"], "view": asset["view"],
                                "path": asset.get("path"), "sha256": _digest(asset.get("sha256")),
                                "origin": asset.get("origin"), "confirmation": confirmation}))
    return result


def _scoped_feedback(project: dict[str, Any], slot: dict[str, Any], subjects: list[str]) -> list[dict[str, Any]]:
    result = []
    for feedback in project.get("feedback") or []:
        if not isinstance(feedback, dict) or feedback.get("status") in {"cancelled", "superseded"}:
            continue
        scope = feedback.get("scope") or {}
        if not isinstance(scope, dict) or not isinstance(scope.get("slots"), list) or slot["id"] not in scope["slots"]:
            continue
        scoped_subjects = scope.get("subjects") or []
        if not isinstance(scoped_subjects, list) or not all(isinstance(subject, str) for subject in scoped_subjects):
            continue
        scoped_subjects = list(dict.fromkeys(_subject_id(project, subject) for subject in scoped_subjects))
        if scoped_subjects and not set(scoped_subjects) & set(subjects):
            continue
        changes = feedback.get("changes") or []
        keep = feedback.get("keep") or []
        if not isinstance(keep, list) or not all(isinstance(item, str) for item in keep):
            continue
        if isinstance(changes, list) and all(isinstance(change, str) for change in changes):
            result.append({"subjects": copy.deepcopy(scoped_subjects or subjects),
                           "changes": list(changes), "keep": list(keep)})
    return result


def compile_slot(project: dict[str, Any], slot_id: str, refs: Any = None) -> dict[str, Any]:
    """Compile one independent prompt; scoped data never changes another slot's camera."""
    plan = validate_plan(project.get("scene_plan"), project)
    slot = next((item for item in plan["slots"] if item["id"] == slot_id), None)
    _require(slot is not None, "unknown_target_slot")
    subjects = _subjects(project, slot)
    pointers = _contract_refs(project, subjects, _spec_refs(slot))
    production = isinstance(project.get("product_contract"), dict)
    selected_specs = {}
    if production:
        for pointer in pointers:
            spec = _resolve(project, pointer)
            token = pointer.rsplit("/", 1)[1].replace("~1", "/").replace("~0", "~")
            selected_specs.setdefault(spec["subject_id"], set()).add(token)
    references = slot["reference_bindings"] + (_selected_references(project, subjects) if refs is None else _reference_list(refs))
    references += _view_references(project, slot, subjects, pointers)
    bindings = []
    seen = {}
    path_hashes = {}
    for reference in references:
        reference = copy.deepcopy(reference)
        if reference.get("slot_id") not in (None, slot_id):
            continue
        if reference.get("subject") is not None:
            reference["subject"] = _subject_id(project, reference["subject"])
        if reference.get("subject") is not None and reference["subject"] not in subjects:
            continue
        if production and reference["kind"] == "view":
            if (reference.get("confirmation") or {}).get("status") != "confirmed":
                continue
            if reference.get("spec_id") not in selected_specs.get(reference.get("subject"), set()):
                continue
            if reference.get("view") != slot["view"] and not (slot["view"] == "three-quarter" and reference.get("view") == "front"):
                continue
        digest = reference.get("sha256")
        if digest is not None:
            _require(reference["path"] not in path_hashes or path_hashes[reference["path"]] == digest,
                     "conflicting_reference_hash")
            path_hashes[reference["path"]] = digest
        key = (reference["kind"], reference["path"], reference.get("subject"),
               reference.get("view"), reference.get("spec_id"))
        if key not in seen:
            seen[key] = len(bindings)
            bindings.append(copy.deepcopy(reference))
        elif digest is not None:
            bindings[seen[key]]["sha256"] = digest
    for binding in bindings:
        if binding["path"] in path_hashes:
            binding["sha256"] = path_hashes[binding["path"]]
    if slot["view"] in {"back", "left", "right"}:
        approved = {binding.get("subject") for binding in bindings
                    if binding["kind"] == "view" and binding.get("view") == slot["view"]
                    and (binding.get("confirmation") or {}).get("status") == "confirmed"}
        _require(set(subjects) <= approved, "missing_view_reference: " + slot["view"])
    contracts = {pointer: copy.deepcopy(_resolve(project, pointer)) for pointer in pointers}
    bible = (project.get("brief") or {}).get("bible")
    if bible:
        if isinstance(bible, dict) and isinstance(bible.get("subjects"), dict):
            for subject in subjects:
                name = (_subject_map(project).get(subject) or {}).get("name", subject)
                key = subject if subject in bible["subjects"] else name
                selected = bible["subjects"].get(key)
                if selected is not None:
                    token = key.replace("~", "~0").replace("/", "~1")
                    contracts["/brief/bible/subjects/" + token] = copy.deepcopy(selected)
            if bible.get("shared") is not None:
                contracts["/brief/bible/shared"] = copy.deepcopy(bible["shared"])
        else:
            contracts["/brief/bible"] = copy.deepcopy(bible)
    contract_hashes = {pointer: _hash(value) for pointer, value in contracts.items()}
    feedback = _scoped_feedback(project, slot, subjects)
    display = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    policy = slot.get("constraint_policy", {})

    def instruction(field: str, text: str) -> str:
        rule = policy.get(field)
        if rule is None:
            return text
        label = "建议（advisory，非硬合同）" if rule["level"] == "advisory" else "硬合同（required）"
        return f"【{field}：{label}；来源={rule['origin']}；依据={rule['evidence']}】{text}"

    lines = [f"任务：生成图位 {slot_id} 的独立商业产品场景图；图位作用：{slot['role']}。",
             f"产品形态：{(project.get('answers') or {}).get('product_form') or '沿用已确认产品'}。",
             f"出场主体：{display(subjects)}；仅这些主体出场，不融合角色、不随机新增角色。",
             f"目标观察方向：{VIEW_LABELS[slot['view']]}（view={slot['view']}）；此为生成目标，不是图像验收结论。"]
    if policy:
        lines.extend([instruction("pose", f"产品姿态：{slot['pose']}。"),
                      instruction("framing", f"构图与景别：{slot['framing']}。")])
        lines.append("仅显式标为 advisory 的字段值是建议，非硬合同；未标字段沿用原有硬约束。"
                     "实际认脸完整性、目标视角、职责所需信息量、尺度相容性仍必须满足并独立看图验收；"
                     "建议不能覆盖身份、产品规格与尺寸、role、view、keep、coverage 或用户要求。")
    else:
        lines.append(f"产品姿态：{slot['pose']}。构图与景别：{slot['framing']}。")
    if slot["yaw"] is not None:
        lines.append(instruction("yaw", f"相对产品正面的水平观察角 yaw={slot['yaw']}°；0°为正面，正值向产品右侧，±180°为背面。"))
    if slot["elevation"] is not None:
        lines.append(instruction("elevation", f"相机俯仰 elevation={slot['elevation']}°；正值从上往下，负值从下往上，与产品姿态分开。"))
    if slot.get("description"):
        lines.append(instruction("description", "本图新信息：" + slot["description"]))
    lines.append("适用设计合同与身份资料：" + display(contracts))
    lines.append(instruction("scale_anchor", "尺度锚点：" + display(slot["scale_anchor"]))
                 + "；手掌仅作定性参照，不由画面推算厘米；未知尺寸不编造。")
    lines.append("保持项：" + display(slot["keep"]))
    lines.append("仅本图位适用的反馈：" + display(feedback))
    for index, binding in enumerate(bindings, 1):
        lines.append(f"参考{index}：{binding['path']}；类型={binding['kind']}；主体={binding.get('subject') or '按本图主体'}；"
                     f"形体资料视角={binding.get('view') or '未声明'}；"
                     f"只继承 {display(binding['allowed_aspects'])}，不得继承机位、姿态、景别；以本图目标为准。")
    lines.extend(["环境参考只约束场域、光线和氛围；风格参考只约束配色、工艺语言和光照，不能复制其机位。",
                  "保持各主体的轮廓、五官、色区、材质分区与连接结构；未确认隐藏面不得自行当成定稿。",
                  "光源、投影、支撑、悬挂和人体接触须可信；默认干净无字，不带水印、界面或提示条。",
                  "这是二维设计效果图，不声称真实3D模型、实物尺寸测量或已打样；生成后另做实际图验收与独立物理审查。"])
    result = {"slot_id": slot_id, "prompt": "\n".join(lines), "bindings": bindings,
              "contract_hashes": contract_hashes, "slot_sha256": slot_hash(slot),
              "unhashed_references": list(dict.fromkeys(binding["path"] for binding in bindings if "sha256" not in binding)),
              "evidence": "plan-only", "requires_visual_review": True}
    if "constraint_policy" in slot:
        result["constraint_policy"] = copy.deepcopy(slot["constraint_policy"])
    result["signature"] = _hash(result)
    return result
