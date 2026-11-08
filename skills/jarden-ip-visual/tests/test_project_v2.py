from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

import test_workflow
import project
import serve_gallery


class ProjectV2Tests(unittest.TestCase):
    setUp = test_workflow.WorkflowTests.setUp
    call = test_workflow.WorkflowTests.call
    init = test_workflow.WorkflowTests.init
    known = test_workflow.WorkflowTests.known
    round = test_workflow.WorkflowTests.round
    review = test_workflow.WorkflowTests.review

    def delivery(self, round_id=None):
        with project.project_lock(self.job):
            state = project.load_project(self.job)
            delivery_id = f"D{len(state.get('deliveries', [])) + 1:04d}"
            items = []
            kind = None
            if round_id:
                entry = project.find_round(state, round_id)
                kind = entry["kind"]
                for item in project.read_json(self.job / entry["dir"] / "round.json")["items"]:
                    original = self.job / entry["dir"] / item["image"]
                    items.append({"id": item["id"], "image_sha256": hashlib.sha256(original.read_bytes()).hexdigest() if original.exists() else None})
            manifest = {"schema_version": 1, "project_id": state["project_id"], "delivery_id": delivery_id, "project_revision": state["revision"], "round_id": round_id, "kind": kind, "items": items}
            project.write_json(self.job / "deliveries" / delivery_id / "manifest.json", manifest)
            state.setdefault("deliveries", []).append({"delivery_id": delivery_id})
            state["presentations"] = {"latest_delivery_id": delivery_id}
            project.write_json(self.job / "project.json", state)
        return manifest

    def payload(self, manifest, action="select", ids=None):
        return {"schema_version": 1, "request_id": "request-001", "project_id": manifest["project_id"], "delivery_id": manifest["delivery_id"], "expected_revision": manifest["project_revision"], "round_id": manifest["round_id"], "action": action, "items": [{"id": item["id"], "sha256": item["image_sha256"]} for item in manifest["items"] if item["id"] in (ids or ["A"])], "note": "模拟客户采用，保留身份", "keep": ["配色"]}

    def submit(self, payload, code=0):
        path = self.root / "request.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return self.call("project.py", "submit", self.job, "--request", path, code=code)

    def test_receipt_retry_same_id_and_stale_other_tab(self):
        self.init()
        self.known()
        round_id, _ = self.round("concept")
        manifest = self.delivery(round_id)
        payload = self.payload(manifest)
        first = self.submit(payload)
        after = (self.job / "project.json").read_bytes()
        self.assertTrue(first["committed"])
        self.assertEqual(first["new_revision"], manifest["project_revision"] + 1)
        self.assertEqual(first, self.submit(payload))
        self.assertEqual(after, (self.job / "project.json").read_bytes())
        different = copy.deepcopy(payload)
        different["note"] = "不同内容"
        self.assertIn("request_id_conflict", self.submit(different, 2)["error"])
        different["request_id"] = "request-002"
        self.assertIn("stale_revision", self.submit(different, 2)["error"])
        self.assertEqual(after, (self.job / "project.json").read_bytes())

    def test_current_question_uses_existing_validator_and_one_field(self):
        self.init()
        manifest = self.delivery()
        payload = self.payload(manifest, "answer-field")
        payload.update(field="answers.note_type", value="single-multiview", items=[])
        before = (self.job / "project.json").read_bytes()
        self.assertIn("stale_question", self.submit(payload, 2)["error"])
        self.assertEqual(before, (self.job / "project.json").read_bytes())
        payload.update(field="answers.product_form", value="puzzle")
        self.assertTrue(self.submit(payload)["committed"])
        self.assertEqual(project.load_project(self.job)["answers"]["product_form"], "puzzle")

    def test_changed_image_history_and_duplicate_items_rejected(self):
        self.init()
        self.known()
        round_id, directory = self.round("concept")
        manifest = self.delivery(round_id)
        payload = self.payload(manifest)
        payload["items"] *= 2
        before = (self.job / "project.json").read_bytes()
        self.assertIn("invalid_items", self.submit(payload, 2)["error"])
        self.assertEqual(before, (self.job / "project.json").read_bytes())
        payload["items"] = payload["items"][:1]
        (directory.parent / "A.png").write_bytes(self.source.read_bytes() + b"changed")
        self.assertIn("changed_image", self.submit(payload, 2)["error"])
        self.delivery(round_id)
        self.assertIn("stale_delivery", self.submit(payload, 2)["error"])

    def test_intent_does_not_select_or_start_generation(self):
        self.init()
        self.known()
        round_id, _ = self.round("concept")
        payload = self.payload(self.delivery(round_id), "request-variation")
        self.submit(payload)
        state = project.load_project(self.job)
        self.assertFalse((state.get("selections") or {}).get("concept"))
        self.assertEqual(state["pending_intents"][0]["action"], "request-variation")
        self.assertEqual(len(state["rounds"]), 1)

    def test_simulated_choice_keeps_inferred_origin_in_receipt_and_references(self):
        import review_checks
        self.init()
        self.known()
        round_id, _ = self.round("concept")
        payload = self.payload(self.delivery(round_id))
        payload["origin"] = "agent-inferred"
        result = self.submit(payload)
        state = project.load_project(self.job)
        self.assertEqual(result["origin"], "agent-inferred")
        self.assertEqual(state["selections"]["concept"]["小云"]["origin"], "agent-inferred")
        references = review_checks._reference_images(self.job, state)
        self.assertEqual(references[-1]["origin"], "agent-inferred")
        state["selections"]["concept"]["小云"].pop("origin")
        references = review_checks._reference_images(self.job, state)
        self.assertEqual(references[-1]["origin"], "reference-derived")

    def test_parallel_cli_writes_and_single_revision(self):
        self.init()
        initial = project.load_project(self.job)["revision"]
        script = str(test_workflow.SKILL / "scripts/project.py")
        workers = [subprocess.Popen([sys.executable, script, "set", str(self.job), f"brief.field{number}=value{number}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for number in range(4)]
        for worker in workers:
            stdout, stderr = worker.communicate(timeout=30)
            self.assertEqual(worker.returncode, 0, stdout + stderr)
        state = project.load_project(self.job)
        self.assertEqual(state["revision"], initial + 4)
        for number in range(4):
            self.assertEqual(state["brief"][f"field{number}"], f"value{number}")

    def test_migration_preview_preserves_unknown_and_stale_plan_refused(self):
        self.init()
        current = project.load_project(self.job)
        current.update(schema_version=1, revision=7, custom={"unknown": "preserved"})
        current.pop("project_id")
        project.write_json(self.job / "project.json", current)
        before = (self.job / "project.json").read_bytes()
        plan_file = self.root / "migration.json"
        self.call("migrate_project.py", self.job, "--out", plan_file)
        self.assertEqual(before, (self.job / "project.json").read_bytes())
        self.call("project.py", "set", self.job, "brief.ip_name=wrong", code=2)
        result = self.call("migrate_project.py", self.job, "--apply", "--plan", plan_file)
        after = project.load_project(self.job)
        self.assertEqual(after["schema_version"], 2)
        self.assertEqual(after["requirements_revision"], 7)
        self.assertEqual(after["custom"], {"unknown": "preserved"})
        self.assertEqual(Path(result["backup"]).read_bytes(), before)
        migrated = (self.job / "project.json").read_bytes()
        self.assertTrue(self.call("migrate_project.py", self.job, "--apply", "--plan", plan_file)["unchanged"])
        self.assertEqual(migrated, (self.job / "project.json").read_bytes())
        project.write_json(self.job / "project.json", current)
        current["custom"]["unknown"] = "changed"
        project.write_json(self.job / "project.json", current)
        self.assertIn("stale_migration_plan", self.call("migrate_project.py", self.job, "--apply", "--plan", plan_file, code=2)["error"])

    def test_future_schema_and_journal_recovery(self):
        self.init()
        state = project.load_project(self.job)
        state["brief"]["recovered"] = "yes"
        state["revision"] += 1
        project.atomic_json(self.job / ".state-transaction.json", {"schema_version": 1, "writes": [{"path": "decisions/recovery.json", "data": {"committed": True}}, {"path": "project.json", "data": state}]})
        self.call("project.py", "show", self.job)
        self.assertTrue((self.job / "decisions/recovery.json").exists())
        self.assertFalse((self.job / ".state-transaction.json").exists())
        self.assertEqual(project.load_project(self.job)["brief"]["recovered"], "yes")
        state["schema_version"] = 99
        project.write_json(self.job / "project.json", state)
        before = (self.job / "project.json").read_bytes()
        self.call("project.py", "set", self.job, "brief.ip_name=wrong", code=2)
        self.assertEqual(before, (self.job / "project.json").read_bytes())

    def test_http_scope_auth_receipt_and_core_selection(self):
        self.init()
        self.known()
        round_id, _ = self.round("concept")
        manifest = self.delivery(round_id)
        payload = self.payload(manifest)
        server = serve_gallery.server_for(self.job, "test-session-token")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        headers = {"Authorization": "Bearer test-session-token", "Content-Type": "application/json", "Origin": base}
        with self.assertRaises(urllib.error.HTTPError) as unauthorized:
            urllib.request.urlopen(base + "/api/state")
        self.assertEqual(unauthorized.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/project.json")
        bad = {**headers, "Origin": "https://untrusted.example"}
        with self.assertRaises(urllib.error.HTTPError) as cross_origin:
            urllib.request.urlopen(urllib.request.Request(base + "/api/decision", data=json.dumps(payload).encode(), headers=bad))
        self.assertEqual(cross_origin.exception.code, 403)
        request = urllib.request.Request(base + "/api/decision", data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(request) as response:
            result = json.load(response)
        self.assertTrue(result["committed"])
        with urllib.request.urlopen(urllib.request.Request(base + "/api/receipt/request-001", headers=headers)) as response:
            self.assertEqual(result, json.load(response))
        self.assertEqual(project.load_project(self.job)["selections"]["concept"]["小云"]["item"], "A")

    def test_restored_done_routes_to_missing_reviews_without_rewriting_stage(self):
        self.init()
        self.known()
        current = project.load_project(self.job)
        current["stage"] = "done"
        current["finals"] = [{"path": "inputs/original.png", "slot": slot["id"], "role": slot["role"]}
                             for slot in project.scene_slots(current)]
        project.save_project(self.job, current)
        before = (self.job / "project.json").read_bytes()
        restored = self.call("project.py", "show", self.job)
        self.assertEqual(restored["stage"], "done")
        self.assertEqual(restored["next"]["action"], "review")
        self.assertTrue(any(item.get("category") == "physics" for item in restored["next"]["findings"]))
        self.assertEqual(before, (self.job / "project.json").read_bytes())
        self.review(["inputs/original.png"])
        restored = self.call("project.py", "show", self.job)
        self.assertEqual(restored["next"]["action"], "package")

    def test_planned_existing_images_resume_review_or_record_not_generation(self):
        self.init()
        self.known()
        for stage in ("concept", "material"):
            round_id, _ = self.round(stage)
            self.call("project.py", "select", self.job, round_id, "A")
        round_id, _ = self.round("cutout")
        self.call("project.py", "select", self.job, round_id, "--approve")
        round_id, _ = self.round("scene", register=False)
        before = (self.job / "project.json").read_bytes()
        restored = self.call("project.py", "show", self.job)
        self.assertEqual(restored["next"]["action"], "review")
        self.assertEqual(restored["next"]["round"], round_id)
        self.assertEqual(before, (self.job / "project.json").read_bytes())
        current = project.load_project(self.job)
        entry = project.find_round(current, round_id)
        data = project.read_json(self.job / entry["dir"] / "round.json")
        paths = [entry["dir"] + "/" + item["image"] for item in data["items"]]
        self.review(paths)
        restored = self.call("project.py", "show", self.job)
        self.assertEqual(restored["next"]["action"], "record-round")

    def test_http_and_wait_choice_use_file_aware_next_action(self):
        self.init()
        self.known()
        for stage in ("concept", "material"):
            round_id, _ = self.round(stage)
            self.call("project.py", "select", self.job, round_id, "A")
        round_id, _ = self.round("cutout")
        self.call("project.py", "select", self.job, round_id, "--approve")
        round_id, _ = self.round("scene", register=False)
        self.call("present_round.py", self.job, "--no-open")
        server = serve_gallery.server_for(self.job, "next-action-test-token")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        endpoint = f"http://127.0.0.1:{server.server_port}/api/state"
        headers = {"Authorization": "Bearer next-action-test-token"}

        def assert_consistent(expected):
            before = (self.job / "project.json").read_bytes()
            cli_state = self.call("project.py", "show", self.job)
            with urllib.request.urlopen(urllib.request.Request(endpoint, headers=headers)) as response:
                http_state = json.load(response)
            waited = self.call("serve_gallery.py", self.job, "--wait-choice", "--after-revision", "0", "--timeout", "1")
            self.assertEqual(cli_state["next"]["action"], expected)
            self.assertEqual(http_state["next"], cli_state["next"])
            self.assertEqual(http_state["manifest"]["next"]["action"], cli_state["next"]["action"])
            self.assertEqual(waited["next"], cli_state["next"])
            self.assertEqual(waited["status"], "changed")
            self.assertEqual(before, (self.job / "project.json").read_bytes())

        assert_consistent("review")
        current = project.load_project(self.job)
        entry = project.find_round(current, round_id)
        data = project.read_json(self.job / entry["dir"] / "round.json")
        self.review([entry["dir"] + "/" + item["image"] for item in data["items"]])
        self.call("present_round.py", self.job, "--no-open")
        assert_consistent("record-round")
        Image.new("RGB", (180, 240), "purple").save(self.job / "done-unreviewed.png")
        with project.project_lock(self.job):
            current = project.load_project(self.job)
            current["stage"] = "done"
            current["rounds"] = []
            current["finals"] = [{"path": "done-unreviewed.png", "slot": slot["id"], "role": slot["role"]}
                                 for slot in project.scene_slots(current)]
            project.save_project(self.job, current)
        self.call("present_round.py", self.job, "--no-open")
        assert_consistent("review")


if __name__ == "__main__":
    unittest.main()
