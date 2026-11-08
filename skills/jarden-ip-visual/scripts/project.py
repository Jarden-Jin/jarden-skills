#!/usr/bin/env python3
"""jarden-ip-visual 的项目状态工具。

管理项目目录里的 project.json 和每轮的 round.json，并给出下一步动作。
只用 Python 标准库；写入先写临时文件再原子替换，保留未知字段。
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import physics_review

SCHEMA_VERSION = 1
STAGES = ("concept", "material", "cutout", "scene", "package", "done")
PER_SUBJECT_STAGES = ("concept", "material")
ASK_ORDER = ("product_form", "subject_scope", "note_type", "concept_count", "delivery")
LETTERS = "ABCDE"
MAX_DIRECTIONS = 5
MIN_PROMPT_CHARS = 40

PRODUCT_FORMS = [
    {"value": "plush", "label": "毛绒", "intro": "毛绒挂件、公仔、抱枕这类布艺毛绒"},
    {"value": "figure", "label": "手办", "intro": "搪胶、树脂、PVC 摆件或盲盒"},
    {"value": "puzzle", "label": "拼图", "intro": "把 IP 画面做成拼图，含盒面和拼装场景"},
    {"value": "other", "label": "其他周边", "intro": "亚克力、徽章、文具等，说一下具体品类"},
]
NOTE_TYPES = [
    {
        "value": "series-collection",
        "label": "同系列合集",
        "intro": "把一组同世界观的角色做成风格统一的全家福、单只展示和生活场景图，适合新 IP 出道测款、预售预热。",
    },
    {
        "value": "design-manuscript",
        "label": "IP 设计手稿",
        "intro": "把手稿或线稿做成作品集级 IP 设计提案：三视图、成品渲染、配色款、世界观场景和表情包等延展，适合设计师涨粉、参赛、接单。",
    },
    {
        "value": "single-multiview",
        "label": "单个玩偶多维展示",
        "intro": "单只玩偶上新：把一只成型玩偶拆成正侧背、细节、手持、挂包、穿搭和包装图，配新品文案与 tag，适合品牌上新、带货或工厂接单。",
    },
    {
        "value": "multi-scene",
        "label": "多个玩偶多场景展示",
        "intro": "一组玩偶同框出镜，从合照到居家、主题场景再到挂包出门，突出集齐欲和陪伴感，适合系列上新、开箱种草、门店带货。",
    },
]
DELIVERIES = [
    {"value": "full-post", "label": "整套发帖", "intro": "按笔记结构排好的 5 到 9 张图，加标题、正文和 tag"},
    {"value": "grid-9", "label": "九宫格", "intro": "正好 9 张按顺序排好的图和一张 3×3 预览，同样附标题、正文和 tag"},
]
SEQUENCES = {
    "series-collection": ("cover", "character", "scene", "extension"),
    "design-manuscript": ("cover", "lineup", "turnaround", "manuscript", "scene", "extension"),
    "single-multiview": ("cover", "scale", "detail", "angle", "scene", "info"),
    "multi-scene": ("cover", "group", "home", "theme", "outing", "display"),
}
ENUMS = {
    "answers.product_form": {item["value"] for item in PRODUCT_FORMS},
    "answers.subject_scope": {"single", "all", "selected"},
    "answers.note_type": set(SEQUENCES),
    "answers.delivery": {item["value"] for item in DELIVERIES},
    "image_backend.kind": {"builtin", "connected", "api"},
    "stage": set(STAGES),
}
INTEGERS = {
    "answers.concept_count": (1, 12),
    "preferences.directions_per_round": (1, MAX_DIRECTIONS),
}
SETTABLE_ROOTS = {"project", "brief", "answers", "preferences", "image_backend", "stage", "notes"}
INPUT_KINDS = {"wireframe", "sketch", "flat-design", "ai-image", "photo", "text", "reference"}
IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF")
REUSABLE_ANSWERS = ("product_form", "note_type", "delivery")
METHODS = ("generate", "derive", "compose")


class ProjectError(Exception):
    """可以直接告诉 Agent 的错误。"""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ProjectError(f"没有找到 {path.as_posix()}") from error
    except json.JSONDecodeError as error:
        raise ProjectError(f"{path.as_posix()} 不是有效的 JSON：{error}") from error
    if not isinstance(data, dict):
        raise ProjectError(f"{path.as_posix()} 的顶层必须是对象")
    return data


def load_project(root: Path) -> dict[str, Any]:
    path = root / "project.json"
    if not path.is_file():
        raise ProjectError(f"{root.as_posix()} 里没有 project.json；先运行 init")
    project = read_json(path)
    if project.get("schema_version") != SCHEMA_VERSION:
        raise ProjectError("project.json 的 schema_version 必须是 1")
    return project


def save_project(root: Path, project: dict[str, Any]) -> None:
    project["updated_at"] = now()
    write_json(root / "project.json", project)


def require_physics(root: Path, project: dict[str, Any], paths: list[str]) -> list[dict[str, Any]]:
    try:
        return physics_review.require(root, project, paths)
    except physics_review.ReviewError as error:
        raise ProjectError(str(error)) from error


def new_project(name: str, inputs: list[dict[str, str]]) -> dict[str, Any]:
    stamp = now()
    return {
        "schema_version": SCHEMA_VERSION,
        "project": name,
        "created_at": stamp,
        "updated_at": stamp,
        "brief": {"ip_name": "", "selling_points": [], "bible": {}},
        "inputs": inputs,
        "answers": {},
        "preferences": {"directions_per_round": 3},
        "image_backend": {},
        "stage": "concept",
        "rounds": [],
        "selections": {},
        "finals": [],
    }


def subjects_of(project: dict[str, Any]) -> list[str]:
    answers = project.get("answers") or {}
    subjects = [item.strip() for item in answers.get("subjects") or [] if isinstance(item, str) and item.strip()]
    if not subjects:
        subjects = [((project.get("brief") or {}).get("ip_name") or "").strip() or "主体"]
    count = answers.get("concept_count")
    if isinstance(count, int):
        counter = len(subjects) + 1
        while len(subjects) < count:
            name = f"角色{counter}"
            counter += 1
            if name not in subjects:
                subjects.append(name)
    return subjects


def directions(project: dict[str, Any]) -> int:
    value = (project.get("preferences") or {}).get("directions_per_round", 3)
    return value if isinstance(value, int) and 1 <= value <= MAX_DIRECTIONS else 3


def selections(project: dict[str, Any], stage: str) -> dict[str, Any]:
    return project.setdefault("selections", {}).setdefault(stage, {})


def find_round(project: dict[str, Any], round_id: str) -> dict[str, Any]:
    for entry in project.get("rounds") or []:
        if entry.get("id") == round_id:
            return entry
    raise ProjectError(f"没有轮次 {round_id}；用 show 查看已有轮次")


def open_round(project: dict[str, Any]) -> dict[str, Any] | None:
    for entry in reversed(project.get("rounds") or []):
        if entry.get("status") in ("planned", "recorded"):
            return entry
    return None


def stage_complete(project: dict[str, Any], stage: str) -> bool:
    chosen = (project.get("selections") or {}).get(stage) or {}
    if stage in PER_SUBJECT_STAGES or stage == "cutout":
        return all(subject in chosen for subject in subjects_of(project))
    if stage == "scene":
        return bool(chosen.get("_set")) and not missing_scene_slots(project)
    return False


def advance(project: dict[str, Any]) -> None:
    stage = project.get("stage") or "concept"
    while stage in STAGES[:4] and stage_complete(project, stage):
        stage = STAGES[STAGES.index(stage) + 1]
    project["stage"] = stage


def recommended_note_type(project: dict[str, Any]) -> str:
    answers = project.get("answers") or {}
    if len(answers.get("subjects") or []) > 1:
        return "series-collection"
    kinds = {item.get("kind") for item in project.get("inputs") or []}
    if kinds & {"wireframe", "sketch"} and answers.get("product_form") == "figure":
        return "design-manuscript"
    return "single-multiview"


def note_types_of(project: dict[str, Any]) -> list[dict[str, str]]:
    if (project.get("answers") or {}).get("product_form") in (None, "plush"):
        return [dict(item) for item in NOTE_TYPES]
    return [
        {**item, "label": item["label"].replace("玩偶", "产品"),
         "intro": item["intro"].replace("玩偶", "产品").replace("手持、挂包、穿搭和包装图", "尺度、细节、角度和使用场景图").replace("挂包出门", "使用或携带场景")}
        for item in NOTE_TYPES
    ]


def ask_action(project: dict[str, Any], field: str) -> dict[str, Any]:
    answers = project.get("answers") or {}
    action: dict[str, Any] = {"action": "ask", "field": f"answers.{field}"}
    if field == "product_form":
        action["options"] = PRODUCT_FORMS
        action["say"] = "只问产品形态。用户已经说过就直接 set，不要问"
    elif field == "subject_scope":
        action["options"] = []
        action["say"] = (
            "看输入图，把每个主体单独列成一个选项，最后加一项“全部”。"
            "用户回答后写入 answers.subject_scope 和 answers.subjects。只有一个主体时不要问，直接写入 single"
        )
    elif field == "note_type":
        recommended = recommended_note_type(project)
        action["options"] = sorted(note_types_of(project), key=lambda item: item["value"] != recommended)
        action["recommended"] = recommended
        action["say"] = "4 类全部列出，每类带上 intro 的简介；可以按用户的情况换推荐项，推荐项放第一"
    elif field == "concept_count":
        many = answers.get("note_type") in ("series-collection", "multi-scene")
        count = len(answers.get("subjects") or [])
        recommended = (count if count > 1 else 4) if many else max(count, 1)
        values = [recommended] + [value for value in ((3, 4, 6) if many else (1, 2, 3)) if value != recommended]
        action["options"] = [{"value": value, "label": f"{value} 个"} for value in values[:3]]
        action["recommended"] = recommended
        action["say"] = "问一共做几个角色或设计；说明每个都会分开确认，系列会先定第 1 个的风格"
    else:
        action["options"] = DELIVERIES
        action["recommended"] = "full-post"
        action["say"] = "问最后交付整套发帖还是九宫格"
    return action


def scene_slots(project: dict[str, Any]) -> list[dict[str, str]]:
    answers = project.get("answers") or {}
    note_type = answers.get("note_type")
    subjects = subjects_of(project)
    slots: list[dict[str, str]] = [{"id": "cover", "role": "cover"}]
    if note_type == "series-collection":
        slots += [{"id": f"character-{index}", "role": "character", "subject": name} for index, name in enumerate(subjects, 1)]
        slots += [{"id": "scene-1", "role": "scene"}, {"id": "scene-2", "role": "scene"}]
    elif note_type == "design-manuscript":
        if len(subjects) > 1:
            slots.append({"id": "lineup", "role": "lineup"})
        slots += [{"id": "turnaround", "role": "turnaround"}, {"id": "manuscript", "role": "manuscript"}]
        slots += [{"id": "scene-1", "role": "scene"}, {"id": "extension-1", "role": "extension"}]
    elif note_type == "multi-scene":
        slots += [{"id": role, "role": role} for role in ("group", "home", "theme", "outing")]
    else:
        slots += [{"id": role, "role": role} for role in ("scale", "detail", "angle")]
        slots += [{"id": "scene-1", "role": "scene"}, {"id": "info", "role": "info"}]
    if answers.get("delivery") == "grid-9":
        if note_type == "series-collection" and len(subjects) > 7:
            raise ProjectError("九宫格放不下全家福、每个角色单独展示和必要场景；请改整套发帖，或先确认减少/分组主体，不能静默丢图")
        if len(slots) > 9 and note_type == "series-collection":
            slots = [slot for slot in slots if slot["id"] != "scene-2"]
        extra = 1
        while len(slots) < 9:
            role = "extension" if extra % 2 else "scene"
            slots.append({"id": f"{role}-x{extra}", "role": role})
            extra += 1
    return slots


def mapped_scene_finals(project: dict[str, Any]) -> list[dict[str, Any]]:
    required = scene_slots(project)
    finals = [dict(final) for final in project.get("finals") or []]
    available = {final.get("slot") for final in finals}
    unmatched = [slot for slot in required if slot["id"] not in available]
    for final in finals:
        if final.get("slot"):
            continue
        match = next((slot for slot in unmatched if slot["role"] == final.get("role")
                      and (not final.get("subject") or slot.get("subject") == final["subject"])), None)
        if match:
            final["slot"] = match["id"]
            unmatched.remove(match)
    return finals


def missing_scene_slots(project: dict[str, Any]) -> list[str]:
    required = scene_slots(project)
    available = {final.get("slot") for final in mapped_scene_finals(project)}
    return [slot["id"] for slot in required if slot["id"] not in available]


def requirement_signature(project: dict[str, Any], stage: str | None = None) -> str:
    answers = project.get("answers") or {}
    brief = project.get("brief") or {}
    data = {
        "product_form": answers.get("product_form"),
        "subject_scope": answers.get("subject_scope"),
        "subjects": subjects_of(project),
        "concept_count": answers.get("concept_count"),
        "product_detail": brief.get("product_detail"),
    }
    if stage is None or stage == "scene":
        data.update(note_type=answers.get("note_type"), delivery=answers.get("delivery"))
    if stage is None:
        data["revision"] = project.get("revision", 0)
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def stage_action(project: dict[str, Any]) -> dict[str, Any]:
    stage = project.get("stage") or "concept"
    subjects = subjects_of(project)
    if stage in PER_SUBJECT_STAGES:
        chosen = (project.get("selections") or {}).get(stage) or {}
        for index, subject in enumerate(subjects):
            if subject not in chosen:
                action = {"action": "round", "stage": stage, "subject": subject, "kind": "alternatives", "directions": directions(project)}
                action["say"] = "先读 stages.md 里这个阶段的要求，再开一轮"
                if index:
                    action["say"] += "；沿用第 1 个角色已选定的风格，只换角色"
                return action
    if stage == "cutout":
        chosen = (project.get("selections") or {}).get("cutout") or {}
        pending = [f"cutout-{index}" for index, name in enumerate(subjects, 1) if name not in chosen]
        return {"action": "round", "stage": "cutout", "kind": "set", "slots": pending, "say": "只补未通过角色的透明正面全身图，用 check_images.py --alpha 检查"}
    if stage == "scene":
        chosen = (project.get("selections") or {}).get("scene") or {}
        if "_" not in chosen:
            return {"action": "round", "stage": "scene", "kind": "alternatives", "directions": directions(project), "say": "先出关键场景方向，逐张经零上下文 Agent 物理审查后再登记和选定"}
        pending = missing_scene_slots(project)
        action = {"action": "round", "stage": "scene", "kind": "set", "say": "按所选笔记类型的图片序列出整组图"}
        if pending:
            action["slots"] = pending
            action["say"] = "只重出用户没通过的几张"
        return action
    if stage == "package":
        return {"action": "package", "say": "写 copy.json，默认不在图上加字；先 dry-run，再 --prepare-review 审查排版后的实际输出，最后正式打包"}
    return {"action": "done", "say": "交付发帖目录和对比页，提醒合规事项，列出接下来可以做"}


def next_action(project: dict[str, Any]) -> dict[str, Any]:
    answers = project.get("answers") or {}
    for field in ASK_ORDER:
        if answers.get(field) in (None, "", []):
            return ask_action(project, field)
    if answers.get("delivery") == "grid-9" and answers.get("note_type") == "series-collection" and len(subjects_of(project)) > 7:
        action = ask_action(project, "delivery")
        action["say"] = "九宫格放不下每只角色和必要场景。只问这一冲突：改整套发帖（推荐），或先减少/分组角色后仍做九宫格；不要开始生成或删掉角色图"
        action["issue"] = "grid-capacity"
        return action
    if not (project.get("image_backend") or {}).get("kind"):
        return {"action": "backend", "say": "按 SKILL.md 的顺序选定生图能力并写入 image_backend.kind；宿主自带或已连接的能力可用时不问用户"}
    entry = open_round(project)
    if entry and entry.get("status") == "planned":
        return {
            "action": "generate",
            "round": entry["id"],
            "round_file": f"{entry['dir']}/round.json",
            "say": "填写每项的 title、idea、prompt，按 refs 生图并保存；scene 轮须按 physics-review.md 完成逐张零上下文物理审查，再运行 record-round",
        }
    if entry:
        return {
            "action": "await-choice",
            "round": entry["id"],
            "kind": entry.get("kind"),
            "gallery": "gallery.html",
            "say": "回复对比页路径，问三选一：文字方向已经可以并保留意向、要新的方向、重新生成；缺图不能 select" if entry.get("missing") else "回复对比页路径，问三选一：已经可以并选定、要新的图、重新生成",
        }
    return stage_action(project)


def emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", True) else 2


def warnings_of(project: dict[str, Any]) -> list[str]:
    answers = project.get("answers") or {}
    subjects = answers.get("subjects") or []
    found = []
    if answers.get("note_type") == "single-multiview" and len(subjects) > 1:
        found.append("单个主体多维展示只围绕一个主体；先和用户确认主角是哪一个，或者改选同系列合集、多主体多场景展示")
    if answers.get("note_type") in ("series-collection", "multi-scene") and answers.get("concept_count") == 1:
        found.append("这一类需要多个角色；concept_count 为 1 时先和用户确认要扩成几个")
    return found


def status_payload(project: dict[str, Any], note: str = "") -> dict[str, Any]:
    payload = {
        "ok": True,
        "project": project.get("project"),
        "stage": project.get("stage"),
        "answers": project.get("answers"),
        "brief": project.get("brief"),
        "image_backend": project.get("image_backend"),
        "subjects": subjects_of(project),
        "rounds": [
            {key: entry.get(key) for key in ("id", "stage", "subject", "kind", "status", "pick")}
            for entry in (project.get("rounds") or [])[-6:]
        ],
        "finals": len(project.get("finals") or []),
        "next": next_action(project),
    }
    warnings = warnings_of(project)
    if warnings:
        payload["warnings"] = warnings
    if note:
        payload["note"] = note
    return payload


def parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def validate_setting(key: str, value: Any) -> None:
    root = key.split(".", 1)[0]
    if root not in SETTABLE_ROOTS:
        raise ProjectError(f"不能用 set 修改 {key}；rounds、selections、finals 由其他命令维护")
    if key in ENUMS and (not isinstance(value, str) or value not in ENUMS[key]):
        raise ProjectError(f"{key} 只能是 {', '.join(sorted(ENUMS[key]))}")
    if key in INTEGERS:
        low, high = INTEGERS[key]
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise ProjectError(f"{key} 必须是 {low} 到 {high} 的整数")
    if key in ("answers.subjects", "brief.selling_points"):
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            raise ProjectError(f"{key} 必须是非空字符串列表，例如 '[\"云团\",\"雨滴\"]'")
        if key == "answers.subjects" and len({name.strip() for name in value}) != len(value):
            raise ProjectError("角色名必须唯一；同名的不同角色请加区别词，确保分别确认")


def apply_setting(project: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    target = project
    for part in parts[:-1]:
        child = target.get(part)
        if not isinstance(child, dict):
            child = {}
            target[part] = child
        target = child
    target[parts[-1]] = value


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.project)
    if (root / "project.json").is_file():
        return emit(status_payload(load_project(root), note="项目已存在，沿用已有记录，没有改动"))
    if args.kind not in INPUT_KINDS:
        raise ProjectError(f"--kind 只能是 {', '.join(sorted(INPUT_KINDS))}")
    sources = [Path(value) for value in args.input or []]
    for source in sources:
        if not source.is_file():
            raise ProjectError(f"输入文件不存在：{source.as_posix()}")
    inputs = []
    used = set()
    for source in sources:
        filename = source.name
        counter = 2
        while filename in used or (root / "inputs" / filename).exists():
            filename = f"{source.stem}-{counter}{source.suffix}"
            counter += 1
        used.add(filename)
        target = root / "inputs" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(source, target)
        inputs.append({"path": f"inputs/{filename}", "kind": args.kind, "original_name": source.name})
    project = new_project(args.name or root.resolve().name, inputs)
    if args.reuse_from:
        previous = load_project(Path(args.reuse_from))
        old_answers = previous.get("answers") or {}
        project["answers"] = {key: old_answers[key] for key in REUSABLE_ANSWERS if old_answers.get(key)}
        project["preferences"] = previous.get("preferences") or project["preferences"]
        project["image_backend"] = previous.get("image_backend") or {}
    save_project(root, project)
    note = "已建立项目" + ("，沿用了上一个项目的产品形态、笔记类型、交付形式和生图设置" if args.reuse_from else "")
    return emit(status_payload(project, note=note))


def cmd_show(args: argparse.Namespace) -> int:
    return emit(status_payload(load_project(Path(args.project))))


def cmd_set(args: argparse.Namespace) -> int:
    root = Path(args.project)
    project = load_project(root)
    old_answers = dict(project.get("answers") or {})
    old_detail = (project.get("brief") or {}).get("product_detail")
    changed = set()
    for pair in args.pairs:
        if "=" not in pair:
            raise ProjectError(f"参数要写成 key=value：{pair}")
        key, raw = pair.split("=", 1)
        key = key.strip()
        value = parse_value(raw)
        validate_setting(key, value)
        apply_setting(project, key, value)
        changed.add(key)
    answers = project.get("answers") or {}
    if "answers.subjects" in changed:
        answers["subjects"] = [name.strip() for name in answers["subjects"]]
        old_subjects = [name.strip() for name in old_answers.get("subjects") or []]
        if "answers.concept_count" not in changed and answers["subjects"] != old_subjects and old_answers.get("concept_count"):
            answers["concept_count"] = len(answers["subjects"]) if len(answers["subjects"]) < len(old_subjects) else max(old_answers["concept_count"], len(answers["subjects"]))
    if answers.get("subject_scope") == "single" and len(answers.get("subjects") or []) > 1:
        raise ProjectError("subject_scope 为 single 时 subjects 只能有 1 个")
    if answers.get("concept_count") and answers["concept_count"] < len(answers.get("subjects") or []):
        raise ProjectError("概念数量小于已确认的角色名单；先确认保留哪些角色，并在同一 set 更新 subjects，不能自动丢角色")
    if answers.get("subject_scope") in ("all", "selected") and len(answers.get("subjects") or []) > 1 \
            and not answers.get("concept_count"):
        answers["concept_count"] = len(answers["subjects"])
    restart = None
    for field, stage in (("product_form", "concept"), ("subjects", "concept"), ("concept_count", "concept"), ("note_type", "scene"), ("delivery", "scene")):
        if field in old_answers and old_answers.get(field) != answers.get(field):
            if restart is None or STAGES.index(stage) < STAGES.index(restart):
                restart = stage
    reset_note = ""
    if old_detail != (project.get("brief") or {}).get("product_detail"):
        restart = "concept"
    if restart and (project.get("rounds") or project.get("selections") or project.get("finals")):
        invalidate_from(project, restart)
        reset_note = f"；需求有变化，已回到 {restart}，旧图片仍保留但不再视为当前定稿"
    advance(project)
    save_project(root, project)
    return emit(status_payload(project, note="已写入" + reset_note))


def invalidate_from(project: dict[str, Any], stage: str) -> None:
    current = project.get("stage") or "concept"
    affected = set(STAGES[STAGES.index(stage):])
    chosen = project.setdefault("selections", {})
    for value in affected:
        chosen.pop(value, None)
    for entry in project.get("rounds") or []:
        if entry.get("stage") in affected:
            entry["status"] = "superseded"
    project["finals"] = []
    project.pop("post", None)
    project["revision"] = project.get("revision", 0) + 1
    project["stage"] = STAGES[min(STAGES.index(current), STAGES.index(stage))]


def cmd_accept_input(args: argparse.Namespace) -> int:
    root = Path(args.project).resolve()
    project = load_project(root)
    subject = args.subject or subjects_of(project)[0]
    if subject not in subjects_of(project):
        raise ProjectError("先把主体写入 answers.subjects")
    path = (root / args.path).resolve()
    if not path.is_relative_to(root) or not is_image(path):
        raise ProjectError("--path 必须是项目里可读取的图片")
    for prerequisite in STAGES[:STAGES.index(args.stage)]:
        if not stage_complete(project, prerequisite):
            raise ProjectError(f"先完成 {prerequisite}，再登记 {args.stage}；不能跳过已要求的确认")
    invalidate_from(project, STAGES[STAGES.index(args.stage) + 1])
    chosen = selections(project, args.stage)
    if subject in chosen and subject == subjects_of(project)[0]:
        chosen.clear()
    for entry in project.get("rounds") or []:
        if entry.get("stage") == args.stage and (entry.get("subject") == subject or not chosen):
            entry["status"] = "superseded"
    selections(project, args.stage)[subject] = {"round": "input", "item": "accepted", "image": path.relative_to(root).as_posix()}
    project["stage"] = args.stage
    advance(project)
    save_project(root, project)
    return emit(status_payload(project, note="已登记用户明确确认的输入图；没有调用生图"))


def stage_refs(project: dict[str, Any], stage: str, subject: str | None) -> dict[str, list[str]]:
    chosen = project.get("selections") or {}
    subjects = subjects_of(project)
    refs: dict[str, list[str]] = {}
    inputs = [item["path"] for item in project.get("inputs") or [] if item.get("path")]
    if stage in ("concept", "material", "cutout"):
        source = subject_ref(project, subject, before=stage) if subject else []
        refs["source"] = source or inputs
    if stage in PER_SUBJECT_STAGES and subject and subject != subjects[0]:
        first = (chosen.get(stage) or {}).get(subjects[0])
        if first:
            refs["style"] = [first["image"]]
    if stage == "scene":
        products = [path for name in subjects for path in subject_ref(project, name)]
        refs["source"] = products or inputs
        style = (chosen.get("scene") or {}).get("_")
        if style:
            refs["style"] = [style["image"]]
    return {key: value for key, value in refs.items() if value}


def subject_ref(project: dict[str, Any], subject: str, before: str = "scene") -> list[str]:
    """返回某个主体在 before 阶段之前最近一次选定的图。"""
    chosen = project.get("selections") or {}
    order = ("concept", "material", "cutout")
    earlier = [stage for stage in order if STAGES.index(stage) < STAGES.index(before)]
    for stage in reversed(earlier):
        pick = (chosen.get(stage) or {}).get(subject)
        if pick:
            return [pick["image"]]
    return []


def cmd_new_round(args: argparse.Namespace) -> int:
    root = Path(args.project)
    project = load_project(root)
    stage = args.stage
    if stage not in STAGES[:4]:
        raise ProjectError("--stage 只能是 concept、material、cutout、scene")
    subjects = subjects_of(project)
    subject = args.subject
    if stage in PER_SUBJECT_STAGES:
        subject = subject or next((name for name in subjects if name not in ((project.get("selections") or {}).get(stage) or {})), subjects[0])
        if subject not in subjects:
            raise ProjectError(f"--subject 必须是 {', '.join(subjects)} 之一；新角色先用 set 写入 answers.subjects")
    scene_style = ((project.get("selections") or {}).get("scene") or {}).get("_")
    kind = args.kind or ("set" if stage == "cutout" or (stage == "scene" and scene_style) else "alternatives")
    if stage in PER_SUBJECT_STAGES and kind != "alternatives":
        raise ProjectError("概念和材质用 alternatives 按角色逐个确认")
    if stage == "cutout" and kind != "set":
        raise ProjectError("透明资产用 set，每个角色一张；不支持 alternatives")
    for prerequisite in STAGES[:STAGES.index(stage)]:
        if not stage_complete(project, prerequisite):
            raise ProjectError(f"先完成 {prerequisite}，再开 {stage}；不能跳过已要求的确认")
    if stage == "scene" and kind == "set" and not scene_style:
        raise ProjectError("先确认关键场景方向，再出整组图")
    refs = stage_refs(project, stage, subject)
    if args.based_on:
        base_round, _, base_item = args.based_on.partition(":")
        base = find_round(project, base_round)
        base_data = read_json(root / base["dir"] / "round.json")
        match = next((item for item in base_data.get("items", []) if item.get("id") == base_item), None)
        if not match:
            raise ProjectError(f"{base_round} 里没有方向 {base_item}")
        refs["based_on"] = [f"{base['dir']}/{match['image']}"]
    items: list[dict[str, Any]] = []
    if kind == "alternatives":
        count = args.count or directions(project)
        if not 1 <= count <= MAX_DIRECTIONS:
            raise ProjectError(f"方向数必须在 1 到 {MAX_DIRECTIONS} 之间")
        for letter in LETTERS[:count]:
            items.append({"id": letter, "title": "", "idea": "", "prompt": "", "image": f"{letter}.png", "status": "planned"})
    else:
        if stage == "cutout":
            slots = [{"id": f"cutout-{index}", "role": "cutout", "subject": name} for index, name in enumerate(subjects, 1)]
            if not args.slots:
                chosen = (project.get("selections") or {}).get("cutout") or {}
                slots = [slot for slot in slots if slot["subject"] not in chosen] or slots
        else:
            slots = scene_slots(project)
        if args.slots:
            wanted = [value.strip() for value in args.slots.split(",") if value.strip()]
            known = {slot["id"]: slot for slot in slots}
            unknown = [value for value in wanted if value not in known]
            if unknown:
                raise ProjectError(f"未知的图位：{', '.join(unknown)}；可用：{', '.join(known)}")
            slots = [known[value] for value in wanted]
        for slot in slots:
            item = {**slot, "title": "", "idea": "", "prompt": "", "image": f"{slot['id']}.png", "status": "planned"}
            if slot.get("subject"):
                item["refs"] = subject_ref(project, slot["subject"])
            items.append(item)
    rounds = project.setdefault("rounds", [])
    suffix = f"-s{subjects.index(subject) + 1}" if stage in PER_SUBJECT_STAGES and subject else ""
    if stage == "scene" and kind == "set":
        suffix = "-set"
    round_id = f"{len(rounds) + 1:02d}-{stage}{suffix}"
    round_dir = root / "rounds" / round_id
    if round_dir.exists() and any(round_dir.iterdir()):
        raise ProjectError(f"{round_dir.as_posix()} 已存在且不为空，没有覆盖")
    if stage in PER_SUBJECT_STAGES:
        chosen = selections(project, stage)
        replacing = subject in chosen
        if replacing:
            invalidate_from(project, STAGES[STAGES.index(stage) + 1])
            chosen = selections(project, stage)
            if subject == subjects[0]:
                chosen.clear()
                for entry in rounds:
                    if entry.get("stage") == stage:
                        entry["status"] = "superseded"
            else:
                chosen.pop(subject, None)
    elif stage == "cutout":
        chosen = selections(project, "cutout")
        if any(item["subject"] in chosen for item in items):
            invalidate_from(project, "scene")
            for item in items:
                chosen.pop(item["subject"], None)
    elif stage == "scene":
        if kind == "alternatives":
            invalidate_from(project, "scene")
        else:
            wanted = {item["id"] for item in items}
            project["finals"] = [final for final in mapped_scene_finals(project) if final.get("slot") not in wanted]
            project.pop("post", None)
    for entry in rounds:
        if entry.get("status") in ("planned", "recorded"):
            entry["status"] = "superseded"
    data = {
        "schema_version": SCHEMA_VERSION,
        "id": round_id,
        "stage": stage,
        "subject": subject,
        "kind": kind,
        "based_on": args.based_on,
        "note": args.note or "",
        "created_at": now(),
        "refs": refs,
        "requirements": requirement_signature(project, stage),
        "items": items,
    }
    write_json(round_dir / "round.json", data)
    rounds.append({"id": round_id, "stage": stage, "subject": subject, "kind": kind, "dir": f"rounds/{round_id}", "status": "planned"})
    project["stage"] = stage
    save_project(root, project)
    return emit({
        "ok": True,
        "round": round_id,
        "round_file": f"rounds/{round_id}/round.json",
        "kind": kind,
        "subject": subject,
        "refs": refs,
        "items": [item["id"] for item in items],
        "say": "填写每项的 title、idea、prompt，生图后保存为 image 字段的文件名，再运行 record-round",
    })


def is_image(path: Path) -> bool:
    try:
        head = path.read_bytes()[:12]
    except OSError:
        return False
    return head.startswith(IMAGE_MAGIC[:2]) or (head.startswith(b"RIFF") and head[8:12] == b"WEBP")


def cmd_record_round(args: argparse.Namespace) -> int:
    root = Path(args.project)
    project = load_project(root)
    entry = find_round(project, args.round)
    if entry.get("status") not in ("planned", "recorded"):
        raise ProjectError("这轮已通过或已失效；重做请建新轮，不能重新激活旧轮")
    round_file = root / entry["dir"] / "round.json"
    data = read_json(round_file)
    if data.get("requirements") and data["requirements"] != requirement_signature(project, entry["stage"]):
        raise ProjectError("这轮需求已变化；先建立当前需求的新轮")
    items = data.get("items") or []
    problems = []
    for item in items:
        method = item.get("method", "generate")
        if method not in METHODS:
            problems.append(f"{item.get('id')} 的 method 只能是 {', '.join(METHODS)}")
            continue
        if not str(item.get("title", "")).strip():
            problems.append(f"{item.get('id')} 缺少 title")
        if method != "generate":
            if not item.get("sources"):
                problems.append(f"{item.get('id')} 用 {method} 时要在 sources 里写明来源图")
        elif len(str(item.get("prompt", "")).strip()) < MIN_PROMPT_CHARS:
            problems.append(f"{item.get('id')} 的 prompt 为空或少于 {MIN_PROMPT_CHARS} 个字符")
    if data.get("kind") == "alternatives":
        for index, first in enumerate(items):
            for second in items[index + 1:]:
                if not first.get("prompt") or not second.get("prompt"):
                    continue
                ratio = difflib.SequenceMatcher(None, str(first.get("prompt")), str(second.get("prompt"))).ratio()
                if ratio == 1:
                    problems.append(f"{first.get('id')} 和 {second.get('id')} 的提示词相同，不能只换文件名")
    if problems:
        raise ProjectError("；".join(problems))
    missing = []
    for item in items:
        path = root / entry["dir"] / item["image"]
        if path.is_file():
            if not is_image(path):
                raise ProjectError(f"{item['image']} 不是 PNG、JPEG 或 WEBP 图片")
            if entry["stage"] == "scene":
                require_physics(root, project, [f"{entry['dir']}/{item['image']}"])
            item["status"] = "generated"
        else:
            item["status"] = "missing"
            missing.append(item["image"])
    if missing and not args.allow_missing:
        raise ProjectError(f"缺少图片：{', '.join(missing)}。生图不可用时加 --allow-missing，对比页会显示占位，不要伪造图片")
    write_json(round_file, data)
    entry["status"] = "recorded"
    entry["missing"] = missing
    save_project(root, project)
    payload = status_payload(project, note=f"已登记 {entry['id']}，缺图 {len(missing)} 张")
    payload["say"] = "运行 build_gallery.py，然后回复对比页路径并问三选一"
    return emit(payload)


def cmd_select(args: argparse.Namespace) -> int:
    root = Path(args.project)
    project = load_project(root)
    entry = find_round(project, args.round)
    if entry.get("status") != "recorded":
        raise ProjectError("先运行 record-round 校验这一轮；旧轮或尚未登记的轮次不能直接选定")
    data = read_json(root / entry["dir"] / "round.json")
    if data.get("requirements") and data["requirements"] != requirement_signature(project, entry["stage"]):
        raise ProjectError("这轮对应的需求已变化，不能采用旧图")
    items = {item["id"]: item for item in data.get("items") or []}
    stage = entry["stage"]
    if entry.get("kind") == "alternatives":
        if not args.item or args.approve:
            raise ProjectError("多方向轮次要指定一个方向，例如 select <项目> <轮次> A")
        item = items.get(args.item)
        if not item:
            raise ProjectError(f"{entry['id']} 里没有方向 {args.item}")
        image = f"{entry['dir']}/{item['image']}"
        if not (root / image).is_file():
            raise ProjectError(f"{image} 还没有生成，不能选定")
        if stage == "scene":
            require_physics(root, project, [f"{entry['dir']}/{candidate['image']}" for candidate in items.values()
                                           if (root / entry["dir"] / candidate["image"]).is_file()])
        key = entry.get("subject") if stage in PER_SUBJECT_STAGES else "_"
        selections(project, stage)[key] = {"round": entry["id"], "item": item["id"], "image": image}
        entry["status"] = "picked"
        entry["pick"] = item["id"]
    else:
        if not args.approve:
            raise ProjectError("整组轮次用 --approve 通过，可以加 --skip 列出要重出的图位")
        skip = {value.strip() for value in (args.skip or "").split(",") if value.strip()}
        unknown = skip - set(items)
        if unknown:
            raise ProjectError(f"未知的图位：{', '.join(sorted(unknown))}")
        approved = [item for key, item in items.items() if key not in skip]
        missing = [item["image"] for item in approved if not (root / entry["dir"] / item["image"]).is_file()]
        if missing:
            raise ProjectError(f"这些图还没有生成，不能通过：{', '.join(missing)}")
        if stage == "cutout":
            import check_images
            import imaging

            checks = argparse.Namespace(alpha=True, ratio=None, tolerance=0.02, min_side=0)
            try:
                invalid = [item["image"] for item in approved if not check_images.inspect(root / entry["dir"] / item["image"], checks)["ok"]]
            except imaging.ImagingError as error:
                raise ProjectError(str(error)) from error
            if invalid:
                raise ProjectError(f"这些图没有通过真透明检查：{', '.join(invalid)}；先修复再登记")
            for item in approved:
                selections(project, "cutout")[item["subject"]] = {"round": entry["id"], "item": item["id"], "image": f"{entry['dir']}/{item['image']}"}
        else:
            require_physics(root, project, [f"{entry['dir']}/{item['image']}" for item in approved])
            finals = mapped_scene_finals(project)
            project["finals"] = finals
            for item in approved:
                record = {"role": item.get("role", "scene"), "slot": item["id"], "path": f"{entry['dir']}/{item['image']}"}
                if item.get("subject"):
                    record["subject"] = item["subject"]
                finals[:] = [final for final in finals if final.get("slot") != item["id"]]
                finals.append(record)
            order = [slot["id"] for slot in scene_slots(project)]
            finals.sort(key=lambda final: order.index(final["slot"]) if final.get("slot") in order else len(order))
            scene = selections(project, "scene")
            scene["_set"] = entry["id"]
            scene["_pending"] = missing_scene_slots(project)
        entry["status"] = "approved" if not skip else "partly-approved"
        entry["skipped"] = sorted(skip)
    advance(project)
    save_project(root, project)
    return emit(status_payload(project, note="已记录选择"))


def cmd_final(args: argparse.Namespace) -> int:
    root = Path(args.project)
    project = load_project(root)
    if not (root / args.path).is_file():
        raise ProjectError(f"{args.path} 不存在；路径相对于项目目录")
    require_physics(root, project, [args.path])
    record = {"role": args.role, "path": Path(args.path).as_posix()}
    if args.subject:
        record["subject"] = args.subject
    if args.slot:
        record["slot"] = args.slot
    if args.note:
        record["note"] = args.note
    finals = mapped_scene_finals(project) if args.slot else project.setdefault("finals", [])
    project["finals"] = finals
    finals[:] = [item for item in finals if item.get("path") != record["path"] and not (args.slot and item.get("slot") == args.slot)]
    finals.append(record)
    project.pop("post", None)
    if (project.get("answers") or {}).get("note_type"):
        project["stage"] = "package" if not missing_scene_slots(project) else "scene"
    save_project(root, project)
    return emit(status_payload(project, note="已登记定稿图"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="jarden-ip-visual 项目状态")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="建立项目；已存在时只读取")
    init.add_argument("project")
    init.add_argument("--input", action="append", help="输入图片，可重复")
    init.add_argument("--kind", default="reference", help="输入类型：" + "、".join(sorted(INPUT_KINDS)))
    init.add_argument("--name")
    init.add_argument("--reuse-from", help="同一会话里上一个项目的目录，沿用它的通用答案和生图设置")
    init.set_defaults(func=cmd_init)
    show = sub.add_parser("show", help="查看状态和下一步")
    show.add_argument("project")
    show.set_defaults(func=cmd_show)
    setter = sub.add_parser("set", help="写入答案或设置，例如 answers.product_form=plush")
    setter.add_argument("project")
    setter.add_argument("pairs", nargs="+")
    setter.set_defaults(func=cmd_set)
    accept = sub.add_parser("accept-input", help="登记用户已明确确认的概念或材质输入图，不调用生图")
    accept.add_argument("project")
    accept.add_argument("--stage", required=True, choices=PER_SUBJECT_STAGES)
    accept.add_argument("--subject")
    accept.add_argument("--path", required=True, help="项目内的图片路径")
    accept.set_defaults(func=cmd_accept_input)
    new = sub.add_parser("new-round", help="开新一轮")
    new.add_argument("project")
    new.add_argument("--stage", required=True)
    new.add_argument("--subject")
    new.add_argument("--kind", choices=("alternatives", "set"))
    new.add_argument("--count", type=int)
    new.add_argument("--slots", help="整组轮次只出这些图位，逗号分隔")
    new.add_argument("--based-on", help="在某个方向上继续出图，格式 轮次:方向")
    new.add_argument("--note")
    new.set_defaults(func=cmd_new_round)
    record = sub.add_parser("record-round", help="校验并登记一轮")
    record.add_argument("project")
    record.add_argument("round")
    record.add_argument("--allow-missing", action="store_true")
    record.set_defaults(func=cmd_record_round)
    select = sub.add_parser("select", help="记录用户的选择")
    select.add_argument("project")
    select.add_argument("round")
    select.add_argument("item", nargs="?")
    select.add_argument("--approve", action="store_true")
    select.add_argument("--skip", help="整组轮次里不通过、要重出的图位")
    select.set_defaults(func=cmd_select)
    final = sub.add_parser("final", help="手动登记定稿图")
    final.add_argument("project")
    final.add_argument("--role", required=True)
    final.add_argument("--path", required=True)
    final.add_argument("--subject")
    final.add_argument("--slot")
    final.add_argument("--note")
    final.set_defaults(func=cmd_final)
    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 10):
        print("需要 Python 3.10 或更高版本", file=sys.stderr)
        return 2
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ProjectError as error:
        return emit({"ok": False, "error": str(error)})


if __name__ == "__main__":
    sys.exit(main())
