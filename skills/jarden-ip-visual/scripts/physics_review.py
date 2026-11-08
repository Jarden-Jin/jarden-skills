#!/usr/bin/env python3
"""准备最小盲审包，保存逐图物理审查，并按图片哈希校验有效性。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

CHECKS = ("gravity_support", "connections_materials", "contact_anatomy", "lighting_perspective", "overall_realism")
VERDICTS = ("pass", "needs_revision", "uncertain")
TASK = """你是独立物理可信度审查者。不要继承或索取制图者对话、提示词、审美偏好、选中信息或以往通过结论。
只读 request.json、其中指定的图片和已确认产品形态，必须用图片查看能力逐张看完整图，必要时放大关键连接和接触。
不假设不可见支架、刚性固定、瞬间平衡或特殊材质。逐项检查：
gravity_support：重力、支撑、重心与载荷路径；接触阴影不能代替实际支撑。
connections_materials：环、链、绳、织带等连接闭合、可转动关系、松紧与软硬材质；小五金也逐个看。
contact_anatomy：人体结构、承托、压痕、遮挡、穿插与接触。
lighting_perspective：光源、反射、接触影、平面透视及厚度。
overall_realism：上述局部共同构成的物理可信度，不以漂亮或文案解释补救。
明显缺陷判 needs_revision；决定真实性的关键关系看不清判 uncertain，不能当 pass。
纯二维说明页可在不适用项填 not_applicable 并解释，但不得用它豁免实际画出的产品或场景。
这是视觉物理审查，不是刚体或数值物理仿真；不要宣称运行物理引擎。
返回 JSON：schema_version=1、request_id、reviewer={id:实际Agent标识,zero_context:true,viewed_images:true}、results。
每个 result 包含 id、sha256、verdict(pass/needs_revision/uncertain)、summary、findings 数组，
以及 checks：五个指定键，每项={verdict:pass/needs_revision/uncertain/not_applicable,evidence:可见依据}。
问题写 findings=[{region:具体位置,issue:可见缺陷或不确定点,fix:最小返修建议}]。
只有各项通过或有依据地不适用，且没有待修 findings，才能整体 pass。不要改图片或项目。
"""


class ReviewError(Exception):
    pass


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReviewError(f"无法读取物理审查 JSON：{path}") from error
    if not isinstance(value, dict):
        raise ReviewError("物理审查 JSON 顶层必须是对象")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".review-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def local_image(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ReviewError(f"物理审查图片必须是存在的项目内文件：{relative}")
    return path


def reject_author_metadata(path: Path) -> None:
    try:
        from PIL import Image

        allowed = {"transparency", "gamma", "srgb", "chromaticity", "dpi", "jfif", "jfif_version", "jfif_unit", "jfif_density", "exif"}
        with Image.open(path) as image:
            image.load()
            if getattr(image, "text", {}):
                raise ReviewError("送审图含PNG文本元数据；先另存仅像素副本并绑定为待定稿，再重新准备盲审包")
            extra = set(image.info) - allowed
            exif = image.getexif()
            safe_tags = {256, 257, 274, 282, 283, 296, 40961, 40962, 40963}
            fields = {tag: value for tag, value in exif.items() if tag != 34665}
            fields.update(exif.get_ifd(34665) if 34665 in exif else {})
            unsafe_exif = any(tag not in safe_tags or not isinstance(value, (int, float)) and not hasattr(value, "numerator") for tag, value in fields.items())
            unsafe_marker = any(marker in ("APP2", "APP13", "COM") or marker == "APP1" and not value.startswith(b"Exif\x00\x00") for marker, value in getattr(image, "applist", []))
            if extra or unsafe_exif or unsafe_marker:
                raise ReviewError("送审图含元数据，可能泄露作者提示词或结论；先另存仅含像素的净化副本并绑定该副本为待定稿图，再重新准备盲审包")
    except ImportError as error:
        raise ReviewError("物理盲审包检查需要 Pillow；缺少时停止，不跳过元数据检查") from error
    except OSError as error:
        raise ReviewError("送审图片无法解码") from error


def context_of(project: dict[str, Any]) -> dict[str, str]:
    answers = project.get("answers") or {}
    form = answers.get("product_form")
    if not isinstance(form, str) or not form:
        raise ReviewError("先确认产品形态，再准备物理盲审")
    context = {"product_form": form}
    if form == "other":
        context["product_detail"] = str((project.get("brief") or {}).get("product_detail") or "")
    return context


def context_key(project: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(context_of(project), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def status(root: Path, project: dict[str, Any], relative: str) -> dict[str, Any]:
    try:
        image = local_image(root, relative)
        key = f"{context_key(project)}:{digest(image)}"
        index = root / "reviews/physics/index.json"
        record = (read_json(index).get("images") or {}).get(key) if index.is_file() else None
        if not isinstance(record, dict):
            return {"verdict": "missing", "reason": "缺少独立物理审查"}
        report = local_image(root, record["report"])
        if digest(report) != record.get("report_sha256"):
            raise ReviewError("物理审查报告已变化，需要重新登记")
        content = read_json(report)
        reviewer = content.get("reviewer") or {}
        if not isinstance(reviewer, dict) or reviewer.get("zero_context") is not True or reviewer.get("viewed_images") is not True:
            raise ReviewError("报告缺少独立看图声明")
        reported = content.get("results")
        if not isinstance(reported, list) or any(not isinstance(result, dict) for result in reported):
            raise ReviewError("报告逐图结果格式无效")
        results = [result for result in reported if result.get("sha256") == digest(image)]
        if not results:
            raise ReviewError("报告未覆盖当前图片")
        for result in results:
            validate_result(result)
        verdict = results[0]["verdict"]
        if any(result["verdict"] != verdict for result in results):
            raise ReviewError("同图判定冲突")
        return {"verdict": verdict, "reason": results[0]["summary"], "report": record["report"]}
    except (ReviewError, OSError, KeyError, TypeError) as error:
        return {"verdict": "invalid", "reason": str(error)}


def require(root: Path, project: dict[str, Any], paths: list[str]) -> list[dict[str, Any]]:
    reviews = [{"image": path, **status(root, project, path)} for path in paths]
    blocked = [review for review in reviews if review["verdict"] != "pass"]
    if blocked:
        raise ReviewError("物理审查未通过：" + "；".join(f"{review['image']}（{review['reason']}）" for review in blocked)
                          + "。按 physics-review.md 交给零上下文 Agent，返修后按新图片哈希复审")
    return reviews


def prepare(root: Path, project: dict[str, Any], paths: list[str], producer: str) -> dict[str, Any]:
    if not paths or not producer.strip():
        raise ReviewError("没有可供物理审查的图片")
    originals = [(path, local_image(root, path)) for path in dict.fromkeys(paths)]
    for _, original in originals:
        reject_author_metadata(original)
    context = context_of(project)
    request_id = secrets.token_hex(12)
    directory = root / "reviews/physics" / request_id
    packet = directory / "packet"
    packet.mkdir(parents=True)
    images, mapping = [], {}
    for index, (relative, original) in enumerate(originals, 1):
        image_id = f"image-{index:03d}"
        filename = image_id + original.suffix.lower()
        shutil.copyfile(original, packet / filename)
        images.append({"id": image_id, "image": filename, "sha256": digest(packet / filename)})
        mapping[image_id] = relative
    request = {"schema_version": 1, "request_id": request_id, "product": context, "images": images}
    write_json(packet / "request.json", request)
    (packet / "task.md").write_text(TASK, encoding="utf-8")
    write_json(directory / "mapping.json", {"sources": mapping, "producer": producer, "generation": time.time_ns(), "context": context_key(project), "request_sha256": digest(packet / "request.json")})
    return {"ok": True, "request_id": request_id, "packet": str(packet.resolve()), "images": len(images),
            "say": "新建不继承会话的物理审查 Agent，只提供 packet；不要提供 mapping、制图提示词或作者结论"}


def validate_result(result: dict[str, Any]) -> None:
    if result.get("verdict") not in VERDICTS or not isinstance(result.get("summary"), str) or not result["summary"].strip():
        raise ReviewError("每张图必须有 verdict 和具体 summary")
    checks = result.get("checks")
    if not isinstance(checks, dict) or set(checks) != set(CHECKS):
        raise ReviewError("物理审查必须覆盖全部五项检查")
    for name, check in checks.items():
        if not isinstance(check, dict) or check.get("verdict") not in (*VERDICTS, "not_applicable") or not isinstance(check.get("evidence"), str) or not check["evidence"].strip():
            raise ReviewError(f"{name} 必须有逐项判定及可见证据")
    findings = result.get("findings")
    if not isinstance(findings, list) or any(not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key].strip() for key in ("region", "issue", "fix")) for item in findings):
        raise ReviewError("findings 必须列出区域、问题与最小修复")
    if result["verdict"] == "pass" and (findings or any(check["verdict"] not in ("pass", "not_applicable") for check in checks.values()) or checks["overall_realism"]["verdict"] != "pass"):
        raise ReviewError("存在物理缺陷或不确定项，不能登记为通过")
    if result["verdict"] != "pass" and not findings:
        raise ReviewError("未通过的图片必须列出具体返修或不确定项")


def record(root: Path, project: dict[str, Any], request_id: str, report_path: Path) -> dict[str, Any]:
    if not request_id or any(character not in "0123456789abcdef" for character in request_id):
        raise ReviewError("request_id 不是有效的盲审包标识")
    directory = root / "reviews/physics" / request_id
    request_file = directory / "packet/request.json"
    mapping = read_json(directory / "mapping.json")
    request, report = read_json(request_file), read_json(report_path)
    if digest(request_file) != mapping.get("request_sha256") or context_key(project) != mapping.get("context"):
        raise ReviewError("盲审包或产品形态已变化，请重新准备")
    reviewer = report.get("reviewer") or {}
    if report.get("schema_version") != 1 or report.get("request_id") != request_id or not isinstance(reviewer, dict) or not isinstance(reviewer.get("id"), str) or not reviewer["id"].strip() or reviewer.get("zero_context") is not True or reviewer.get("viewed_images") is not True:
        raise ReviewError("报告必须绑定本次 request_id，并声明独立 Agent 标识、零上下文和实际看图")
    if reviewer["id"] == mapping.get("producer"):
        raise ReviewError("制图执行者不能自行批准物理审查")
    results = report.get("results")
    expected = {image["id"]: image for image in request["images"]}
    if not isinstance(results, list) or any(not isinstance(result, dict) or not isinstance(result.get("id"), str) for result in results) or len(results) != len(expected) or {result.get("id") for result in results} != set(expected):
        raise ReviewError("报告必须逐张覆盖盲审包图片，不能遗漏或重复")
    by_hash = {}
    for result in results:
        validate_result(result)
        original = local_image(root, mapping["sources"][result["id"]])
        sha = expected[result["id"]]["sha256"]
        if result.get("sha256") != sha or digest(original) != sha or digest(directory / "packet" / expected[result["id"]]["image"]) != sha:
            raise ReviewError("图片在送审后已变化，不能复用旧报告")
        if sha in by_hash and by_hash[sha]["verdict"] != result["verdict"]:
            raise ReviewError("相同图片的审查判定不一致，请审查者核对")
        by_hash[sha] = result
    fingerprint = hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    sealed_file = directory / "recorded.json"
    if sealed_file.is_file() and read_json(sealed_file).get("fingerprint") != fingerprint:
        raise ReviewError("该请求已登记另一份报告，不能更改结论；请使用新盲审包")
    index_file = root / "reviews/physics/index.json"
    index = read_json(index_file) if index_file.is_file() else {"schema_version": 1, "images": {}}
    generation = mapping.get("generation", 0)
    for sha in by_hash:
        previous = index["images"].get(f"{context_key(project)}:{sha}") or {}
        if previous.get("generation", 0) > generation:
            raise ReviewError("已有更新的物理审查判定，不能重放旧报告覆盖")
    report_file = directory / f"result-{fingerprint}.json"
    write_json(report_file, report)
    for sha, result in by_hash.items():
        index["images"][f"{context_key(project)}:{sha}"] = {"verdict": result["verdict"], "summary": result["summary"],
            "generation": generation, "request_id": request_id, "report": report_file.relative_to(root).as_posix(), "report_sha256": digest(report_file)}
    write_json(sealed_file, {"fingerprint": fingerprint})
    write_json(index_file, index)
    return {"ok": True, "report": str(report_file.resolve()), "passed": sum(result["verdict"] == "pass" for result in results), "images": len(results)}


def main() -> int:
    import project as state

    parser = argparse.ArgumentParser(description="独立物理审查的盲审包与哈希门禁，不执行数值仿真")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("project")
    prepare_parser.add_argument("--producer", required=True, help="宿主提供的制图执行者标识，审查者必须不同")
    source = prepare_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--round")
    source.add_argument("--images", nargs="+")
    record_parser = sub.add_parser("record")
    record_parser.add_argument("project")
    record_parser.add_argument("--request", required=True)
    record_parser.add_argument("--report", required=True)
    args = parser.parse_args()
    root = Path(args.project).resolve()
    try:
        project = state.load_project(root)
        if args.command == "record":
            return state.emit(record(root, project, args.request, Path(args.report)))
        paths = args.images
        if args.round:
            entry = state.find_round(project, args.round)
            data = state.read_json(root / entry["dir"] / "round.json")
            paths = [f"{entry['dir']}/{item['image']}" for item in data.get("items") or [] if (root / entry["dir"] / item["image"]).is_file()]
        return state.emit(prepare(root, project, paths, args.producer))
    except (ReviewError, state.ProjectError, OSError) as error:
        return state.emit({"ok": False, "error": str(error)})


if __name__ == "__main__":
    sys.exit(main())
