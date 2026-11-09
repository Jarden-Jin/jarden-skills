#!/usr/bin/env python3
"""Independent G1 contract reviews; declarations and hashes are not visual proof."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import secrets
import shutil
import struct
import sys
import time
import zlib
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
STAGES = ("scene", "package", "done")
CHECKS = ("identity", "scale", "view")
SEQUENCE_CHECKS = ("coverage", "preservation", "order")
VERDICTS = ("pass", "needs_revision", "uncertain")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_CHUNKS = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"sRGB", b"cHRM", b"sBIT"}
PRIVATE_KEYS = {
    "prompt", "prompts", "rationale", "author_preference", "preferences", "favorite",
    "winner", "expected_verdict", "must_pass", "review", "reviews", "reviewer", "report",
    "created_at", "updated_at", "revision", "status_history", "generation_task", "notes",
}
SLOT_RUNTIME_KEYS = {"path", "image", "output", "output_path", "source", "source_path", "sha256", "status"}
TASK = """你是不继承创作者对话的独立 G1 合同视觉审查者。只读本包，不索取作者提示词、偏好、历史好评或物理审查结果。
逐张实际查看 images 和必要 reference_images 中的 PNG；scope 是合同及计划，不是实际观察证据。
identity 检查可见身份、结构、附件左右与已批准依据；未知隐藏面不是已还原事实。
view_assets 的 confirmation 与 origin 必须分别保留理解；确认设计目标不把 agent-inferred 变成参考还原事实。
scale 检查效果图与设计数值/范围/定性目标相容性；未知参照、手型、透视或画面占比不能精测厘米。证据不足判 uncertain。
尺度检查遵守该图位的 scale_anchor：认脸封面或微距未声称厘米时，不要求每张都放尺具；可核对可见比例未冲突并明确本图不验证绝对尺寸。实际数值尺度图或大小款比较须有可用证据，缺证据仍 uncertain；完整发布集合必须包含合同约定的尺度信息，不能用逐图不适用来逃避。
view 分开核对实际方位、俯仰、姿态与取景；文件名、提示词、yaw 字段、相同/不同哈希均不是实际角度证据。
slot.constraint_policy 明确为 advisory 且 origin=agent-inferred、带依据的构图建议，不仅因偏离该建议数字或道具位置就阻断；仍须核对认脸、完整性、视角职责、信息增量、尺度相容性。用户尺寸/口径、身份、必须保持项、role/view/coverage和交付规格不得降为建议。无 policy 的旧约束不自动降级；新的来源校准须显式计划和依据，历史负评不得改写。若画出尺具，其刻度、零点、支撑与透视仍必须可信，不能因表达方法是建议而豁免错误。
sequence 检查本批实际覆盖、保持图及实际排列；publication 范围必须核对完整发布集合，不能只看新增图。
collection.mode=batch 时仅检查 collection.required_slots 和本批 plan.coverage，不要求尚未生成的全套图；publication 必须检查全部真实必需图位和完整覆盖。候选替代方案不是最终笔记集合。
独立记录 observed_views。不可把元数据角度抄作观察；程序只能验证报告结构及声明，不能证明真的看图或独立。
返回 schema_version=1、request_id、scope_signature、reviewer={id:实际执行者ID,independence:zero-context,viewed_images:true}、verdict、results、sequence。
results 逐张含 slot_id/path/sha256/verdict/observed_views(非空字符串数组)/findings，以及 checks 的 identity/scale/view 三项。
每项 check={verdict:pass|needs_revision|uncertain,evidence:具体可见依据,basis:visible_pixels}。
findings 是列表；未通过时每项含 code/evidence/fix，pass 时为空。不可仅返回一个总 pass。
sequence 含 verdict/checks/findings/observed_views/preserve_images/actual_order。
sequence.checks 必须有 coverage/preservation/order，格式同上述 check；order.evidence 说明实际排列。
sequence.observed_views 逐图含 slot_id/sha256/views/evidence/basis:visible_pixels，并与逐图 observed_views 相符。
sequence.preserve_images 逐项对应 scope.collection.preserve_images，含 slot_id/sha256/verdict/evidence/basis:visible_pixels。
sequence.actual_order 按 scope.ordered_slots 返回实际查看的 slot_id 数组。图序的声明不能替代实际看图。
任一关键检查失败或不确定均不能整体 pass。没有可见证据不要脑补。不要修改图片、项目或物理盲审包。
"""


class ContractReviewError(Exception):
    """A malformed, stale, missing, or blocked G1 contract review."""


def _signature(value: Any) -> str:
    try:
        content = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ContractReviewError("合同必须是有限数值组成的 JSON 数据") from error
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    import project as state

    try:
        value = state.read_json(path)
    except (state.ProjectError, OSError, ValueError) as error:
        raise ContractReviewError(f"无法读取合同审查 JSON：{path.name}") from error
    if not isinstance(value, dict):
        raise ContractReviewError("合同审查 JSON 必须是对象")
    return value


def _local(root: Path, relative: str, *, exists: bool = True) -> Path:
    if not isinstance(relative, str) or not relative.strip() or Path(relative).is_absolute():
        raise ContractReviewError("图片和报告必须使用项目内相对路径")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or exists and not path.is_file():
        raise ContractReviewError(f"项目内文件不存在或路径越界：{relative}")
    return path


def _active(project: dict[str, Any], stage: str) -> bool:
    contract = project.get("product_contract")
    return project.get("schema_version") == 3 and isinstance(contract, dict) and contract.get("enabled") is True and stage in STAGES


def _neutral(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _neutral(child) for key, child in value.items() if str(key).casefold() not in PRIVATE_KEYS}
    if isinstance(value, list):
        return [_neutral(child) for child in value]
    if isinstance(value, str) and re.search(r"must.?pass|should pass|必须通过|作者.{0,10}(喜欢|偏好|希望)|预设结论", value, re.I):
        raise ContractReviewError("合同投影含作者结论或通过暗示；先提供中性合同")
    return value


def _records(value: Any, label: str) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        rows = [{"id": identifier, **row} for identifier, row in value.items() if isinstance(row, dict)]
        if len(rows) != len(value):
            raise ContractReviewError(f"{label} 必须是对象列表或以 ID 为键的对象")
        return rows
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):
        return copy.deepcopy(value)
    raise ContractReviewError(f"{label} 必须是对象列表或以 ID 为键的对象")


def _review_rounds(project: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in project.get("rounds") or [] if isinstance(entry, dict)
            and entry.get("stage") == "scene" and entry.get("status") in {"planned", "recorded"}]


def _slots(project: dict[str, Any], stage: str = "scene") -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    plan = project.get("scene_plan")
    if not isinstance(plan, dict):
        raise ContractReviewError("启用 G1 前必须提供 scene_plan")
    rows = _records(plan.get("slots", []), "scene_plan.slots")
    if stage == "scene":
        for entry in _review_rounds(project):
            rows.extend(_records(entry.get("review_slots", []), "round.review_slots"))
    found = {}
    for row in rows:
        identifier = row.get("slot_id") or row.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in found:
            raise ContractReviewError("图位必须有唯一稳定 ID")
        found[identifier] = row
    if not found:
        raise ContractReviewError("没有可绑定的场景图位")
    return plan, found


def _identifiers(slot: dict[str, Any], singular: str) -> set[str]:
    values = slot.get(singular + "s", [])
    if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
        raise ContractReviewError(f"{singular}s 必须是 ID 列表")
    result = set(values)
    if slot.get(singular) is not None:
        if not isinstance(slot[singular], str) or not slot[singular].strip():
            raise ContractReviewError(f"{singular} 必须是非空 ID")
        result.add(slot[singular])
    return result


def _contract_records(contract: dict[str, Any], field: str) -> dict[str, dict[str, Any]]:
    value = contract.get(field, {})
    if isinstance(value, dict):
        if any(not isinstance(identifier, str) or not identifier.strip() or not isinstance(row, dict)
               or row.get("id", identifier) != identifier for identifier, row in value.items()):
            raise ContractReviewError(f"product_contract.{field} 必须使用一致的稳定 ID")
        return value
    found = {}
    identifier_key = {"subjects": "subject_id", "specs": "spec_id", "variants": "variant_id"}.get(field, "id")
    for row in _records(value, "product_contract." + field):
        identifier = row.get("id") or row.get(identifier_key)
        if not isinstance(identifier, str) or not identifier.strip() or identifier in found:
            raise ContractReviewError(f"product_contract.{field} 必须使用唯一稳定 ID")
        found[identifier] = row
    return found


def _resolve(project: dict[str, Any], pointer: str) -> tuple[list[str], Any]:
    if not isinstance(pointer, str) or not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise ContractReviewError("spec_ref 必须是合法项目 JSON pointer")
    tokens = [token.replace("~1", "/").replace("~0", "~") for token in pointer.split("/")[1:]]
    current: Any = project
    for token in tokens:
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and re.fullmatch(r"0|[1-9][0-9]*", token) and int(token) < len(current):
            current = current[int(token)]
        else:
            raise ContractReviewError(f"图位引用了不存在的 spec_ref：{pointer}")
    return tokens, current


def _contract_for(project: dict[str, Any], slot: dict[str, Any]) -> dict[str, Any]:
    contract = copy.deepcopy(project["product_contract"])
    subjects = _contract_records(contract, "subjects")
    specs = _contract_records(contract, "specs")
    variants = _contract_records(contract, "variants")
    subject_ids = set()
    for subject in _identifiers(slot, "subject") | _identifiers(slot, "subject_id"):
        matches = [identifier for identifier, row in subjects.items()
                   if identifier == subject or row.get("name") == subject]
        if len(matches) != 1:
            raise ContractReviewError("图位引用了未知或不唯一的主体：" + subject)
        subject_ids.add(matches[0])
    requested_specs = _identifiers(slot, "spec_id") | _identifiers(slot, "variant_id")
    if not requested_specs.issubset(specs.keys() | variants.keys()):
        raise ContractReviewError("图位引用了不存在的款式")
    spec_ids = requested_specs.intersection(specs)
    variant_ids = requested_specs.intersection(variants)
    resolved = {}
    pointers = slot.get("spec_refs", [])
    if not isinstance(pointers, list):
        raise ContractReviewError("spec_refs 必须是 JSON pointer 列表")
    for pointer in ([slot["spec_ref"]] if slot.get("spec_ref") is not None else []) + pointers:
        tokens, value = _resolve(project, pointer)
        if len(tokens) >= 3 and tokens[:2] in (["product_contract", "specs"], ["product_contract", "variants"]):
            inventory = specs if tokens[1] == "specs" else variants
            identifier = tokens[2] if isinstance(project["product_contract"][tokens[1]], dict) else list(inventory)[int(tokens[2])]
            (spec_ids if tokens[1] == "specs" else variant_ids).add(identifier)
        elif tokens[0] == "product_contract":
            raise ContractReviewError("spec_ref 必须指向具体款式，不能绑定整个产品合同")
        elif isinstance(value, dict):
            resolved[pointer] = copy.deepcopy(value)
        else:
            raise ContractReviewError("spec_ref 必须指向款式对象")
    if not spec_ids.issubset(specs) or not variant_ids.issubset(variants):
        raise ContractReviewError("图位引用了不存在的款式")
    if not spec_ids:
        spec_ids = {identifier for identifier, row in specs.items() if not subject_ids or row.get("subject_id") in subject_ids}
    if not variant_ids:
        variant_ids = {identifier for identifier, row in variants.items()
                       if (not subject_ids or row.get("subject_id") in subject_ids)
                       and (row.get("spec_id") is None or row["spec_id"] in spec_ids)}
    owners = {row["subject_id"] for inventory, identifiers in ((specs, spec_ids), (variants, variant_ids))
              for identifier, row in inventory.items() if identifier in identifiers and row.get("subject_id") is not None}
    if not owners.issubset(subjects) or subject_ids and not owners.issubset(subject_ids):
        raise ContractReviewError("图位主体与款式不匹配")
    subject_ids = subject_ids or owners or set(subjects)
    selections = {"subjects": subject_ids, "specs": spec_ids, "variants": variant_ids}
    assets = _contract_records(contract, "view_assets")
    selections["view_assets"] = {identifier for identifier, row in assets.items()
                                 if (row.get("subject_id") is None or row["subject_id"] in subject_ids)
                                 and (row.get("spec_id") is None or row["spec_id"] in spec_ids | variant_ids)}
    for field, identifiers in selections.items():
        if field not in contract:
            continue
        inventory = _contract_records(contract, field)
        selected = {identifier: row for identifier, row in inventory.items() if identifier in identifiers}
        contract[field] = selected if isinstance(contract[field], dict) else list(selected.values())
    if resolved:
        contract["resolved_spec_refs"] = resolved
    contract.pop("revision", None)
    contract.pop("updated_at", None)
    return _neutral(contract)


def _slot_projection(slot: dict[str, Any]) -> dict[str, Any]:
    return _neutral({key: value for key, value in slot.items() if key not in SLOT_RUNTIME_KEYS})


def _reference_files(root: Path, value: Any) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}

    def visit(node: Any, in_references: bool = False) -> None:
        if isinstance(node, list):
            for child in node:
                visit(child, in_references)
        elif isinstance(node, dict):
            relative = node.get("path") or node.get("image")
            if in_references and isinstance(relative, str):
                original = _local(root, relative)
                sha = _hash(original)
                if node.get("sha256") is not None and node["sha256"] != sha:
                    raise ContractReviewError(f"身份参考摘要已变化：{relative}")
                found[relative] = {"path": relative, "sha256": sha}
            for key, child in node.items():
                visit(child, in_references or key in {"refs", "references", "reference_bindings", "views", "view_assets", "assets"})
        elif in_references and isinstance(node, str) and node.lower().endswith(".png"):
            original = _local(root, node)
            found[node] = {"path": node, "sha256": _hash(original)}

    visit(value)
    return [found[key] for key in sorted(found)]


def _source_paths(project: dict[str, Any], slot_id: str, slot: dict[str, Any], stage: str = "scene", root: Path | None = None) -> list[str]:
    paths = [slot[key] for key in ("path", "image", "source_path") if isinstance(slot.get(key), str)]
    for final in project.get("finals") or []:
        if isinstance(final, dict) and (final.get("slot_id") or final.get("slot")) == slot_id and isinstance(final.get("path"), str):
            paths.append(final["path"])
    for entry in _review_rounds(project) if stage == "scene" else []:
        review_paths = entry.get("review_paths", {})
        if not isinstance(review_paths, dict):
            raise ContractReviewError("round.review_paths 必须是图位到项目内相对路径的映射")
        if slot_id in review_paths:
            relative = review_paths[slot_id]
            if not isinstance(relative, str) or not relative.strip():
                raise ContractReviewError("round.review_paths 必须使用非空相对路径")
            paths.append(relative)
        if root is not None and isinstance(entry.get("dir"), str):
            round_file = _local(root, entry["dir"] + "/round.json", exists=False)
            if round_file.is_file():
                data = _read(round_file)
                for item in data.get("items") or []:
                    identifier = item.get("review_slot_id") or item.get("slot_id") or item.get("id")
                    if identifier == slot_id and isinstance(item.get("image"), str):
                        paths.append(entry["dir"] + "/" + item["image"])
    return list(dict.fromkeys(paths))


def _mapping(root: Path, project: dict[str, Any], paths: list[str], slots: dict[str, dict[str, Any]], ordered_slots: list[Any] | None, stage: str = "scene") -> list[dict[str, str]]:
    if not isinstance(paths, list) or not paths or any(not isinstance(path, str) or not path.strip() for path in paths) or len(set(paths)) != len(paths):
        raise ContractReviewError("G1 需要非空且不重复的实际图片路径")
    if ordered_slots is not None and (not isinstance(ordered_slots, list) or len(ordered_slots) != len(paths)):
        raise ContractReviewError("ordered_slots 必须与实际图片逐项同序对应")
    mapping = []
    seen = set()
    for index, relative in enumerate(paths):
        original = _local(root, relative)
        sha = _hash(original)
        declared = ordered_slots[index] if ordered_slots is not None else None
        if isinstance(declared, str):
            declared = {"slot_id": declared}
        if declared is None:
            matches = []
            for slot_id, slot in slots.items():
                for source in _source_paths(project, slot_id, slot, stage, root):
                    candidate = _local(root, source, exists=False)
                    if not candidate.is_file():
                        continue
                    if source == relative or _hash(candidate) == sha:
                        matches.append({"slot_id": slot_id, "source": source})
                        break
            if len(matches) != 1:
                raise ContractReviewError("无法唯一映射实际图位；排版图请由调用方提供 ordered_slots/source，不能猜测")
            declared = matches[0]
        if not isinstance(declared, dict):
            raise ContractReviewError("ordered_slots 每项必须为稳定 ID 或映射对象")
        slot_id = declared.get("slot_id") or declared.get("slot") or declared.get("id")
        if not isinstance(slot_id, str) or slot_id not in slots or slot_id in seen or declared.get("path", relative) != relative:
            raise ContractReviewError("图位未知、重复或实际图片排列与映射不一致")
        seen.add(slot_id)
        source = declared.get("source") or declared.get("source_path")
        known = _source_paths(project, slot_id, slots[slot_id], stage, root)
        if source is None:
            source = next((value for value in known if _local(root, value, exists=False).is_file()
                           and _hash(_local(root, value)) == sha), None)
            if source is None and not known and ordered_slots is not None:
                source = relative
        if not isinstance(source, str):
            raise ContractReviewError("排版图不同于源图；必须提供显式 source 映射")
        source_sha = _hash(_local(root, source))
        if known and source not in known and not any(_local(root, value, exists=False).is_file()
                                                     and _hash(_local(root, value)) == source_sha for value in known):
            raise ContractReviewError("source 映射不属于指定图位")
        if declared.get("source_sha256", source_sha) != source_sha or declared.get("sha256", sha) != sha:
            raise ContractReviewError("映射中的图片摘要与当前字节不一致")
        mapping.append({"slot_id": slot_id, "path": relative, "sha256": sha, "source": source, "source_sha256": source_sha})
    return mapping


def _preserved(root: Path, plan: dict[str, Any], mappings: list[dict[str, str]], publication: bool) -> list[dict[str, str]]:
    values = copy.deepcopy(plan.get("preserve_images") or [])
    if not isinstance(values, list):
        raise ContractReviewError("preserve_images 必须是列表")
    coverage = plan.get("coverage") or {}
    if not isinstance(coverage, dict):
        raise ContractReviewError("coverage 必须是对象")
    slots = coverage.get("preserve_slots", [])
    if not isinstance(slots, list) or any(not isinstance(identifier, str) for identifier in slots) or len(set(slots)) != len(slots):
        raise ContractReviewError("coverage.preserve_slots 必须是唯一图位列表")
    for identifier in slots:
        if any(isinstance(value, dict) and (value.get("slot_id") or value.get("slot")) == identifier for value in values):
            continue
        matches = [row for row in mappings if row["slot_id"] == identifier]
        if matches:
            values.append({"slot_id": identifier, "sha256": matches[0]["source_sha256"]})
        elif publication:
            raise ContractReviewError("实际发布集合缺少必须保留的图位")
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, dict):
            raise ContractReviewError("保持图需要 slot_id 和 sha256")
        slot_id = value.get("slot_id") or value.get("slot")
        sha = value.get("sha256")
        if not isinstance(slot_id, str) or slot_id in seen or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ContractReviewError("保持图需要稳定图位和原图摘要")
        seen.add(slot_id)
        matches = [row for row in mappings if row["slot_id"] == slot_id]
        if not matches:
            if publication:
                raise ContractReviewError("实际发布集合缺少必须保留的图片")
            continue
        if matches[0]["source_sha256"] != sha:
            raise ContractReviewError("必须保留的原图已替换，不能沿用保持声明")
        result.append({"slot_id": slot_id, "sha256": matches[0]["sha256"], "source_sha256": sha})
    return result


def _scope(root: Path, project: dict[str, Any], paths: list[str], stage: str, ordered_slots: list[Any] | None) -> dict[str, Any]:
    plan, slots = _slots(project, stage)
    mappings = _mapping(root, project, paths, slots, ordered_slots, stage)
    images = []
    for row in mappings:
        contract = _contract_for(project, slots[row["slot_id"]])
        slot = _slot_projection(slots[row["slot_id"]])
        identity_references = []
        subjects = _contract_records(contract, "subjects")
        names = set(subjects) | {subject.get("name") for subject in subjects.values() if subject.get("name")}
        for prerequisite in ("concept", "material", "cutout"):
            for subject, selected in ((project.get("selections") or {}).get(prerequisite) or {}).items():
                if subject in names and isinstance(selected, dict) and selected.get("image"):
                    identity_references.append({"path": selected["image"], "stage": prerequisite,
                                                "origin": selected.get("origin", "reference-derived")})
        if not identity_references:
            identity_references = [{"path": item["path"], "origin": "reference-derived"}
                                   for item in project.get("inputs") or [] if isinstance(item, dict) and item.get("path")]
        references = _reference_files(root, {"contract": contract, "slot": slot, "references": identity_references})
        applicability = {"contract": contract, "slot": slot, "reference_images": references}
        images.append({**row, **applicability, "applicable_signature": _signature(applicability)})
    publication = stage in {"package", "done"}
    required = plan.get("required_slots")
    if required is None:
        required = [identifier for identifier, slot in slots.items() if slot.get("required", True)]
    if not isinstance(required, list) or any(not isinstance(identifier, str) or identifier not in slots for identifier in required):
        raise ContractReviewError("required_slots 必须引用已定义的图位")
    if publication:
        required = list(slots)
    if publication and not set(required).issubset(row["slot_id"] for row in mappings):
        raise ContractReviewError("实际发布集合未覆盖必需图位")
    batch_plan = _neutral({key: value for key, value in plan.items() if key not in {"revision", "updated_at", "slots"}})
    selected_slots = set(row["slot_id"] for row in mappings)
    if not publication:
        coverage = copy.deepcopy(batch_plan.get("coverage") or {})
        coverage["required_views"] = list(dict.fromkeys(slots[row["slot_id"]].get("view") for row in mappings
                                                       if isinstance(slots[row["slot_id"]].get("view"), str)))
        coverage["preserve_slots"] = [identifier for identifier in coverage.get("preserve_slots", []) if identifier in selected_slots]
        batch_plan["coverage"] = coverage
    collection = {
        "mode": "publication" if publication else "batch",
        "plan": batch_plan,
        "slot_requirements": {identifier: _slot_projection(slot) for identifier, slot in slots.items() if publication or identifier in selected_slots},
        "required_slots": required if publication else [row["slot_id"] for row in mappings],
        "preserve_images": _preserved(root, plan, mappings, publication),
        "ordered_images": [{key: row[key] for key in ("slot_id", "sha256", "applicable_signature")} for row in images],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "publication" if publication else "scene",
        "images": images,
        "ordered_slots": [row["slot_id"] for row in mappings],
        "collection": collection,
        "collection_signature": _signature(collection),
    }


def _clean_png(original: Path) -> bytes:
    content = original.read_bytes()
    if not content.startswith(PNG_SIGNATURE):
        raise ContractReviewError("标准库 G1 准备器只接受 PNG；请先登记真实像素 PNG，不改后缀冒充")
    offset = len(PNG_SIGNATURE)
    chunks = []
    image_data = []
    header = None
    finished = False
    while offset < len(content):
        if offset + 12 > len(content):
            raise ContractReviewError("PNG 块不完整")
        length, kind = struct.unpack(">I4s", content[offset:offset + 8])
        end = offset + length + 12
        if end > len(content):
            raise ContractReviewError("PNG 块长度无效")
        payload = content[offset + 8:offset + 8 + length]
        checksum = struct.unpack(">I", content[end - 4:end])[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != checksum:
            raise ContractReviewError("PNG CRC 无效")
        if not chunks and kind != b"IHDR":
            raise ContractReviewError("PNG 缺少首个 IHDR")
        if kind in {b"acTL", b"fcTL", b"fdAT"}:
            raise ContractReviewError("G1 不接受多帧 PNG，以免查看副本丢失帧")
        if kind == b"IHDR":
            if header is not None or length != 13:
                raise ContractReviewError("PNG IHDR 无效")
            header = struct.unpack(">IIBBBBB", payload)
        if kind == b"IDAT":
            image_data.append(payload)
        if kind not in PNG_CHUNKS and kind[0] & 32 == 0:
            raise ContractReviewError("PNG 含未知关键块")
        if kind in PNG_CHUNKS:
            chunks.append(content[offset:end])
        offset = end
        if kind == b"IEND":
            finished = length == 0 and offset == len(content)
            break
    if not header or not finished or not image_data:
        raise ContractReviewError("PNG 不完整或含尾随数据")
    width, height, depth, color, compression, filtering, interlace = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
    if not width or not height or not channels or depth not in depths[color] or compression or filtering or interlace:
        raise ContractReviewError("仅支持合法的非交错单帧 PNG")
    expected = height * (1 + (width * channels * depth + 7) // 8)
    if expected > 128 * 1024 * 1024:
        raise ContractReviewError("PNG 解码大小超出 G1 查看副本预算")
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(b"".join(image_data), expected + 1)
        stride = expected // height
        if len(pixels) != expected or not decoder.eof or decoder.unused_data or any(pixels[position] > 4 for position in range(0, expected, stride)):
            raise ContractReviewError("PNG 像素流无效")
    except zlib.error as error:
        raise ContractReviewError("PNG 像素流无法解码") from error
    return PNG_SIGNATURE + b"".join(chunks)


def prepare(root: Path, project: dict[str, Any], paths: list[str], producer: str, stage: str = "scene", ordered_slots: list[Any] | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    if not _active(project, stage):
        return {"ok": True, "applicable": False, "verdict": "not_applicable", "blocking": False}
    if not isinstance(producer, str) or not producer.strip():
        raise ContractReviewError("必须声明实际制图执行者")
    scope = _scope(root, project, paths, stage, ordered_slots)
    references = {row["path"]: row for image in scope["images"] for row in image["reference_images"]}
    originals = [(row, _clean_png(_local(root, row["path"]))) for row in scope["images"]]
    reference_copies = [(row, _clean_png(_local(root, row["path"]))) for row in references.values()]
    request_id = f"contract-{time.time_ns()}-{secrets.token_hex(6)}"
    directory = _local(root, "reviews/contract/" + request_id, exists=False)
    packet = directory / "packet"
    packet.mkdir(parents=True, exist_ok=False)
    try:
        images, reference_images = [], []
        for prefix, items, destination in (("image", originals, images), ("reference", reference_copies, reference_images)):
            for index, (row, content) in enumerate(items, 1):
                filename = f"{prefix}-{index:03d}.png"
                (packet / filename).write_bytes(content)
                if _hash(_local(root, row["path"])) != row["sha256"]:
                    raise ContractReviewError("准备期间原图或参考字节已变化")
                destination.append({"path": row["path"], "sha256": row["sha256"], "image": filename, "preview_sha256": _hash(packet / filename)})
        request = {"schema_version": SCHEMA_VERSION, "request_id": request_id,
                   "project_id": project.get("project_id"), "scope": scope, "scope_signature": _signature(scope),
                   "images": images, "reference_images": reference_images, "task": TASK}
        import project as state

        state.atomic_json(packet / "request.json", request)
        state.atomic_json(directory / "binding.json", {"schema_version": SCHEMA_VERSION, "producer": producer.strip(),
                          "generation": time.time_ns(), "request_sha256": _hash(packet / "request.json")})
    except BaseException:
        shutil.rmtree(directory)
        raise
    return {"ok": True, "applicable": True, "request_id": request_id, "packet": str(packet),
            "request": str(packet / "request.json"), "scope": scope,
            "say": "仅将 packet 交给新建独立看图执行者；binding 不送审，G1 结果不送入 G2"}


def _evidence(value: Any, metadata: dict[str, Any] | None = None) -> None:
    if not isinstance(value, dict) or value.get("basis") != "visible_pixels" or not isinstance(value.get("evidence"), str) or len(value["evidence"].strip()) < 8:
        raise ContractReviewError("每项必须声明 visible_pixels 并提供具体可见证据，元数据角度不能当依据")
    labels = {"view", "views", "yaw", "pitch", "azimuth", "elevation", "pose", "framing", "camera",
              "front", "back", "side", "left", "right", "three-quarter", "detail", "null", "none",
              "视角", "方位", "俯仰", "姿态", "取景", "正面", "背面", "侧面", "度"}
    if metadata:
        fields = {key: metadata[key] for key in ("view", "yaw", "elevation", "pose", "framing") if key in metadata}
        if isinstance(metadata.get("camera"), dict):
            fields.update(metadata["camera"])
        labels.update(str(item) for item in fields.values() if isinstance(item, (str, int, float)))
    remainder = re.sub(r"[\W_]+", "", value["evidence"].casefold())
    for label in sorted({re.sub(r"[\W_]+", "", item.casefold()) for item in labels}, key=len, reverse=True):
        if label:
            remainder = remainder.replace(label, "")
    if not re.sub(r"[0-9]+", "", remainder):
        raise ContractReviewError("仅角度元数据或标签不是可见证据")


def _checks(value: Any, names: tuple[str, ...], verdict: str, metadata: dict[str, Any] | None = None) -> None:
    if not isinstance(value, dict) or set(value) != set(names):
        raise ContractReviewError("报告必须覆盖全部逐项检查，不能只有总判定")
    for item in value.values():
        _evidence(item, metadata)
        if item.get("verdict") not in VERDICTS or verdict == "pass" and item["verdict"] != "pass":
            raise ContractReviewError("失败或不确定检查不能登记为通过")


def _findings(value: Any, verdict: str) -> None:
    if not isinstance(value, list) or any(not isinstance(row, dict) or any(not isinstance(row.get(key), str) or not row[key].strip() for key in ("code", "evidence", "fix")) for row in value):
        raise ContractReviewError("findings 必须逐项包含 code/evidence/fix")
    if (verdict == "pass" and value) or (verdict != "pass" and not value):
        raise ContractReviewError("通过须无缺陷，失败或不确定须说明具体问题")


def _views(value: Any) -> None:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value) or len(set(value)) != len(value):
        raise ContractReviewError("observed_views 必须是独立观察的非空视角列表")


def _validate_report(report: dict[str, Any], request: dict[str, Any], producer: str) -> None:
    if report.get("schema_version") != SCHEMA_VERSION or report.get("request_id") != request["request_id"] or report.get("scope_signature") != request["scope_signature"]:
        raise ContractReviewError("报告没有绑定当前请求与合同范围")
    reviewer = report.get("reviewer")
    if not isinstance(reviewer, dict) or not isinstance(reviewer.get("id"), str) or not reviewer["id"].strip() or reviewer["id"].strip() == producer.strip():
        raise ContractReviewError("必须声明独立审查者，制图者不能自批")
    if reviewer.get("independence") != "zero-context" or reviewer.get("viewed_images") is not True:
        raise ContractReviewError("审查者必须声明隔离上下文且实际看图")
    if report.get("verdict") not in VERDICTS:
        raise ContractReviewError("报告判定无效")
    expected = request["scope"]["images"]
    results = report.get("results")
    if not isinstance(results, list) or len(results) != len(expected):
        raise ContractReviewError("报告必须逐图覆盖请求，不能遗漏或重复")
    by_slot = {}
    for row in results:
        if not isinstance(row, dict) or row.get("slot_id") in by_slot:
            raise ContractReviewError("逐图结果无效或重复")
        by_slot[row.get("slot_id")] = row
    for image in expected:
        row = by_slot.get(image["slot_id"])
        if not row or row.get("path") != image["path"] or row.get("sha256") != image["sha256"] or row.get("verdict") not in VERDICTS:
            raise ContractReviewError("逐图结果的图位、原图摘要或判定不符")
        _checks(row.get("checks"), CHECKS, row["verdict"], image["slot"])
        _findings(row.get("findings"), row["verdict"])
        _views(row.get("observed_views"))
    sequence = report.get("sequence")
    if not isinstance(sequence, dict) or sequence.get("verdict") not in VERDICTS:
        raise ContractReviewError("缺少整套覆盖验收")
    _checks(sequence.get("checks"), SEQUENCE_CHECKS, sequence["verdict"])
    _findings(sequence.get("findings"), sequence["verdict"])
    if sequence.get("actual_order") != request["scope"]["ordered_slots"]:
        raise ContractReviewError("整套验收未覆盖实际输出顺序")
    observations = sequence.get("observed_views")
    if not isinstance(observations, list) or len(observations) != len(expected):
        raise ContractReviewError("整套验收必须逐图记录实际观察角度")
    observed_ids = set()
    for observed in observations:
        if not isinstance(observed, dict) or observed.get("slot_id") not in by_slot or observed["slot_id"] in observed_ids:
            raise ContractReviewError("整套观察范围无效或重复")
        row = by_slot[observed["slot_id"]]
        if observed.get("sha256") != row["sha256"] or observed.get("views") != row["observed_views"]:
            raise ContractReviewError("整套角度观察与逐图结果不符")
        _evidence(observed, next(image["slot"] for image in expected if image["slot_id"] == observed["slot_id"]))
        observed_ids.add(observed["slot_id"])
    preserves = sequence.get("preserve_images")
    required_preserves = request["scope"]["collection"]["preserve_images"]
    if not isinstance(preserves, list) or len(preserves) != len(required_preserves):
        raise ContractReviewError("必须逐项验收保持图")
    seen_preserves = set()
    for required in required_preserves:
        matches = [row for row in preserves if isinstance(row, dict) and row.get("slot_id") == required["slot_id"] and row.get("sha256") == required["sha256"]]
        if len(matches) != 1 or required["slot_id"] in seen_preserves:
            raise ContractReviewError("保持图范围不一致")
        preserved = matches[0]
        _evidence(preserved)
        if preserved.get("verdict") not in VERDICTS or sequence["verdict"] == "pass" and preserved["verdict"] != "pass":
            raise ContractReviewError("保持图未通过，不能整套通过")
        seen_preserves.add(required["slot_id"])
    if report["verdict"] == "pass" and (sequence["verdict"] != "pass" or any(row["verdict"] != "pass" for row in results)):
        raise ContractReviewError("逐图或整套未通过，不能总通过")
    if report["verdict"] != "pass" and sequence["verdict"] == "pass" and all(row["verdict"] == "pass" for row in results):
        raise ContractReviewError("总判定与逐项判定矛盾")


def _key(image: dict[str, Any]) -> str:
    return _signature({name: image[name] for name in ("slot_id", "sha256", "source_sha256", "applicable_signature")})


def _request(root: Path, request_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    if not isinstance(request_id, str) or not re.fullmatch(r"contract-\d+-[0-9a-f]{12}", request_id):
        raise ContractReviewError("无效合同审查 request_id")
    directory = _local(root, "reviews/contract/" + request_id, exists=False)
    binding = _read(directory / "binding.json")
    request_file = directory / "packet/request.json"
    if _hash(request_file) != binding.get("request_sha256"):
        raise ContractReviewError("合同审查请求摘要已变化")
    request = _read(request_file)
    if request.get("request_id") != request_id or request.get("schema_version") != SCHEMA_VERSION or request.get("scope_signature") != _signature(request.get("scope")):
        raise ContractReviewError("请求范围摘要无效")
    for image in [*(request.get("images") or []), *(request.get("reference_images") or [])]:
        preview = _local(root, (directory / "packet" / image["image"]).relative_to(root).as_posix())
        if _hash(preview) != image.get("preview_sha256"):
            raise ContractReviewError("干净查看副本已篡改")
    return directory, request, binding


def record(root: Path, project: dict[str, Any], request_id: str, report_file: Path) -> dict[str, Any]:
    import project as state

    root = Path(root).resolve()
    with state.project_lock(root):
        current = state.load_project(root, writable=True)
        directory, request, binding = _request(root, request_id)
        stage = "package" if request["scope"]["stage"] == "publication" else "scene"
        if not _active(current, stage) or current.get("project_id") != request.get("project_id"):
            raise ContractReviewError("请求不适用于当前项目或合同未启用")
        scope = request["scope"]
        mappings = [{key: image[key] for key in ("slot_id", "path", "source", "source_sha256")} for image in scope["images"]]
        actual = _scope(root, current, [image["path"] for image in scope["images"]], stage, mappings)
        if actual != scope:
            raise ContractReviewError("请求后原文件、适用合同、参考、图位或顺序已变化")
        report = _read(Path(report_file))
        _validate_report(report, request, binding["producer"])
        keys = {_key(image) for image in scope["images"]}
        for previous in (current.get("contract_reviews") or {}).values():
            if previous["generation"] <= binding["generation"]:
                continue
            previous_scope = previous["scope"]
            if keys.intersection(_key(image) for image in previous_scope["images"]) or previous_scope["collection_signature"] == scope["collection_signature"]:
                raise ContractReviewError("已有较新判定，不能重放旧报告")
        fingerprint = _signature(report)
        sealed_file = directory / "recorded.json"
        output = directory / "report.json"
        if sealed_file.is_file():
            seal = _read(sealed_file)
            if seal.get("fingerprint") != fingerprint or not output.is_file() or _hash(output) != seal.get("report_sha256"):
                raise ContractReviewError("请求已封存或已登记报告被篡改，不允许改判重放")
            if request_id not in (current.get("contract_reviews") or {}):
                raise ContractReviewError("封存报告缺少项目登记，保留证据重新送审")
            return {"ok": True, "request_id": request_id, "unchanged": True, "verdict": report["verdict"], "revision": current["revision"]}
        encoded = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        sha = hashlib.sha256(encoded).hexdigest()
        state.write_json(output, report)
        state.write_json(sealed_file, {"fingerprint": fingerprint, "report_sha256": sha})
        current.setdefault("contract_reviews", {})[request_id] = {
            "scope": scope, "generation": binding["generation"], "report": output.relative_to(root).as_posix(),
            "report_sha256": sha, "request_sha256": binding["request_sha256"], "binding_sha256": _hash(directory / "binding.json"),
        }
        state.save_project(root, current)
        project.clear()
        project.update(copy.deepcopy(current))
        return {"ok": True, "request_id": request_id, "unchanged": False, "verdict": report["verdict"],
                "report": str(output), "revision": current["revision"]}


def _loaded_record(root: Path, project: dict[str, Any], request_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    directory, request, binding = _request(root, request_id)
    if entry.get("binding_sha256") != _hash(directory / "binding.json") or entry.get("request_sha256") != binding["request_sha256"] or entry.get("scope") != request["scope"]:
        raise ContractReviewError("已登记请求或合同范围不完整")
    if request.get("project_id") != project.get("project_id"):
        raise ContractReviewError("报告属于另一个项目")
    report_path = _local(root, entry["report"])
    if _hash(report_path) != entry.get("report_sha256"):
        raise ContractReviewError("已登记报告字节变化")
    report = _read(report_path)
    seal = _read(directory / "recorded.json")
    if seal.get("fingerprint") != _signature(report) or seal.get("report_sha256") != entry["report_sha256"]:
        raise ContractReviewError("已登记报告封存不一致")
    _validate_report(report, request, binding["producer"])
    return report


def status(root: Path, project: dict[str, Any], paths: list[str], stage: str = "scene", ordered_slots: list[Any] | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    if not _active(project, stage):
        return {"ok": True, "applicable": False, "blocking": False, "verdict": "not_applicable", "paths": [], "sequence": {"state": "not_applicable"}}
    try:
        scope = _scope(root, project, paths, stage, ordered_slots)
    except (ContractReviewError, OSError) as error:
        return {"ok": True, "applicable": True, "blocking": True, "verdict": "blocked", "paths": [], "sequence": {"state": "blocked", "reason": str(error)}}
    evidence = []
    for request_id, entry in (project.get("contract_reviews") or {}).items():
        try:
            report = _loaded_record(root, project, request_id, entry)
            evidence.append((entry, report, request_id, None))
        except (ContractReviewError, OSError, KeyError, TypeError, ValueError) as error:
            evidence.append((entry, None, request_id, str(error)))
    evidence.sort(key=lambda item: item[0].get("generation", 0), reverse=True)
    rows = []
    for image in scope["images"]:
        result = {"path": image["path"], "slot_id": image["slot_id"], "sha256": image["sha256"],
                  "state": "needs_review", "verdict": "not_assessed", "reason": "缺少当前原图及适用合同的独立 G1 报告"}
        for entry, report, request_id, error in evidence:
            if not any(_key(previous) == _key(image) for previous in entry.get("scope", {}).get("images", [])):
                continue
            result["request_id"] = request_id
            if error:
                result.update(state="blocked", verdict="invalid", reason=error)
            else:
                reviewed = next(row for row in report["results"] if row["slot_id"] == image["slot_id"])
                result.update(state="reused" if reviewed["verdict"] == "pass" else "blocked", verdict=reviewed["verdict"], checks=reviewed["checks"], reviewer_id=report["reviewer"]["id"], reason="当前原图和合同范围已绑定独立报告")
            break
        if result["state"] == "reused":
            import physics_review
            physics = physics_review.status(root, project, image["path"])
            if physics.get("reviewer_id") == result.get("reviewer_id"):
                result.update(state="blocked", verdict="invalid", reason="G1 与 G2 必须由不同新上下文审查者完成")
        rows.append(result)
    collection = {"state": "needs_review", "verdict": "not_assessed", "reason": "整套覆盖或实际顺序尚未独立验收"}
    for entry, report, request_id, error in evidence:
        if entry.get("scope", {}).get("collection_signature") != scope["collection_signature"]:
            continue
        if error:
            collection.update(state="blocked", verdict="invalid", reason=error, request_id=request_id)
        else:
            reviewed = report["sequence"]
            collection.update(state="reused" if reviewed["verdict"] == "pass" else "blocked", verdict=reviewed["verdict"], request_id=request_id, checks=reviewed["checks"], reason="整套覆盖与实际排列已绑定独立报告")
        break
    blocking = any(row["state"] != "reused" for row in rows) or collection["state"] != "reused"
    return {"ok": True, "applicable": True, "blocking": blocking, "verdict": "blocked" if blocking else "pass",
            "paths": rows, "sequence": collection, "scope": scope,
            "claim_boundary": "程序仅验证格式、摘要、适用范围和独立看图声明，不证明实际独立、视角或厘米测量正确"}


def require(root: Path, project: dict[str, Any], paths: list[str], stage: str = "scene", ordered_slots: list[Any] | None = None) -> dict[str, Any]:
    result = status(root, project, paths, stage, ordered_slots)
    if result["blocking"]:
        raise ContractReviewError("G1 合同视觉审查未通过：" + json.dumps({"paths": result["paths"], "sequence": result["sequence"]}, ensure_ascii=False))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="独立中性合同 G1 审查；不执行生图，不改变 G2")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "record", "status"):
        command = commands.add_parser(name)
        command.add_argument("project")
        if name == "record":
            command.add_argument("--request", required=True)
            command.add_argument("--report", required=True)
        else:
            command.add_argument("--images", nargs="+", required=True)
            command.add_argument("--stage", choices=STAGES, default="scene")
            command.add_argument("--ordered-slots", help="按实际输出顺序的 JSON 映射列表文件")
            if name == "prepare":
                command.add_argument("--producer", required=True)
    args = parser.parse_args(argv)
    import project as state
    from runtime_activity import execution_lease

    try:
        root = Path(args.project).resolve()
        with execution_lease(), state.project_lock(root):
            project = state.load_project(root, writable=args.command != "status")
            if args.command == "record":
                result = record(root, project, args.request, Path(args.report))
            else:
                mappings = json.loads(Path(args.ordered_slots).read_text(encoding="utf-8")) if args.ordered_slots else None
                if args.command == "prepare":
                    result = prepare(root, project, args.images, args.producer, args.stage, mappings)
                else:
                    result = status(root, project, args.images, args.stage, mappings)
        return state.emit(result)
    except (ContractReviewError, state.ProjectError, OSError, ValueError, KeyError, TypeError) as error:
        return state.emit({"ok": False, "error": str(error)})


if __name__ == "__main__":
    sys.exit(main())
