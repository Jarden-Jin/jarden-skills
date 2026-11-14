from __future__ import annotations

import copy
import json
import struct
import sys
import tempfile
import threading
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import generation_tasks
import product_contract
import project as project_module
import scene_plan
import workflow_safety


def fixture_png():
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00\x70\x40\x20")) + chunk(b"IEND", b"")


FIXTURE_PNG = fixture_png()


def configured_project(schema_version=3, subjects=None):
    subjects = subjects or ["云", "雨"]
    current = {
        "schema_version": schema_version, "project_id": "workflow-safety-fixture", "revision": 12,
        "answers": {"product_form": "plush", "subjects": subjects, "concept_count": len(subjects),
                    "note_type": "single-multiview", "delivery": "full-post"},
        "brief": {"bible": {"shared": {"material": "短绒"},
                             "subjects": {name: {"eyes": name + "的圆眼"} for name in subjects}}},
        "selections": {}, "finals": [], "rounds": [], "feedback": [],
        "unrelated_extension": {"keep": "未知字段保留"},
    }
    product_contract.initialize(current)
    for name in subjects:
        product_contract.set_spec(current, name, {
            "usage": {"text": "协议测试：桌面摆放", "status": "confirmed", "origin": "user-confirmed"},
            "size": {"mode": "numeric", "unit": "cm", "height": 12, "measurement_basis": "本体全身，不含配件",
                     "status": "confirmed", "origin": "user-confirmed", "evidence_kind": "design_target"},
        })
    return current


class GenerationCurrentSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = configured_project()
        self.project["scene_plan"] = scene_plan.default_plan(self.project, [
            {"id": "cloud", "role": "cover", "subject": "云"},
            {"id": "rain", "role": "character", "subject": "雨"},
        ])
        self.save_current()

    def save_current(self):
        (self.root / "project.json").write_text(json.dumps(self.project, ensure_ascii=False), encoding="utf-8")

    def prepare(self, compiled=None, slot_id="cloud", intent_id="fixture-intent"):
        compiled = compiled or project_module.compile_scene(self.root, self.project, slot_id)
        return generation_tasks.prepare(self.root, self.project, slot_id, intent_id, compiled,
                                        {"kind": "builtin", "tool": "fixture-no-external-call"}, {"approved": True})

    def validator(self, checked):
        return workflow_safety.validate_current_plan(self.root, checked, self.project)

    def record(self, plan):
        return self.root / "tasks" / (plan["task_id"] + ".json")

    def change_size(self, subject="云"):
        product_contract.set_spec(self.project, subject, {
            "size": {"mode": "qualitative", "description": "协议测试：落地大型公仔，明显高于桌面",
                     "measurement_basis": "本体全身，不含配件", "status": "confirmed", "origin": "user-confirmed"},
        })
        self.save_current()

    def test_current_validation_is_pure_and_supports_project_schemas_three_and_four(self):
        for schema in (3, 4):
            with self.subTest(schema=schema):
                self.project["schema_version"] = schema
                self.save_current()
                plan = self.prepare()
                before = (self.root / "project.json").read_bytes()
                result = self.validator(plan)
                self.assertTrue(result["valid"])
                self.assertFalse(result["relevant_change"])
                self.assertEqual(result["change_scope"], "none")
                self.assertEqual(result["changed_paths"], [])
                self.assertEqual(result["expected_compile_fingerprint"], result["current_compile_fingerprint"])
                self.assertEqual(before, (self.root / "project.json").read_bytes())
                self.assertFalse((self.root / "tasks").exists())

    def test_related_size_change_rejects_old_plan_before_claim_then_new_plan_can_claim(self):
        plan = self.prepare()
        self.change_size()
        with self.assertRaisesRegex(generation_tasks.TaskError, "stale_generation_plan") as caught:
            generation_tasks.begin(self.root, plan, current_validator=self.validator)
        self.assertEqual(caught.exception.details["change_scope"], "product_contract")
        self.assertTrue(caught.exception.details["relevant_change"])
        self.assertTrue(any(path.startswith("/contract_hashes/") for path in caught.exception.details["changed_paths"]))
        self.assertFalse(self.record(plan).exists())
        fresh = self.prepare(intent_id="fresh-authorized-fixture")
        self.assertNotEqual(fresh["task_id"], plan["task_id"])
        claimed = generation_tasks.begin(self.root, fresh, current_validator=self.validator)
        self.assertTrue(claimed["should_call"])
        self.assertEqual(json.loads(self.record(fresh).read_text())["status"], "requesting")

    def test_slot_camera_change_is_relevant_but_other_slot_camera_is_not(self):
        plan = self.prepare()
        self.project["scene_plan"]["slots"][1]["yaw"] = 30
        self.project["scene_plan"]["revision"] += 1
        self.assertTrue(self.validator(plan)["valid"])
        self.project["scene_plan"]["slots"][0]["yaw"] = 15
        with self.assertRaises(generation_tasks.TaskError) as caught:
            self.validator(plan)
        self.assertEqual(caught.exception.details["change_scope"], "scene_contract")

    def test_revision_copy_sequence_and_other_subject_contract_do_not_invalidate(self):
        plan = self.prepare()
        self.project["revision"] += 400
        self.project["copy"] = {"title": "只改发帖文字", "body": "与生图无关"}
        self.project["post"] = {"sequence": ["rain", "cloud"]}
        self.project["scene_plan"]["revision"] += 1
        self.project["scene_plan"]["slots"].reverse()
        self.project["brief"]["bible"]["subjects"]["雨"]["eyes"] = "仅雨角色改变"
        self.change_size("雨")
        self.assertTrue(self.validator(plan)["valid"])
        self.assertTrue(generation_tasks.begin(self.root, plan, current_validator=self.validator)["should_call"])

    def test_scoped_feedback_changes_only_targeted_slot(self):
        plan = self.prepare()
        self.project["feedback"].append({"scope": {"slots": ["rain"]}, "changes": ["另一角色更圆"]})
        self.assertTrue(self.validator(plan)["valid"])
        self.project["feedback"].append({"scope": {"slots": ["cloud"]}, "changes": ["云角色更圆"]})
        with self.assertRaisesRegex(generation_tasks.TaskError, "stale_generation_plan"):
            self.validator(plan)

    def test_existing_requesting_unknown_and_completed_are_resumed_before_validation(self):
        for status in ("requesting", "unknown", "completed"):
            with self.subTest(status=status):
                plan = self.prepare(intent_id="resume-" + status)
                claim = generation_tasks.begin(self.root, plan, current_validator=self.validator)
                if status == "unknown":
                    generation_tasks.unknown(self.root, plan["task_id"], "protocol fixture: response lost",
                                              claim_id=claim["claim_id"])
                if status == "completed":
                    (self.root / "output.png").write_bytes(FIXTURE_PNG)
                    generation_tasks.complete(self.root, plan["task_id"], "output.png", claim_id=claim["claim_id"])
                self.project["scene_plan"]["slots"][0]["yaw"] = (self.project["scene_plan"]["slots"][0]["yaw"] or 0) + 15
                self.save_current()
                before = self.record(plan).read_bytes()
                never_called = mock.Mock(side_effect=AssertionError("current validator must not run on recovery"))
                result = generation_tasks.begin(self.root, plan, retry=True, current_validator=never_called)
                self.assertFalse(result["should_call"])
                self.assertEqual(result["status"], status)
                never_called.assert_not_called()
                self.assertEqual(self.record(plan).read_bytes(), before)

    def test_recovery_survives_current_reference_loss_without_resending(self):
        reference = self.root / "reference.png"
        reference.write_bytes(FIXTURE_PNG)
        compiled = project_module.compile_scene(self.root, self.project, "cloud")
        compiled["references"] = ["reference.png"]
        plan = self.prepare(compiled)
        generation_tasks.begin(self.root, plan)
        reference.unlink()
        never_called = mock.Mock(side_effect=AssertionError("must recover receipt"))
        self.assertFalse(generation_tasks.begin(self.root, plan, retry=True,
                                                current_validator=never_called)["should_call"])
        never_called.assert_not_called()
        changed = copy.deepcopy(plan)
        changed["compiled"]["prompt"] += "篡改请求"
        with self.assertRaisesRegex(generation_tasks.TaskError, "plan_conflict"):
            generation_tasks.begin(self.root, changed)

    def test_explicit_safe_retry_revalidates_and_leaves_failure_unchanged_on_stale(self):
        plan = self.prepare()
        claim = generation_tasks.begin(self.root, plan, current_validator=self.validator)
        generation_tasks.failed(self.root, plan["task_id"], "protocol fixture: definitely not dispatched",
                                 claim_id=claim["claim_id"], retryable=True)
        failed = self.record(plan).read_bytes()
        self.change_size()
        self.assertFalse(generation_tasks.begin(self.root, plan, current_validator=self.validator)["should_call"])
        with self.assertRaisesRegex(generation_tasks.TaskError, "stale_generation_plan"):
            generation_tasks.begin(self.root, plan, retry=True, current_validator=self.validator)
        self.assertEqual(self.record(plan).read_bytes(), failed)

    def test_false_unknown_validator_results_and_exceptions_fail_closed(self):
        for result in (False, {}, {"valid": False}, {"should_call": True}, "yes", 1):
            with self.subTest(result=result):
                plan = self.prepare(intent_id="invalid-" + str(result))
                with self.assertRaisesRegex(generation_tasks.TaskError, "current_plan_invalid"):
                    generation_tasks.begin(self.root, plan, current_validator=lambda checked: result)
                self.assertFalse(self.record(plan).exists())
        plan = self.prepare(intent_id="raised-validator")
        with self.assertRaisesRegex(generation_tasks.TaskError, "current_plan_invalid"):
            generation_tasks.begin(self.root, plan, current_validator=mock.Mock(side_effect=RuntimeError("fixture")))
        self.assertFalse(self.record(plan).exists())

    def test_validator_receives_copy_and_runs_under_lock_once_for_parallel_claims(self):
        plan = self.prepare()
        barrier = threading.Barrier(5)
        called = []

        def validate(checked):
            self.assertTrue((self.root / "tasks" / ".generation.lock").is_file())
            called.append(checked["task_id"])
            checked["compiled"]["prompt"] = "cannot change the actual prepared request"
            return True

        def begin_once(number):
            barrier.wait(timeout=5)
            return generation_tasks.begin(self.root, plan, current_validator=validate)

        with ThreadPoolExecutor(max_workers=5) as executor:
            results = list(executor.map(begin_once, range(5)))
        self.assertEqual(sum(result["should_call"] for result in results), 1)
        self.assertEqual(len(called), 1)
        self.assertEqual(json.loads(self.record(plan).read_text())["request"], plan["request"])

    def test_explicit_current_compiled_adapter_checks_alternative_prompt_not_derived_signature(self):
        alternative = project_module.compile_scene(self.root, self.project, "cloud")
        alternative["prompt"] += "\n本候选探索暖色背景。"
        plan = self.prepare(alternative)
        current = copy.deepcopy(alternative)
        current["signature"] = "a derived adapter signature does not define dependencies"
        current["compiled_at"] = "not-a-generation-dependency"
        current["output_path"] = "another-destination.png"
        self.assertTrue(workflow_safety.validate_current_plan(
            self.root, plan, self.project, current_compiled=current)["valid"])
        current["prompt"] += "changed actual request"
        with self.assertRaisesRegex(generation_tasks.TaskError, "stale_generation_plan"):
            generation_tasks.validate_current_compiled(self.root, plan, current)


