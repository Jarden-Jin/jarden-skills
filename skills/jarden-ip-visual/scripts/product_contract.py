"""Product design targets and view provenance, independent of generation tools."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
ORIGINS = ("user-confirmed", "agent-inferred", "reference-derived")
VIEWS = ("front", "three-quarter", "left", "right", "back", "detail")
STATUSES = ("unknown", "proposed", "confirmed")


class ContractError(ValueError):
    pass


def _hash(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ContractError("产品约定必须是有限数值组成的 JSON") from error
    return hashlib.sha256(encoded.encode()).hexdigest()


def _positive(value: Any, field: str) -> float:
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ContractError(f"{field} 必须是有限正数，不能是布尔值")
    return value


def _source(value: dict[str, Any]) -> None:
    if value.get("origin", "agent-inferred") not in ORIGINS:
        raise ContractError("产品约定来源无效")
    if value.get("status", "unknown") not in STATUSES:
        raise ContractError("确认状态只能是 unknown、proposed 或 confirmed")


def normalized_size(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("size 必须是对象")
    result = copy.deepcopy(value)
    mode = result.setdefault("mode", "unknown")
    if mode not in ("unknown", "numeric", "range", "qualitative"):
        raise ContractError("未知尺寸模式")
    result.setdefault("status", "unknown")
    result.setdefault("origin", "agent-inferred")
    result.setdefault("evidence_kind", "design_target")
    _source(result)
    if result["evidence_kind"] not in ("design_target", "user_specification", "measured_sample"):
        raise ContractError("尺寸必须区分设计目标、提供规格和样品测量")
    if result["evidence_kind"] == "measured_sample":
        evidence = result.get("evidence")
        if not evidence or isinstance(evidence, str) and not evidence.strip():
            raise ContractError("样品测量必须有依据，不能把设计目标当实测")
    if mode == "unknown":
        if result["status"] == "confirmed":
            raise ContractError("未知尺寸不能标为已确认")
        return result
    if not isinstance(result.get("measurement_basis"), str) or not result["measurement_basis"].strip():
        raise ContractError("需要尺寸量取口径，例如本体站高、不含挂扣")
    if mode == "qualitative":
        if not isinstance(result.get("description"), str) or len(result["description"].strip()) < 4:
            raise ContractError("定性尺度需要可观察的完整描述")
        return result
    factors = {"mm": 1, "cm": 10, "m": 1000}
    unit = result.get("unit")
    if unit not in factors:
        raise ContractError("长度单位只能是 mm、cm 或 m")
    dimensions = {}
    for field in ("height", "width", "depth", "total_length"):
        if field not in result or result[field] is None:
            continue
        if mode == "numeric":
            dimensions[field + "_mm"] = _positive(result[field], field) * factors[unit]
        else:
            bounds = result[field]
            if not isinstance(bounds, list) or len(bounds) != 2:
                raise ContractError("范围尺寸必须是两个值的列表")
            low = _positive(bounds[0], field)
            high = _positive(bounds[1], field)
            if low > high:
                raise ContractError("尺寸范围下限不能大于上限")
            dimensions[field + "_mm"] = [low * factors[unit], high * factors[unit]]
    if not dimensions:
        raise ContractError("至少给出一个有依据的尺寸维度")
    result["normalized_mm"] = dimensions
    _hash(result)
    return result


def size_from_text(value: str) -> dict[str, Any]:
    text = value.strip()
    if not text:
        raise ContractError("请填写设计尺度")
    number_pattern = r"\d+(?:\.\d+)?"
    unit_pattern = r"mm|cm|毫米|厘米"
    numbers = re.findall(number_pattern, text)
    measurement = re.search(rf"({number_pattern})\s*({unit_pattern})", text, re.I)
    if not measurement:
        return {"mode": "qualitative", "description": text, "measurement_basis": "认可的整体相对尺度设计目标"}
    if not any(marker in text for marker in ("本体", "含", "整体", "站高")):
        raise ContractError("请注明量取口径，例如本体12cm、不含扣件")
    def dimension(prefix: str) -> str:
        return "width" if re.search(r"宽(?:度)?\s*$", prefix) else "depth" if re.search(r"(深(?:度)?|厚(?:度)?)\s*$", prefix) else "total_length" if re.search(r"(总长|全长)\s*$", prefix) else "height"

    ranged = re.search(rf"({number_pattern})\s*(?:({unit_pattern})\s*)?(?:-|–|—|~|～|至|到)\s*({number_pattern})\s*({unit_pattern})", text, re.I)
    if ranged and len(numbers) == 2:
        initial_unit = ranged.group(2)
        final_unit = ranged.group(4)
        millimetres = lambda unit: unit.lower() in ("mm", "毫米")
        if initial_unit and millimetres(initial_unit) != millimetres(final_unit):
            return {"mode": "qualitative", "description": text, "measurement_basis": text}
        unit = "mm" if millimetres(final_unit) else "cm"
        return {"mode": "range", dimension(text[:ranged.start()]): [float(ranged.group(1)), float(ranged.group(3))],
                "unit": unit, "measurement_basis": text}
    if re.search(rf"(?:^|[^\d])[-−]\s*{number_pattern}\s*(?:{unit_pattern})", text, re.I):
        raise ContractError("设计尺度必须为正数")
    if len(numbers) == 1:
        prefix = text[:measurement.start()]
        field = dimension(prefix)
        unit = "mm" if measurement.group(2).lower() in ("mm", "毫米") else "cm"
        return {"mode": "numeric", field: float(measurement.group(1)), "unit": unit, "measurement_basis": text}
    return {"mode": "qualitative", "description": text, "measurement_basis": text}


def initialize(project: dict[str, Any], enabled: bool = True) -> dict[str, Any]:
    contract = project.setdefault("product_contract", {
        "schema_version": SCHEMA_VERSION, "enabled": enabled,
        "subjects": {}, "specs": {}, "view_assets": {}, "requirements": {},
    })
    if contract.get("schema_version") != SCHEMA_VERSION:
        raise ContractError("不支持的产品约定版本")
    if enabled:
        contract["enabled"] = True
    sync_subjects(project)
    return contract


def sync_subjects(project: dict[str, Any]) -> dict[str, str]:
    contract = project.get("product_contract")
    if not isinstance(contract, dict):
        return {}
    subjects = contract.setdefault("subjects", {})
    specs = contract.setdefault("specs", {})
    if not isinstance(subjects, dict) or not isinstance(specs, dict):
        raise ContractError("subjects 和 specs 必须是稳定 ID 映射")
    names = list((project.get("answers") or {}).get("subjects") or [])
    count = (project.get("answers") or {}).get("concept_count")
    if type(count) is int:
        counter = len(names) + 1
        while len(names) < count:
            name = f"角色{counter}"
            counter += 1
            if name not in names:
                names.append(name)
    active = {}
    for name in names:
        identifier = next((key for key, item in subjects.items() if item.get("name") == name), None)
        if identifier is None:
            identifier = "subject-" + hashlib.sha256(name.encode()).hexdigest()[:12]
            subjects[identifier] = {"name": name, "active": True}
        subjects[identifier]["active"] = True
        active[name] = identifier
        spec_id = "spec-" + identifier.removeprefix("subject-")
        specs.setdefault(spec_id, {
            "subject_id": identifier,
            "usage": {"text": "", "status": "unknown", "origin": "agent-inferred"},
            "size": {"mode": "unknown", "status": "unknown", "origin": "agent-inferred", "evidence_kind": "design_target"},
            "structure": {},
        })
    for identifier, subject in subjects.items():
        subject["active"] = identifier in active.values()
    return active


def subject_id(project: dict[str, Any], subject: str) -> str:
    subjects = (project.get("product_contract") or {}).get("subjects") or {}
    if subject in subjects and subjects[subject].get("active", True):
        return subject
    identifier = next((key for key, item in subjects.items() if item.get("name") == subject and item.get("active", True)), None)
    if identifier is None:
        raise ContractError("产品约定引用未知主体：" + subject)
    return identifier


def spec_for(project: dict[str, Any], subject: str, spec_id: str | None = None) -> tuple[str, dict[str, Any]]:
    identifier = subject_id(project, subject)
    specs = project["product_contract"]["specs"]
    if spec_id is not None:
        spec = specs.get(spec_id)
        if not spec or spec.get("subject_id") != identifier:
            raise ContractError("款式与主体不匹配")
        return spec_id, spec
    selected = next(((key, value) for key, value in specs.items() if value.get("subject_id") == identifier), None)
    if selected is None:
        raise ContractError("主体没有产品款式")
    return selected


def validate_contract(contract: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(contract, dict) or type(contract.get("schema_version")) is not int or contract["schema_version"] != SCHEMA_VERSION:
        raise ContractError("产品约定版本无效")
    if contract.get("enabled") is not True:
        raise ContractError("当前项目必须启用产品约定")
    for field in ("subjects", "specs", "view_assets", "requirements"):
        if not isinstance(contract.get(field), dict):
            raise ContractError(field + " 必须是对象")
    result = copy.deepcopy(contract)
    for identifier, spec in result["specs"].items():
        if not isinstance(spec, dict) or spec.get("subject_id") not in result["subjects"]:
            raise ContractError("款式引用未知主体：" + identifier)
        usage = spec.get("usage")
        if not isinstance(usage, dict) or not isinstance(usage.get("text", ""), str):
            raise ContractError("用途需要保留自由输入原文")
        _source(usage)
        if usage.get("status") == "confirmed" and not usage.get("text", "").strip():
            raise ContractError("空用途不能标已确认")
        spec["size"] = normalized_size(spec.get("size", {}))
        if not isinstance(spec.get("structure", {}), dict):
            raise ContractError("结构约定必须是对象")
    for asset in result["view_assets"].values():
        if not isinstance(asset, dict) or asset.get("subject_id") not in result["subjects"] or asset.get("spec_id") not in result["specs"]:
            raise ContractError("视图引用未知主体或款式")
        if asset.get("view") not in VIEWS or asset.get("origin") not in ORIGINS:
            raise ContractError("视图方向或来源无效")
        if result["specs"][asset["spec_id"]].get("subject_id") != asset["subject_id"]:
            raise ContractError("视图主体与产品款式不匹配")
        if re.fullmatch(r"[0-9a-f]{64}", str(asset.get("sha256", ""))) is None:
            raise ContractError("视图缺少原图片摘要")
    _hash(result)
    return result


def set_spec(project: dict[str, Any], subject: str, values: dict[str, Any], spec_id: str | None = None) -> dict[str, Any]:
    working = copy.deepcopy(project)
    initialize(working)
    if not isinstance(values, dict) or set(values) - {"usage", "size", "structure", "name"}:
        raise ContractError("产品更新只能修改用途、尺寸、结构和款式名称")
    if spec_id is not None and spec_id not in working["product_contract"]["specs"]:
        if not isinstance(spec_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", spec_id) is None:
            raise ContractError("新款式 ID 必须是稳定的字母、数字、点、短线或下划线")
        identifier = subject_id(working, subject)
        selected_id = spec_id
        current = {"subject_id": identifier,
                   "usage": {"text": "", "status": "unknown", "origin": "agent-inferred"},
                   "size": {"mode": "unknown", "status": "unknown", "origin": "agent-inferred", "evidence_kind": "design_target"},
                   "structure": {}}
    else:
        selected_id, current = spec_for(working, subject, spec_id)
    candidate = {**copy.deepcopy(current), **copy.deepcopy(values)}
    contract = copy.deepcopy(working["product_contract"])
    contract["specs"][selected_id] = candidate
    project["product_contract"] = validate_contract(contract)
    return {"spec_id": selected_id, "subject_id": candidate["subject_id"], "signature": signature(project, subject)}


def projection(project: dict[str, Any], subject: str | None = None) -> dict[str, Any]:
    contract = copy.deepcopy(project.get("product_contract") or {})
    if subject is None or not contract:
        return contract
    identifier = subject_id(project, subject)
    contract["subjects"] = {identifier: contract["subjects"][identifier]}
    contract["specs"] = {key: value for key, value in contract["specs"].items() if value.get("subject_id") == identifier}
    contract["view_assets"] = {key: value for key, value in contract["view_assets"].items() if value.get("subject_id") == identifier}
    return contract


def signature(project: dict[str, Any], subject: str | None = None, domains: list[str] | None = None) -> str:
    contract = projection(project, subject)
    if domains is not None:
        contract = {key: value for key, value in contract.items() if key in domains}
    return _hash(contract)


def missing_requirements(project: dict[str, Any], stage: str, subject: str | None = None) -> list[dict[str, str]]:
    if project.get("schema_version", 3) < 3:
        return []
    contract = project.get("product_contract")
    if not isinstance(contract, dict) or contract.get("enabled") is not True:
        return [{"field": "product_contract", "reason": "缺少当前产品约定"}]
    validate_contract(contract)
    names = [subject] if subject else [item["name"] for item in contract["subjects"].values() if item.get("active", True)]
    missing = []
    for name in names:
        selected_id, spec = spec_for(project, name)
        if spec["usage"].get("status") != "confirmed" or not spec["usage"].get("text", "").strip():
            missing.append({"field": "usage", "subject": name, "spec_id": selected_id, "reason": "请说明主要怎么用，可自由填写双用途"})
        if stage in ("scene", "package", "done"):
            size = spec["size"]
            if size.get("mode") == "unknown" or size.get("status") != "confirmed":
                missing.append({"field": "size", "subject": name, "spec_id": selected_id, "reason": "场景前认可设计数值、范围或可观察定性尺度；不是实物测量"})
    return missing


def record_view(root: Path, project: dict[str, Any], subject: str, view: str, path: str,
                origin: str, spec_id: str | None = None) -> dict[str, Any]:
    working = copy.deepcopy(project)
    initialize(working)
    if view not in VIEWS or origin not in ORIGINS:
        raise ContractError("视图或来源无效")
    directory = root.resolve()
    source = (directory / path).resolve()
    if Path(path).is_absolute() or not source.is_relative_to(directory) or not source.is_file():
        raise ContractError("视图必须是项目内实际图片")
    from PIL import Image
    with Image.open(source) as image:
        image.verify()
    selected_id, spec = spec_for(working, subject, spec_id)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    identifier = "view-" + _hash({"subject": spec["subject_id"], "spec": selected_id, "view": view, "sha256": digest})[:20]
    asset = {"id": identifier, "subject_id": spec["subject_id"], "spec_id": selected_id,
             "view": view, "path": source.relative_to(directory).as_posix(), "sha256": digest,
             "origin": origin, "confirmation": {"status": "proposed"}, "spec_signature": _hash(spec)}
    working["product_contract"]["view_assets"][identifier] = asset
    project["product_contract"] = validate_contract(working["product_contract"])
    return project["product_contract"]["view_assets"][identifier]


def confirm_view(root: Path, project: dict[str, Any], asset_id: str, origin: str,
                 evidence: str) -> dict[str, Any]:
    validate_contract(project.get("product_contract"))
    asset = (project.get("product_contract") or {}).get("view_assets", {}).get(asset_id)
    if not asset or origin not in ORIGINS or not evidence.strip():
        raise ContractError("确认需要已登记视图、真实来源与依据")
    source = (root.resolve() / asset["path"]).resolve()
    if not source.is_relative_to(root.resolve()) or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != asset["sha256"]:
        raise ContractError("视图在确认前已变化")
    spec = project["product_contract"]["specs"][asset["spec_id"]]
    if _hash(spec) != asset["spec_signature"]:
        raise ContractError("视图对应产品约定已变化，请重新登记")
    asset["confirmation"] = {"status": "confirmed", "origin": origin, "evidence": evidence}
    return asset


def view_assets(project: dict[str, Any], subject: str, view: str | None = None) -> list[dict[str, Any]]:
    identifier = subject_id(project, subject)
    specs = project["product_contract"]["specs"]
    return [copy.deepcopy(asset) for asset in project["product_contract"]["view_assets"].values()
            if asset.get("subject_id") == identifier and (view is None or asset.get("view") == view)
            and asset.get("confirmation", {}).get("status") == "confirmed"
            and asset.get("spec_signature") == _hash(specs[asset["spec_id"]])]
