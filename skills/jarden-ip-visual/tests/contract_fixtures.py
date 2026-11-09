"""Synthetic protocol fixtures; no independent visual approval is performed."""

from __future__ import annotations

import json
from pathlib import Path

import contract_review
import project


def generation(root: Path, entry: dict, data: dict) -> None:
    import generation_tasks

    current = project.load_project(root)
    for item in data.get("items") or []:
        relative = entry["dir"] + "/" + item["image"]
        if not (root / relative).is_file() or item.get("method", "generate") != "generate":
            continue
        compiled = dict(item.get("compiled") or {})
        compiled["prompt"] = item.get("prompt", "")
        compiled["synthetic_protocol_fixture"] = "Not external generation or visual acceptance"
        item["compiled"] = compiled
        plan = generation_tasks.prepare(root, current, item["id"], "protocol-fixture-" + entry["id"] + "-" + item["id"],
                                        compiled, {"kind": "builtin", "tool": "synthetic-protocol-fixture"}, {"approved": True})
        claim = generation_tasks.begin(root, plan)
        generation_tasks.complete(root, claim["task_id"], [relative], claim_id=claim["claim_id"],
                                  tool_request_id="synthetic-protocol-fixture-not-external")
        item["generation_task"] = claim["task_id"]


def identity(root: Path, paths: list[str]) -> None:
    import review_checks

    current = project.load_project(root)
    packet = review_checks.prepare(root, current, "identity", paths, "synthetic-protocol-producer", "cutout")
    request = project.read_json(Path(packet["request"]))
    scope = request["scope"]
    evidence = "Synthetic protocol fixture; no real independent image acceptance performed."
    report = {"schema_version": 2, "review_id": packet["review_id"], "category": "identity", "scope": scope,
              "image_hashes": [item["sha256"] for item in scope["paths"]],
              "decision_signature": scope.get("decision_signature"), "template_version": scope.get("template_version"),
              "reviewer": {"id": "synthetic-identity-reviewer", "independence": "zero-context", "viewed_images": True},
              "verdict": "pass", "findings": [], "evidence": evidence,
              "paths": [{**item, "verdict": "pass", "evidence": evidence} for item in scope["paths"]]}
    output = root / "synthetic-identity-report.json"
    project.atomic_json(output, report)
    review_checks.record(root, current, "identity", packet["review_id"], output)


def configure(root: Path) -> None:
    import product_contract
    import scene_plan

    with project.project_lock(root):
        current = project.load_project(root, writable=True)
        product_contract.initialize(current)
        for subject in current["product_contract"]["subjects"].values():
            if not subject.get("active", True):
                continue
            name = subject["name"]
            product_contract.set_spec(current, name, {
                "usage": {"text": "协议测试夹具：桌面摆放", "status": "confirmed", "origin": "agent-inferred"},
                "size": {"mode": "qualitative", "description": "协议测试夹具：同款掌心小公仔的相对尺度布局", "measurement_basis": "本体全身，不含配件", "status": "confirmed", "origin": "agent-inferred", "evidence_kind": "design_target"},
            })
        answers = current.get("answers") or {}
        capacity_conflict = answers.get("delivery") == "grid-9" and answers.get("note_type") == "series-collection" and len(project.subjects_of(current)) > 7
        if answers.get("note_type") and answers.get("delivery") and not current.get("scene_plan") and not capacity_conflict:
            current["scene_plan"] = scene_plan.default_plan(current, project.legacy_scene_slots(current))
        project.save_project(root, current)


def bind_final(root: Path, path: str, slot_id: str) -> None:
    with project.project_lock(root):
        current = project.load_project(root, writable=True)
        slot = next(slot for slot in project.scene_slots(current) if slot["id"] == slot_id)
        project.project_path(root, path).read_bytes()
        finals = project.mapped_scene_finals(current)
        current["finals"] = [original for original, final in zip(current.get("finals") or [], finals)
                             if final.get("slot") != slot_id and final.get("path") != path]
        current["finals"].append({"slot": slot_id, "role": slot["role"], "path": path})
        project.save_project(root, current)


def review(root: Path, paths: list[str], stage: str = "scene", mappings: list[dict] | None = None) -> None:
    current = project.load_project(root)
    mappings = mappings or project.review_mappings(root, current, paths, stage)
    packet = contract_review.prepare(root, current, paths, "protocol-fixture-producer", stage, mappings)
    request = project.read_json(Path(packet["request"]))

    def check() -> dict:
        return {"verdict": "pass", "basis": "visible_pixels", "evidence": "仅为自动化协议测试预置声明，未进行独立视觉验收。"}

    rows = [{"slot_id": image["slot_id"], "path": image["path"], "sha256": image["sha256"],
             "verdict": "pass", "checks": {name: check() for name in contract_review.CHECKS},
             "observed_views": [image["slot"].get("view", "front")], "findings": []}
            for image in request["scope"]["images"]]
    report = {"schema_version": 1, "request_id": request["request_id"], "scope_signature": request["scope_signature"],
              "reviewer": {"id": "protocol-fixture-reviewer", "independence": "zero-context", "viewed_images": True},
              "verdict": "pass", "results": rows,
              "sequence": {"verdict": "pass", "findings": [], "checks": {name: check() for name in contract_review.SEQUENCE_CHECKS},
                           "actual_order": request["scope"]["ordered_slots"],
                           "observed_views": [{"slot_id": row["slot_id"], "sha256": row["sha256"], "views": row["observed_views"], "basis": "visible_pixels", "evidence": "测试夹具，非实际图片观察"} for row in rows],
                           "preserve_images": [{"slot_id": row["slot_id"], "sha256": row["sha256"], **check()} for row in request["scope"]["collection"]["preserve_images"]]}}
    report_file = root / "fixture-contract-report.json"
    report_file.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    contract_review.record(root, current, request["request_id"], report_file)
