#!/usr/bin/env python3
"""分类审查的独立报告、范围失效与阻断汇总。

物理审查继续由 physics_review.py 负责；本模块只读取它的状态，不修改
匿名盲审包、原图或项目选择。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any

import physics_review

CATEGORIES = ("files", "identity", "creative", "physics", "sequence", "copy", "ux")
INDEPENDENT = {"identity", "creative", "sequence", "copy", "ux"}
VERDICTS = ("pass", "needs_revision", "uncertain")
ORIGINS = ("user-confirmed", "agent-inferred", "reference-derived")
STAGE_CATEGORIES = {
    "concept": ("identity", "creative"),
    "material": ("identity", "creative"),
    "cutout": ("identity", "creative"),
    "scene": ("identity", "creative", "physics"),
    "package": ("identity", "creative", "physics", "sequence", "copy"),
    "done": ("identity", "creative", "physics", "sequence", "copy"),
}
REQUIRED_BY_CATEGORY = {
    "files": "program",
    "identity": "concept, material, cutout and scene",
    "creative": "concept and material first-round representative",
    "physics": "scene and final package",
    "sequence": "package",
    "copy": "package",
    "ux": "authoring and release regression",
}
CREATIVE_CRITERIA = (
    "distinct_directions",
    "material_credibility",
    "purpose_fit",
    "identity_continuity",
    "propagation",
)
PROPAGATION_CRITERIA = (
    "cover_recognition",
    "meaningful_difference",
    "life_scene_fit",
    "information_gain",
    "verifiable_copy",
    "relevant_tags",
)
SEQUENCE_CRITERIA = PROPAGATION_CRITERIA[:4]
COPY_CRITERIA = (*PROPAGATION_CRITERIA[4:], "copy_image_match")
UX_CRITERIA = ("selectable", "comparison", "disabled_reason", "recovery", "mobile_keyboard")
COPY_CLAIM_PATTERN = re.compile(r"\d|价格|尺寸|厘米|cm|mm|元|发售|上市|授权|打样|现货|包邮|销量")
FINDING_FIELDS = ("code", "severity", "detail", "evidence", "fix")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MIN_EVIDENCE = 8
SENSITIVE_KEYS = {
    "preference", "preferences", "favorite", "favourite", "winner", "selected",
    "selection", "conclusion", "prompt", "reason", "rationale", "must_pass",
    "作者", "偏好", "结论", "提示词", "必须通过", "喜欢", "选中",
}


class ReviewError(Exception):
    """可以直接展示给 Agent 的审查错误。"""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ReviewError(f"没有找到 {path.as_posix()}") from error
    except (OSError, ValueError) as error:
        raise ReviewError(f"无法读取审查 JSON：{path.as_posix()}") from error
    if not isinstance(value, dict):
        raise ReviewError(f"{path.as_posix()} 的顶层必须是对象")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".tmp-{secrets.token_hex(6)}-{path.name}")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_signature(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_file_hash(value: dict[str, Any]) -> str:
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _stored_json(path: Path) -> dict[str, Any] | None:
    import project as state

    try:
        return state.read_json(path)
    except state.ProjectError as error:
        if isinstance(error.__cause__, FileNotFoundError):
            return None
        raise ReviewError(str(error)) from error


def _stored_hash(path: Path, value: dict[str, Any]) -> str:
    return _file_hash(path) if path.is_file() else _json_file_hash(value)


def _project_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ReviewError(f"路径不在项目内：{relative}")
    return path


def _relative_path(root: Path, path: Path) -> str:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ReviewError(f"路径不在项目内：{path}")
    return resolved.relative_to(root.resolve()).as_posix()


def _copy(root: Path, copy_path: Path | None = None) -> tuple[dict[str, Any] | None, str | None]:
    path = copy_path if copy_path is not None else root / "copy.json"
    if not path.is_file():
        return None, None
    try:
        value = _read_json(path)
    except ReviewError:
        return None, None
    return value, _file_hash(path)


def _decisions(project: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    values = (project.get("brief") or {}).get("decisions") or []
    if not isinstance(values, list):
        return [], _json_signature([])
    normalized = []
    for value in values:
        if not isinstance(value, dict):
            continue
        origin = value.get("origin")
        if origin not in ORIGINS or not isinstance(value.get("rule"), str) or not value["rule"].strip():
            continue
        normalized.append({
            "id": value.get("id"),
            "rule": value["rule"],
            "origin": origin,
            "scope": value.get("scope") or {},
            "evidence": value.get("evidence") or {},
        })
    return normalized, _json_signature(normalized)


def _template_files(root: Path) -> list[Path]:
    skill = Path(__file__).resolve().parents[1]
    return [
        root / "assets" / name if (root / "assets" / name).is_file() else skill / "assets" / name
        for name in ("gallery.html", "gallery.css", "gallery.js")
    ]


def template_signature(root: Path) -> str:
    files = _template_files(root)
    if not files[0].is_file():
        raise ReviewError("没有找到页面模板或审查路由，无法绑定 UX 报告")
    return _json_signature({path.name: _file_hash(path) if path.is_file() else None for path in files})


def _all_images(root: Path, project: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for directory in ("inputs", "rounds"):
        base = root / directory
        if base.is_dir():
            found.extend(
                path.relative_to(root).as_posix()
                for path in base.rglob("*")
                if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
            )
    for final in project.get("finals") or []:
        if isinstance(final, dict) and isinstance(final.get("path"), str) and (root / final["path"]).is_file():
            found.append(Path(final["path"]).as_posix())
    return sorted(dict.fromkeys(found))


def _final_images(root: Path, project: dict[str, Any]) -> list[str]:
    return sorted(
        dict.fromkeys(
            Path(final["path"]).as_posix()
            for final in project.get("finals") or []
            if isinstance(final, dict) and isinstance(final.get("path"), str)
        )
    )


def _default_paths(root: Path, project: dict[str, Any], category: str) -> list[str]:
    if category in {"sequence", "copy"}:
        return _final_images(root, project)
    if category == "ux":
        return []
    return _all_images(root, project)


def _reference_images(root: Path, project: dict[str, Any]) -> list[dict[str, str]]:
    references = []
    for item in project.get("inputs") or []:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            references.append({"path": item["path"], "origin": "reference-derived"})
    for stage in ("concept", "material", "cutout"):
        for item in ((project.get("selections") or {}).get(stage) or {}).values():
            if isinstance(item, dict) and isinstance(item.get("image"), str):
                origin = item.get("origin")
                references.append({"path": item["image"], "origin": origin if origin in ORIGINS else "reference-derived", "purpose": stage})
    for item in references:
        path = _project_file(root, item["path"])
        if not path.is_file():
            raise ReviewError(f"身份参考不存在：{item['path']}")
        item["sha256"] = _file_hash(path)
    return references


def _normalize_paths(root: Path, project: dict[str, Any], category: str, paths: list[str] | None) -> list[dict[str, str]]:
    values = _default_paths(root, project, category) if paths is None else paths
    if category == "ux":
        return []
    result = []
    seen = set()
    for relative in values:
        if not isinstance(relative, str) or not relative.strip():
            raise ReviewError("paths 必须是非空项目内路径")
        path = _project_file(root, relative)
        if not path.is_file():
            raise ReviewError(f"审查图片不存在：{relative}")
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            raise ReviewError(f"不是支持的图片格式：{relative}")
        normalized = _relative_path(root, path)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append({"path": normalized, "sha256": _file_hash(path)})
    return result


def _neutral(value: Any) -> Any:
    if isinstance(value, str):
        if re.search(r"must.?pass|should pass|winner|author.{0,15}(prefer|choose)|必须通过|作者.{0,10}(喜欢|偏好|选择)|我希望它选|评分.{0,5}[89]", value, re.I):
            return None
        return value[:700]
    if isinstance(value, list):
        return [clean for item in value[:30] if (clean := _neutral(item)) is not None]
    if isinstance(value, dict):
        return {
            str(key): clean for key, item in list(value.items())[:40]
            if not any(marker in str(key).casefold() for marker in SENSITIVE_KEYS)
            and (clean := _neutral(item)) is not None
        }
    return value if isinstance(value, (int, float, bool, type(None))) else None


def _bible_reference(project: dict[str, Any]) -> Any:
    bible = (project.get("brief") or {}).get("bible")
    if not isinstance(bible, (dict, list)):
        return None
    return _neutral(bible)


def _creative_reference_packet(root: Path, project: dict[str, Any], paths: list[dict[str, str]], category: str, stage: str) -> dict[str, Any]:
    decisions, _ = _decisions(project)
    answers = project.get("answers") or {}
    references = []
    for index, item in enumerate(paths, 1):
        references.append({"ref": f"reference-{index:03d}", "sha256": item["sha256"]})
    return {
        "subject": answers.get("subjects") or [],
        "product_form": answers.get("product_form"),
        "note_type": answers.get("note_type"),
        "delivery": answers.get("delivery"),
        "confirmed_bible": _bible_reference(project),
        "decisions": [
            {"id": item["id"], "rule": rule, "origin": item["origin"], "scope": _neutral(item["scope"]), "evidence": _neutral(item["evidence"])}
            for item in decisions if (rule := _neutral(item["rule"])) is not None
        ],
        "images": references,
        "instruction_boundary": "confirmed reference data only; no author preference, prompt, prior approval or expected verdict",
        "stage": stage,
        "category": category,
    }


def _copy_scope(root: Path, project: dict[str, Any], copy_path: Path | None = None) -> dict[str, Any]:
    copy, copy_sha256 = _copy(root, copy_path)
    if copy is None or copy_sha256 is None:
        raise ReviewError("没有可审查的 copy.json")
    return {
        "copy": {key: copy[key] for key in ("title", "body", "tags", "ai_disclosure", "image_text", "overlays") if key in copy},
        "copy_sha256": copy_sha256,
        "note_type": (project.get("answers") or {}).get("note_type"),
        "subjects": (project.get("answers") or {}).get("subjects") or [],
    }


def _report_scope(root: Path, project: dict[str, Any], category: str, paths: list[dict[str, str]], stage: str, copy_path: Path | None = None) -> dict[str, Any]:
    _, decision_signature = _decisions(project)
    scope: dict[str, Any] = {
        "category": category,
        "paths": paths,
        "decision_signature": _json_signature({"decisions": decision_signature, "bible": _bible_reference(project)}) if category == "identity" else None,
        "template_version": template_signature(root) if category == "ux" else None,
    }
    if category == "copy":
        scope["copy"] = _copy_scope(root, project, copy_path)
    if category in {"identity", "creative"}:
        scope["reference_images"] = _reference_images(root, project)
    if category in {"creative", "sequence"}:
        scope["requirements"] = {
            "note_type": (project.get("answers") or {}).get("note_type"),
            "delivery": (project.get("answers") or {}).get("delivery"),
            "subjects": (project.get("answers") or {}).get("subjects") or [],
            "product_form": (project.get("answers") or {}).get("product_form"),
        }
    if category == "sequence":
        scope["sequence_signature"] = _json_signature([
            {key: item.get(key) for key in ("path", "slot", "role", "subject")}
            for item in project.get("finals") or [] if isinstance(item, dict)
        ])
    return scope


def _task(category: str) -> dict[str, Any]:
    requirements: dict[str, Any] = {
        "schema_version": 2,
        "review_id": "复制请求中的 review_id",
        "category": category,
        "scope": "逐字复制请求中的 scope",
        "image_hashes": "按顺序复制 scope.paths 中的 sha256 字符串；UX 为 []",
        "decision_signature": "复制请求中的 scope.decision_signature",
        "template_version": "复制请求中的 scope.template_version；非 UX 可为 null",
        "reviewer": {"id": "实际独立执行者标识", "independence": "zero-context", "viewed_images": True},
        "verdict": "pass | needs_revision | uncertain",
        "findings": [],
        "evidence": "可见证据摘要，不能只写通过",
        "paths": [],
    }
    if category in {"identity", "creative", "sequence", "copy"}:
        requirements["paths"] = [
            {"path": "相对项目路径", "sha256": "原图摘要", "verdict": "pass | needs_revision | uncertain", "evidence": "逐张可见证据"}
        ]
    if category == "identity":
        requirements["criteria_definitions"] = {
            "identity": "并排检查轮廓、五官、色区、配件、角色数量与系列规则；必须指出可见差异和对应确认依据。",
        }
    if category == "creative":
        requirements["criteria"] = {name: {"verdict": "pass | needs_revision | uncertain | not_applicable", "evidence": "可见依据"} for name in CREATIVE_CRITERIA}
        requirements["criteria_definitions"] = {
            "distinct_directions": "候选之间有轮廓、材质或场景策略的实质差异，不是只换色或背景。",
            "material_credibility": "材质、纤维、涂装、缝线和配件表达符合已确认品类。",
            "purpose_fit": "首轮代表方案符合当前图位和任务目的，不只是否讨喜。",
            "identity_continuity": "已确认身份、数量和系列规则没有被创作方向改坏。",
            "propagation": "只评估可观察的封面识别、差异、生活场景代入、信息增量、可验证文案与 tag；不得预测流量或销量。",
        }
    if category in {"sequence", "copy"}:
        names = SEQUENCE_CRITERIA if category == "sequence" else COPY_CRITERIA
        requirements["criteria"] = {name: {"verdict": "pass | needs_revision | uncertain | not_applicable", "evidence": "可见依据"} for name in names}
        definitions = {
            "cover_recognition": "封面第一眼能认出主要角色或系列，不依赖长文案。",
            "meaningful_difference": "相邻图提供不同角度、信息、场景或关系，不是同图换色凑数。",
            "life_scene_fit": "生活场景与角色人设、产品用途和尺度相符，能让人代入真实使用。",
            "information_gain": "每张图增加可见信息，整组覆盖所选笔记类型的必要图位。",
            "verifiable_copy": "标题、正文和 tag 的事实主张有依据；不把概念图说成实拍或已上市。",
            "relevant_tags": "tag 与图文、品类和已确认活动相关，不复制参考帖品牌或无关活动。",
            "copy_image_match": "文案描述的角色、数量、场景和状态能在对应图中找到。",
        }
        requirements["criteria_definitions"] = {name: definitions[name] for name in names}
    if category == "copy":
        requirements["facts"] = [{"claim": "正文中的事实主张", "basis": "用户提供或已确认依据", "status": "verified | unknown | contradicted"}]
    if category == "ux":
        requirements["reviewer"]["viewed_page"] = True
        requirements["criteria"] = {name: {"verdict": "pass | needs_revision | uncertain", "evidence": "真实页面证据"} for name in UX_CRITERIA}
        requirements["criteria_definitions"] = {
            "selectable": "真实点击能提交当前合法选择，回执与项目状态一致。",
            "comparison": "当前、上次交付和比较基线能看懂，不混淆阶段或主体。",
            "disabled_reason": "缺图、待审、失效或物理失败的禁用原因可读。",
            "recovery": "提交冲突、服务失败或静态模式有明确恢复路径。",
            "mobile_keyboard": "手机与键盘的主要操作可见、可聚焦、无遮挡。",
        }
    requirements["claim_boundary"] = "传播力只审查可观察的封面识别、实质差异、生活场景代入、信息增量、真实可验证文案与相关 tag；不得保证或预测流量、销量、爆款。"
    return requirements


def prepare(root: Path, project: dict[str, Any], category: str, paths: list[str] | None = None, producer: str = "", stage: str = "review", copy_path: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    if category not in CATEGORIES:
        raise ReviewError("未知审查类别：" + category)
    if category == "physics":
        raise ReviewError("物理审查只读聚合状态；准备匿名包请直接使用 physics_review.py prepare")
    if category in INDEPENDENT and not producer.strip():
        raise ReviewError("独立审查必须声明 --producer，不能由作者自批")
    normalized = _normalize_paths(root, project, category, paths)
    if category in {"identity", "creative", "sequence", "copy"} and not normalized:
        raise ReviewError(f"{category} 审查没有可绑定的图片")

    review_id = f"review-{time.time_ns()}-{secrets.token_hex(4)}"
    directory = _project_file(root, f"reviews/categories/{category}/requests/{review_id}")
    scope = _report_scope(root, project, category, normalized, stage, copy_path)
    request = {
        "schema_version": 1,
        "review_id": review_id,
        "project_id": project.get("project_id", str(root)),
        "category": category,
        "producer": producer,
        "created_at": time.time_ns(),
        "scope": scope,
        "task": _task(category),
    }
    if category == "copy" and copy_path is not None:
        request["copy_source"] = str(copy_path.resolve())
    if category in {"identity", "creative", "sequence", "copy"}:
        request["reference"] = _creative_reference_packet(root, project, normalized, category, stage)
    directory.mkdir(parents=True, exist_ok=False)
    if normalized:
        from PIL import Image

        request["images"] = []
        for index, item in enumerate(normalized, 1):
            filename = f"image-{index:03d}.png"
            with Image.open(root / item["path"]) as original:
                original.load()
                image = original.convert("RGBA" if "transparency" in original.info or "A" in original.getbands() else "RGB")
                image.info.clear()
                image.save(directory / filename, format="PNG")
            if _file_hash(root / item["path"]) != item["sha256"]:
                raise ReviewError("准备期间原图已变化，请重新准备")
            request["images"].append({**item, "image": filename, "preview_sha256": _file_hash(directory / filename)})
        request["reference_images"] = []
        for index, item in enumerate(scope.get("reference_images") or [], 1):
            filename = f"reference-{index:03d}.png"
            with Image.open(root / item["path"]) as original:
                original.load()
                image = original.convert("RGBA" if "transparency" in original.info or "A" in original.getbands() else "RGB")
                image.info.clear()
                image.save(directory / filename, format="PNG")
            if _file_hash(root / item["path"]) != item["sha256"]:
                raise ReviewError("准备期间身份参考已变化，请重新准备")
            request["reference_images"].append({**item, "image": filename, "preview_sha256": _file_hash(directory / filename)})
    request_file = directory / "request.json"
    _write_json(request_file, request)
    (directory / "request.sha256").write_text(_file_hash(request_file) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "review_id": review_id,
        "category": category,
        "request": str(request_file.resolve()),
        "packet": str(directory.resolve()),
        "scope": scope,
        "say": "交给不继承作者会话的独立执行者；只读请求包，按 task 返回 JSON。报告不能由制图者自批。",
    }


def _validate_findings(findings: Any) -> None:
    if not isinstance(findings, list):
        raise ReviewError("findings 必须是列表")
    for finding in findings:
        if not isinstance(finding, dict) or any(not isinstance(finding.get(field), str) or not finding[field].strip() for field in FINDING_FIELDS):
            raise ReviewError("每条 finding 都需要 code、severity、detail、evidence、fix")
        if finding["severity"] not in ("blocking", "advisory"):
            raise ReviewError("finding.severity 只能是 blocking 或 advisory")


def _validate_path_verdicts(report: dict[str, Any], scope: dict[str, Any]) -> None:
    expected = {item["path"]: item["sha256"] for item in scope.get("paths") or []}
    rows = report.get("paths")
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ReviewError("报告必须逐张覆盖审查范围，不能遗漏或重复")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("path") not in expected or row["path"] in seen:
            raise ReviewError("报告路径不在范围内或重复")
        if row.get("sha256") != expected[row["path"]] or row.get("verdict") not in VERDICTS:
            raise ReviewError("报告图片摘要或逐图判定无效")
        if not isinstance(row.get("evidence"), str) or len(row["evidence"].strip()) < MIN_EVIDENCE:
            raise ReviewError(f"{row['path']} 缺少可核查的逐图证据")
        seen.add(row["path"])
    if any(row["verdict"] != "pass" for row in rows) and report.get("verdict") == "pass":
        raise ReviewError("有图片未通过时不能把报告整体登记为通过")


def _validate_criteria(report: dict[str, Any], required: tuple[str, ...]) -> None:
    criteria = report.get("criteria")
    if not isinstance(criteria, dict) or set(criteria) != set(required):
        raise ReviewError("审查必须覆盖本类别的全部检查项")
    for name, item in criteria.items():
        if not isinstance(item, dict) or item.get("verdict") not in (*VERDICTS, "not_applicable"):
            raise ReviewError(f"{name} 缺少有效判定")
        if not isinstance(item.get("evidence"), str) or len(item["evidence"].strip()) < MIN_EVIDENCE:
            raise ReviewError(f"{name} 缺少可见证据")
        if item["verdict"] == "not_applicable" and len(item["evidence"].strip()) < 20:
            raise ReviewError(f"{name} 的 not_applicable 必须解释原因")
    if report.get("verdict") == "pass" and any(item["verdict"] not in {"pass", "not_applicable"} for item in criteria.values()):
        raise ReviewError("存在未通过检查时不能登记整体通过")


def _validate_copy_facts(report: dict[str, Any]) -> None:
    facts = report.get("facts", [])
    if not isinstance(facts, list):
        raise ReviewError("copy.facts 必须是列表")
    for fact in facts:
        if not isinstance(fact, dict) or fact.get("status") not in ("verified", "unknown", "contradicted"):
            raise ReviewError("文案事实必须标记 verified、unknown 或 contradicted")
        if not isinstance(fact.get("claim"), str) or not fact["claim"].strip() or not isinstance(fact.get("basis"), str) or not fact["basis"].strip():
            raise ReviewError("文案事实必须写明 claim 与 basis")
        if report.get("verdict") == "pass" and fact["status"] != "verified":
            raise ReviewError("无依据或矛盾的事实不能随文案报告通过")


def _copy_needs_facts(request: dict[str, Any]) -> bool:
    copy = ((request.get("scope") or {}).get("copy") or {}).get("copy") or {}
    text = "\n".join(str(copy.get(key) or "") for key in ("title", "body")) + "\n" + " ".join(map(str, copy.get("tags") or []))
    return bool(COPY_CLAIM_PATTERN.search(text))


def _validate_report(report: dict[str, Any], request: dict[str, Any]) -> None:
    if report.get("schema_version") != 2 or report.get("review_id") != request["review_id"] or report.get("category") != request["category"]:
        raise ReviewError("报告必须绑定本次 review_id 和类别")
    if report.get("scope") != request["scope"]:
        raise ReviewError("报告 scope 与本次请求不一致")
    scope = request["scope"]
    if report.get("decision_signature") != scope.get("decision_signature") or report.get("template_version") != scope.get("template_version"):
        raise ReviewError("报告版本签名与请求不一致")
    hashes = [item["sha256"] for item in scope.get("paths") or []]
    if report.get("image_hashes") != hashes:
        raise ReviewError("报告 image_hashes 必须逐张对应请求原图")
    reviewer = report.get("reviewer")
    if not isinstance(reviewer, dict) or not isinstance(reviewer.get("id"), str) or not reviewer["id"].strip():
        raise ReviewError("报告必须声明实际独立执行者标识")
    if request["category"] in INDEPENDENT and reviewer.get("independence") != "zero-context":
        raise ReviewError("报告缺少零上下文声明")
    if request["category"] in INDEPENDENT and reviewer["id"].strip() == request.get("producer", "").strip():
        raise ReviewError("制图执行者不能自行批准审查")
    verdict = report.get("verdict")
    if verdict not in VERDICTS:
        raise ReviewError("verdict 只能是 pass、needs_revision 或 uncertain")
    _validate_findings(report.get("findings"))
    evidence = report.get("evidence")
    if not isinstance(evidence, str) or len(evidence.strip()) < MIN_EVIDENCE:
        raise ReviewError("报告缺少可核查的整体证据")
    if verdict == "pass" and report["findings"]:
        raise ReviewError("存在 findings 时不能登记为 pass")
    if verdict != "pass" and not report["findings"]:
        raise ReviewError("未通过的审查必须列出具体问题")
    if request["category"] in {"identity", "creative", "sequence", "copy"}:
        _validate_path_verdicts(report, scope)
    if request["category"] == "identity" and reviewer.get("viewed_images") is not True:
        raise ReviewError("身份审查必须声明实际看图")
    if request["category"] == "creative":
        if reviewer.get("viewed_images") is not True:
            raise ReviewError("创作审查必须声明实际看图")
        _validate_criteria(report, CREATIVE_CRITERIA)
    if request["category"] == "sequence":
        if reviewer.get("viewed_images") is not True:
            raise ReviewError("图序审查必须声明实际看图")
        _validate_criteria(report, SEQUENCE_CRITERIA)
    if request["category"] == "copy":
        if reviewer.get("viewed_images") is not True:
            raise ReviewError("文案与图文对应审查必须声明实际看图")
        _validate_criteria(report, COPY_CRITERIA)
        _validate_copy_facts(report)
        if _copy_needs_facts(request) and not report.get("facts"):
            raise ReviewError("文案含规格、价格、进度或其他事实主张时必须逐项登记依据")
    if request["category"] == "ux":
        if reviewer.get("viewed_page") is not True:
            raise ReviewError("UX 报告必须声明实际查看页面")
        _validate_criteria(report, UX_CRITERIA)


def record(root: Path, project: dict[str, Any] | None, category: str, review_id: str, report_path: Path) -> dict[str, Any]:
    root = root.resolve()
    if category not in CATEGORIES:
        raise ReviewError("未知审查类别：" + category)
    if category == "physics":
        raise ReviewError("物理审查必须用 physics_review.py record，不能改写为类别报告")
    if not re.fullmatch(r"review-\d+-[0-9a-f]{8,}", review_id):
        raise ReviewError("review_id 不是有效的类别审查标识")
    import project as state

    with state.project_lock(root):
        project = state.load_project(root, writable=True)
        directory = _project_file(root, f"reviews/categories/{category}/requests/{review_id}")
        request_file = directory / "request.json"
        digest_file = directory / "request.sha256"
        if not digest_file.is_file() or digest_file.read_text(encoding="utf-8").strip() != _file_hash(request_file):
            raise ReviewError("审查请求包已变化，不能登记旧报告")
        request = _read_json(request_file)
        report = _read_json(report_path)
        if request.get("review_id") != review_id or request.get("category") != category:
            raise ReviewError("请求包与 review_id 或类别不一致")
        if request.get("project_id") != project.get("project_id", str(root)):
            raise ReviewError("审查请求不属于当前项目")
        _validate_report(report, request)
        scope = request["scope"]
        try:
            normalized = scope.get("paths") or []
            copy_path = Path(request["copy_source"]) if request.get("copy_source") else None
            current = _current_scope(root, project, category, normalized, "review", copy_path)
        except ReviewError as error:
            raise ReviewError("报告准备后项目已变化，请重新准备") from error
        if current != scope:
            raise ReviewError("报告准备后项目、图片、文案或模板已变化，不能登记旧判定")
        for item in [*(request.get("images") or []), *(request.get("reference_images") or [])]:
            preview = _project_file(root, (directory / item["image"]).relative_to(root).as_posix())
            if not preview.is_file() or _file_hash(preview) != item.get("preview_sha256"):
                raise ReviewError("审查包内预览已变化，请重新准备")
        for previous in (project.get("review_records") or {}).values():
            if previous.get("category") != category or previous.get("generation", 0) <= request["created_at"]:
                continue
            previous_paths = {item.get("path") for item in (previous.get("scope") or {}).get("paths", [])}
            if category == "ux" or previous_paths.intersection(item["path"] for item in scope["paths"]):
                raise ReviewError("已有更新的审查请求判定，不能重放旧报告覆盖")
        if category == "files" and report["verdict"] == "pass":
            _, problems = _inspect_files(root, project, [item["path"] for item in scope["paths"]], "review")
            if problems:
                raise ReviewError("当前文件程序检查未过，不能导入通过报告")
        fingerprint = _json_signature(report)
        sealed_file = directory / "recorded.json"
        report_file = _project_file(root, f"reviews/categories/{category}/{review_id}.json")
        sealed = _stored_json(sealed_file)
        if sealed is not None:
            if sealed.get("fingerprint") != fingerprint:
                raise ReviewError("该请求已登记另一份报告，不能重放或改字节")
            stored_report = _stored_json(report_file)
            if stored_report is None or _stored_hash(report_file, stored_report) != sealed.get("report_sha256"):
                raise ReviewError("已登记报告字节变化，不能用旧请求修补批准")
            return {
                "ok": True,
                "review_id": review_id,
                "category": category,
                "verdict": report["verdict"],
                "report": str(report_file.resolve()),
                "blocking": report["verdict"] != "pass",
                "revision": project.get("revision", 0),
                "unchanged": True,
            }
        report_sha256 = _json_file_hash(report)
        relative_report = report_file.relative_to(root).as_posix()
        sealed = {
            "fingerprint": fingerprint,
            "report": relative_report,
            "report_sha256": report_sha256,
        }
        state.write_json(report_file, report)
        state.write_json(sealed_file, sealed)
        index_file = _project_file(root, "reviews/categories/index.json")
        index = _stored_json(index_file) or {"schema_version": 1, "reviews": {}}
        index.setdefault("reviews", {})[review_id] = {
            "category": category,
            "verdict": report["verdict"],
            "report": relative_report,
            "report_sha256": report_sha256,
            "path_count": len(report.get("image_hashes") or []),
            "recorded_at": time.time_ns(),
            "generation": request["created_at"],
        }
        state.write_json(index_file, index)
        project.setdefault("review_records", {})[review_id] = {
            "category": category,
            "verdict": report["verdict"],
            "report": relative_report,
            "report_sha256": report_sha256,
            "scope_sha256": _json_signature(scope),
            "scope": scope,
            "generation": request["created_at"],
            "recorded_at": time.time_ns(),
        }
        state.save_project(root, project)
        return {
            "ok": True,
            "review_id": review_id,
            "category": category,
            "verdict": report["verdict"],
            "report": str(report_file.resolve()),
            "blocking": report["verdict"] != "pass",
            "revision": project.get("revision", 0),
            "unchanged": False,
        }


def _candidate_reports(root: Path, project: dict[str, Any], category: str) -> list[dict[str, Any]]:
    import project as state

    index_file = _project_file(root, "reviews/categories/index.json")
    try:
        index = _stored_json(index_file) or {}
    except ReviewError as error:
        raise ReviewError("分类报告索引损坏，无法验证已登记审查") from error
    indexed = index.get("reviews") or {}
    reports = []
    for review_id, record in (project.get("review_records") or {}).items():
        if record.get("category") != category:
            continue
        scope = record.get("scope") or {"paths": []}
        report = {
            "review_id": review_id,
            "scope": scope,
            "category": category,
            "decision_signature": scope.get("decision_signature"),
            "template_version": scope.get("template_version"),
        }
        try:
            path = _project_file(root, record["report"])
            content = state.read_json(path)
            entry = indexed.get(review_id) or {}
            if _stored_hash(path, content) != record["report_sha256"] or entry.get("report_sha256") != record["report_sha256"]:
                raise ReviewError("报告摘要与登记索引不一致")
            directory = path.parent / "requests" / review_id
            request_file = directory / "request.json"
            if (directory / "request.sha256").read_text(encoding="utf-8").strip() != _file_hash(request_file):
                raise ReviewError("审查请求摘要已变化")
            _validate_report(content, _read_json(request_file))
            report = content
            report["_integrity_error"] = None
        except (ReviewError, state.ProjectError, OSError, KeyError, TypeError, ValueError) as error:
            report["_integrity_error"] = str(error)
        report["_generation"] = record.get("generation", 0)
        reports.append(report)
    return reports


def _current_scope(root: Path, project: dict[str, Any], category: str, paths: list[dict[str, str]], stage: str, copy_path: Path | None = None) -> dict[str, Any]:
    actual = _normalize_paths(root, project, category, [item["path"] for item in paths])
    return _report_scope(root, project, category, actual, stage, copy_path)


def _matches(report: dict[str, Any], scope: dict[str, Any]) -> tuple[bool, str]:
    if report.get("decision_signature") != scope.get("decision_signature"):
        return False, "decision_changed"
    if report.get("template_version") != scope.get("template_version"):
        return False, "template_changed"
    previous = report.get("scope") or {}
    for field, reason in (("copy", "copy_changed"), ("sequence_signature", "sequence_changed"),
                          ("paths", "images_changed"), ("reference_images", "references_changed"),
                          ("requirements", "requirements_changed")):
        if previous.get(field) != scope.get(field):
            return False, reason
    return (True, "") if report.get("scope") == scope else (False, "scope_changed")


def _objective_finding(code: str, detail: str, evidence: str, fix: str, path: str | None = None, severity: str = "blocking") -> dict[str, Any]:
    return {
        "category": code.split(".", 1)[0],
        "path": path,
        "code": code,
        "severity": severity,
        "detail": detail,
        "evidence": evidence,
        "fix": fix,
        "verdict": "needs_revision",
    }


def _inspect_files(root: Path, project: dict[str, Any], paths: list[str] | None, stage: str) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    try:
        normalized = _normalize_paths(root, project, "files", paths)
    except ReviewError as error:
        finding = _objective_finding("files.unreadable_path", str(error), "路径不存在或不在项目内", "修正路径或补齐图片")
        return [], [finding]
    findings = []
    try:
        from PIL import Image
    except ImportError:
        finding = _objective_finding("files.decoder_unavailable", "图片解码依赖不可用", "缺少 Pillow，无法完成解码检查", "安装 Pillow 后重新检查")
        return normalized, [finding]
    for item in normalized:
        path = root / item["path"]
        try:
            with Image.open(path) as image:
                width, height = image.size
                image.load()
            with Image.open(path) as image:
                image.verify()
        except (OSError, ValueError) as error:
            findings.append(_objective_finding("files.decode_failed", "图片无法解码", str(error), "重新导出可解码图片", item["path"]))
            continue
        if width < 1 or height < 1:
            findings.append(_objective_finding("files.empty_dimensions", "图片尺寸无效", f"{width}x{height}", "重新导出有效尺寸图片", item["path"]))
        if stage in {"package", "done"} and abs(width / height - 0.75) > 0.01:
            findings.append(_objective_finding("files.ratio", "输出图片不是 3:4 竖版", f"实际 {width}x{height}", "按 3:4 重新导出", item["path"]))
        if stage == "cutout":
            import check_images

            inspection = check_images.inspect(path, argparse.Namespace(alpha=True, ratio=None, tolerance=0.02, min_side=0))
            if not inspection["ok"]:
                findings.append(_objective_finding(
                    "files.alpha_invalid",
                    "真透明检查未过；仅允许留档，批准仍由项目核心阻断",
                    "；".join(inspection["problems"]),
                    "修复 alpha、边缘和主体覆盖后重新检查",
                    item["path"],
                    severity="advisory",
                ))
    return normalized, findings


def _inspect_copy(root: Path, project: dict[str, Any], copy_path: Path | None = None) -> list[dict[str, Any]]:
    copy, _ = _copy(root, copy_path)
    if copy is None:
        return [_objective_finding("copy.missing_or_invalid", "copy.json 缺失或无法解析", "打包需要可解析的文案", "补齐或修正 copy.json")]
    import package_post
    import project as state

    findings = []
    note_type = (project.get("answers") or {}).get("note_type")
    try:
        warnings = package_post.validate_copy(copy, note_type)
    except state.ProjectError as error:
        return [_objective_finding("copy.invalid", "文案程序检查失败", str(error), "按 copywriting.md 修正文案")]
    for warning in warnings:
        findings.append(_objective_finding("copy.recommendation", "文案有待核对建议", warning, "根据图文语义判断，不当作平台硬限制", severity="advisory"))
    return findings


def _inspect_sequence(root: Path, project: dict[str, Any]) -> list[dict[str, Any]]:
    import project as state

    if not (project.get("answers") or {}).get("note_type"):
        return [_objective_finding("sequence.note_type", "缺少笔记类型", "无法确定必要图位", "先确认笔记类型")]
    try:
        missing = state.missing_scene_slots(project)
    except state.ProjectError as error:
        return [_objective_finding("sequence.slots_invalid", "无法计算必要图位", str(error), "修正项目要求后重查")]
    findings = []
    if missing:
        findings.append(_objective_finding("sequence.missing_slots", "整套图缺少必要图位", ", ".join(missing), "补齐图位后再交付"))
    finals = state.mapped_scene_finals(project)
    if not finals:
        findings.append(_objective_finding("sequence.no_finals", "没有可排序的定稿图", "finals 为空", "先登记有效定稿"))
    elif not any(final.get("role") == "cover" or final.get("slot") == "cover" for final in finals):
        findings.append(_objective_finding("sequence.cover", "缺少封面", "没有 role=cover 的定稿", "补封面并重新排序"))
    return findings


def _bound_reports(root: Path, project: dict[str, Any], category: str, stage: str, target_paths: list[str], copy_path: Path | None = None) -> list[tuple[dict[str, Any], str | None]]:
    bound = []
    targets = _normalize_paths(root, project, category, target_paths)
    target_hashes = {item["sha256"] for item in targets}
    for report in _candidate_reports(root, project, category):
        declared = report.get("scope") or {}
        declared_paths = declared.get("paths") or []
        relevant = category == "ux" or any(item.get("path") in target_paths or item.get("sha256") in target_hashes for item in declared_paths if isinstance(item, dict))
        if not relevant:
            continue
        try:
            current = _report_scope(root, project, category, declared_paths, stage, copy_path)
        except ReviewError:
            bound.append((report, "scope_changed"))
            continue
        ok, reason = _matches(report, current)
        if not ok:
            bound.append((report, reason or "scope_changed"))
            continue
        if report.get("_integrity_error"):
            report["verdict"] = "uncertain"
            report["evidence"] = report["_integrity_error"]
            report["findings"] = [{
                "code": "report.integrity",
                "severity": "blocking",
                "detail": "已登记报告不可验证，不能把它当作缺少可选报告",
                "evidence": report["_integrity_error"],
                "fix": "保留损坏证据，用新的独立请求重新审查",
            }]
        if category == "ux":
            bound.append((report, None))
            continue
        try:
            unchanged = _normalize_paths(root, project, category, [item["path"] for item in declared_paths]) == declared_paths
        except ReviewError:
            unchanged = False
        identical_set = sorted(item["sha256"] for item in declared_paths) == sorted(item["sha256"] for item in targets)
        rows = {item["path"]: item for item in report.get("paths") or []}
        has_path_failure = any(row.get("verdict") != "pass" for row in rows.values())
        projected = False
        for target in targets:
            sources = [item for item in declared_paths if item.get("sha256") == target["sha256"]]
            if not sources:
                continue
            source_rows = [rows.get(item["path"]) or {"verdict": report.get("verdict"), "evidence": report.get("evidence")} for item in sources]
            row = next((item for item in source_rows if item.get("verdict") != "pass"), source_rows[0])
            verdict = row.get("verdict")
            if report.get("_integrity_error") or (report.get("verdict") != "pass" and not has_path_failure):
                verdict = report.get("verdict")
            if verdict == "pass" and not (unchanged or identical_set):
                continue
            projection = {
                **report,
                "scope": {**declared, "paths": [target]},
                "image_hashes": [target["sha256"]],
                "paths": [{**row, **target, "verdict": verdict}],
                "verdict": verdict,
                "findings": report.get("findings") or [] if verdict != "pass" else [],
            }
            bound.append((projection, None))
            projected = True
        if not unchanged or not projected:
            bound.append((report, "images_changed"))
    return bound


def _report_summary(bound: list[tuple[dict[str, Any], str | None]], target_paths: list[str]) -> dict[str, Any]:
    by_scope = {}
    for report, error in bound:
        key = _json_signature(report.get("scope"))
        previous = by_scope.get(key)
        if previous is None or previous[0].get("_generation", 0) < report.get("_generation", 0):
            by_scope[key] = (report, error)
    active = list(by_scope.values())
    valid = [report for report, error in active if error is None]
    failed = [report for report in valid if report.get("verdict") != "pass"]
    covered = {
        item.get("path")
        for report in valid
        for item in report.get("scope", {}).get("paths", [])
        if isinstance(item, dict)
    }
    stale = [{"review_id": report["review_id"], "reason": error} for report, error in active if error is not None]
    path_results = {}
    for report in valid:
        rows = {row["path"]: row for row in report.get("paths") or []}
        for item in report.get("scope", {}).get("paths", []):
            path = item["path"]
            row = rows.get(path) or {"path": path, "verdict": report["verdict"], "evidence": report.get("evidence")}
            if path not in path_results or row["verdict"] != "pass":
                path_results[path] = row
    errors = {}
    for report, error in active:
        if error is None:
            continue
        for item in report.get("scope", {}).get("paths", []):
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                errors[item["path"]] = error
    return {
        "valid": valid,
        "failed": failed,
        "stale": stale,
        "errors": errors,
        "covered": covered,
        "path_results": path_results,
        "complete": bool(target_paths) and set(target_paths).issubset(covered),
    }


def _program_status(root: Path, project: dict[str, Any], category: str, paths: list[str] | None, stage: str, copy_path: Path | None = None) -> dict[str, Any]:
    if category == "files":
        normalized, objective = _inspect_files(root, project, paths, stage)
    elif category == "copy":
        normalized = _normalize_paths(root, project, category, paths)
        objective = _inspect_copy(root, project, copy_path)
    else:
        normalized = _normalize_paths(root, project, category, paths)
        objective = _inspect_sequence(root, project)
        if stage in {"package", "done"}:
            _, file_problems = _inspect_files(root, project, _final_images(root, project), "review")
            objective.extend(file_problems)
    target_paths = [item["path"] for item in normalized]
    bound = _bound_reports(root, project, category, stage, target_paths, copy_path)
    summary = _report_summary(bound, target_paths)
    report_findings = []
    for report in summary["failed"]:
        report_findings.append({
            "category": category,
            "path": None,
            "verdict": report["verdict"],
            "review_id": report["review_id"],
            "reason": report.get("evidence") or "当前报告未通过",
            "findings": report.get("findings") or [],
        })
    findings = objective + report_findings
    blocking_findings = [finding for finding in findings if finding.get("severity") != "advisory"]
    reviewed = summary["complete"] and not summary["failed"]
    return {
        "category": category,
        "verdict": "blocked" if blocking_findings else "pass",
        "blocking": bool(blocking_findings),
        "not_assessed": not summary["complete"],
        "findings": findings,
        "stale": summary["stale"],
        "paths": [
            {
                "path": path,
                "verdict": "needs_revision" if any(item.get("path") == path for item in objective)
                else summary["path_results"].get(path, {}).get("verdict", "pass"),
                "reviewed": path in summary["covered"],
            }
            for path in target_paths
        ],
        "coverage": {"reviewed": len(set(target_paths) & summary["covered"]), "targeted": len(target_paths)},
        "review": "blocked" if summary["failed"] else ("pass" if reviewed else "not_assessed"),
        "required_at": REQUIRED_BY_CATEGORY[category],
    }


def _optional_status(root: Path, project: dict[str, Any], category: str, paths: list[str] | None, stage: str) -> dict[str, Any]:
    normalized = _normalize_paths(root, project, category, paths)
    target_paths = [item["path"] for item in normalized]
    bound = _bound_reports(root, project, category, stage, target_paths)
    summary = _report_summary(bound, target_paths)
    report_findings = []
    for report in summary["failed"]:
        report_findings.append({
            "category": category,
            "path": None,
            "verdict": report["verdict"],
            "review_id": report["review_id"],
            "reason": report.get("evidence") or "当前报告未通过",
            "findings": report.get("findings") or [],
        })
    complete = bool(summary["valid"]) if category in {"creative", "ux"} else summary["complete"]
    complete = complete and not summary["failed"]
    not_assessed = not complete and not report_findings
    return {
        "category": category,
        "verdict": "blocked" if report_findings else ("pass" if complete else "not_assessed"),
        "blocking": bool(report_findings),
        "not_assessed": not_assessed,
        "findings": report_findings,
        "stale": summary["stale"],
        "paths": [
            {
                "path": path,
                "verdict": summary["path_results"].get(path, {}).get("verdict", "not_assessed"),
                "reason": "已绑定当前报告" if path in summary["covered"] else summary["errors"].get(path, "缺少可选独立报告"),
            }
            for path in target_paths
        ],
        "coverage": {"reviewed": len(set(target_paths) & summary["covered"]), "targeted": len(target_paths)},
        "review": "blocked" if report_findings else ("pass" if complete else "not_assessed"),
        "required_at": REQUIRED_BY_CATEGORY[category],
    }


def _single_status(root: Path, project: dict[str, Any], category: str, paths: list[str] | None, stage: str, copy_path: Path | None = None) -> dict[str, Any]:
    if category == "physics":
        physical = paths if paths is not None else _default_paths(root, project, category)
        rows = []
        for relative in physical:
            state = physics_review.status(root, project, relative)
            rows.append({"path": relative, "verdict": state["verdict"], "reason": state["reason"], "report": state.get("report")})
        blocking = any(row["verdict"] != "pass" for row in rows)
        if not rows:
            return {"category": category, "verdict": "not_applicable", "blocking": False,
                    "not_assessed": False, "paths": [], "reason": "没有实际图片可供物理审查；缺图登记不等于物理通过"}
        return {"category": category, "verdict": "blocked" if blocking else "pass", "blocking": blocking, "paths": rows,
                "required_at": REQUIRED_BY_CATEGORY[category]}
    if category in {"files", "copy", "sequence"}:
        return _program_status(root, project, category, paths, stage, copy_path)
    return _optional_status(root, project, category, paths, stage)


def status(root: Path, project: dict[str, Any], category: str, paths: list[str] | None = None, copy_path: Path | None = None) -> dict[str, Any]:
    """返回一个类别的当前状态；不创建报告，不把缺失报告写成通过。"""
    root = root.resolve()
    if category not in CATEGORIES:
        raise ReviewError("未知审查类别：" + category)
    return _single_status(root, project, category, paths, "review", copy_path)


def blocking_findings(root: Path, project: dict[str, Any], paths: list[str] | None, stage: str, copy_path: Path | None = None) -> dict[str, Any]:
    """按阶段汇总客观缺陷和有效失败报告；可选独立检查未做则标未评。"""
    root = root.resolve()
    categories = STAGE_CATEGORIES.get(stage)
    if categories is None:
        if stage in ("review", "all"):
            categories = ("identity", "creative", "physics", "sequence", "copy")
        else:
            raise ReviewError("未知审查阶段：" + stage)
    results = {}
    findings = []
    not_assessed = []
    if paths is None and stage in {"package", "done"}:
        paths = _final_images(root, project)
    file_paths = paths if paths is not None else _default_paths(root, project, "files")
    normalized, file_findings = _inspect_files(root, project, file_paths, stage)
    if file_findings:
        findings.extend(file_findings)
    file_blocking = any(finding.get("severity") == "blocking" for finding in file_findings)
    for category in categories:
        category_paths = paths
        try:
            state = _single_status(root, project, category, category_paths, stage, copy_path)
        except ReviewError as error:
            state = {"category": category, "verdict": "invalid", "blocking": True, "paths": [], "reason": str(error)}
        if category == "physics" or state.get("blocking"):
            results[category] = state
        if state.get("not_assessed"):
            not_assessed.append({
                "category": category,
                "verdict": "not_assessed",
                "reason": "缺少可选独立报告；程序或物理门槛并未因此写成通过",
                "required_at": state.get("required_at"),
            })
        if not state.get("blocking"):
            continue
        if state.get("findings"):
            findings.extend(state["findings"])
        else:
            for row in state.get("paths") or [{"verdict": state.get("verdict"), "reason": state.get("reason")}]:
                if row.get("verdict") == "pass":
                    continue
                findings.append({
                    "category": category,
                    "path": row.get("path"),
                    "verdict": row.get("verdict"),
                    "review_id": row.get("review_id"),
                    "reason": row.get("reason"),
                    "required_at": state.get("required_at"),
                })
    return {
        "ok": True,
        "stage": stage,
        "blocking": file_blocking or any(finding.get("severity") != "advisory" for finding in findings),
        "not_assessed": not_assessed,
        "findings": findings,
        "inspections": {"files": {
            "verdict": "blocked" if file_blocking else ("pass" if normalized else "not_applicable"),
            "checked": len(normalized),
            "findings": file_findings,
            "reason": "已检查实际图片" if normalized else "没有实际图片；缺图留档不等于完成选择或发布验收",
        }},
        "categories": results,
    }


def _emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="分类审查报告、范围失效和阻断汇总；物理审查委托 physics_review.py")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("project")
    prepare_parser.add_argument("--category", required=True, choices=CATEGORIES)
    prepare_parser.add_argument("--images", nargs="*")
    prepare_parser.add_argument("--producer", default="")
    prepare_parser.add_argument("--stage", default="review")
    prepare_parser.add_argument("--copy", help="本次实际文案 JSON；默认 <项目>/copy.json")
    record_parser = sub.add_parser("record")
    record_parser.add_argument("project")
    record_parser.add_argument("--category", required=True, choices=CATEGORIES)
    record_parser.add_argument("--request", required=True)
    record_parser.add_argument("--report", required=True)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("project")
    status_parser.add_argument("--category", required=True, choices=CATEGORIES)
    status_parser.add_argument("--images", nargs="*")
    status_parser.add_argument("--copy", help="本次实际文案 JSON；默认 <项目>/copy.json")
    check_parser = sub.add_parser("check")
    check_parser.add_argument("project")
    check_parser.add_argument("--stage", required=True)
    check_parser.add_argument("--images", nargs="*")
    check_parser.add_argument("--copy", help="本次实际文案 JSON；默认 <项目>/copy.json")
    args = parser.parse_args(argv)
    root = Path(args.project).resolve()
    copy_path = Path(args.copy).resolve() if getattr(args, "copy", None) else None
    try:
        import project as state
        from runtime_activity import RuntimeBusyError, execution_lease
    except (ImportError, OSError) as error:
        return _emit({"ok": False, "error": str(error)})
    try:
        with execution_lease():
            if args.command == "record":
                return _emit(record(root, None, args.category, args.request, Path(args.report)))
            if args.command == "prepare":
                with state.project_lock(root):
                    project = state.load_project(root, writable=True)
                    return _emit(prepare(root, project, args.category, args.images, args.producer, args.stage, copy_path))
            project = state.load_project(root)
            if args.command == "status":
                return _emit({"ok": True, **status(root, project, args.category, args.images, copy_path)})
            return _emit(blocking_findings(root, project, args.images, args.stage, copy_path))
    except (ReviewError, physics_review.ReviewError, state.ProjectError, RuntimeBusyError, OSError) as error:
        return _emit({"ok": False, "error": str(error)})


if __name__ == "__main__":
    sys.exit(main())
