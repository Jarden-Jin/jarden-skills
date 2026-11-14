from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

import asset_workflow
import build_gallery
import present_round
import project
import scene_plan
import test_workflow


class AssetStudioIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.job = self.root / "asset studio"

    def cli(self, *arguments, code=0):
        result = subprocess.run([sys.executable, "-B", str(SKILL / "scripts/project.py"), *map(str, arguments)],
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_default_init_selects_assets_before_product_questions(self):
        result = self.cli("init", self.job, "--kind", "text")
        self.assertEqual(result["schema_version"], 4)
        self.assertEqual(result["next"]["action"], "choose-assets")
        self.assertEqual(len(result["asset_workflow"]["catalog"]), 21)
        self.assertEqual(project.load_project(self.job)["rounds"], [])
        self.assertFalse((self.job / "tasks").exists())

    def test_asset_selection_uses_real_delivery_receipt_and_is_idempotent(self):
        self.cli("init", self.job, "--kind", "text")
        result = present_round.present_round(self.job, open_browser=False)
        manifest = project.read_json(Path(result["manifest"]))
        payload = {"schema_version": 1, "request_id": "studio-select-001", "project_id": manifest["project_id"],
                   "delivery_id": manifest["delivery_id"], "expected_revision": manifest["project_revision"],
                   "round_id": None, "action": "choose-assets", "asset_ids": ["08"], "items": [], "note": "", "keep": []}
        receipt = project.submit_decision(self.job, payload)
        self.assertTrue(receipt["committed"])
        self.assertEqual(project.load_project(self.job)["asset_workflow"]["selected"], ["08"])
        before = (self.job / "project.json").read_bytes()
        self.assertEqual(receipt, project.submit_decision(self.job, payload))
        self.assertEqual(before, (self.job / "project.json").read_bytes())
        altered = {**payload, "asset_ids": ["02"]}
        with self.assertRaisesRegex(project.ProjectError, "request_id_conflict"):
            project.submit_decision(self.job, altered)

    def test_source_schema_three_stays_writable_without_asset_mode(self):
        self.cli("init", self.job, "--workflow", "legacy")
        current = project.load_project(self.job)
        current.update(schema_version=3, custom_extension={"keep": True})
        project.atomic_json(self.job / "project.json", current)
        self.cli("set", self.job, "brief.ip_name=旧项目")
        self.assertEqual(project.load_project(self.job)["schema_version"], 3)
        self.assertFalse(asset_workflow.is_enabled(project.load_project(self.job)))
        self.assertFalse(build_gallery.build_data(self.job, project.load_project(self.job), prefer_latest=False)["readonly"])

    def test_migration_preserves_old_approvals_and_requires_explicit_asset_choice(self):
        self.cli("init", self.job, "--workflow", "legacy")
        current = project.load_project(self.job)
        current.update(schema_version=3, requirements_revision=7, custom_extension={"keep": "unknown"})
        current["selections"] = {"concept": {"主体": {"image": "inputs/old.png", "origin": "reference-derived"}}}
        project.atomic_json(self.job / "project.json", current)
        plan = project.migration_plan(self.job)
        project.apply_migration(self.job, plan)
        migrated = project.load_project(self.job)
        self.assertEqual(migrated["requirements_revision"], 7)
        self.assertEqual(migrated["custom_extension"], current["custom_extension"])
        self.assertEqual(migrated["selections"], current["selections"])
        self.assertFalse(asset_workflow.is_enabled(migrated))
        self.assertTrue(project.apply_migration(self.job, plan)["unchanged"])


class LegacyStudioSafetyIntegrationTests(unittest.TestCase):
    setUp = test_workflow.WorkflowTests.setUp
    call = test_workflow.WorkflowTests.call
    init = test_workflow.WorkflowTests.init
    known = test_workflow.WorkflowTests.known
    round = test_workflow.WorkflowTests.round
    review = test_workflow.WorkflowTests.review

    def test_changed_profile_is_explicitly_coordinated_without_dropping_custom_slot(self):
        self.init()
        self.known()
        current = project.load_project(self.job)
        current["scene_plan"]["slots"].append({"id": "extra-back", "role": "angle", "view": "back", "keep": ["原背面"]})
        current["scene_plan"] = scene_plan.validate_plan(current["scene_plan"], current)
        project.atomic_json(self.job / "project.json", current)
        self.round("concept")
        self.call("project.py", "set", self.job, "answers.note_type=design-manuscript")
        pending = self.call("project.py", "scene-plan", self.job)
        self.assertTrue(pending["needs_coordination"])
        self.assertIn("turnaround", pending["reconciliation"]["added_slots"])
        self.assertIn("extra-back", pending["reconciliation"]["retained_slots"])
        updated = self.call("project.py", "scene-plan", self.job, "--reconcile", "--expected-hash", pending["signature"])
        self.assertTrue(updated["committed"])
        slots = {slot["id"]: slot for slot in project.load_project(self.job)["scene_plan"]["slots"]}
        self.assertIn("turnaround", slots)
        self.assertEqual(slots["extra-back"]["view"], "back")
        self.assertEqual(slots["extra-back"]["keep"], ["原背面"])

    def test_current_specification_rejects_old_plan_before_new_claim(self):
        self.init()
        self.known()
        for stage in ("concept", "material"):
            round_id, _ = self.round(stage)
            self.call("project.py", "select", self.job, round_id, "A")
        round_id, _ = self.round("cutout")
        self.call("project.py", "select", self.job, round_id, "--approve")
        prepared = self.call("project.py", "task", self.job, "prepare", "--slot", "cover", "--intent", "studio-safety", "--authorized")
        specification = self.root / "size-change.json"
        project.write_json(specification, {"size": {"mode": "qualitative", "description": "改为较大的桌面产品",
                           "measurement_basis": "本体全身，不含配件", "status": "confirmed", "origin": "user-confirmed",
                           "evidence_kind": "design_target"}})
        self.call("project.py", "product", self.job, "--subject", "小云", "--file", specification)
        rejected = self.call("project.py", "task", self.job, "begin", "--plan", prepared["plan_file"], "--authorized", code=2)
        self.assertIn("stale_generation_plan", rejected["error"])
        self.assertFalse((self.job / "tasks" / (prepared["plan"]["task_id"] + ".json")).exists())

    def test_missing_sibling_does_not_disable_healthy_candidate(self):
        self.init()
        self.known()
        round_id, round_file = self.round("concept", register=False)
        data = project.read_json(round_file)
        missing_path = round_file.parent / data["items"][1]["image"]
        missing_path.unlink()
        self.call("project.py", "record-round", self.job, round_id, "--allow-missing")
        current = project.load_project(self.job)
        gate = project.round_eligibility(self.job, current, project.find_round(current, round_id))
        self.assertTrue(gate["A"]["selectable"])
        self.assertFalse(gate["B"]["selectable"])
        self.call("project.py", "select", self.job, round_id, "A")
        self.assertEqual(project.load_project(self.job)["selections"]["concept"]["小云"]["item"], "A")


if __name__ == "__main__":
    unittest.main()
