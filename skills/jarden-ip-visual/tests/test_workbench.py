from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
import contextlib
import copy
import http.client
import io
import json
import os
from pathlib import Path
import socket
import stat
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

import generation_tasks
import contract_fixtures
import product_contract
import project
import runtime_activity
import serve_gallery
import workbench


def png_bytes(red: int = 100, alpha: bool = False) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)

    header = struct.pack(">IIBBBBB", 32, 32, 8, 6 if alpha else 2, 0, 0, 0)
    if alpha:
        pixels = zlib.compress(b"".join(bytes([0]) + b"".join(
            bytes([red, 120, 200, 255 if 8 <= column < 24 and 8 <= row < 24 else 0])
            for column in range(32)) for row in range(32)))
    else:
        pixels = zlib.compress((bytes([0]) + bytes([red, 120, 200]) * 32) * 32)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve()
        self.root = self.home / "恢复 项目"
        self.root.mkdir()
        environment = mock.patch.dict(os.environ, {
            "JARDEN_UPDATE_HOME": str(self.home / "runtime"),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.current = project.new_project("恢复测试", [])
        self.current["answers"] = {
            "product_form": "plush", "subject_scope": "single", "subjects": ["小云"],
            "concept_count": 1, "note_type": "single-multiview", "delivery": "full-post",
        }
        self.current["image_backend"] = {"kind": "builtin", "tool": "image_gen.imagegen"}
        product_contract.initialize(self.current)
        product_contract.set_spec(self.current, "小云", {
            "usage": {"text": "协议测试：桌面摆放", "status": "confirmed", "origin": "agent-inferred"},
            "size": {"mode": "numeric", "height": 12, "unit": "cm", "measurement_basis": "本体站高，不含扣件",
                     "status": "confirmed", "origin": "agent-inferred", "evidence_kind": "design_target"},
        })
        self.save(self.current)

    def save(self, current):
        project.atomic_json(self.root / "project.json", current)

    def load(self):
        return project.load_project(self.root)

    def run_workbench(self, **arguments):
        return workbench.run_workbench(self.root, open_browser=False, **arguments)

    def make_round(self, images=True, recorded=False):
        with project.project_lock(self.root):
            result = project._invoke(project.cmd_new_round, argparse.Namespace(
                project=str(self.root), stage="concept", subject=None, kind=None, count=1,
                slots=None, based_on=None, note=None,
            ))
        round_file = self.root / result["round_file"]
        plan = project.read_json(round_file)
        plan["items"][0].update(title="柔软小云", prompt="完整正面小云毛绒公仔，保留角色轮廓和五官，使用细密短绒及米白配色，干净背景，柔和灯光，没有文字与水印。")
        project.atomic_json(round_file, plan)
        if images:
            (round_file.parent / "A.png").write_bytes(png_bytes())
            contract_fixtures.generation(self.root, project.find_round(self.load(), result["round"]), plan)
            project.atomic_json(round_file, plan)
        if recorded:
            with project.project_lock(self.root):
                project._invoke(project.cmd_record_round, argparse.Namespace(
                    project=str(self.root), round=result["round"], allow_missing=False,
                ))
        return result["round"], round_file

    def cache_server(self, info, mode=0o600):
        directory = self.root / ".workbench"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / "server.json"
        project.atomic_json(path, info)
        path.chmod(mode)
        return path

    def live_server(self, root=None):
        scope = root or self.root
        token = uuid.uuid4().hex
        server = serve_gallery.server_for(scope, token)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
        worker.start()

        def close():
            server.shutdown()
            worker.join(timeout=2)
            server.server_close()

        self.addCleanup(close)
        origin = f"http://127.0.0.1:{server.server_port}"
        return {"pid": os.getpid(), "project": str(scope), "origin": origin,
                "url": origin + "/gallery.html#token=" + token}

    def track_launch(self, command, **options):
        process = self.real_popen(command, **options)

        def close():
            workbench.stop_created_process(process)
            process.wait(timeout=3)

        self.addCleanup(close)
        self.launched.append(process)
        return process

    def launch_spy(self):
        self.real_popen = subprocess.Popen
        self.launched = []
        return mock.patch.object(workbench.subprocess, "Popen", side_effect=self.track_launch)

    def decision(self, manifest, action="select"):
        return {"schema_version": 1, "request_id": "workbench-request-001", "project_id": manifest["project_id"],
                "delivery_id": manifest["delivery_id"], "expected_revision": manifest["project_revision"],
                "round_id": manifest.get("round_id"), "action": action,
                "items": [{"id": item["id"], "sha256": item["image_sha256"]} for item in manifest["items"]],
                "origin": "agent-inferred", "note": "仅测试状态协议", "keep": ["角色配色"]}

    def test_ready_images_require_explicit_record_and_repeat_reuses_delivery(self):
        round_id, round_file = self.make_round()
        before = self.load()["revision"]
        with mock.patch.object(project, "cmd_record_round", wraps=project.cmd_record_round) as record:
            first = self.run_workbench()
            second = self.run_workbench()
            record.assert_not_called()
        self.assertEqual(first["next"]["action"], "record-round")
        self.assertEqual(first["next"]["round"], round_id)
        self.assertFalse(first["recorded"])
        self.assertTrue(second["presentation"]["reused"])
        self.assertEqual(first["delivery_id"], second["delivery_id"])
        self.assertEqual(self.load()["revision"], before)
        self.assertEqual(self.load()["rounds"][0]["status"], "planned")
        recorded = self.run_workbench(record_ready=True)
        self.assertTrue(recorded["recorded"])
        self.assertEqual(recorded["next"]["action"], "await-choice")
        self.assertEqual(self.load()["revision"], before + 1)
        self.assertEqual(project.read_json(round_file)["items"][0]["status"], "generated")
        self.assertEqual(self.run_workbench(record_ready=True)["delivery_id"], recorded["delivery_id"])

    def test_real_record_gates_reject_incomplete_prompt_and_invalid_image(self):
        round_id, round_file = self.make_round()
        plan = project.read_json(round_file)
        plan["items"][0]["prompt"] = "too short"
        project.atomic_json(round_file, plan)
        before = (self.root / "project.json").read_bytes()
        resumed = self.run_workbench(record_ready=True)
        self.assertEqual(resumed["next"]["action"], "reconcile-task")
        self.assertFalse(resumed["recorded"])
        previous = json.loads(before)
        actual = self.load()
        self.assertEqual({key: value for key, value in actual.items() if key not in {"presentations", "deliveries"}},
                         {key: value for key, value in previous.items() if key not in {"presentations", "deliveries"}})
        plan["items"][0]["prompt"] = "A complete soft character with its approved shape, face and color, full body."
        project.atomic_json(round_file, plan)
        (round_file.parent / "A.png").write_bytes(b"not a real image")
        resumed = self.run_workbench(record_ready=True)
        self.assertEqual(resumed["next"]["action"], "reconcile-task")
        self.assertFalse(resumed["recorded"])
        self.assertEqual(self.load()["rounds"][0]["id"], round_id)
        self.assertEqual(self.load()["rounds"][0]["status"], "planned")
        self.assertEqual(project.read_json(self.root / "deliveries" / resumed["delivery_id"] / "manifest.json")["next"]["action"], "reconcile-task")

    def test_missing_images_never_record_or_start_generation(self):
        self.make_round(images=False)
        with mock.patch.object(project, "cmd_record_round", side_effect=AssertionError("recorded missing images")), \
                mock.patch.object(generation_tasks, "begin", side_effect=AssertionError("started generation")):
            result = self.run_workbench(record_ready=True)
        self.assertEqual(result["next"]["action"], "generate")
        self.assertFalse(result["recorded"])
        self.assertFalse(result["automatic_generation"])
        self.assertFalse(result["should_call"])

    def test_scene_contract_review_is_not_bypassed_by_record_ready(self):
        (self.root / "reference.png").write_bytes(png_bytes(alpha=True))
        for stage in ("concept", "material", "cutout"):
            if stage == "cutout":
                contract_fixtures.identity(self.root, ["reference.png"])
            with project.project_lock(self.root):
                project._invoke(project.cmd_accept_input, argparse.Namespace(
                    project=str(self.root), stage=stage, subject="小云", path="reference.png",
                    origin="agent-inferred", evidence="Synthetic protocol fixture, not visual acceptance",
                ))
        with project.project_lock(self.root):
            project._invoke(project.cmd_scene_plan, argparse.Namespace(project=str(self.root), file=None))
            result = project._invoke(project.cmd_new_round, argparse.Namespace(
                project=str(self.root), stage="scene", subject=None, kind=None, count=1,
                slots=None, based_on=None, note=None,
            ))
        round_file = self.root / result["round_file"]
        plan = project.read_json(round_file)
        plan["items"][0].update(title="桌面小云", prompt="A complete soft cloud character, resting on a desk at the approved scale and front view.")
        project.atomic_json(round_file, plan)
        (round_file.parent / "A.png").write_bytes(png_bytes())
        before = self.load()["revision"]
        with mock.patch.object(project, "cmd_record_round", side_effect=AssertionError("bypassed review")):
            result = self.run_workbench(record_ready=True)
        self.assertEqual(result["next"]["action"], "review")
        self.assertEqual(result["next"]["category"], "contract")
        self.assertFalse(result["recorded"])
        self.assertEqual(self.load()["revision"], before)
        self.assertEqual(self.load()["rounds"][-1]["status"], "planned")

    def test_questions_and_pending_intents_only_return_next(self):
        current = self.load()
        current["answers"] = {}
        self.save(current)
        with mock.patch.object(project, "cmd_select", side_effect=AssertionError("automatic selection")), \
                mock.patch.object(project, "cmd_set", side_effect=AssertionError("automatic answer")):
            question = self.run_workbench(record_ready=True)
        self.assertEqual(question["next"]["field"], "answers.product_form")
        current = copy.deepcopy(self.current)
        current["pending_intents"] = [{"request_id": "intent-001", "status": "pending", "action": "request-redo"}]
        self.save(current)
        with mock.patch.object(project, "cmd_resolve_intent", side_effect=AssertionError("automatic intent resolution")):
            result = self.run_workbench(record_ready=True)
        self.assertEqual(result["next"]["action"], "resolve-intent")
        self.assertEqual(self.load()["pending_intents"], current["pending_intents"])
        self.assertEqual(self.load()["selections"], {})

    def test_same_revision_business_change_cannot_reuse(self):
        self.make_round(recorded=True)
        first = self.run_workbench()
        current = self.load()
        current["brief"]["selling_points"] = ["新增要求"]
        self.save(current)
        result = self.run_workbench()
        self.assertEqual(first["revision"], result["revision"])
        self.assertNotEqual(first["delivery_id"], result["delivery_id"])
        self.assertFalse(result["presentation"]["reused"])

    def test_revision_change_creates_delivery(self):
        first = self.run_workbench()
        current = self.load()
        current["revision"] += 1
        self.save(current)
        second = self.run_workbench()
        self.assertNotEqual(first["delivery_id"], second["delivery_id"])

    def test_presentation_metadata_is_excluded_from_fingerprint(self):
        first = self.run_workbench()
        current = self.load()
        current["presentations"]["host_note"] = "does not change business state"
        current["deliveries"][0]["host_note"] = "does not change business state"
        self.save(current)
        second = self.run_workbench()
        self.assertTrue(second["presentation"]["reused"])
        self.assertEqual(first["delivery_id"], second["delivery_id"])

    def test_source_hash_and_round_metadata_changes_create_new_delivery(self):
        self.make_round(recorded=True)
        first = self.run_workbench()
        manifest_bytes = Path(first["presentation"]["manifest"]).read_bytes()
        entry = self.load()["rounds"][0]
        image = self.root / entry["dir"] / "A.png"
        image.write_bytes(png_bytes(10))
        second = self.run_workbench()
        self.assertNotEqual(first["delivery_id"], second["delivery_id"])
        self.assertEqual(Path(first["presentation"]["manifest"]).read_bytes(), manifest_bytes)
        plan_file = self.root / entry["dir"] / "round.json"
        plan = project.read_json(plan_file)
        plan["items"][0]["title"] = "改了标题"
        project.atomic_json(plan_file, plan)
        third = self.run_workbench()
        self.assertNotEqual(second["delivery_id"], third["delivery_id"])

    def test_new_reference_file_and_removed_image_invalidate_cache(self):
        _, plan_file = self.make_round(images=False)
        plan = project.read_json(plan_file)
        plan["refs"]["source"] = ["inputs/later.png"]
        project.atomic_json(plan_file, plan)
        first = self.run_workbench()
        (self.root / "inputs").mkdir()
        (self.root / "inputs/later.png").write_bytes(png_bytes())
        second = self.run_workbench()
        self.assertNotEqual(first["delivery_id"], second["delivery_id"])
        (plan_file.parent / "A.png").write_bytes(png_bytes())
        third = self.run_workbench()
        (plan_file.parent / "A.png").unlink()
        fourth = self.run_workbench()
        self.assertNotEqual(third["delivery_id"], fourth["delivery_id"])

    def test_missing_gallery_rebuilds_without_new_delivery(self):
        first = self.run_workbench()
        (self.root / "gallery.html").unlink()
        second = self.run_workbench()
        self.assertTrue(Path(second["gallery"]).is_file())
        self.assertEqual(first["delivery_id"], second["delivery_id"])

    def test_changed_derivation_source_and_review_records_invalidate_cache(self):
        _, round_file = self.make_round()
        (self.root / "derivation-source.png").write_bytes(png_bytes())
        plan = project.read_json(round_file)
        plan["items"][0]["sources"] = ["derivation-source.png"]
        project.atomic_json(round_file, plan)
        first = self.run_workbench()
        (self.root / "derivation-source.png").write_bytes(png_bytes(2))
        second = self.run_workbench()
        self.assertNotEqual(first["delivery_id"], second["delivery_id"])
        project.atomic_json(self.root / "reviews/physics/index.json", {"images": {}})
        third = self.run_workbench()
        self.assertNotEqual(second["delivery_id"], third["delivery_id"])

    def test_corrupt_snapshot_is_not_reused_or_overwritten(self):
        first = self.run_workbench()
        snapshot = Path(first["presentation"]["snapshot"])
        snapshot.write_text("damaged snapshot", encoding="utf-8")
        second = self.run_workbench()
        self.assertNotEqual(first["delivery_id"], second["delivery_id"])
        self.assertEqual(snapshot.read_text(encoding="utf-8"), "damaged snapshot")

    def test_corrupt_content_addressed_image_fails_closed(self):
        self.make_round(recorded=True)
        first = self.run_workbench()
        manifest_file = Path(first["presentation"]["manifest"])
        manifest = project.read_json(manifest_file)
        frozen = self.root / manifest["items"][0]["display_path"]
        frozen.write_bytes(b"broken frozen image")
        before = (self.root / "project.json").read_bytes()
        with self.assertRaisesRegex(project.ProjectError, "内容寻址对象损坏"):
            self.run_workbench()
        self.assertEqual(before, (self.root / "project.json").read_bytes())
        self.assertEqual(frozen.read_bytes(), b"broken frozen image")

    def test_corrupt_recovery_record_does_not_trust_revision_alone(self):
        first = self.run_workbench()
        (self.root / ".workbench/presentation.json").write_text("{broken", encoding="utf-8")
        second = self.run_workbench()
        self.assertNotEqual(first["delivery_id"], second["delivery_id"])
        self.assertTrue(self.run_workbench()["presentation"]["reused"])

    def test_untracked_present_is_frozen_again_without_modifying_old_manifest(self):
        first = workbench.presentation.present_round(self.root, open_browser=False)
        original = Path(first["manifest"]).read_bytes()
        result = self.run_workbench()
        self.assertNotEqual(first["delivery_id"], result["delivery_id"])
        self.assertEqual(original, Path(first["manifest"]).read_bytes())

    def test_parallel_entries_share_one_delivery(self):
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(lambda number: self.run_workbench(), range(3)))
        self.assertEqual(len({result["delivery_id"] for result in results}), 1)
        self.assertEqual(len(self.load()["deliveries"]), 1)

    def test_committed_choice_resumes_from_current_state_without_reselect(self):
        self.make_round(recorded=True)
        first = self.run_workbench()
        manifest = project.read_json(Path(first["presentation"]["manifest"]))
        committed = project.submit_decision(self.root, self.decision(manifest))
        current = self.load()
        with mock.patch.object(project, "cmd_select", side_effect=AssertionError("reselected")), \
                mock.patch.object(project, "submit_decision", side_effect=AssertionError("resubmitted")):
            resumed = self.run_workbench()
        self.assertEqual(resumed["committed_receipt"], committed)
        self.assertEqual(resumed["next"], project.next_action(copy.deepcopy(current), self.root))
        self.assertEqual(resumed["next"]["stage"], "material")
        self.assertEqual(self.load()["revision"], current["revision"])
        self.assertEqual(self.load()["selections"], current["selections"])

    def test_committed_intent_is_not_automatically_resolved(self):
        self.make_round(recorded=True)
        first = self.run_workbench()
        manifest = project.read_json(Path(first["presentation"]["manifest"]))
        receipt = project.submit_decision(self.root, self.decision(manifest, "request-variation"))
        result = self.run_workbench()
        self.assertEqual(result["committed_receipt"], receipt)
        self.assertEqual(result["next"]["action"], "resolve-intent")
        self.assertFalse(self.load()["selections"].get("concept"))
        self.assertEqual(len(self.load()["rounds"]), 1)

    def test_unknown_native_task_is_read_only_and_never_resent(self):
        self.make_round(images=False)
        current = self.load()
        plan = generation_tasks.prepare(
            self.root, current, "cover", "intent-001",
            {"prompt": "A complete full body soft cloud character.", "subject_id": "cloud", "references": []},
            {"kind": "builtin", "tool": "image_gen.imagegen"}, {"approved": True},
        )
        generation_tasks.begin(self.root, plan)
        task_file = self.root / "tasks" / (plan["task_id"] + ".json")
        for mark_unknown in (False, True):
            if mark_unknown:
                generation_tasks.unknown(self.root, plan["task_id"], "native connection lost")
            original = task_file.read_bytes()
            with mock.patch.object(generation_tasks, "begin", side_effect=AssertionError("resent")), \
                    mock.patch.object(generation_tasks, "complete", side_effect=AssertionError("invented output")):
                result = self.run_workbench(record_ready=True)
            self.assertEqual(result["next"]["action"], "reconcile-task")
            self.assertEqual(result["project_next"]["action"], "generate")
            self.assertEqual(result["generation_tasks"][0]["resume_status"], "unknown")
            self.assertFalse(result["generation_tasks"][0]["should_call"])
            self.assertEqual(task_file.read_bytes(), original)

    def test_legacy_schemas_render_read_only_without_present_or_server(self):
        for schema in (1, 2):
            with self.subTest(schema=schema):
                current = copy.deepcopy(self.current)
                current["schema_version"] = schema
                if schema == 1:
                    current.pop("project_id")
                self.save(current)
                original = (self.root / "project.json").read_bytes()
                with mock.patch.object(workbench.presentation, "present_round", side_effect=AssertionError("legacy present")), \
                        mock.patch.object(workbench.subprocess, "Popen", side_effect=AssertionError("legacy server")):
                    result = self.run_workbench(serve=True, record_ready=True)
                self.assertEqual(result["next"]["action"], "migration")
                self.assertTrue(result["presentation"]["readonly"])
                self.assertEqual(result["interaction"]["mode"], "static")
                self.assertEqual(original, (self.root / "project.json").read_bytes())
                self.assertFalse((self.root / "deliveries").exists())

    def test_loopback_server_uses_numeric_identity_without_dns(self):
        token = uuid.uuid4().hex
        with mock.patch.object(socket, "getfqdn", side_effect=RuntimeError("reverse DNS unavailable")) as dns:
            server = serve_gallery.server_for(self.root, token)
            self.addCleanup(server.server_close)
            self.assertEqual(server.server_name, "127.0.0.1")
            self.assertGreater(server.server_port, 0)
            self.assertEqual(server.socket.getsockname(), ("127.0.0.1", server.server_port))
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
            worker.start()

            def close():
                server.shutdown()
                worker.join(timeout=2)

            self.addCleanup(close)
            authorized = {"Authorization": "Bearer " + token}
            for headers, expected_status in (
                (authorized, 200),
                ({}, 403),
                ({**authorized, "Host": "example.com"}, 403),
                ({**authorized, "Origin": "http://example.com"}, 403),
            ):
                with self.subTest(headers=list(headers), expected_status=expected_status), contextlib.closing(
                    http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                ) as connection:
                    connection.request("GET", "/api/state", headers=headers)
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                    self.assertEqual(response.status, expected_status)
                    self.assertEqual(payload["ok"], expected_status == 200)
                    if expected_status == 200:
                        self.assertEqual(payload["project_id"], self.current["project_id"])
            dns.assert_not_called()

    def test_health_checked_server_reuses_authenticated_same_project(self):
        info = self.live_server()
        self.cache_server(info)
        with mock.patch.object(workbench.subprocess, "Popen", side_effect=AssertionError("unneeded server")), \
                mock.patch.object(workbench, "healthy_server", wraps=workbench.healthy_server) as health:
            result = self.run_workbench(serve=True)
        health.assert_called_once()
        self.assertTrue(result["server"]["reused"])
        self.assertFalse(result["server"]["created"])
        self.assertEqual(result["url"], info["url"])
        self.assertEqual(result["interaction"]["mode"], "interactive")

    def test_live_pid_alone_is_not_health_and_never_killed(self):
        other = self.home / "other"
        other.mkdir()
        current = project.new_project("other", [])
        project.atomic_json(other / "project.json", current)
        wrong = self.live_server(other)
        wrong["project"] = str(self.root)
        self.cache_server(wrong)
        real_kill = os.kill
        with mock.patch.object(workbench.os, "kill", wraps=real_kill) as signals, \
                mock.patch.object(workbench.subprocess, "Popen", side_effect=OSError("cannot launch")):
            result = self.run_workbench(serve=True)
        self.assertEqual(result["server"]["status"], "unavailable")
        self.assertEqual(result["interaction"]["status"], "not_submitted")
        self.assertIn("尚未提交", result["interaction"]["say"])
        self.assertTrue(all(call.args[1] == 0 for call in signals.call_args_list))

    def test_invalid_server_records_are_not_reused(self):
        info = self.live_server()
        variants = [
            ({**info, "project": str(self.home)}, 0o600),
            ({**info, "project_id": "wrong"}, 0o600),
            ({**info, "expires_at": time.time() - 1}, 0o600),
            ({**info, "pid": True}, 0o600),
            ({**info, "url": info["origin"] + "/gallery.html#token=wrong"}, 0o600),
            (info, 0o644),
        ]
        for record, mode in variants:
            with self.subTest(record=record, mode=mode):
                self.cache_server(record, mode)
                with mock.patch.object(workbench.subprocess, "Popen", side_effect=OSError("cannot launch")):
                    result = self.run_workbench(serve=True)
                self.assertEqual(result["server"]["status"], "unavailable")
                self.assertIsNone(result["url"])

    def test_health_rejects_unsafe_urls_before_sending_token(self):
        info = {"pid": os.getpid(), "project": str(self.root)}
        urls = [
            "https://127.0.0.1:1234/gallery.html#token=abc",
            "http://example.com:1234/gallery.html#token=abc",
            "http://localhost:1234/gallery.html#token=abc",
            "http://127.0.0.1:1234/gallery.html?token=abc",
            "http://user:pass@127.0.0.1:1234/gallery.html#token=abc",
            "http://127.0.0.1:1234/gallery.html#token=abc&token=other",
            "http://127.0.0.1:1234/gallery.html#token=abc&extra=other",
            "http://127.0.0.1:1234/other#token=abc",
            "http://127.0.0.1:99999/gallery.html#token=abc",
        ]
        with mock.patch.object(workbench.http.client, "HTTPConnection", side_effect=AssertionError("sent token")):
            for url in urls:
                with self.subTest(url=url):
                    self.assertFalse(workbench.healthy_server(self.root, self.current["project_id"], {**info, "url": url}))

    def test_real_child_ready_url_and_cache_reuse(self):
        with self.launch_spy() as launch:
            first = self.run_workbench(serve=True)
            second = self.run_workbench(serve=True)
        self.assertEqual(launch.call_count, 1, workbench.redact_server_error(repr((first["server"], second["server"]))))
        self.assertTrue(first["server"]["created"], workbench.redact_server_error(repr(first["server"])))
        self.assertTrue(second["server"]["reused"])
        self.assertEqual(first["url"], second["url"])
        self.assertIn("#token=", first["url"])
        cache = self.root / ".workbench/server.json"
        self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o600)
        self.assertEqual(project.read_json(cache)["pid"], self.launched[0].pid)
        self.assertIsNone(self.launched[0].poll())
        command = launch.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(Path(command[1]), SCRIPTS / "serve_gallery.py")
        self.assertIn("--ready-file", command)
        self.assertIn("--idle-timeout", command)
        self.assertEqual(list(cache.parent.glob(".ready-*.json")), [])
        self.assertEqual(first["open"]["status"], "unavailable")
        stderr_log = Path(first["server"]["stderr_log"])
        self.assertEqual(stat.S_IMODE(stderr_log.stat().st_mode), 0o600)
        diagnostics = stderr_log.read_text(encoding="utf-8")
        self.assertIn("startup: serving", diagnostics)
        self.assertNotIn(first["url"].split("#token=")[1], diagnostics)

    def test_child_has_bounded_ttl(self):
        with mock.patch.object(workbench, "SERVER_TTL", 4), self.launch_spy():
            result = self.run_workbench(serve=True)
        self.assertTrue(result["server"]["created"], workbench.redact_server_error(repr(result["server"])))
        self.launched[0].wait(timeout=8)
        self.assertEqual(self.launched[0].returncode, 0)
        self.assertFalse(workbench.healthy_server(self.root, self.current["project_id"], result["server"]))

    def test_start_timeout_only_terminates_created_child_within_three_seconds(self):
        real_popen = subprocess.Popen
        created = []

        def slow_launch(command, **options):
            child = real_popen([sys.executable, "-c", "import time; time.sleep(20)"], **options)
            created.append(child)
            self.addCleanup(workbench.stop_created_process, child)
            return child

        started = time.monotonic()
        with mock.patch.object(workbench, "SERVER_READY_TIMEOUT", 0.3), \
                mock.patch.object(workbench.subprocess, "Popen", side_effect=slow_launch):
            result = self.run_workbench(serve=True)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(result["server"]["status"], "unavailable")
        self.assertIsNotNone(created[0].poll())
        self.assertFalse((self.root / ".workbench/server.json").exists())

    def test_child_failure_preserves_private_diagnostics_and_redacts_reason(self):
        self.save({**self.current, "schema_version": 999})
        with self.assertRaisesRegex(project.ProjectError, "child_exit=2") as raised:
            workbench.ensure_server(self.root, self.current["project_id"])
        logs = list((self.root / ".workbench").glob(".server-*.stderr.log"))
        self.assertEqual(len(logs), 1)
        self.assertEqual(stat.S_IMODE(logs[0].stat().st_mode), 0o600)
        self.assertIn("startup: load_project", logs[0].read_text(encoding="utf-8"))
        self.assertIn("startup: load_project", str(raised.exception))
        logs[0].write_text("token=private-token Bearer private-bearer", encoding="utf-8")
        detail = workbench.server_failure_detail(logs[0])
        self.assertNotIn("private-token", detail)
        self.assertNotIn("private-bearer", detail)
        self.save(self.current)
        with mock.patch.object(workbench.subprocess, "Popen", side_effect=OSError("token=private-launch")):
            result = self.run_workbench(serve=True)
        self.assertNotIn("private-launch", result["server"]["reason"])

    def test_ready_file_with_wrong_pid_cannot_be_accepted(self):
        healthy = self.live_server()
        fake = mock.Mock(pid=os.getpid() + 100000)
        fake.poll.return_value = None
        fake.wait.return_value = 0

        def launch(command, **options):
            ready = Path(command[command.index("--ready-file") + 1])
            project.atomic_json(ready, healthy)
            ready.chmod(0o600)
            return fake

        with mock.patch.object(workbench, "SERVER_READY_TIMEOUT", 0.1), \
                mock.patch.object(workbench.subprocess, "Popen", side_effect=launch):
            result = self.run_workbench(serve=True)
        self.assertEqual(result["server"]["status"], "unavailable")
        fake.terminate.assert_called_once_with()
        self.assertFalse((self.root / ".workbench/server.json").exists())

    def test_no_open_skips_dispatch_and_open_dispatch_is_never_verified(self):
        with mock.patch.object(workbench.presentation, "dispatch_open", side_effect=AssertionError("opened")):
            result = self.run_workbench()
        self.assertEqual(result["open"]["status"], "unavailable")
        info = self.live_server()
        self.cache_server(info)
        with mock.patch.object(workbench.presentation, "dispatch_open", return_value={"status": "open_dispatched"}) as dispatch:
            result = workbench.run_workbench(self.root, serve=True)
        dispatch.assert_called_once_with(info["url"])
        self.assertEqual(result["open"]["status"], "open_dispatched")
        with mock.patch.object(workbench.presentation, "dispatch_open", return_value={"status": "opened_verified"}):
            result = workbench.run_workbench(self.root)
        self.assertEqual(result["open"]["status"], "open_dispatched")

    def test_headless_open_is_unavailable(self):
        with mock.patch.object(workbench.presentation.sys, "platform", "linux"), \
                mock.patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}):
            result = workbench.run_workbench(self.root)
        self.assertEqual(result["open"]["status"], "unavailable")

    def test_symlinked_workbench_does_not_write_outside_project(self):
        outside = self.home / "outside"
        outside.mkdir()
        (self.root / ".workbench").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(project.ProjectError, "unsafe_workbench"):
            self.run_workbench()
        self.assertEqual(list(outside.iterdir()), [])

    def test_server_record_symlink_is_not_read_or_overwritten(self):
        info = self.live_server()
        outside = self.home / "private-server.json"
        project.atomic_json(outside, info)
        directory = self.root / ".workbench"
        directory.mkdir()
        (directory / "server.json").symlink_to(outside)
        before = outside.read_bytes()
        with mock.patch.object(workbench.subprocess, "Popen", side_effect=AssertionError("spawned")):
            result = self.run_workbench(serve=True)
        self.assertEqual(result["server"]["status"], "unavailable")
        self.assertEqual(outside.read_bytes(), before)

    def test_cli_holds_execution_lease_through_open_and_json_emit(self):
        def require_lease():
            leases = runtime_activity.active_leases(runtime_activity.instance_home(), runtime_activity.real_root())
            self.assertTrue(any(lease["pid"] == os.getpid() for lease in leases))

        original_emit = project.emit
        original_present = workbench.ensure_presentation

        def present(root):
            require_lease()
            return original_present(root)

        def dispatch(target):
            require_lease()
            return {"status": "open_dispatched"}

        def emit(payload):
            require_lease()
            return original_emit(payload)

        with mock.patch.object(workbench, "ensure_presentation", side_effect=present), \
                mock.patch.object(workbench.presentation, "dispatch_open", side_effect=dispatch), \
                mock.patch.object(project, "emit", side_effect=emit), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(workbench.main([str(self.root)]), 0)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        self.assertEqual(runtime_activity.active_leases(runtime_activity.instance_home(), runtime_activity.real_root()), [])

    def test_cli_returns_json_error_and_releases_lease_after_real_gate_failure(self):
        _, plan_file = self.make_round()
        plan = project.read_json(plan_file)
        plan["items"][0]["title"] = ""
        project.atomic_json(plan_file, plan)
        original_emit = project.emit

        def emit(payload):
            leases = runtime_activity.active_leases(runtime_activity.instance_home(), runtime_activity.real_root())
            self.assertTrue(any(lease["pid"] == os.getpid() for lease in leases))
            return original_emit(payload)

        with mock.patch.object(project, "emit", side_effect=emit), contextlib.redirect_stdout(io.StringIO()) as output:
            code = workbench.main([str(self.root), "--no-open", "--record-ready"])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output.getvalue())["ok"])
        self.assertEqual(runtime_activity.active_leases(runtime_activity.instance_home(), runtime_activity.real_root()), [])

    def test_real_cli_returns_json_and_keeps_flags(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / "workbench.py"), str(self.root), "--no-open"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["project_id"], self.current["project_id"])
        self.assertEqual(payload["interaction"]["status"], "not_submitted")
        self.assertEqual(payload["open"]["status"], "unavailable")

    def test_python_310_syntax_has_no_inline_comments(self):
        source = (SCRIPTS / "workbench.py").read_text(encoding="utf-8")
        ast.parse(source, feature_version=(3, 10))
        self.assertNotIn("  #", source)


if __name__ == "__main__":
    unittest.main()
