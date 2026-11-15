"""Temporary synthetic protocol fixtures; no external generation or visual review."""

from __future__ import annotations

import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid
import zlib


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import asset_workflow as assets
import build_gallery
import generation_tasks
import product_contract
import project as state
import serve_gallery
import workflow_status


def png_bytes() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)

    header = struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)
    pixels = zlib.compress((b"\x00" + b"\x80\x90\xa0" * 8) * 8)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


class WorkflowStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="synthetic-workflow-status-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.current = state.new_project("Synthetic workflow fixture", [], workflow="assets")
        self.current.update(created_at="2026-11-01T09:00:00+08:00", updated_at="2026-11-01T10:00:00+08:00")
        self.persist()
        environment = mock.patch.dict(os.environ, {"JARDEN_UPDATE_HOME": str(self.root / "synthetic-runtime")})
        environment.start()
        self.addCleanup(environment.stop)

    def persist(self):
        state.atomic_json(self.root / "project.json", self.current)

    def choose(self, identifiers=None):
        assets.choose(self.root, self.current, identifiers or ["08"], evidence="Synthetic explicit scope only")
        self.persist()

    def candidate(self, **options):
        result = assets.new(self.root, self.current, "08", source_kind="local-authoring", **options)
        self.persist()
        return assets._instance(self.current, result["instances"][0]["id"])

    def document(self, instance):
        path = "synthetic-setting.md"
        (self.root / path).write_text("# Synthetic protocol fixture\n\nNot actual artwork or visual review.\n", encoding="utf-8")
        assets.record(self.root, self.current, {"instance_id": instance["id"], "files": [path],
            "source": {"kind": "local-authoring", "producer": "synthetic-author", "evidence": "Local synthetic document fixture"}})
        self.persist()

    def review(self, instance, verdict="pass"):
        report = assets.review_template(self.current, instance)
        report.update(verdict=verdict, reviewer={"id": "synthetic-reviewer", "independent": True, "viewed_files": True})
        report["checks"] = {criterion: {"verdict": verdict, "evidence": "Synthetic protocol declaration; no actual visual acceptance"}
                            for criterion in report["checks"]}
        report["physical_applicability"]["reason"] = "Synthetic plain text document has no physical product relationships"
        path = f"synthetic-{instance['id']}-review.json"
        state.atomic_json(self.root / path, report)
        descriptor = {"path": path, "sha256": hashlib.sha256((self.root / path).read_bytes()).hexdigest()}
        assets.record(self.root, self.current, {"instance_id": instance["id"], "reviews": {"design": descriptor}})
        self.persist()
        return descriptor

    def adopted(self):
        self.choose()
        instance = self.candidate()
        self.document(instance)
        self.review(instance)
        assets.approve(self.root, self.current, instance["id"], evidence="Synthetic user adoption")
        self.persist()
        return instance

    def generation_plan(self):
        self.choose(["01"])
        result = assets.new(self.root, self.current, "01", count=1, prompt="Synthetic plan; no external image call",
                            authorization={"approved": True, "evidence": "Synthetic authorization fixture"})
        self.persist()
        instance = assets._instance(self.current, result["instances"][0]["id"])
        plan = state.read_json(self.root / instance["generation_plan"])
        return instance, plan

    def status(self):
        result = workflow_status.build_status(self.root, self.current)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["project_id"], self.current["project_id"])
        self.assertEqual(result["revision"], self.current["revision"])
        self.assertEqual(result["observed_at"], self.current["updated_at"])
        self.assertIn(result["status"], {"idle", "working", "waiting", "blocked", "complete"})
        self.assertIn(result["actor"], {"agent", "user", "system", "none"})
        self.assertEqual([phase["id"] for phase in result["phases"]], [identifier for identifier, _ in workflow_status.PHASES])
        self.assertEqual([phase["label"] for phase in result["phases"]],
                         ["理解需求", "选择资产", "设计制作", "质量审查", "确认方案", "整理交付"])
        self.assertTrue(all(phase["status"] in {"done", "current", "pending", "blocked"} for phase in result["phases"]))
        json.dumps(result, ensure_ascii=False, allow_nan=False)
        return result

    def phase_states(self, result):
        return {phase["id"]: phase["status"] for phase in result["phases"]}

    def generation_task(self, result, identifier):
        return next(task for task in result["tasks"] if task["id"] == identifier)

    def tree(self):
        return {path.relative_to(self.root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.root.rglob("*") if path.is_file()}

    def legacy(self, schema=3, status="planned", image=False):
        self.current = state.new_project("Synthetic legacy fixture", [])
        self.current["schema_version"] = schema
        self.current["answers"] = {"product_form": "other", "subject_scope": "single", "subjects": ["小云"], "concept_count": 1}
        self.current.pop("product_contract", None)
        product_contract.initialize(self.current)
        product_contract.set_spec(self.current, "小云", {
            "usage": {"text": "Synthetic desktop protocol fixture", "status": "confirmed", "origin": "agent-inferred"},
            "size": {"mode": "qualitative", "description": "Synthetic relative scale",
                     "measurement_basis": "Synthetic body only", "status": "confirmed", "origin": "agent-inferred",
                     "evidence_kind": "design_target"},
        })
        self.current["image_backend"] = {"kind": "builtin", "tool": "image_gen.imagegen"}
        directory = self.root / "rounds" / "01-concept-s1"
        directory.mkdir(parents=True)
        entry = {"id": directory.name, "dir": directory.relative_to(self.root).as_posix(), "stage": "concept",
                 "subject": "小云", "kind": "alternatives", "status": status,
                 "created_at": "2026-11-01T11:00:00+08:00"}
        self.current["rounds"].append(entry)
        self.persist()
        data = {"id": entry["id"], "stage": "concept", "kind": "alternatives",
                "requirements": state.requirement_signature(self.current, "concept", "小云"),
                "items": [{"id": "A", "image": "A.png", "prompt": "Synthetic protocol fixture"}]}
        state.atomic_json(directory / "round.json", data)
        if image:
            (directory / "A.png").write_bytes(png_bytes())
        return entry, data

    def test_asset_initial_waits_for_scope_without_completing_other_phases(self):
        result = self.status()
        self.assertEqual((result["phase_id"], result["status"], result["actor"]), ("scope", "waiting", "user"))
        self.assertEqual(result["counts"], {"selected": 0, "approved": 0, "pending": 0})
        self.assertEqual(self.phase_states(result)["create"], "pending")
        self.assertFalse(any(phase["status"] == "done" for phase in result["phases"]))

    def test_selected_scope_is_idle_and_only_scope_has_completion_evidence(self):
        self.choose(["02", "04"])
        result = self.status()
        self.assertEqual((result["phase_id"], result["status"], result["current_label"]), ("create", "idle", "资产02"))
        self.assertEqual(result["counts"], {"selected": 2, "approved": 0, "pending": 2})
        self.assertEqual(self.phase_states(result)["scope"], "done")
        for identifier in ("review", "confirm", "deliver"):
            self.assertEqual(self.phase_states(result)[identifier], "pending")
        self.assertNotIn("material", json.dumps(result))

    def test_recorded_document_waits_for_review_and_does_not_infer_pass(self):
        self.choose()
        instance = self.candidate()
        self.document(instance)
        result = self.status()
        self.assertEqual((result["phase_id"], result["status"], result["actor"]), ("review", "waiting", "agent"))
        self.assertEqual(result["counts"]["approved"], 0)
        self.assertEqual(self.phase_states(result)["create"], "done")
        self.assertEqual(self.phase_states(result)["confirm"], "pending")
        self.assertIn("design_review_required", result["tasks"][0]["detail"])

    def test_qualified_document_waits_for_user_adoption(self):
        self.choose()
        instance = self.candidate()
        self.document(instance)
        self.review(instance)
        result = self.status()
        self.assertEqual((result["phase_id"], result["status"], result["actor"]), ("confirm", "waiting", "user"))
        self.assertEqual(self.phase_states(result)["review"], "done")
        self.assertEqual(result["counts"]["approved"], 0)

    def test_adopted_is_idle_until_export_and_export_is_complete(self):
        self.adopted()
        result = self.status()
        self.assertEqual((result["phase_id"], result["status"]), ("deliver", "idle"))
        self.assertEqual(result["counts"], {"selected": 1, "approved": 1, "pending": 0})
        self.assertEqual(self.phase_states(result)["confirm"], "done")
        assets.export(self.root, self.current)
        self.persist()
        result = self.status()
        self.assertEqual((result["phase_id"], result["status"], result["actor"]), ("deliver", "complete", "none"))
        self.assertEqual(self.phase_states(result)["deliver"], "done")

    def test_stale_requirements_cannot_count_an_old_approval(self):
        self.adopted()
        self.current["brief"]["ip_name"] = "Changed synthetic requirements"
        self.persist()
        result = self.status()
        self.assertEqual(result["counts"], {"selected": 1, "approved": 0, "pending": 1})
        self.assertNotEqual(result["status"], "complete")
        self.assertEqual(self.phase_states(result)["confirm"], "pending")

    def test_missing_or_replaced_files_cannot_reuse_approval(self):
        instance = self.adopted()
        path = self.root / instance["files"][0]["path"]
        for replacement in (b"Changed synthetic bytes", None):
            if replacement is None:
                path.unlink()
            else:
                path.write_bytes(replacement)
            with self.subTest(replacement=replacement):
                result = self.status()
                self.assertEqual(result["counts"]["approved"], 0)
                self.assertEqual(result["status"], "blocked")
                self.assertTrue(result["blockers"])

    def test_missing_design_report_is_not_a_current_approval(self):
        instance = self.adopted()
        (self.root / instance["design_review"]["path"]).unlink()
        result = self.status()
        self.assertEqual(result["counts"]["approved"], 0)
        self.assertNotEqual(self.phase_states(result)["review"], "done")

    def test_failed_candidate_requires_revision(self):
        self.choose()
        instance = self.candidate()
        self.document(instance)
        self.review(instance, "needs_revision")
        result = self.status()
        self.assertEqual((result["phase_id"], result["status"]), ("review", "blocked"))
        self.assertEqual(result["counts"]["approved"], 0)

    def test_unsupported_asset_is_blocked_and_keeps_scope(self):
        self.choose(["06"])
        result = self.status()
        self.assertEqual((result["phase_id"], result["status"]), ("create", "blocked"))
        self.assertEqual(result["counts"], {"selected": 1, "approved": 0, "pending": 1})
        self.assertTrue(result["blockers"])

    def test_planned_generation_is_not_working(self):
        instance, _ = self.generation_plan()
        result = self.status()
        self.assertEqual(result["status"], "idle")
        self.assertEqual(self.generation_task(result, instance["generation_task"])["status"], "not_started")
        self.assertFalse((self.root / "tasks").exists())

    def test_requesting_and_unknown_come_from_actual_receipts(self):
        instance, plan = self.generation_plan()
        claim = generation_tasks.begin(self.root, plan)
        result = self.status()
        self.assertEqual((result["phase_id"], result["status"]), ("create", "working"))
        self.assertEqual(self.generation_task(result, instance["generation_task"])["status"], "requesting")
        generation_tasks.unknown(self.root, instance["generation_task"], "Synthetic interrupted request", claim_id=claim["claim_id"])
        result = self.status()
        self.assertEqual((result["phase_id"], result["status"]), ("create", "blocked"))
        self.assertEqual(self.generation_task(result, instance["generation_task"])["status"], "unknown")
        self.assertTrue(any(blocker["id"] == instance["generation_task"] for blocker in result["blockers"]))

    def test_revision_and_observed_time_stay_stable_while_ledger_changes(self):
        instance, plan = self.generation_plan()
        before = self.status()
        claim = generation_tasks.begin(self.root, plan)
        requesting = self.status()
        generation_tasks.failed(self.root, instance["generation_task"], "Synthetic definite failure", claim_id=claim["claim_id"])
        failed = self.status()
        self.assertEqual([value["status"] for value in (before, requesting, failed)], ["idle", "working", "blocked"])
        self.assertEqual(before["revision"], failed["revision"])
        self.assertEqual(before["observed_at"], failed["observed_at"])
        self.assertIsNotNone(failed["ledger_updated_at"])
        self.assertNotEqual(before["recent_events"], failed["recent_events"])
        self.assertEqual(self.generation_task(failed, instance["generation_task"])["status"], "failed")

    def test_completed_generation_receipt_does_not_complete_workflow(self):
        instance, plan = self.generation_plan()
        claim = generation_tasks.begin(self.root, plan)
        (self.root / "synthetic-output.png").write_bytes(png_bytes())
        generation_tasks.complete(self.root, instance["generation_task"], "synthetic-output.png", claim_id=claim["claim_id"])
        result = self.status()
        self.assertEqual(self.generation_task(result, instance["generation_task"])["status"], "completed")
        self.assertEqual(result["counts"]["approved"], 0)
        self.assertNotEqual(result["status"], "complete")

    def test_malformed_ledger_blocks_instead_of_reporting_working_or_success(self):
        instance, plan = self.generation_plan()
        generation_tasks.begin(self.root, plan)
        (self.root / "tasks" / (instance["generation_task"] + ".json")).write_text("{}", encoding="utf-8")
        result = self.status()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.generation_task(result, instance["generation_task"])["status"], "unknown")

    def test_projection_is_read_only_without_nested_locks_or_writes(self):
        instance, plan = self.generation_plan()
        generation_tasks.begin(self.root, plan)
        before = self.tree()
        project_before = copy.deepcopy(self.current)
        with mock.patch.object(state, "project_lock", side_effect=AssertionError("Nested project lock")), \
             mock.patch.object(generation_tasks, "task_lock", side_effect=AssertionError("Unexpected task lock")), \
             mock.patch.object(state, "save_project", side_effect=AssertionError("Unexpected save")), \
             mock.patch.object(state, "write_json", side_effect=AssertionError("Unexpected write")):
            self.status()
            self.status()
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.current, project_before)
        self.assertEqual(self.generation_task(self.status(), instance["generation_task"])["status"], "requesting")

    def test_supplied_read_only_views_are_reused_without_additional_asset_validation(self):
        self.adopted()
        view = assets.view(self.root, self.current)
        expected = workflow_status.build_status(self.root, self.current, asset_view=view, next_action=view["next"])
        with mock.patch.object(assets, "view", side_effect=AssertionError("Duplicate view")), \
             mock.patch.object(assets, "eligibility", side_effect=AssertionError("Duplicate file hashing")), \
             mock.patch.object(state, "next_action", side_effect=AssertionError("Duplicate next")):
            actual = workflow_status.build_status(self.root, self.current, asset_view=view, next_action=view["next"])
        self.assertEqual(actual, expected)

    def test_legacy_generate_suggestion_is_idle_without_a_claim(self):
        self.legacy()
        result = self.status()
        self.assertEqual(result["next_action"], "generate")
        self.assertEqual((result["phase_id"], result["status"]), ("create", "idle"))
        self.assertEqual(result["counts"], {"selected": 1, "approved": 0, "pending": 1})

    def test_human_summary_omits_machine_instructions_but_preserves_route(self):
        self.legacy()
        for action in (*workflow_status.ACTIONS, "future-action"):
            if action == "await-choice":
                continue
            with self.subTest(action=action):
                result = workflow_status.build_status(self.root, self.current, next_action={
                    "action": action, "field": "answers.product_form",
                    "label": "image_backend.kind",
                    "say": "image_backend.kind copy.json --prepare-review dry-run set",
                })
                self.assertEqual(result["next_action"], action)
                for machine_text in ("image_backend.kind", "copy.json", "--prepare-review", "dry-run", " set"):
                    self.assertNotIn(machine_text, result["title"] + result["detail"])
                self.assertRegex(result["detail"], "[\u4e00-\u9fff]")

    def test_await_choice_summary_uses_candidate_instructions_for_the_user(self):
        self.legacy(status="recorded", image=True)
        action = state.next_action(self.current, self.root)
        self.assertEqual(action["action"], "await-choice")
        self.assertIn("回复对比页路径", action["say"])
        result = workflow_status.build_status(self.root, self.current, next_action=action)
        self.assertEqual(result["next_action"], "await-choice")
        self.assertEqual(result["detail"], "比较本轮候选，选一个继续或在聊天提出修改。")
        self.assertEqual(self.phase_states(result)["create"], "done")
        self.assertEqual(self.phase_states(result)["review"], "pending")
        self.assertEqual(next(phase["detail"] for phase in result["phases"] if phase["id"] == "confirm"), result["detail"])
        self.assertIn("回复对比页路径", action["say"])

    def test_creation_summaries_remain_idle_until_a_real_request(self):
        self.legacy()
        details = {
            "backend": "图片能力尚未就绪，回到聊天后由 Agent 核对并继续。",
            "generate": "已保存本轮计划，等待开始制作。",
            "record-asset": "已安排制作，等待文件返回与登记。",
        }
        for action, detail in details.items():
            with self.subTest(action=action):
                result = workflow_status.build_status(self.root, self.current, next_action={"action": action})
                self.assertEqual(result["detail"], detail)
                self.assertNotEqual(result["status"], "working")

    def test_readonly_legacy_requires_migration_without_mutation(self):
        self.legacy(schema=2)
        before = self.tree()
        result = self.status()
        self.assertEqual((result["phase_id"], result["status"], result["actor"]), ("intake", "blocked", "user"))
        self.assertEqual(self.tree(), before)

    def test_legacy_recorded_missing_image_is_blocked_instead_of_waiting_for_choice(self):
        self.legacy(status="recorded")
        result = self.status()
        self.assertEqual((result["phase_id"], result["status"]), ("review", "blocked"))
        self.assertEqual(result["counts"]["approved"], 0)

    def test_legacy_selected_current_image_and_stale_requirements(self):
        entry, data = self.legacy(status="picked", image=True)
        self.current["selections"] = {"concept": {"小云": {"round": entry["id"], "item": "A",
             "image": entry["dir"] + "/A.png", "origin": "user-confirmed"}}}
        self.persist()
        self.assertEqual(self.status()["counts"]["approved"], 1)
        data["requirements"] = "stale-synthetic-signature"
        state.atomic_json(self.root / entry["dir"] / "round.json", data)
        result = self.status()
        self.assertEqual(result["counts"]["approved"], 0)
        self.assertTrue(result["blockers"])

    def test_legacy_pick_cannot_adopt_another_subjects_round(self):
        entry, _ = self.legacy(status="picked", image=True)
        self.current["answers"]["subjects"] = ["另一个主体"]
        self.current["selections"] = {"concept": {"另一个主体": {"round": entry["id"], "item": "A",
             "image": entry["dir"] + "/A.png", "origin": "user-confirmed"}}}
        self.persist()
        result = self.status()
        self.assertEqual(result["counts"]["approved"], 0)
        self.assertTrue(result["blockers"])

    def test_build_data_appends_current_projection_and_handles_new_inputs(self):
        before = build_gallery.build_data(self.root, self.current, prefer_latest=False)
        self.assertEqual(before["workflow_status"]["phase_id"], "scope")
        self.current["brief"]["ip_name"] = "Synthetic new input"
        self.persist()
        after = build_gallery.build_data(self.root, self.current, prefer_latest=False)
        self.assertEqual(self.phase_states(after["workflow_status"])["intake"], "done")
        self.assertEqual(before["project_revision"], after["project_revision"])
        self.assertEqual(before["workflow_status"]["observed_at"], after["workflow_status"]["observed_at"])

    def test_old_snapshot_gets_separate_live_status_without_changing_frozen_business(self):
        self.choose()
        frozen = {"current": None, "picks": [], "history": [], "answers": {"frozen": "old input"},
                  "asset_workflow": {"selected": ["04"], "current": None}}
        manifest = {"project_id": self.current["project_id"], "delivery_id": "D0001", "project_revision": -1,
                    "stage": "assets", "kind": "set", "next": {"action": "approve-asset", "asset_id": "04"},
                    "created_at": "2026-10-01T10:00:00+08:00", "items": [], "view": frozen}
        original = copy.deepcopy(manifest)
        before = self.tree()
        result = build_gallery.delivery_data(self.root, self.current, manifest)
        self.assertEqual(result["next"], manifest["next"])
        self.assertEqual(result["asset_workflow"], frozen["asset_workflow"])
        self.assertEqual(result["answers"], frozen["answers"])
        self.assertEqual(result["snapshot_at"], manifest["created_at"])
        self.assertEqual(result["project_revision"], -1)
        self.assertEqual(result["workflow_status"]["revision"], self.current["revision"])
        self.assertEqual(result["workflow_status"]["current_label"], "资产08")
        self.assertTrue(result["workflow_status"]["snapshot_separate"])
        self.assertEqual(result["workflow_status"]["snapshot_revision"], -1)
        self.assertEqual(manifest, original)
        self.assertEqual(self.tree(), before)

    def test_existing_snapshot_status_remains_frozen(self):
        frozen = self.status()
        manifest = {"delivery_id": "D0001", "project_revision": 0, "view": {"workflow_status": frozen}, "items": []}
        self.choose()
        result = build_gallery.delivery_data(self.root, self.current, manifest)
        self.assertEqual(result["workflow_status"], frozen)
        self.assertEqual(result["workflow_status"]["phase_id"], "scope")

    def test_explicit_empty_snapshot_status_is_preserved(self):
        manifest = {"delivery_id": "D0001", "project_revision": 0, "view": {"workflow_status": {}}, "items": []}
        with mock.patch.object(workflow_status, "build_status", side_effect=AssertionError("Existing snapshot must stay frozen")):
            result = build_gallery.delivery_data(self.root, self.current, manifest)
        self.assertEqual(result["workflow_status"], {})

    def test_api_state_updates_ledger_without_revision_or_snapshot_writes(self):
        instance, plan = self.generation_plan()
        token = uuid.uuid4().hex
        server = serve_gallery.server_for(self.root, token)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()

        def close():
            server.shutdown()
            worker.join(timeout=2)
            server.server_close()

        self.addCleanup(close)

        def read_state():
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request("GET", "/api/state", headers={"Authorization": "Bearer " + token})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                return json.loads(response.read())
            finally:
                connection.close()

        before = read_state()
        project_bytes = (self.root / "project.json").read_bytes()
        claim = generation_tasks.begin(self.root, plan)
        active = read_state()
        generation_tasks.unknown(self.root, instance["generation_task"], "Synthetic unknown", claim_id=claim["claim_id"])
        uncertain = read_state()
        self.assertEqual([value["workflow_status"]["status"] for value in (before, active, uncertain)], ["idle", "working", "blocked"])
        self.assertEqual(before["revision"], uncertain["revision"])
        self.assertIsNone(uncertain["manifest"])
        self.assertEqual((self.root / "project.json").read_bytes(), project_bytes)
        self.assertFalse((self.root / "deliveries").exists())

    def test_render_inlines_archify_svg_and_keeps_payload_escaped(self):
        assets_directory = self.root / "synthetic-render-assets"
        assets_directory.mkdir()
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>工作流</text></svg>'
        for name, content in {"workflow-map.svg": svg, "gallery.css": "body{}", "gallery.js": "void 0;",
                              "gallery.html": '<template id="workflow-map">__WORKFLOW_MAP__</template><style>__GALLERY_CSS__</style><script>__GALLERY_JS__</script><script type="application/json">__GALLERY_DATA__</script>'}.items():
            (assets_directory / name).write_text(content, encoding="utf-8")
        with mock.patch.object(build_gallery, "ASSETS", assets_directory), \
             mock.patch.object(build_gallery, "TEMPLATE", assets_directory / "gallery.html"):
            rendered = build_gallery.render({"workflow_status": self.status(), "title": "</script><script>synthetic</script>"})
        self.assertIn('<template id="workflow-map">' + svg + "</template>", rendered)
        self.assertNotIn("__WORKFLOW_MAP__", rendered)
        self.assertNotIn("</script><script>synthetic", rendered)
        self.assertIn("\\u003c/script\\u003e", rendered)


class ServerIdleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="synthetic-server-idle-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = mock.patch.dict(os.environ, {"JARDEN_UPDATE_HOME": str(self.root / "runtime"),
                                                  "PYTHONDONTWRITEBYTECODE": "1"})
        environment.start()
        self.addCleanup(environment.stop)
        state.atomic_json(self.root / "project.json", state.new_project("Synthetic idle fixture", [], workflow="assets"))
        self.token = uuid.uuid4().hex
        self.server = serve_gallery.server_for(self.root, self.token)
        self.addCleanup(self.server.server_close)

    def start(self):
        worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()

        def stop():
            self.server.shutdown()
            worker.join(timeout=2)

        self.addCleanup(stop)

    def request(self, path, *, method="GET", headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            request_headers = {"Authorization": "Bearer " + self.token, **(headers or {})}
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_authorized_state_poll_renews_activity(self):
        self.start()
        self.server.last_activity = -100.0
        status, payload = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertGreater(self.server.last_activity, 0)
        first_activity = self.server.last_activity
        self.request("/api/state")
        self.assertGreater(self.server.last_activity, first_activity)

    def test_invalid_token_origin_and_unknown_endpoint_do_not_renew_activity(self):
        self.start()
        for path, headers in (
            ("/api/state", {"Authorization": "Bearer " + uuid.uuid4().hex}),
            ("/api/state", {"Origin": "https://invalid.example"}),
            ("/api/unknown", {}),
        ):
            with self.subTest(path=path, headers=headers):
                self.server.last_activity = -100.0
                status, _ = self.request(path, headers=headers)
                self.assertIn(status, {403, 404})
                self.assertEqual(self.server.last_activity, -100.0)

    def test_valid_committed_decision_renews_activity(self):
        self.start()
        self.server.last_activity = -100.0
        receipt = {"ok": True, "committed": True, "request_id": "synthetic-choice"}
        with mock.patch.object(state, "submit_decision", return_value=receipt), \
             mock.patch.object(state, "receipt", return_value=receipt), \
             mock.patch("workbench.ensure_presentation", return_value={"delivery_id": "D0001"}):
            status, payload = self.request("/api/decision", method="POST", body=b"{}",
                                           headers={"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["committed"])
        self.assertGreater(self.server.last_activity, 0)

    def test_rejected_decision_does_not_renew_activity(self):
        self.start()
        self.server.last_activity = -100.0
        with mock.patch.object(state, "submit_decision", side_effect=state.ProjectError("Synthetic invalid decision")):
            status, _ = self.request("/api/decision", method="POST", body=b"{}",
                                    headers={"Content-Type": "application/json"})
        self.assertEqual(status, 409)
        self.assertEqual(self.server.last_activity, -100.0)

    def test_watchdog_waits_for_actual_idle_after_multiple_activity_renewals(self):
        self.server.last_activity = 10.0
        clock = [10.0]
        observations = [(10.4, False), (10.8, True), (11.6, True), (12.3, False), (12.7, False)]
        stopped = mock.Mock(spec=threading.Event)

        def advance(interval):
            self.assertEqual(interval, 0.25)
            stamp, active = observations.pop(0)
            clock[0] = stamp
            if active:
                self.server.record_activity()
            return False

        stopped.wait.side_effect = advance
        with mock.patch.object(serve_gallery.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(self.server, "shutdown") as shutdown:
            serve_gallery.watch_idle(self.server, 1.0, stopped)
        self.assertEqual(stopped.wait.call_count, 5)
        shutdown.assert_called_once_with()
        self.assertEqual(self.server.last_activity, 11.6)

    def test_stopping_watchdog_does_not_shutdown_server(self):
        stopped = threading.Event()
        stopped.set()
        with mock.patch.object(self.server, "shutdown") as shutdown:
            serve_gallery.watch_idle(self.server, 1.0, stopped)
        shutdown.assert_not_called()

    def test_main_survives_active_polling_then_exits_after_real_idle(self):
        ready_file = self.root / "synthetic-ready.json"
        process = subprocess.Popen(
            [sys.executable, str(SCRIPTS / "serve_gallery.py"), str(self.root),
             "--idle-timeout", "1", "--ready-file", str(ready_file)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        def stop():
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=3)

        self.addCleanup(stop)
        deadline = time.monotonic() + 3
        ready = {}
        while time.monotonic() < deadline and process.poll() is None:
            if ready_file.exists():
                ready = json.loads(ready_file.read_text(encoding="utf-8"))
                break
            threading.Event().wait(0.02)
        self.assertTrue(ready, "Synthetic server did not write its ready file")
        from urllib.parse import urlsplit
        parsed = urlsplit(ready["url"])
        token = parsed.fragment.removeprefix("token=")
        active_until = time.monotonic() + 1.4
        polls = 0
        while time.monotonic() < active_until:
            connection = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=2)
            try:
                connection.request("GET", "/api/state", headers={"Authorization": "Bearer " + token})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())["workflow_status"]["schema_version"], 1)
                polls += 1
            finally:
                connection.close()
            threading.Event().wait(0.15)
        self.assertGreater(polls, 4)
        self.assertIsNone(process.poll(), "Active polling must keep the server alive past the original timeout")
        process.communicate(timeout=3)
        self.assertEqual(process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
