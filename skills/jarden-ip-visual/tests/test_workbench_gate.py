from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import asset_workflow
import generation_tasks
import project
import workbench


class WorkbenchGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="workbench-gate-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.root = self.home / "asset project"
        environment = mock.patch.dict(os.environ, {"JARDEN_UPDATE_HOME": str(self.home / "runtime")})
        environment.start()
        self.addCleanup(environment.stop)

    def command(self, *arguments, script="project.py", code=0):
        output = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, arguments)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(output.returncode, code, output.stdout + output.stderr)
        return json.loads(output.stdout)

    def invoke(self, *arguments, code=0):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            actual = project.main(list(map(str, arguments)))
        self.assertEqual(actual, code, output.getvalue())
        return json.loads(output.getvalue())

    def latest(self):
        current = project.load_project(self.root)
        identifier = current["presentations"]["latest_delivery_id"]
        manifest = project.read_json(self.root / "deliveries" / identifier / "manifest.json")
        self.assertEqual(manifest["project_revision"], current["revision"])
        self.assertTrue((self.root / "gallery.html").is_file())
        return current, manifest

    def test_init_and_mutations_present_after_transaction_commit_without_opening(self):
        with mock.patch.object(workbench.presentation, "dispatch_open", side_effect=AssertionError("Unexpected browser tab")):
            first = self.invoke("init", self.root, "--kind", "text")
            self.assertEqual(first["workbench"]["status"], "synced")
            self.assertTrue(first["operation_committed"])
            current, manifest = self.latest()
            self.assertEqual(manifest["next"]["action"], "choose-assets")
            second = self.invoke("set", self.root, "brief.ip_name=Golden Collie")
            self.assertGreater(second["revision"], current["revision"])
            _, updated = self.latest()
            self.assertNotEqual(updated["delivery_id"], manifest["delivery_id"])
            self.assertEqual(project.read_json(self.root / "deliveries" / manifest["delivery_id"] / "manifest.json"), manifest)

    def test_repeated_init_reuses_delivery_without_revision_or_byte_changes(self):
        first = self.command("init", self.root, "--kind", "text")
        before = (self.root / "project.json").read_bytes()
        second = self.command("init", self.root, "--kind", "text")
        self.assertTrue(second["workbench"]["reused"])
        self.assertEqual(first["workbench"]["delivery_id"], second["workbench"]["delivery_id"])
        self.assertEqual(before, (self.root / "project.json").read_bytes())

    def test_assets_standalone_entrypoint_synchronizes_each_committed_mutation(self):
        self.command("init", self.root, "--kind", "text")
        chosen = self.command("assets", self.root, "choose", "08", "--evidence", "Synthetic scope",
                              script="asset_workflow.py")
        self.assertEqual(chosen["workbench"]["status"], "synced")
        self.latest()
        planned = self.command("assets", self.root, "new", "--asset", "08", "--quality", "final",
                               script="asset_workflow.py")
        self.assertTrue(planned["operation_committed"])
        _, manifest = self.latest()
        self.assertEqual(manifest["round_id"], planned["round"]["id"])
        self.assertFalse(planned["should_call"])

    def test_failed_post_commit_sync_keeps_business_and_requires_presentation_recovery(self):
        self.invoke("init", self.root, "--kind", "text")
        before = project.load_project(self.root)["revision"]
        with mock.patch.object(workbench, "ensure_presentation", side_effect=OSError("Synthetic renderer unavailable")):
            result = self.invoke("set", self.root, "brief.ip_name=Saved design", code=2)
        self.assertFalse(result["ok"])
        self.assertTrue(result["operation_committed"])
        self.assertTrue(result["presentation_required"])
        current = project.load_project(self.root)
        self.assertEqual(current["brief"]["ip_name"], "Saved design")
        self.assertEqual(current["revision"], before + 1)
        workbench.ensure_presentation(self.root)
        self.assertEqual(project.load_project(self.root)["revision"], current["revision"])
        self.latest()

    def test_sync_failure_blocks_planning_before_commit_and_preserves_project(self):
        self.invoke("init", self.root, "--kind", "text")
        self.invoke("assets", self.root, "choose", "08", "--evidence", "Synthetic scope")
        with mock.patch.object(workbench, "ensure_presentation", side_effect=OSError("Synthetic sync failure")):
            before = (self.root / "project.json").read_bytes()
            result = self.invoke("assets", self.root, "new", "--asset", "08", code=2)
        self.assertTrue(result["presentation_required"])
        self.assertFalse(result["operation_committed"])
        self.assertEqual((self.root / "project.json").read_bytes(), before)
        self.assertEqual(project.load_project(self.root)["asset_workflow"]["instances"], [])

    def test_sync_failure_rolls_back_valid_approval_and_selection_transactions(self):
        self.invoke("init", self.root, "--kind", "text")

        def selection(args):
            current = project.load_project(self.root)
            current["brief"]["ip_name"] = "Synthetic valid selection"
            project.save_project(self.root, current)
            return project.emit({"ok": True})

        cases = [("assets", self.root, "approve", "--instance", "synthetic"),
                 ("select", self.root, "synthetic-round", "A"),
                 ("task", self.root, "begin", "--plan", "synthetic.json")]
        for arguments in cases:
            with self.subTest(command=arguments), \
                    mock.patch.object(workbench, "ensure_presentation", side_effect=OSError("Synthetic sync failure")), \
                    mock.patch.object(asset_workflow, "approve", return_value={"ok": True}), \
                    mock.patch.object(project, "cmd_select", side_effect=selection), \
                    mock.patch.object(project, "cmd_task", side_effect=selection):
                before = (self.root / "project.json").read_bytes()
                result = self.invoke(*arguments, code=2)
                self.assertTrue(result["presentation_required"])
                self.assertFalse(result["operation_committed"])
                self.assertEqual((self.root / "project.json").read_bytes(), before)

    def test_invalid_command_does_not_present_or_mutate_project(self):
        self.invoke("init", self.root, "--kind", "text")
        with mock.patch.object(workbench, "ensure_presentation", side_effect=AssertionError("Invalid operation rendered")):
            before = (self.root / "project.json").read_bytes()
            result = self.invoke("select", self.root, "missing-round", "A", code=2)
        self.assertFalse(result["ok"])
        self.assertEqual((self.root / "project.json").read_bytes(), before)

    def test_native_claim_sync_failure_does_not_create_requesting_ledger(self):
        self.invoke("init", self.root, "--kind", "text")
        self.invoke("assets", self.root, "choose", "01", "--evidence", "Synthetic scope")
        plan_file = self.home / "new.json"
        project.atomic_json(plan_file, {"asset_id": "01", "count": 1, "prompt": "Synthetic native claim fixture",
                                      "authorization": {"approved": True, "evidence": "Synthetic authorized test"}})
        planned = self.invoke("assets", self.root, "new", "--file", plan_file)
        instance = planned["instances"][0]
        with mock.patch.object(workbench, "ensure_presentation", side_effect=OSError("Synthetic sync failure")):
            result = self.invoke("task", self.root, "begin", "--plan", instance["generation_plan"], "--authorized", code=2)
        self.assertFalse(result["operation_committed"])
        self.assertTrue(result["presentation_required"])
        self.assertFalse((self.root / "tasks" / (instance["generation_task"] + ".json")).exists())

    def test_post_claim_sync_failure_records_only_new_undispatched_claim_as_retryable(self):
        self.invoke("init", self.root, "--kind", "text")
        self.invoke("assets", self.root, "choose", "01", "--evidence", "Synthetic scope")
        plan_file = self.home / "new.json"
        project.atomic_json(plan_file, {"asset_id": "01", "count": 1, "prompt": "Synthetic native claim fixture",
                                      "authorization": {"approved": True, "evidence": "Synthetic authorized test"}})
        planned = self.invoke("assets", self.root, "new", "--file", plan_file)
        instance = planned["instances"][0]
        original_sync = workbench.ensure_presentation
        calls = 0

        def fail_after_claim(root):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("Synthetic post-claim sync failure")
            return original_sync(root)

        arguments = ("task", self.root, "begin", "--plan", instance["generation_plan"], "--authorized")
        with mock.patch.object(workbench, "ensure_presentation", side_effect=fail_after_claim):
            result = self.invoke(*arguments, code=2)
        self.assertFalse(result["should_call"])
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["retryable"])
        self.assertTrue(result["operation_committed"])
        retry = self.invoke(*arguments, "--retry")
        self.assertTrue(retry["should_call"])
        self.assertEqual(retry["attempt"], 2)
        generation_tasks.unknown(self.root, instance["generation_task"], "Synthetic existing uncertain outcome",
                                 claim_id=retry["claim_id"])
        calls = 0
        with mock.patch.object(workbench, "ensure_presentation", side_effect=fail_after_claim):
            resumed = self.invoke(*arguments, code=2)
        self.assertFalse(resumed["should_call"])
        self.assertEqual(generation_tasks.resume_status(self.root, instance["generation_task"])["status"], "unknown")

    def test_show_stays_read_only_and_catalog_needs_no_project(self):
        self.command("init", self.root, "--kind", "text")
        before = (self.root / "project.json").read_bytes()
        self.command("show", self.root)
        self.assertEqual((self.root / "project.json").read_bytes(), before)
        missing = self.home / "missing"
        result = self.command("assets", missing, "catalog")
        self.assertEqual(len(result["assets"]), 21)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