class SceneProfileSafetyTests(unittest.TestCase):
    def setUp(self):
        self.project = configured_project(subjects=["云"])
        binding = scene_plan.default_profile_binding(self.project)
        self.project["scene_plan"] = scene_plan.default_plan(self.project, binding["slots"])

    def switch_profile(self, note_type="design-manuscript"):
        self.project["answers"]["note_type"] = note_type

    def coordinate(self, current=None, **options):
        current = current or self.project
        options.setdefault("expected_revision", current["scene_plan"]["revision"])
        return scene_plan.coordinate_profile(current, **options)

    def test_default_binding_is_independent_of_existing_plan_and_outer_schema(self):
        for schema in (3, 4):
            with self.subTest(schema=schema):
                self.project["schema_version"] = schema
                before = copy.deepcopy(self.project)
                binding = scene_plan.default_profile_binding(self.project, note_type="design-manuscript")
                self.assertEqual([slot["id"] for slot in binding["slots"]],
                                 ["cover", "turnaround", "manuscript", "scene-1", "extension-1"])
                self.assertEqual(before, self.project)
                self.assertEqual(scene_plan.profile_diff(self.project)["status"], "current")

    def test_profile_switch_is_read_only_pending_until_explicit_coordination(self):
        self.switch_profile()
        before = copy.deepcopy(self.project)
        diff = scene_plan.profile_diff(self.project)
        self.assertEqual(diff["status"], "pending")
        self.assertTrue(diff["needs_coordination"])
        self.assertEqual(diff["added_slots"], ["turnaround", "manuscript", "extension-1"])
        self.assertEqual(diff["removed_slots"], ["scale", "detail", "angle", "info"])
        self.assertEqual(diff["reused_slots"], ["cover", "scene-1"])
        self.assertEqual(before, self.project)
        updated, impact = self.coordinate()
        self.assertEqual([slot["id"] for slot in updated["scene_plan"]["slots"]],
                         ["cover", "turnaround", "manuscript", "scene-1", "extension-1"])
        self.assertEqual(impact["invalidated_slots"], [])
        self.assertEqual(scene_plan.profile_diff(updated)["status"], "current")
        self.assertEqual(before, self.project)

    def test_custom_angles_keep_items_images_and_historical_slots_are_retained(self):
        slots = {slot["id"]: slot for slot in self.project["scene_plan"]["slots"]}
        slots["angle"].update(view="left", yaw=-85, framing="保留用户左侧机位")
        slots["detail"]["keep"] = ["保留耳部走线"]
        self.project["scene_plan"]["coverage"]["preserve_slots"] = ["cover"]
        self.project["finals"] = [{"slot": "scale", "path": "real-old-scale.png", "sha256": "a" * 64,
                                  "approval": {"origin": "user-confirmed"}}]
        self.project["rounds"] = [{"id": "old-scene", "stage": "scene", "review_paths": {"info": "old-info.png"}}]
        self.project["scene_plan"]["slots"].append(scene_plan._slot({"id": "custom-back", "role": "angle",
                                                                       "view": "back", "keep": ["自定背面"]}))
        before = copy.deepcopy(self.project)
        before_compiled = scene_plan.compile_slot(self.project, "cover", [])
        self.switch_profile()
        updated, impact = self.coordinate()
        for identifier in ("angle", "detail", "scale", "info", "cover", "custom-back"):
            actual = next(slot for slot in updated["scene_plan"]["slots"] if slot["id"] == identifier)
            expected = next(slot for slot in before["scene_plan"]["slots"] if slot["id"] == identifier)
            self.assertEqual(actual, expected)
            self.assertIn(identifier, impact["preserved_slots"])
        for field in ("finals", "rounds", "selections", "feedback", "product_contract", "revision", "unrelated_extension"):
            self.assertEqual(updated[field], before[field])
        self.assertEqual(scene_plan.compile_slot(updated, "cover", []), before_compiled)
        self.assertEqual(scene_plan.coverage_status(updated["scene_plan"])["preserve_slots"], ["cover"])

    def test_profile_coordination_archives_removed_clean_defaults_without_touching_unknown_data(self):
        self.switch_profile()
        old_slots = {slot["id"]: slot for slot in self.project["scene_plan"]["slots"]}
        updated, impact = self.coordinate()
        removed = updated["scene_profile_history"][-1]["removed_slots"]
        self.assertEqual(removed, [old_slots[identifier] for identifier in impact["removed_slots"]])
        self.assertEqual(updated["revision"], self.project["revision"])
        self.assertEqual(updated["unrelated_extension"], self.project["unrelated_extension"])

    def test_bound_legacy_plan_preserves_camera_instead_of_relabeling_it_as_default(self):
        self.project["scene_plan"].pop("default_profile")
        self.project["scene_plan"]["slots"][3]["yaw"] = 40
        original = copy.deepcopy(self.project)
        bound = scene_plan.bind_default_profile(self.project)
        self.assertEqual(self.project, original)
        bound["answers"]["note_type"] = "design-manuscript"
        diff = scene_plan.profile_diff(bound)
        self.assertIn("angle", diff["customized_slots"])
        self.assertIn("angle", diff["retained_slots"])
        self.assertNotIn("angle", diff["removed_slots"])
        self.assertEqual(diff["binding_source"], "bound")

    def test_unbound_old_profile_can_be_inferred_after_type_switch_without_overwrite(self):
        self.project["scene_plan"].pop("default_profile")
        self.switch_profile()
        diff = scene_plan.profile_diff(self.project)
        self.assertEqual(diff["binding_source"], "inferred")
        self.assertEqual(diff["current_profile"]["note_type"], "single-multiview")
        updated, impact = self.coordinate()
        self.assertIn("turnaround", [slot["id"] for slot in updated["scene_plan"]["slots"]])
        self.assertEqual(impact["invalidated_slots"], [])

    def test_repeated_coordination_is_noop_with_current_precondition(self):
        self.switch_profile()
        updated, first = self.coordinate()
        repeated, second = self.coordinate(updated, expected_hash=first["after_hash"])
        self.assertEqual(updated, repeated)
        self.assertTrue(second["unchanged"])
        self.assertFalse(second["committed"])
        self.assertEqual(second["before_hash"], second["after_hash"])

    def test_profile_coordination_requires_fresh_preconditions_and_no_partial_writes(self):
        self.switch_profile()
        before = copy.deepcopy(self.project)
        for options, expected in (({}, "missing_precondition"), ({"expected_revision": 99}, "stale_plan_revision"),
                                  ({"expected_hash": "0" * 64}, "stale_plan_hash")):
            with self.subTest(options=options), self.assertRaisesRegex(scene_plan.ScenePlanError, expected):
                scene_plan.coordinate_profile(self.project, **options)
            self.assertEqual(before, self.project)

    def test_grid_conflict_never_drops_historical_images_or_weakens_keeps(self):
        self.project.pop("scene_plan")
        self.project["answers"]["delivery"] = "grid-9"
        binding = scene_plan.default_profile_binding(self.project)
        self.project["scene_plan"] = scene_plan.default_plan(self.project, binding["slots"])
        for slot in self.project["scene_plan"]["slots"]:
            slot["keep"] = ["保留旧图"]
        self.switch_profile()
        before = copy.deepcopy(self.project)
        self.assertTrue(any("grid_capacity" in item for item in scene_plan.profile_diff(self.project)["conflicts"]))
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "grid_capacity"):
            self.coordinate()
        self.assertEqual(before, self.project)

    def test_patching_keeps_binding_and_profile_diff_never_counts_plain_copy_or_product_revision(self):
        binding = copy.deepcopy(self.project["scene_plan"]["default_profile"])
        updated, impact = scene_plan.apply_patch(self.project, {
            "schema_version": 1, "op": "edit", "slot_id": "angle", "changes": {"yaw": 45},
            "scope": {"slots": ["angle"]}, "expected_revision": 0,
        })
        self.assertEqual(binding, updated["scene_plan"]["default_profile"])
        updated["revision"] += 10
        updated["copy"] = {"title": "只是文案变了"}
        self.assertFalse(scene_plan.profile_diff(updated)["needs_coordination"])
        updated["answers"]["note_type"] = "design-manuscript"
        self.assertIn("angle", scene_plan.profile_diff(updated)["retained_slots"])
        self.assertEqual(impact["changed_slots"], ["angle"])

    def test_unknown_unbound_custom_plan_is_preserved_conservatively(self):
        self.project.pop("scene_plan")
        self.project["scene_plan"] = scene_plan.default_plan(self.project, [
            {"id": "custom-hero", "role": "cover", "view": "front", "yaw": 23},
        ])
        self.project["scene_plan"].pop("default_profile")
        self.switch_profile()
        before = copy.deepcopy(self.project["scene_plan"]["slots"][0])
        updated, impact = self.coordinate()
        self.assertIn(before, updated["scene_plan"]["slots"])
        self.assertIn("custom-hero", impact["preserved_slots"])
        self.assertEqual(impact["removed_slots"], [])


if __name__ == "__main__":
    unittest.main()
