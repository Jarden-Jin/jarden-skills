from __future__ import annotations

import copy
import hashlib
import json
import unittest

import test_workflow
import product_contract
import project


class ScopedWorkflowTests(unittest.TestCase):
    setUp = test_workflow.WorkflowTests.setUp
    call = test_workflow.WorkflowTests.call
    init = test_workflow.WorkflowTests.init
    known = test_workflow.WorkflowTests.known

    def seed_selections(self):
        with project.project_lock(self.job):
            current = project.load_project(self.job)
            for stage in ("concept", "material", "cutout"):
                current["selections"][stage] = {
                    name: {"round": "input", "image": "inputs/original.png", "origin": "agent-inferred"}
                    for name in ("云", "雨")
                }
            current["stage"] = "scene"
            project.save_project(self.job, current)
        return project.load_project(self.job)

    def test_adding_subject_preserves_other_subjects_and_image_bytes(self):
        self.init()
        self.known(["云", "雨"], "series-collection")
        before = self.seed_selections()
        image = self.job / "inputs/original.png"
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        self.call("project.py", "set", self.job, 'answers.subjects=["云","雨","风"]', "answers.concept_count=3")
        after = project.load_project(self.job)
        for stage in ("concept", "material", "cutout"):
            self.assertEqual(after["selections"][stage], before["selections"][stage])
        self.assertEqual(after["stage"], "concept")
        self.assertEqual(hashlib.sha256(image.read_bytes()).hexdigest(), digest)
        self.assertEqual(project.requirement_signature(before, "material", "雨"),
                         project.requirement_signature(after, "material", "雨"))

    def test_removing_subject_retains_other_selections_and_inactive_history(self):
        self.init()
        self.known(["云", "雨"], "series-collection")
        before = self.seed_selections()
        self.call("project.py", "set", self.job, 'answers.subjects=["云"]', "answers.concept_count=1")
        after = project.load_project(self.job)
        for stage in ("concept", "material", "cutout"):
            self.assertEqual(after["selections"][stage], {"云": before["selections"][stage]["云"]})
        removed = next(row for row in after["product_contract"]["subjects"].values() if row["name"] == "雨")
        self.assertFalse(removed["active"])

    def test_nonfirst_material_replacement_preserves_other_cutout(self):
        self.init()
        self.known(["云", "雨"], "series-collection")
        before = self.seed_selections()
        self.call("project.py", "accept-input", self.job, "--stage", "material", "--subject", "雨",
                  "--path", "inputs/original.png", "--origin", "agent-inferred")
        after = project.load_project(self.job)
        self.assertEqual(after["selections"]["cutout"]["云"], before["selections"]["cutout"]["云"])
        self.assertNotIn("雨", after["selections"]["cutout"])
        self.assertEqual(after["selections"]["concept"], before["selections"]["concept"])

    def test_size_and_view_proposals_do_not_stale_concept_or_material(self):
        self.init()
        self.known(["云", "雨"], "series-collection")
        current = project.load_project(self.job)
        before = {stage: project.requirement_signature(current, stage, "雨") for stage in ("concept", "material")}
        specification = self.root / "size.json"
        specification.write_text(json.dumps({
            "size": {"mode": "numeric", "height": 15, "unit": "cm", "measurement_basis": "本体高度",
                     "status": "confirmed", "origin": "agent-inferred", "evidence_kind": "design_target"}
        }), encoding="utf-8")
        self.call("project.py", "product", self.job, "--subject", "雨", "--file", specification)
        self.call("project.py", "view", self.job, "record", "--subject", "雨", "--view", "front",
                  "--path", "inputs/original.png", "--origin", "reference-derived")
        after = project.load_project(self.job)
        for stage in before:
            self.assertEqual(project.requirement_signature(after, stage, "雨"), before[stage])

    def test_structure_edit_only_invalidates_affected_subject_and_group(self):
        self.init()
        self.known(["云", "雨"], "series-collection")
        current = self.seed_selections()
        current["finals"] = [
            {"slot": "character-1", "subject": "云", "path": "inputs/original.png"},
            {"slot": "character-2", "subject": "雨", "path": "inputs/original.png"},
            {"slot": "cover", "path": "inputs/original.png"}
        ]
        with project.project_lock(self.job):
            project.save_project(self.job, current)
        before = copy.deepcopy(current["selections"])
        specification = self.root / "structure.json"
        specification.write_text(json.dumps({"structure": {"hanging": "背部织带挂点设计提案"}}), encoding="utf-8")
        identifier = product_contract.subject_id(current, "雨")
        self.call("project.py", "product", self.job, "--subject", identifier, "--file", specification)
        after = project.load_project(self.job)
        for stage in ("material", "cutout"):
            self.assertEqual(after["selections"][stage]["云"], before[stage]["云"])
            self.assertNotIn("雨", after["selections"][stage])
        finals = {row["slot"]: row for row in after["finals"]}
        self.assertFalse(finals["character-1"].get("pending_change", False))
        self.assertTrue(finals["character-2"]["pending_change"])
        self.assertTrue(finals["cover"]["pending_change"])

    def test_missing_task_and_view_arguments_return_controlled_json_errors(self):
        self.init()
        self.known()
        for arguments in (("view", "record", "--origin", "agent-inferred"),
                          ("view", "confirm", "--origin", "agent-inferred"),
                          ("task", "prepare"), ("task", "begin"), ("task", "status")):
            with self.subTest(arguments=arguments):
                result = self.call("project.py", arguments[0], self.job, *arguments[1:], code=2)
                self.assertFalse(result["ok"])
                self.assertIn("需要", result["error"])

    def test_free_text_range_is_saved_without_turning_upper_bound_into_height(self):
        self.init()
        self.known()
        with project.project_lock(self.job):
            current = project.load_project(self.job)
            product_contract.set_spec(current, "小云", {"size": {"mode": "unknown", "status": "unknown"}})
            current["stage"] = "scene"
            project.save_project(self.job, current)
        delivery = self.call("present_round.py", self.job, "--no-open")
        current = project.load_project(self.job)
        response = {
            "schema_version": 1, "request_id": "range-from-real-page", "project_id": current["project_id"],
            "delivery_id": delivery["delivery_id"], "expected_revision": current["revision"],
            "round_id": None, "action": "answer-field", "items": [], "keep": [], "note": "",
            "field": project.next_action(current, self.job)["field"], "value": "本体站高10 - 12cm，不含扣件",
            "origin": "agent-inferred"
        }
        request = self.root / "range-answer.json"
        request.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
        result = self.call("project.py", "submit", self.job, "--request", request)
        self.assertTrue(result["committed"])
        after = project.load_project(self.job)
        _, specification = product_contract.spec_for(after, "小云")
        self.assertEqual(specification["size"]["mode"], "range")
        self.assertEqual(specification["size"]["normalized_mm"]["height_mm"], [100, 120])
        self.assertEqual(specification["size"]["origin"], "agent-inferred")

    def test_multi_dimension_text_stays_intact_and_negative_size_is_rejected(self):
        self.assertEqual(product_contract.size_from_text("本体高12cm宽8cm深6cm")["mode"], "qualitative")
        self.assertEqual(product_contract.size_from_text("本体宽8cm")["width"], 8)
        with self.assertRaises(product_contract.ContractError):
            product_contract.size_from_text("本体站高-12cm")

    def test_native_output_without_server_id_has_explicit_local_artifact_receipt(self):
        self.init()
        self.known()
        compiled = self.root / "compiled.json"
        compiled.write_text(json.dumps({"prompt": "Protocol fixture only, not external generation."}), encoding="utf-8")
        plan = self.call("project.py", "task", self.job, "prepare", "--slot", "protocol-only", "--intent", "local-artifact-test",
                         "--file", compiled, "--authorized")
        claim = self.call("project.py", "task", self.job, "begin", "--plan", plan["plan_file"], "--authorized")
        self.assertTrue(claim["should_call"])
        import shutil
        shutil.copyfile(self.source, self.job / "protocol-output.png")
        result = self.call("project.py", "task", self.job, "complete", "--task-id", claim["task_id"], "--claim", claim["claim_id"], "--output", "protocol-output.png")
        self.assertTrue(result["tool_request_id"].startswith("local-artifact:"))
        self.assertEqual(result["metadata"]["receipt_kind"], "local-artifact")
        self.assertIsNone(result["metadata"]["server_request_id"])
        again = self.call("project.py", "task", self.job, "complete", "--task-id", claim["task_id"], "--claim", claim["claim_id"], "--output", "protocol-output.png")
        self.assertTrue(again["reused"])

    def test_scene_patch_replay_returns_original_impact_without_duplicate_slots(self):
        self.init()
        self.known()
        patch = {"schema_version": 1, "request_id": "append-test-01", "op": "append", "expected_revision": 0,
                 "scope": {"slots": ["extra-angle"]},
                 "slots": [{"id": "extra-angle", "role": "angle", "view": "three-quarter"}]}
        patch_file = self.root / "scene-patch.json"
        patch_file.write_text(json.dumps(patch), encoding="utf-8")
        first = self.call("project.py", "scene-patch", self.job, "--file", patch_file)
        before = (self.job / "project.json").read_bytes()
        repeated = self.call("project.py", "scene-patch", self.job, "--file", patch_file)
        self.assertTrue(repeated["unchanged"])
        self.assertTrue(repeated["committed"])
        self.assertEqual(repeated["request_id"], "append-test-01")
        self.assertEqual(first["impact"], repeated["impact"])
        self.assertEqual(before, (self.job / "project.json").read_bytes())
        self.assertEqual(sum(slot["id"] == "extra-angle" for slot in repeated["scene_plan"]["slots"]), 1)
        patch["expected_revision"] = 1
        patch_file.write_text(json.dumps(patch), encoding="utf-8")
        rejected = self.call("project.py", "scene-patch", self.job, "--file", patch_file, code=2)
        self.assertIn("request_id", rejected["error"])
        self.assertEqual(before, (self.job / "project.json").read_bytes())

    def test_new_round_requires_current_local_generation_receipt_on_record_and_select(self):
        import shutil

        self.init()
        self.known()
        result = self.call("project.py", "new-round", self.job, "--stage", "concept", "--count", "1")
        round_file = self.job / result["round_file"]
        data = project.read_json(round_file)
        item = data["items"][0]
        prompt = "Synthetic protocol fixture, full original character proportions, clean backdrop, no external image generation claimed."
        item.update(title="Protocol fixture", prompt=prompt, compiled={"prompt": prompt})
        project.atomic_json(round_file, data)
        relative = round_file.parent.relative_to(self.job).as_posix() + "/A.png"
        shutil.copyfile(self.source, self.job / relative)
        rejected = self.call("project.py", "record-round", self.job, result["round"], code=2)
        self.assertIn("生成来源", rejected["error"])
        compile_file = self.root / "real-compiled.json"
        project.atomic_json(compile_file, item["compiled"])
        plan = self.call("project.py", "task", self.job, "prepare", "--slot", "A", "--intent", "synthetic-record-gate",
                         "--file", compile_file, "--authorized")
        claim = self.call("project.py", "task", self.job, "begin", "--plan", plan["plan_file"], "--authorized")
        self.assertTrue(claim["should_call"])
        self.call("project.py", "task", self.job, "complete", "--task-id", claim["task_id"], "--claim", claim["claim_id"],
                  "--output", relative, "--tool-request", "synthetic-local-protocol-no-external-call")
        self.call("project.py", "record-round", self.job, result["round"])
        data = project.read_json(round_file)
        data["items"][0]["prompt"] += " Different actual prompt"
        project.atomic_json(round_file, data)
        rejected = self.call("project.py", "select", self.job, result["round"], "A", code=2)
        self.assertIn("生成来源", rejected["error"])


if __name__ == "__main__":
    unittest.main()
