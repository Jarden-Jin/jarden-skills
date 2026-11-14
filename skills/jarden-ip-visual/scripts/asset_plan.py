"""Finite asset catalog and minimal dependency planning; no IO beyond the catalog."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).resolve().parents[1] / "assets" / "asset-catalog.json"
SUPPORT = {"supported", "design-reference", "unverified", "unsupported"}
KINDS = {"image", "document", "bundle", "source"}


class AssetPlanError(ValueError):
    """Invalid catalog, selection, or dependency graph."""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def catalog() -> dict[str, Any]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assets = data["assets"]
    identifiers = {item["id"] for item in assets}
    if len(assets) != 21 or identifiers != {f"{number:02d}" for number in range(1, 22)}:
        raise AssetPlanError("invalid_catalog: expected stable IDs 01–21")
    grouped = [identifier for group in data["groups"] for identifier in group["asset_ids"]]
    if len(data["groups"]) != 4 or len(grouped) != 21 or set(grouped) != identifiers:
        raise AssetPlanError("invalid_catalog: expected four nonoverlapping groups")
    categories = {group["id"]: group["asset_ids"] for group in data["groups"]}
    for item in assets:
        if item["support"] not in SUPPORT or item["kind"] not in KINDS:
            raise AssetPlanError("invalid_catalog: unknown support or kind")
        if item["id"] not in categories.get(item["category"], []):
            raise AssetPlanError("invalid_catalog: category differs from group")
        if not set(item["dependencies"]).issubset(identifiers):
            raise AssetPlanError("invalid_catalog: unknown dependency")
    dependency_order(list(identifiers), {item["id"]: item for item in assets})
    return data


def normalize_ids(values: Any) -> list[str]:
    if not isinstance(values, list) or not values:
        raise AssetPlanError("asset_ids_required: choose a nonempty numbered asset list")
    result = []
    for value in values:
        if not isinstance(value, str) or not value.isascii() or not value.isdigit():
            raise AssetPlanError("invalid_asset_id: use IDs 01–21")
        identifier = value.zfill(2)
        if identifier not in {f"{number:02d}" for number in range(1, 22)}:
            raise AssetPlanError("invalid_asset_id: use IDs 01–21")
        if identifier not in result:
            result.append(identifier)
    return result


def definition(identifier: str) -> dict[str, Any]:
    normalized = normalize_ids([identifier])[0]
    return next(item for item in catalog()["assets"] if item["id"] == normalized)


def dependency_order(selected: list[str], definitions: dict[str, dict[str, Any]]) -> list[str]:
    ordered = []
    visiting: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visiting:
            raise AssetPlanError("dependency_cycle: finite asset graph must be acyclic")
        if identifier in ordered:
            return
        visiting.add(identifier)
        for prerequisite in definitions[identifier]["dependencies"]:
            visit(prerequisite)
        visiting.remove(identifier)
        ordered.append(identifier)

    for identifier in selected:
        visit(identifier)
    return ordered


def compile_plan(project: dict[str, Any], eligible: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    workflow = project.get("asset_workflow") or {}
    selected = workflow.get("selected") or []
    definitions = {item["id"]: item for item in catalog()["assets"]}
    eligible = eligible or {}
    subjects = workflow.get("subjects") or ["主体"]
    executable = [identifier for identifier in selected if definitions[identifier]["support"] not in {"unsupported", "unverified"}]
    ordered = dependency_order(executable, definitions)
    ordered.extend(identifier for identifier in selected if identifier not in ordered)
    rows = []
    for subject in subjects:
        for identifier in ordered:
            item = definitions[identifier]
            key = digest([identifier, subject])
            reusable = eligible.get(key)
            missing = [prerequisite for prerequisite in item["dependencies"]
                       if digest([prerequisite, subject]) not in eligible]
            blocked_reason = item.get("blocked_reason") if item["support"] in {"unsupported", "unverified"} else None
            rows.append({
                "asset_id": identifier, "name": item["name"], "subject": subject,
                "kind": item["kind"], "support": item["support"], "selected": identifier in selected,
                "internal": identifier not in selected, "requires": list(item["dependencies"]),
                "missing_dependencies": missing, "reuse_instance": reusable.get("id") if reusable else None,
                "status": "approved" if reusable else "blocked" if blocked_reason else "waiting" if missing else "ready",
                "blocked_reason": blocked_reason,
            })
    return {
        "revision": workflow.get("plan_revision", 0),
        "signature": digest({"selected": selected, "subjects": subjects, "requirements": workflow.get("requirements", {})}),
        "deliverables": [copy.deepcopy(row) for row in rows if row["selected"]],
        "dependencies": [copy.deepcopy(row) for row in rows if row["internal"]],
        "execution": rows, "blocked": [copy.deepcopy(row) for row in rows if row["blocked_reason"]],
        "additional_generation": {
            "required_images": sum(row["kind"] in {"image", "bundle"} and not row["reuse_instance"]
                                   and not row["blocked_reason"] for row in rows),
            "exploration_candidates_default": 2,
            "estimate_only": True,
            "note": "这是缺项图像资产数，不是调用承诺；已有真实输入可采用，每项候选数可覆盖，未支持项不计入可完成量。",
        },
    }
