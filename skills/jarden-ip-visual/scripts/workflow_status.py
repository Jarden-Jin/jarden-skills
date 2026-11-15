"""Read-only workflow navigation derived from current project and ledger evidence."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asset_workflow
import generation_tasks
import project as state


PHASES = (
    ("intake", "理解需求"), ("scope", "选择资产"), ("create", "设计制作"),
    ("review", "质量审查"), ("confirm", "确认方案"), ("deliver", "整理交付"),
)
READ_ERRORS = (state.ProjectError, OSError, ValueError, KeyError, TypeError, AttributeError)
ACTIONS = {
    "choose-assets": ("scope", "waiting", "user", "待选择资产"),
    "ask": ("intake", "waiting", "user", "待补充需求"),
    "migration": ("intake", "blocked", "user", "旧项目待迁移"),
    "verify-assets": ("review", "blocked", "agent", "资产待核验"),
    "contract": ("scope", "waiting", "agent", "产品约定待补"),
    "confirm-contract": ("scope", "waiting", "user", "产品约定待确认"),
    "scene-plan": ("scope", "idle", "agent", "镜头计划待核对"),
    "backend": ("create", "blocked", "system", "生图能力待配置"),
    "resolve-intent": ("scope", "waiting", "agent", "创作意向待核对"),
    "new-asset": ("create", "idle", "agent", "待创建资产"),
    "round": ("create", "idle", "agent", "待规划当前轮"),
    "generate": ("create", "idle", "agent", "本轮待生成"),
    "record-asset": ("create", "idle", "agent", "资产待生成或登记"),
    "record-round": ("review", "idle", "agent", "本轮待登记"),
    "review-asset": ("review", "waiting", "agent", "候选待审查"),
    "review": ("review", "waiting", "agent", "实际文件待审查"),
    "approve-asset": ("confirm", "waiting", "user", "候选待采用"),
    "await-choice": ("confirm", "waiting", "user", "本轮待选择"),
    "reconcile-generation": ("create", "blocked", "agent", "生成回执待对账"),
    "reconcile-task": ("create", "blocked", "agent", "生成回执待对账"),
    "blocked-assets": ("create", "blocked", "user", "资产范围存在阻塞"),
    "export-assets": ("deliver", "idle", "agent", "已采用，待导出"),
    "package": ("deliver", "idle", "agent", "发布包待整理"),
    "done": ("deliver", "complete", "none", "当前交付已完成"),
}
DETAILS = {
    "choose-assets": "选择这次需要制作的资产，确认范围后继续。",
    "migration": "项目目前保留为只读状态，回到聊天中核对兼容方式后继续。",
    "verify-assets": "当前资产的文件与审查信息尚未核对，回到聊天后继续。",
    "contract": "本轮作品的用途、尺度或外观约定待补充，已有信息会保留。",
    "confirm-contract": "确认已记录的用途与尺度，或在聊天中补充修改。",
    "scene-plan": "当前镜头安排待核对，确认需要保留与新增的画面后继续。",
    "backend": "图片能力尚未就绪，回到聊天后由 Agent 核对并继续。",
    "resolve-intent": "修改或补图意向已记录，等待明确本轮调整范围。",
    "new-asset": "所选资产待开始制作，将按已确认范围逐项推进。",
    "round": "待按当前范围规划下一轮设计，仅补尚未完成的主体或图位。",
    "generate": "已保存本轮计划，等待开始制作。",
    "record-asset": "已安排制作，等待文件返回与登记。",
    "record-round": "本轮文件已保存，等待检查并登记。",
    "review-asset": "候选文件已登记，等待完成质量审查。",
    "review": "当前文件或审查记录待核对，确认通过后继续。",
    "approve-asset": "比较合格候选，确认采用的方案或在聊天中提出修改。",
    "await-choice": "比较本轮候选，选一个继续或在聊天提出修改。",
    "blocked-assets": "所选资产存在暂无法完成的项目，回到聊天中确认调整范围、延期或交接。",
    "package": "待整理已采用图片与文案，核验实际发布集合后交付。",
    "export-assets": "当前所选资产均已有有效采用记录，待整理并导出对应交付文件。",
    "done": "当前所选内容已整理完成，可以查看交付文件。",
    "reconcile-generation": "已有生成调用的结果尚未确认；先核对工具回执和实际文件，再决定下一步。",
    "reconcile-task": "已有生成调用的结果尚未确认；先核对工具回执和实际文件，再决定下一步。",
    "verify-state": "当前文件或状态记录尚未核验；先补齐对应证据，再继续本轮工作。",
}


def _detail(action: dict[str, Any]) -> str:
    if action.get("action") == "ask":
        fields = {"answers.product_form": "产品形态", "answers.subject_scope": "主体范围",
                  "answers.concept_count": "角色或设计数量", "answers.note_type": "展示方式",
                  "answers.delivery": "交付形式"}
        field = action.get("field") or ""
        label = fields.get(field) or ("作品用途" if field.startswith("product.usage:") else
                                      "设计尺度" if field.startswith("product.size:") else "本轮需求")
        return f"待确认{label}；已有说明可直接沿用，缺少的信息在聊天中补充。"
    return DETAILS.get(action.get("action")) or "当前进度待核对，回到聊天后继续。"


def _time_key(value: Any) -> float:
    if not isinstance(value, str):
        return float("-inf")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()
    except (ValueError, OverflowError):
        return float("-inf")


def _latest(values: list[Any]) -> str | None:
    recorded = [value for value in values if _time_key(value) != float("-inf")]
    return max(recorded, key=_time_key) if recorded else None


def _blocker(identifier: str, label: str, detail: str) -> dict[str, str]:
    return {"id": identifier, "label": label, "detail": detail}


def _round_label(entry: dict[str, Any]) -> str:
    label = {"concept": "概念方向", "material": "材质方向", "cutout": "透明素材",
             "scene": "场景设计", "package": "发布素材"}.get(entry.get("stage"), "设计轮次")
    return label + (" · " + str(entry["subject"]) if entry.get("subject") else "")


def _asset_evidence(view: dict[str, Any]) -> dict[str, Any]:
    rows = (view.get("plan") or {}).get("execution") or []
    deliverables = (view.get("plan") or {}).get("deliverables") or []
    instances = view.get("instances") or []
    tasks, blockers = [], []
    active_keys = {(row["asset_id"], row["subject"]) for row in rows if not row["reuse_instance"]}
    bindings = {item["generation_task"]: {"label": f"资产{item['asset_id']} · {item['subject']} · {item['candidate']}",
                    "active": (item["asset_id"], item["subject"]) in active_keys and item.get("disposition") != "not-selected"}
                for item in instances if item.get("generation_task")}
    created, reviewed = [], []
    for row in rows:
        candidates = [item for item in instances if item["asset_id"] == row["asset_id"]
                      and item["subject"] == row["subject"] and item.get("disposition") != "not-selected"]
        qualified = [item for item in candidates if (item.get("eligibility") or {}).get("approvable")]
        recorded = [item for item in candidates if item.get("files")]
        created.append(bool(row["reuse_instance"] or any((item.get("eligibility") or {}).get("selectable") for item in candidates)))
        reviewed.append(bool(row["reuse_instance"] or qualified))
        label = f"资产{row['asset_id']} · {row['name']} · {row['subject']}"
        identifier = f"asset-{row['asset_id']}-{row['subject']}"
        detail = row.get("blocked_reason") or ""
        status = "idle"
        if row["reuse_instance"]:
            status, detail = "complete", "当前文件、来源、审查与用户采用仍有效"
        elif detail:
            status = "blocked"
        elif qualified:
            status, detail = "waiting", "已有合格候选，等待用户采用"
        elif recorded:
            reasons = list(dict.fromkeys((item.get("eligibility") or {}).get("disabled_reason") or "候选审查待补" for item in recorded))
            invalid = any(not (item.get("eligibility") or {}).get("selectable") or item.get("status") in {"approved", "needs_revision"}
                          for item in recorded)
            status, detail = ("blocked" if invalid else "waiting"), "；".join(reasons)
        elif row.get("missing_dependencies"):
            status, detail = "waiting", "待先采用依赖资产 " + "、".join(row["missing_dependencies"])
        else:
            detail = "尚无实际文件；计划和下一步建议不代表任务已运行"
        updated_at = _latest([stamp for item in candidates for stamp in
                              (item.get("created_at"), item.get("recorded_at"), (item.get("approval") or {}).get("created_at"),
                               *(review.get("recorded_at") for review in item.get("review_history") or []))])
        tasks.append({"id": identifier, "label": label, "status": status, "updated_at": updated_at,
                      "kind": "asset", "actor": "none" if row["reuse_instance"] else "user" if qualified else "agent",
                      "detail": detail, "asset_id": row["asset_id"], "subject": row["subject"]})
        if status == "blocked":
            blockers.append(_blocker(identifier, label, detail))
    approved = sum(bool(row["reuse_instance"]) for row in deliverables)
    complete = bool(deliverables) and approved == len(deliverables)
    return {
        "tasks": tasks, "blockers": blockers, "bindings": bindings,
        "counts": {"selected": len(deliverables), "approved": approved, "pending": len(deliverables) - approved},
        "evidence": {"scope": bool(view.get("selected")), "create": bool(rows) and all(created),
                     "review": bool(rows) and all(reviewed), "confirm": complete,
                     "deliver": complete and (view.get("completion") or {}).get("complete") is True},
    }


def _legacy_evidence(root: Path, project: dict[str, Any], action: dict[str, Any],
                     gallery_view: dict[str, Any] | None) -> dict[str, Any]:
    stage = project.get("stage") or "concept"
    answers = project.get("answers") or {}
    scope_known = all(answers.get(field) not in (None, "", []) for field in ("product_form", "subject_scope", "concept_count"))
    entry = state.open_round(project)
    tasks, blockers = [], []
    current_created = False
    gates: dict[str, dict[str, Any]] = {}
    data_by_round: dict[str, dict[str, Any]] = {}

    def round_data(round_entry: dict[str, Any]) -> dict[str, Any]:
        if round_entry["id"] not in data_by_round:
            data_by_round[round_entry["id"]] = state.read_json(state.project_path(root, round_entry["dir"] + "/round.json"))
        return data_by_round[round_entry["id"]]

    def eligible(round_entry: dict[str, Any]) -> dict[str, Any]:
        if round_entry["id"] not in gates:
            gates[round_entry["id"]] = state.round_eligibility(root, project, {**round_entry, "status": "recorded"})
        return gates[round_entry["id"]]

    if entry:
        current = (gallery_view or {}).get("current") or {}
        if current.get("round_id") == entry["id"]:
            actual = current.get("items") or []
        else:
            data = round_data(entry)
            allowed = eligible(entry) if entry["status"] == "recorded" else {}
            actual = [{"id": item["id"], "exists": bool(item.get("image")) and state.project_path(root, entry["dir"] + "/" + item["image"]).is_file(),
                       **(allowed.get(item["id"]) or {})} for item in data.get("items") or []]
        ready = bool(actual) and any(item.get("selectable") for item in actual)
        current_created = ready
        missing = any(not item.get("exists") for item in actual)
        blocked = entry["status"] == "recorded" and not ready
        detail = "等待用户选择当前合格候选" if ready else "本轮仍待生成实际文件" if entry["status"] == "planned" and missing else "实际文件或当前审查待核对"
        tasks.append({"id": entry["id"], "label": _round_label(entry), "kind": "round",
                      "actor": "user" if ready else "agent",
                      "status": "blocked" if blocked else "waiting" if ready else "idle",
                      "detail": detail, "updated_at": entry.get("updated_at") or entry.get("recorded_at") or entry.get("created_at")})
        if blocked:
            blockers.append(_blocker(entry["id"], "本轮候选尚不可采用", detail))
        if action.get("action") == "await-choice" and not ready:
            action.update(action="review", say="本轮缺少可采用的实际图片或当前有效审查；先核对对应文件和报告。")
    selected = approved = 0
    reviewed = False
    if scope_known and stage in {"concept", "material", "cutout"}:
        subjects = state.subjects_of(project)
        selected = len(subjects)
        picks = ((project.get("selections") or {}).get(stage) or {})
        for subject in subjects:
            pick = picks.get(subject) or {}
            if not pick.get("image"):
                continue
            try:
                source = state.find_round(project, pick["round"])
                data = round_data(source)
                item = next(item for item in data.get("items") or [] if item["id"] == pick.get("item"))
                matches = (source.get("stage") == stage and source.get("status") in {"picked", "approved", "partly-approved"}
                           and pick["image"] == source["dir"] + "/" + item["image"]
                           and (source.get("subject") == subject if stage in state.PER_SUBJECT_STAGES else item.get("subject") == subject)
                           and bool(data.get("requirements"))
                           and data["requirements"] == state.requirement_signature(project, stage, source.get("subject"))
                           and eligible(source).get(item["id"], {}).get("selectable"))
                if not matches:
                    raise state.ProjectError("已选图与当前需求、实际文件或审查不一致")
                approved += 1
            except READ_ERRORS + (StopIteration,) as error:
                blockers.append(_blocker(f"selection-{stage}-{subject}", f"{subject} 的已选图待核对", str(error)))
    elif scope_known and project.get("scene_plan"):
        slots = {slot["id"] for slot in state.scene_slots(project)}
        selected = len(slots)
        finals = [final for final in project.get("finals") or [] if final.get("slot") in slots]
        if finals:
            try:
                paths = [final["path"] for final in finals]
                if len(set(paths)) != len(paths) or len({final["slot"] for final in finals}) != len(finals):
                    raise state.ProjectError("定稿图位或实际文件重复绑定")
                if action.get("action") != "done":
                    import contract_review
                    import review_checks
                    state.require_generation_paths(root, project, paths)
                    state.require_physics(root, project, paths)
                    gate = contract_review.status(root, project, paths, "scene",
                                                  ordered_slots=state.review_mappings(root, project, paths, "scene"))
                    reviews = review_checks.blocking_findings(root, project, paths, "scene")
                    if gate.get("blocking") or reviews.get("blocking"):
                        raise state.ProjectError("当前定稿文件或必要的独立审查未通过；旧采用记录不能代替当前核验")
                approved = len(finals)
                reviewed = approved == selected
            except READ_ERRORS as error:
                blockers.append(_blocker("finals-unverified", "定稿文件或审查待核对", str(error)))
    all_approved = bool(selected) and approved == selected
    return {"tasks": tasks, "blockers": blockers, "bindings": {},
            "counts": {"selected": selected, "approved": approved, "pending": selected - approved},
            "evidence": {"scope": scope_known, "create": all_approved or current_created, "review": reviewed,
                         "confirm": all_approved, "deliver": all_approved and action.get("action") == "done"}}


def _ledger(root: Path, bindings: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        directory = generation_tasks._scoped(root, "tasks")
        identifiers = set(bindings) | {path.stem for path in directory.glob("gen-*.json")}
    except READ_ERRORS as error:
        return [{"id": "generation-ledger", "label": "生成账本", "kind": "generation", "status": "unknown",
                 "updated_at": None, "active": True, "detail": str(error)}]
    rows = []
    for identifier in sorted(identifiers):
        binding = bindings.get(identifier) or {}
        row = {"id": identifier, "label": binding.get("label") or "生成任务 · " + identifier[:16],
               "kind": "generation", "status": "not_started", "updated_at": None,
               "active": binding.get("active", True), "detail": "尚未领取；计划不代表正在生成"}
        try:
            task = generation_tasks._load_task(root, identifier)
            if task:
                row.update(status=task["status"], updated_at=task.get("updated_at"),
                           detail=task.get("reason") or {"requesting": "账本已领取请求；等待实际工具回执",
                               "unknown": "调用结果未知；核对已有回执与文件，不能自动重发",
                               "failed": "生成请求失败，需核对失败原因和重试资格",
                               "completed": "生成回执已完成；实际文件登记、审查和用户采用另行核验"}[task["status"]])
        except READ_ERRORS as error:
            row.update(status="unknown", detail="生成账本待核对：" + str(error))
        rows.append(row)
    return rows


def _events(project: dict[str, Any], ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = []

    def add(identifier: str, label: str, at: Any) -> None:
        if _time_key(at) != float("-inf"):
            events.append({"id": identifier, "label": label, "at": at})

    add("project-created", "项目已建立", project.get("created_at"))
    workflow = project.get("asset_workflow") or {}
    for index, choice in enumerate(workflow.get("choices") or []):
        add(f"scope-{index}", "已选择资产 " + "、".join(choice.get("selected") or []), choice.get("created_at"))
    for item in workflow.get("instances") or []:
        label = f"资产{item['asset_id']} · {item.get('candidate', '')}"
        add(item["id"] + "-planned", label + " 已建计划", item.get("created_at"))
        add(item["id"] + "-recorded", label + " 已登记文件", item.get("recorded_at"))
        for index, review in enumerate(item.get("review_history") or []):
            verdict = {"pass": "通过", "needs_revision": "需修改", "uncertain": "待核对", "fail": "未通过"}.get(review.get("verdict"), "待核对")
            add(f"{item['id']}-review-{index}", label + " 审查记录 · " + verdict, review.get("recorded_at"))
        add(item["id"] + "-adopted", label + " 已记录采用", (item.get("approval") or {}).get("created_at"))
    for index, exported in enumerate(workflow.get("exports") or []):
        add(f"export-{index}", "已记录资产导出", exported.get("created_at"))
    for entry in project.get("rounds") or []:
        add("round-" + entry["id"], "已建立" + _round_label(entry), entry.get("created_at"))
    for identifier, receipt in (project.get("decision_receipts") or {}).items():
        if receipt.get("committed"):
            action_label = {"choose-assets": "资产范围", "answer-field": "需求回答", "select": "方案选择",
                            "approve-asset": "资产采用", "approve-set": "整套采用", "asset-intent": "修改意向"}.get(receipt.get("action"), "用户决定")
            add("decision-" + identifier, "已提交选择 · " + action_label, receipt.get("updated_at"))
    for task in ledger:
        task_status = {"not_started": "尚未开始", "requesting": "请求已发出", "unknown": "结果待对账",
                       "completed": "文件已返回", "failed": "请求失败", "output_invalid": "输出待核对"}.get(task["status"], "状态待核对")
        add(task["id"] + "-" + task["status"], task["label"] + " · " + task_status, task["updated_at"])
    return sorted(events, key=lambda event: (_time_key(event["at"]), event["id"]), reverse=True)[:10]


def build_status(root: Path, project: dict[str, Any], *, next_action: dict[str, Any] | None = None,
                 asset_view: dict[str, Any] | None = None,
                 gallery_view: dict[str, Any] | None = None) -> dict[str, Any]:
    """Project evidence only; never claim, lock, save, present, or cache by revision."""
    root = Path(root).resolve()
    action = copy.deepcopy(next_action) if next_action is not None else None
    try:
        if asset_workflow.is_enabled(project):
            view = asset_view if asset_view is not None else asset_workflow.view(root, project)
            action = action if action is not None else copy.deepcopy(view["next"])
            summary = _asset_evidence(view)
        else:
            action = action if action is not None else state.next_action(copy.deepcopy(project), root)
            summary = _legacy_evidence(root, project, action, gallery_view)
    except READ_ERRORS as error:
        action = {"action": "verify-state", "say": "实际文件或状态记录待核对：" + str(error)}
        workflow = project.get("asset_workflow") or {}
        selected = len(workflow.get("selected") or []) * len(workflow.get("subjects") or ["主体"])
        summary = {"tasks": [], "bindings": {}, "blockers": [_blocker("state-unverified", "当前状态待核对", action["say"])],
                   "counts": {"selected": selected, "approved": 0, "pending": selected}, "evidence": {}}
    phase, status, actor, title = ACTIONS.get(action.get("action"), ("review", "blocked", "agent", "当前状态待核对"))
    detail = _detail(action)
    blockers = summary["blockers"]
    if action.get("action") == "done" and not summary["evidence"].get("deliver"):
        phase, status, actor, title = "review", "blocked", "agent", "交付证据待核对"
        detail = "历史完成标记不足以证明当前文件、审查、采用和导出仍有效。"
    if any(task["status"] == "blocked" for task in summary["tasks"]) and action.get("action") in {"review", "review-asset"}:
        status, title = "blocked", "候选待核对或修复"
    ledger = _ledger(root, summary["bindings"])
    active = [task for task in ledger if task["active"]]
    uncertain = [task for task in active if task["status"] == "unknown"]
    requesting = [task for task in active if task["status"] == "requesting"]
    failures = [task for task in active if task["status"] == "failed"]
    for task in uncertain:
        blockers.append(_blocker(task["id"], "生成回执待对账", task["detail"]))
    if uncertain:
        phase, status, actor, title = "create", "blocked", "agent", "生成回执待对账"
        detail = DETAILS["reconcile-generation"]
    elif requesting:
        phase, status, actor, title = "create", "working", "agent", "生成请求进行中"
        detail = "已领取生成请求，正在等待图片工具返回结果。"
    elif failures and action.get("action") in {"generate", "record-asset", "reconcile-task", "reconcile-generation"}:
        phase, status, actor, title = "create", "blocked", "agent", "生成请求失败"
        detail = "生成请求已明确失败；先核对失败原因和已有文件，再确认是否重试。"
        blockers.extend(_blocker(task["id"], "生成请求失败", task["detail"]) for task in failures)
    if status == "blocked" and not blockers:
        blockers.append(_blocker(action.get("action") or "state-unverified", title, detail))
    evidence = summary["evidence"]
    brief = project.get("brief") or {}
    evidence["intake"] = bool(project.get("inputs") or brief.get("ip_name") or brief.get("bible") or project.get("answers"))
    labels = {"intake": "已有项目输入或明确需求", "scope": "已有本轮明确范围",
              "create": "当前范围已有可核验的实际文件", "review": "当前范围已有有效的候选审查",
              "confirm": "当前范围已有有效采用记录", "deliver": "当前交付文件及导出已核验"}
    phases = [{"id": identifier, "label": label,
               "status": ("done" if status == "complete" else "blocked" if status == "blocked" else "current") if identifier == phase
                         else "done" if evidence.get(identifier) else "pending",
               "detail": detail if identifier == phase else labels[identifier] if evidence.get(identifier) else "暂无当前完成证据"}
              for identifier, label in PHASES]
    current_label = ""
    if action.get("asset_id"):
        current_label = "资产" + str(action["asset_id"])
    elif action.get("round"):
        matching = next((entry for entry in project.get("rounds") or [] if entry.get("id") == action["round"]), None)
        current_label = _round_label(matching) if matching else "当前设计轮次"
    elif (project.get("asset_workflow") or {}).get("rounds"):
        current_label = "资产" + project["asset_workflow"]["rounds"][-1]["asset_id"]
    if uncertain or requesting:
        current_label = (uncertain or requesting)[0]["label"]
    try:
        recent_events = _events(project, ledger)
    except READ_ERRORS:
        recent_events = []
    if project.get("live_photo"):
        import live_photo_workflow
        live_view = live_photo_workflow.view(root, project)
        live_jobs = live_view.get("jobs") or []
        live_job = live_view.get("latest") or live_view.get("current") or (live_jobs[-1] if live_jobs else None)
        if live_job:
            live_states = {
                "planned": ("create", "idle", "agent", "Live 样片待制作", "微动态计划已保存，等待官方视频生成或登记已有视频。"),
                "requesting": ("create", "working", "agent", "Live 视频请求进行中", "等待已授权的视频请求返回，不重复提交。"),
                "unknown": ("create", "blocked", "agent", "Live 生成回执待对账", "先核对已有视频节点和回执，不重新发送计费任务。"),
                "failed": ("create", "blocked", "agent", "Live 视频请求失败", "已有失败回执，先核对账户资格和原因；不自动重发计费请求。"),
                "video-recorded": ("deliver", "idle", "agent", "Live 配对待封装", "视频已登记，核对动作后封装静态照片与动态 MOV。"),
                "motion-review": ("review", "waiting", "agent", "Live 动作待核对", "检查五官、绒毛、轮廓及正常速度播放，未通过不能封装。"),
                "packaged": ("confirm", "waiting", "user", "Live 已配对，待图库验收", "格式校验不等于图库播放；导入照片 App 后核对实况和实际回放。"),
                "macos-verified": ("deliver", "complete", "none", "Mac 实况已验收", "Mac Photos 已确认实况播放；iPhone 和发布应用需分别验收。"),
                "iphone-verified": ("deliver", "complete", "none", "iPhone 实况已验收", "具体手机已确认实况与长按播放，发布应用仍需单独检查。"),
                "publishing-app-verified": ("deliver", "complete", "none", "实况目标已验收", "已记录指定发布应用识别，不代表自动发布或其他平台也通过。"),
                "stale": ("review", "blocked", "agent", "Live 文件变化，需重验", "保留原交付，重新核对来源、视频、配对及各目标验收。"),
            }
            phase, status, actor, title, detail = live_states.get(live_job.get("status"), ("review", "blocked", "agent", "Live 任务待恢复", "保留来源和实际文件，核对错误后从已完成步骤继续。"))
            current_label = "Live Photo"
            phases = [{**entry, "status": "done" if status == "complete" else "blocked" if status == "blocked" else "current", "detail": detail}
                      if entry["id"] == phase else {**entry, "status": "pending" if entry["status"] in {"current", "blocked"} else entry["status"]}
                      for entry in phases]
            blockers = [_blocker(str(live_job.get("id")), title, detail)] if status == "blocked" else []
            summary["tasks"].append({"id": str(live_job.get("id")), "label": "Live Photo 微动态", "status": status, "actor": actor,
                                      "updated_at": live_job.get("updated_at"), "detail": detail})
    return {"schema_version": 1, "project_id": project.get("project_id"), "revision": project.get("revision", 0),
            "observed_at": project.get("updated_at") or project.get("created_at"),
            "phase_id": phase, "status": status, "title": title, "detail": detail, "actor": actor,
            "current_label": current_label, "phases": phases, "tasks": summary["tasks"] + ledger,
            "blockers": blockers, "recent_events": recent_events, "counts": summary["counts"],
            "source": "current-project", "ledger_updated_at": _latest([task["updated_at"] for task in ledger]),
            "next_action": action.get("action")}
