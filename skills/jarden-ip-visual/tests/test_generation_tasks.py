from __future__ import annotations

import ast
import base64
import copy
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generation_tasks


def png_bytes(red: int = 125, filter_byte: int = 0) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xffffffff
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(bytes([filter_byte, red, 60, 200]))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


class GenerationTaskTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.root = self.home / "project"
        self.root.mkdir()
        self.project = {"schema_version": 2, "project_id": "stable-project", "revision": 1}
        (self.root / "project.json").write_text(json.dumps(self.project), encoding="utf-8")
        (self.root / "inputs").mkdir()
        self.reference = self.root / "inputs" / "reference.png"
        self.reference.write_bytes(png_bytes())
        self.compiled = {
            "prompt": "A complete blue cloth character with the approved proportions.",
            "subject_id": "cloud", "size": "1024x1024",
            "references": [{"path": "inputs/reference.png", "role": "identity"}],
            "output_path": "images/old.png", "compiled_at": "old-time", "delivery_id": "old-delivery",
        }
        self.backend = {"kind": "builtin", "tool": "host.imagegen", "model": "discovered-model"}
        self.authorization = {"approved": True, "intent_id": "intent-1", "source": "user-explicit"}

    def prepare(self, **changes):
        arguments = {"root": self.root, "project": self.project, "slot_id": "cover:cloud", "intent_id": "intent-1",
                     "compiled": self.compiled, "backend": self.backend, "authorization": self.authorization}
        arguments.update(changes)
        return generation_tasks.prepare(**arguments)

    def output(self, name="images/result.png", red=125):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png_bytes(red))
        return path

    def record_path(self, plan):
        return self.root / "tasks" / (plan["task_id"] + ".json")

    def test_prepare_is_pure_and_separate_from_task_status(self):
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-be-read"}), mock.patch.object(generation_tasks.os, "getenv", side_effect=AssertionError("credentials read")):
            plan = self.prepare()
            self.assertEqual(plan, self.prepare())
            self.assertEqual(generation_tasks.resume_status(self.root, plan)["status"], "not_started")
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertNotIn("status", plan)
        self.assertNotIn("must-not-be-read", json.dumps(plan))
        self.assertFalse((self.root / "tasks").exists())

    def test_stable_signature_ignores_order_delivery_destinations_and_timestamps(self):
        original = self.prepare()
        compiled = dict(reversed(list(self.compiled.items())))
        compiled.update(output_path="elsewhere/new.png", compiled_at="new-time", delivery_id="new-delivery",
                        dest="different/destination.png", dest_path="other.png", timestamp="newer-time",
                        delivery={"image_order": [3, 2, 1]}, fingerprint="path-dependent-legacy-fingerprint")
        changed_project = {**self.project, "revision": 700, "stage": "done", "delivery": "grid-9"}
        changed = self.prepare(compiled=compiled, project=changed_project, backend=dict(reversed(list(self.backend.items()))))
        self.assertEqual(original["task_id"], changed["task_id"])
        self.assertEqual(original["request_sha256"], changed["request_sha256"])
        self.assertEqual(original["identity"]["compile_fingerprint"], changed["identity"]["compile_fingerprint"])

    def test_copied_project_and_reference_paths_keep_same_identity(self):
        original = self.prepare()
        clone = self.home / "copied-project"
        shutil.copytree(self.root, clone)
        renamed = clone / "inputs" / "renamed.png"
        (clone / "inputs" / "reference.png").rename(renamed)
        compiled = copy.deepcopy(self.compiled)
        compiled["references"][0]["path"] = str(renamed)
        changed = self.prepare(root=clone, compiled=compiled)
        self.assertEqual(original["task_id"], changed["task_id"])
        generation_tasks.begin(self.root, original)
        self.output()
        generation_tasks.complete(self.root, original["task_id"], "images/result.png")
        shutil.copytree(self.root / "tasks", clone / "tasks")
        shutil.copytree(self.root / "images", clone / "images")
        self.assertTrue(generation_tasks.begin(clone, changed)["reused"])

    def test_semantic_changes_backend_slot_subject_and_new_intent_change_identity(self):
        original = self.prepare()
        variants = [
            {"compiled": {**self.compiled, "prompt": "A materially different prompt"}},
            {"compiled": {**self.compiled, "subject_id": "rain"}},
            {"backend": {**self.backend, "tool": "another.native.tool"}},
            {"backend": {**self.backend, "model": "another-model"}},
            {"slot_id": "detail:cloud"},
            {"intent_id": "explicit-regenerate-2", "authorization": {"authorized": True}},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertNotEqual(original["task_id"], self.prepare(**variant)["task_id"])
        self.reference.write_bytes(png_bytes(30))
        self.assertNotEqual(original["task_id"], self.prepare()["task_id"])

    def test_reference_hash_and_path_scope_are_checked(self):
        digest = hashlib.sha256(self.reference.read_bytes()).hexdigest()
        valid = copy.deepcopy(self.compiled)
        valid["references"][0]["sha256"] = digest
        self.assertEqual(self.prepare()["task_id"], self.prepare(compiled=valid)["task_id"])
        valid["references"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(generation_tasks.TaskError, "reference_changed"):
            self.prepare(compiled=valid)
        for path in (str(self.home / "outside.png"), "../outside.png", "inputs/missing.png"):
            with self.subTest(path=path):
                compiled = {**self.compiled, "references": [path]}
                with self.assertRaises(generation_tasks.TaskError):
                    self.prepare(compiled=compiled)

    def test_invalid_json_and_secrets_are_rejected_without_recording(self):
        for backend in ({"kind": "api", "api_key": "not-a-real-secret"}, {"kind": "connected", "headers": {"Authorization": "Bearer value"}}, {"kind": "builtin", "temperature": float("nan")}):
            with self.subTest(backend=backend), self.assertRaises(generation_tasks.TaskError):
                self.prepare(backend=backend)
        with self.assertRaises(generation_tasks.TaskError):
            self.prepare(compiled={1: "non-string-key"})
        self.assertFalse((self.root / "tasks").exists())

    def test_project_and_authorization_scope_must_match(self):
        with self.assertRaisesRegex(generation_tasks.TaskError, "project_mismatch"):
            self.prepare(project={"project_id": "another-project"})
        for authorization in ({"approved": True, "intent_id": "other"}, {"approved": True, "slot_id": "other"}, {"approved": True, "project_id": "other"}):
            with self.subTest(authorization=authorization), self.assertRaisesRegex(generation_tasks.TaskError, "scope_mismatch"):
                self.prepare(authorization=authorization)
        for authorization in ({}, {"approved": False}, {"approved": "yes"}, {"approved": True, "authorized": False}):
            with self.subTest(authorization=authorization):
                plan = self.prepare(authorization=authorization)
                with self.assertRaisesRegex(generation_tasks.TaskError, "authorization_required"):
                    generation_tasks.begin(self.root, plan)
                self.assertFalse(self.record_path(plan).exists())

    def test_claim_is_committed_before_tool_and_only_first_begin_can_call(self):
        plan = self.prepare()
        claimed = generation_tasks.begin(self.root, plan, tool_request_id="native-request-1")
        self.assertTrue(claimed["should_call"])
        self.assertTrue(claimed["claimed"])
        durable = json.loads(self.record_path(plan).read_text())
        self.assertEqual(durable["status"], "requesting")
        self.assertEqual(durable["claim_id"], claimed["claim_id"])
        self.assertEqual(durable["tool_request_id"], "native-request-1")
        before = self.record_path(plan).read_bytes()
        for entrypoint in (generation_tasks.begin, generation_tasks.claim, generation_tasks.start):
            result = entrypoint(self.root, plan, retry=True)
            self.assertFalse(result["should_call"])
            self.assertFalse(result["claimed"])
            self.assertEqual(result["resume_status"], "unknown")
        self.assertEqual(before, self.record_path(plan).read_bytes())

    def test_claim_survives_exception_in_project_transaction(self):
        import project
        plan = self.prepare()
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            with project.project_lock(self.root):
                generation_tasks.begin(self.root, plan)
                self.assertEqual(json.loads(self.record_path(plan).read_text())["status"], "requesting")
                raise RuntimeError("interrupted")
        self.assertFalse(generation_tasks.begin(self.root, plan)["should_call"])

    def test_parallel_threads_claim_once(self):
        plan = self.prepare()
        barrier = threading.Barrier(8)

        def claim_once():
            barrier.wait(timeout=10)
            return generation_tasks.begin(self.root, plan)

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda number: claim_once(), range(8)))
        self.assertEqual(sum(result["should_call"] for result in results), 1)
        self.assertEqual(len({result["claim_id"] for result in results}), 1)
        self.assertEqual(len(list((self.root / "tasks").glob("gen-*.json"))), 1)

    def test_parallel_processes_claim_once(self):
        plan = self.prepare()
        plan_path = self.home / "plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        code = (
            "import json,sys; from pathlib import Path; "
            "sys.path.insert(0,sys.argv[1]); import generation_tasks; "
            "plan=json.loads(Path(sys.argv[3]).read_text()); "
            "print(json.dumps(generation_tasks.begin(sys.argv[2],plan)))"
        )
        workers = [subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS), str(self.root), str(plan_path)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for number in range(5)]
        results = []
        for worker in workers:
            stdout, stderr = worker.communicate(timeout=20)
            self.assertEqual(worker.returncode, 0, stdout + stderr)
            results.append(json.loads(stdout))
        self.assertEqual(sum(result["should_call"] for result in results), 1)

    def test_killed_requesting_process_is_not_automatically_resent(self):
        plan = self.prepare()
        plan_path = self.home / "plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        code = (
            "import json,sys,time; from pathlib import Path; "
            "sys.path.insert(0,sys.argv[1]); import generation_tasks; "
            "plan=json.loads(Path(sys.argv[3]).read_text()); "
            "print(json.dumps(generation_tasks.begin(sys.argv[2],plan)),flush=True); time.sleep(90)"
        )
        worker = subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS), str(self.root), str(plan_path)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(json.loads(worker.stdout.readline())["should_call"])
        finally:
            worker.kill()
            worker.communicate(timeout=10)
        status = generation_tasks.resume_status(self.root, plan["task_id"])
        self.assertEqual(status["status"], "requesting")
        self.assertEqual(status["resume_status"], "unknown")
        self.assertFalse(generation_tasks.begin(self.root, plan, retry=True)["should_call"])

    def test_altered_plan_and_same_key_record_conflict_fail_closed(self):
        plan = self.prepare()
        changed = copy.deepcopy(plan)
        changed["compiled"]["prompt"] = "Different meaning under an unchanged key"
        with self.assertRaisesRegex(generation_tasks.TaskError, "plan_conflict"):
            generation_tasks.begin(self.root, changed)
        generation_tasks.begin(self.root, plan)
        task = json.loads(self.record_path(plan).read_text())
        task["request"]["compiled"]["prompt"] = "Tampered receipt content"
        self.record_path(plan).write_text(json.dumps(task), encoding="utf-8")
        before = self.record_path(plan).read_bytes()
        with self.assertRaisesRegex(generation_tasks.TaskError, "task_conflict"):
            generation_tasks.begin(self.root, plan)
        self.assertEqual(before, self.record_path(plan).read_bytes())

    def test_reference_changed_after_prepare_cannot_be_claimed(self):
        plan = self.prepare()
        self.reference.write_bytes(png_bytes(20))
        with self.assertRaisesRegex(generation_tasks.TaskError, "plan_conflict"):
            generation_tasks.begin(self.root, plan)
        self.assertFalse(self.record_path(plan).exists())

    def test_no_completion_without_claim_or_actual_image(self):
        plan = self.prepare()
        self.output()
        with self.assertRaisesRegex(generation_tasks.TaskError, "claim_required"):
            generation_tasks.complete(self.root, plan["task_id"], "images/result.png")
        claimed = generation_tasks.begin(self.root, plan)
        for outputs in ([], None, {"tool": "host.imagegen", "tool_request_id": "request-1"}):
            with self.subTest(outputs=outputs), self.assertRaises(generation_tasks.TaskError):
                generation_tasks.complete(self.root, plan["task_id"], outputs, claim_id=claimed["claim_id"])
        for content in (b"not an image", b"\x89PNG\r\n\x1a\n", png_bytes()[:-8], b"\xff\xd8\xff\xd9", b"RIFF\x04\x00\x00\x00WEBP"):
            path = self.output()
            path.write_bytes(content)
            with self.subTest(content=content[:12]), self.assertRaisesRegex(generation_tasks.TaskError, "invalid_image"):
                generation_tasks.complete(self.root, plan["task_id"], str(path))
        self.assertEqual(json.loads(self.record_path(plan).read_text())["status"], "requesting")

    def test_header_only_and_malformed_images_cannot_complete(self):
        plan = self.prepare()
        generation_tasks.begin(self.root, plan)
        frame = b"\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        scan = b"\xff\xda\x00\x0c\x03\x01\x00\x02\x00\x03\x00\x00\x3f\x00"
        webp = b"VP8 \x0c\x00\x00\x00\x00\x00\x00\x9d\x01\x2a\x01\x00\x01\x00\x00\x00"
        lossless = b"VP8L\x06\x00\x00\x00\x2f\x00\x00\x00\x00\x00"
        malformed = [
            png_bytes(filter_byte=5),
            png_bytes(filter_byte=255),
            b"\xff\xd8" + frame + scan + b"fake pixels" + b"\xff\xd9",
            b"RIFF" + struct.pack("<I", len(webp) + 4) + b"WEBP" + webp,
            b"RIFF" + struct.pack("<I", len(lossless) + 4) + b"WEBP" + lossless,
        ]
        image = self.output()
        for content in malformed:
            with self.subTest(header=content[:20]):
                image.write_bytes(content)
                with self.assertRaisesRegex(generation_tasks.TaskError, "invalid_image"):
                    generation_tasks.complete(self.root, plan["task_id"], image)
                self.assertEqual(generation_tasks.resume_status(self.root, plan)["status"], "requesting")

    def test_real_jpeg_progressive_jpeg_and_webp_images_complete(self):
        fixtures = {
            "jpeg": "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAb/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAABQb/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCeAWhN/9k=",
            "progressive": "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wgARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAVAQEBAAAAAAAAAAAAAAAAAAAEBf/aAAwDAQACEAMQAAABnC0n/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABD3/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPxB//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPxB//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxB//9k=",
            "webp": "UklGRjgAAABXRUJQVlA4ICwAAACQAQCdASoBAAEAAUAmJaACdLoAA5gA/u9RL/6tD/1aH/q0P3Pv2pRBSVsAAA==",
            "lossless": "UklGRh4AAABXRUJQVlA4TBEAAAAvAAAAAAdQnvYVuf+BiOh/AAA=",
        }
        for name, encoded in fixtures.items():
            with self.subTest(format=name):
                plan = self.prepare(slot_id="fixture:" + name)
                generation_tasks.begin(self.root, plan)
                image = self.output("images/" + name + ".image")
                image.write_bytes(base64.b64decode(encoded))
                completed = generation_tasks.complete(self.root, plan["task_id"], image)
                receipt = completed["outputs"][0]
                self.assertEqual((receipt["width"], receipt["height"]), (1, 1))
                self.assertEqual(receipt["format"], "jpeg" if name in ("jpeg", "progressive") else "webp")
                self.assertTrue(generation_tasks.resume_status(self.root, plan)["reused"])

    def test_complete_validates_hash_scope_duplicates_and_symlinks(self):
        plan = self.prepare()
        generation_tasks.begin(self.root, plan)
        image = self.output()
        outside = self.home / "outside.png"
        outside.write_bytes(png_bytes())
        for outputs in (str(outside), "../outside.png", "images/missing.png", [image, image], {"path": image, "sha256": "0" * 64}):
            with self.subTest(outputs=outputs), self.assertRaises(generation_tasks.TaskError):
                generation_tasks.complete(self.root, plan["task_id"], outputs)
        linked = self.root / "images" / "linked.png"
        linked.symlink_to(outside)
        with self.assertRaisesRegex(generation_tasks.TaskError, "unsafe_symlink"):
            generation_tasks.complete(self.root, plan["task_id"], linked)
        self.assertEqual(json.loads(self.record_path(plan).read_text())["status"], "requesting")

    def test_complete_receipt_and_retries_reuse_completed_bytes_without_writes(self):
        plan = self.prepare()
        claimed = generation_tasks.begin(self.root, plan)
        image = self.output()
        descriptor = {"path": str(image), "sha256": hashlib.sha256(image.read_bytes()).hexdigest()}
        completed = generation_tasks.complete(self.root, plan["task_id"], descriptor,
                                               claim_id=claimed["claim_id"], tool_request_id="native-42")
        self.assertEqual(completed["status"], "completed")
        self.assertFalse(completed["reused"])
        self.assertFalse(completed["should_call"])
        self.assertEqual(completed["outputs"][0]["path"], "images/result.png")
        self.assertEqual(completed["outputs"][0]["format"], "png")
        self.assertEqual(completed["outputs"][0]["sha256"], descriptor["sha256"])
        before = (self.record_path(plan).read_bytes(), image.read_bytes(), image.stat().st_mtime_ns)
        for result in (generation_tasks.complete(self.root, plan["task_id"], image, tool_request_id="native-42"),
                       generation_tasks.begin(self.root, plan, retry=True), generation_tasks.resume_status(self.root, plan)):
            self.assertTrue(result["reused"])
            self.assertFalse(result["should_call"])
        self.assertEqual(before, (self.record_path(plan).read_bytes(), image.read_bytes(), image.stat().st_mtime_ns))

    def test_completed_output_tamper_never_reuses_or_resends(self):
        plan = self.prepare()
        generation_tasks.begin(self.root, plan)
        image = self.output()
        original = image.read_bytes()
        generation_tasks.complete(self.root, plan["task_id"], image)
        record_before = self.record_path(plan).read_bytes()
        for altered in (png_bytes(50), b"corrupt", None):
            if altered is None:
                image.unlink()
            else:
                image.write_bytes(altered)
            status = generation_tasks.resume_status(self.root, plan["task_id"])
            self.assertEqual(status["resume_status"], "output_invalid")
            self.assertFalse(status["reused"])
            self.assertFalse(status["should_call"])
            with self.assertRaisesRegex(generation_tasks.TaskError, "output_invalid"):
                generation_tasks.begin(self.root, plan, retry=True)
        self.assertEqual(record_before, self.record_path(plan).read_bytes())
        image.write_bytes(original)
        self.assertTrue(generation_tasks.begin(self.root, plan)["reused"])

    def test_unknown_never_retries_but_can_reconcile_real_outputs(self):
        plan = self.prepare()
        claimed = generation_tasks.begin(self.root, plan)
        first = generation_tasks.unknown(self.root, plan["task_id"], "Tool connection lost", claim_id=claimed["claim_id"])
        self.assertEqual(first["status"], "unknown")
        before = self.record_path(plan).read_bytes()
        generation_tasks.unknown(self.root, plan["task_id"], "Tool connection lost")
        self.assertEqual(before, self.record_path(plan).read_bytes())
        self.assertFalse(generation_tasks.begin(self.root, plan, retry=True)["should_call"])
        with self.assertRaisesRegex(generation_tasks.TaskError, "invalid_transition"):
            generation_tasks.failed(self.root, plan["task_id"], "Cannot prove a failure", retryable=True)
        self.output()
        completed = generation_tasks.complete(self.root, plan["task_id"], "images/result.png", tool_request_id="recovered-request")
        self.assertEqual(completed["status"], "completed")
        self.assertTrue(generation_tasks.begin(self.root, plan)["reused"])

    def test_failed_requires_explicit_safe_retry_and_retries_bind_new_claim(self):
        plan = self.prepare()
        first = generation_tasks.begin(self.root, plan)
        result = generation_tasks.failed(self.root, plan["task_id"], "Rejected before image work", retryable=True, claim_id=first["claim_id"])
        self.assertEqual(result["action"], "retry_available")
        self.assertFalse(generation_tasks.begin(self.root, plan)["should_call"])
        self.assertFalse(generation_tasks.resume_status(self.root, plan)["should_call"])
        retry = generation_tasks.begin(self.root, plan, retry=True)
        self.assertTrue(retry["should_call"])
        self.assertEqual(retry["attempt"], 2)
        self.assertNotEqual(retry["claim_id"], first["claim_id"])
        self.assertEqual(retry["previous_attempts"][0]["status"], "failed")
        self.output()
        with self.assertRaisesRegex(generation_tasks.TaskError, "stale_claim"):
            generation_tasks.complete(self.root, plan["task_id"], "images/result.png", claim_id=first["claim_id"])
        with self.assertRaisesRegex(generation_tasks.TaskError, "claim_required"):
            generation_tasks.complete(self.root, plan["task_id"], "images/result.png")
        generation_tasks.complete(self.root, plan["task_id"], "images/result.png", claim_id=retry["claim_id"])
        self.assertTrue(generation_tasks.begin(self.root, plan, retry=True)["reused"])

    def test_retry_flag_must_be_boolean_before_any_claim(self):
        plan = self.prepare()
        for retry in ("false", "true", 1, None):
            with self.subTest(retry=retry), self.assertRaisesRegex(generation_tasks.TaskError, "invalid_retry"):
                generation_tasks.begin(self.root, plan, retry=retry)
        self.assertFalse(self.record_path(plan).exists())

    def test_stale_failure_and_unknown_cannot_affect_a_new_attempt(self):
        plan = self.prepare()
        first = generation_tasks.begin(self.root, plan)
        generation_tasks.failed(self.root, plan["task_id"], "Rejected before execution", retryable=True)
        second = generation_tasks.begin(self.root, plan, retry=True)
        before = self.record_path(plan).read_bytes()
        for finish in (generation_tasks.failed, generation_tasks.unknown):
            with self.subTest(finish=finish.__name__), self.assertRaisesRegex(generation_tasks.TaskError, "stale_claim"):
                finish(self.root, plan["task_id"], "Late result", claim_id=first["claim_id"])
        self.assertEqual(before, self.record_path(plan).read_bytes())
        self.assertEqual(generation_tasks.resume_status(self.root, plan)["claim_id"], second["claim_id"])

    def test_failure_not_marked_safe_never_retries(self):
        plan = self.prepare()
        generation_tasks.begin(self.root, plan)
        generation_tasks.failed(self.root, plan["task_id"], "Known failure without retry approval")
        with self.assertRaisesRegex(generation_tasks.TaskError, "retry_forbidden"):
            generation_tasks.begin(self.root, plan, retry=True)

    def test_completed_claim_request_and_outputs_are_immutable(self):
        plan = self.prepare()
        claimed = generation_tasks.begin(self.root, plan, tool_request_id="tool-1")
        self.output()
        self.output("images/other.png", 90)
        with self.assertRaisesRegex(generation_tasks.TaskError, "tool_request_conflict"):
            generation_tasks.complete(self.root, plan["task_id"], "images/result.png", tool_request_id="tool-2")
        generation_tasks.complete(self.root, plan["task_id"], "images/result.png", claim_id=claimed["claim_id"])
        before = self.record_path(plan).read_bytes()
        with self.assertRaisesRegex(generation_tasks.TaskError, "completion_conflict"):
            generation_tasks.complete(self.root, plan["task_id"], "images/other.png")
        self.assertEqual(before, self.record_path(plan).read_bytes())

    def test_invalid_task_ids_and_symlinked_store_fail_closed(self):
        for task_id in ("../outside", "", "gen-" + "0" * 63, "anything.json"):
            with self.subTest(task_id=task_id), self.assertRaisesRegex(generation_tasks.TaskError, "invalid_task_id"):
                generation_tasks.resume_status(self.root, task_id)
        outside = self.home / "outside-tasks"
        outside.mkdir()
        (self.root / "tasks").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(generation_tasks.TaskError, "unsafe_symlink"):
            generation_tasks.begin(self.root, self.prepare())
        self.assertEqual(list(outside.iterdir()), [])

    def test_python_310_syntax_and_stdlib_only_imports(self):
        source = (SCRIPTS / "generation_tasks.py").read_text(encoding="utf-8")
        tree = ast.parse(source, feature_version=(3, 10))
        imports = {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imports.update(alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
        self.assertTrue(imports <= sys.stdlib_module_names)


if __name__ == "__main__":
    unittest.main()
