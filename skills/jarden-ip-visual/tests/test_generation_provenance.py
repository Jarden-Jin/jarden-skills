from __future__ import annotations

import ast
import copy
import hashlib
import json
import socket
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generation_provenance as provenance
import generation_tasks


def synthetic_png(red: int = 120) -> bytes:
    """Synthetic 1px protocol fixture; never evidence of external generation."""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    label = b"Description\x00SYNTHETIC TEST IMAGE - NOT EXTERNAL GENERATION"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"tEXt", label)
            + chunk(b"IDAT", zlib.compress(bytes([0, red, 60, 200]))) + chunk(b"IEND", b""))


class GenerationProvenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.root = self.home / "project"
        self.root.mkdir()
        self.entry = {"id": "01-scene", "dir": "rounds/01-scene", "stage": "scene"}
        self.project = {"project_id": "synthetic-protocol-project", "rounds": [self.entry]}
        self.write_json("project.json", self.project)
        self.item = {"id": "A", "slot_id": "cover", "review_slot_id": "review-A",
                     "image": "unexpected-name.png", "prompt": "Synthetic protocol fixture with a blue cloth character."}
        self.backend = {"kind": "builtin", "tool": "synthetic-test-tool"}
        self.reference = self.root / "inputs" / "synthetic-reference.png"
        self.reference.parent.mkdir()
        self.reference.write_bytes(synthetic_png(10))

    def write_json(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def relative_image(self, item=None):
        return self.entry["dir"] + "/" + (item or self.item)["image"]

    def plan(self, *, item=None, slot="cover", intent="synthetic-intent", compiled=None):
        item = item or self.item
        compiled = compiled if compiled is not None else item.get("compiled", {"prompt": item["prompt"]})
        return generation_tasks.prepare(self.root, self.project, slot, intent, compiled, self.backend,
                                        {"approved": True, "source": "synthetic-test-authorization"})

    def complete(self, *, item=None, slot="cover", intent="synthetic-intent", output=None, request_id="synthetic-request"):
        plan = self.plan(item=item, slot=slot, intent=intent)
        claim = generation_tasks.begin(self.root, plan, tool_request_id=request_id)
        self.assertTrue(claim["should_call"])
        durable = json.loads((self.root / "tasks" / (plan["task_id"] + ".json")).read_text())
        self.assertEqual(durable["status"], "requesting")
        self.assertEqual(durable["claim_id"], claim["claim_id"])
        relative = output or self.relative_image(item)
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(synthetic_png())
        result = generation_tasks.complete(self.root, plan["task_id"], relative, claim_id=claim["claim_id"])
        self.assertTrue(result["outputs_valid"])
        return result

    def verify(self, item=None, project=None, entry=None):
        return provenance.verify_generated(self.root, project or self.project, entry or self.entry, item or self.item)

    def mutate_task(self, task, **changes):
        relative = "tasks/" + task["task_id"] + ".json"
        value = json.loads((self.root / relative).read_text())
        value.update(changes)
        self.write_json(relative, value)

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.root.rglob("*") if path.is_file()}

    def test_completed_explicit_receipt_binds_real_local_bytes_without_approval(self):
        task = self.complete()
        self.item["generation_task"] = task["task_id"]
        receipt = self.verify()
        self.assertEqual(receipt["project_id"], self.project["project_id"])
        self.assertEqual(receipt["path"], self.relative_image())
        self.assertEqual(receipt["sha256"], hashlib.sha256(synthetic_png()).hexdigest())
        self.assertEqual(receipt["size_bytes"], len(synthetic_png()))
        for field in ("task_id", "slot_id", "claim_id", "tool_request_id", "request_sha256"):
            self.assertEqual(receipt[field], task["identity"][field] if field == "slot_id" else task[field])
        self.assertTrue(receipt["outputs_valid"])
        self.assertFalse(receipt["external_generation_verified"])
        self.assertFalse(receipt["visual_quality_verified"])
        self.assertTrue({"approved", "authorized", "pass", "should_call"}.isdisjoint(receipt))
        self.item["generation_task"] = receipt
        self.assertEqual(receipt, self.verify())

    def test_discovery_matches_output_not_filename_and_tolerates_unrelated_task(self):
        self.complete(slot="unrelated", intent="unrelated-intent", output="rounds/elsewhere/A.png")
        task = self.complete()
        self.assertEqual(self.verify()["task_id"], task["task_id"])

    def test_no_ledger_is_never_backfilled_from_image_or_plan(self):
        plan = self.plan()
        image = self.root / self.relative_image()
        image.parent.mkdir(parents=True)
        image.write_bytes(synthetic_png())
        before = self.snapshot()
        for binding in (None, plan["task_id"]):
            item = dict(self.item)
            if binding:
                item["generation_task"] = binding
            with self.subTest(binding=binding), self.assertRaises(provenance.ProvenanceError):
                self.verify(item)
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.root / "tasks").exists())

    def test_explicit_missing_or_invalid_binding_never_falls_back_to_discovery(self):
        self.complete()
        for binding in ("gen-" + "0" * 64, "", None, {}, {"task_id": "bad"}, "../tasks/receipt.json", 42):
            with self.subTest(binding=binding), self.assertRaises(provenance.ProvenanceError):
                self.verify({**self.item, "generation_task": binding})

    def test_explicit_wrong_slot_rejected_even_when_discovery_could_succeed(self):
        wrong = self.complete(slot="other", intent="wrong-slot")
        valid = self.complete()
        self.assertEqual(self.verify()["task_id"], valid["task_id"])
        with self.assertRaisesRegex(provenance.ProvenanceError, "slot_mismatch"):
            self.verify({**self.item, "generation_task": wrong["task_id"]})

    def test_all_three_item_slot_identifiers_are_accepted(self):
        for field in ("id", "slot_id", "review_slot_id"):
            task = self.complete(slot=self.item[field], intent=field)
            with self.subTest(field=field):
                receipt = self.verify({**self.item, "generation_task": task["task_id"]})
                self.assertEqual(receipt["slot_id"], self.item[field])

    def test_identical_bytes_at_other_round_path_do_not_match(self):
        task = self.complete(output="rounds/another/unexpected-name.png")
        target = self.root / self.relative_image()
        target.parent.mkdir(parents=True)
        target.write_bytes(synthetic_png())
        with self.assertRaisesRegex(provenance.ProvenanceError, "output_mismatch"):
            self.verify({**self.item, "generation_task": task["task_id"]})
        with self.assertRaisesRegex(provenance.ProvenanceError, "generation_task_not_found"):
            self.verify()

    def test_requesting_unknown_failed_are_not_completion_even_with_old_pass(self):
        for status in ("requesting", "unknown", "failed"):
            plan = self.plan(intent=status)
            claim = generation_tasks.begin(self.root, plan, tool_request_id="synthetic-" + status)
            self.assertTrue(claim["should_call"])
            if status != "requesting":
                getattr(generation_tasks, status)(self.root, plan["task_id"], "Synthetic protocol outcome", claim_id=claim["claim_id"])
            target = self.root / self.relative_image()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(synthetic_png())
            item = {**self.item, "generation_task": {"task_id": plan["task_id"], "outputs_valid": True, "verdict": "pass"}}
            with self.subTest(status=status), self.assertRaisesRegex(provenance.ProvenanceError, "task_incomplete"):
                self.verify(item)

    def test_tool_request_id_is_required_and_nonempty(self):
        task = self.complete(request_id=None)
        for value in (None, "", "   ", 123):
            self.mutate_task(task, tool_request_id=value)
            with self.subTest(value=value), self.assertRaisesRegex(provenance.ProvenanceError, "tool_request_id"):
                self.verify({**self.item, "generation_task": task["task_id"]})

    def test_prompt_must_match_ledger_and_original_compiled_prompt(self):
        task = self.complete()
        variants = [
            {**self.item, "prompt": "Changed item prompt"},
            {**self.item, "compiled": {"prompt": "Changed compiled prompt"}},
            {**self.item, "prompt": "Different", "compiled": {"prompt": "Different"}},
            {**self.item, "prompt": ""},
        ]
        for item in variants:
            with self.subTest(item=item), self.assertRaises(provenance.ProvenanceError):
                self.verify({**item, "generation_task": task["task_id"]})
        self.item["prompt"] = "\n" + self.item["prompt"] + "\n"
        padded = self.complete(intent="prompt-whitespace")
        self.assertEqual(self.verify({**self.item, "generation_task": padded["task_id"]})["task_id"], padded["task_id"])

    def test_original_compiled_semantics_are_recomputed_not_supplied_fingerprint(self):
        self.item["compiled"] = {"prompt": self.item["prompt"], "size": "1024x1024"}
        task = self.complete()
        changed = copy.deepcopy(self.item)
        changed["compiled"].update(size="1536x1024", fingerprint=task["identity"]["compile_fingerprint"])
        with self.assertRaisesRegex(provenance.ProvenanceError, "request_mismatch"):
            self.verify({**changed, "generation_task": task["task_id"]})

    def test_binding_refs_validate_current_bytes_through_real_prepare(self):
        self.item["compiled"] = {"prompt": self.item["prompt"], "bindings": [
            {"kind": "identity", "path": "inputs/synthetic-reference.png",
             "sha256": hashlib.sha256(self.reference.read_bytes()).hexdigest()}]}
        task = self.complete()
        with mock.patch.object(generation_tasks, "prepare", wraps=generation_tasks.prepare) as prepare:
            self.assertEqual(self.verify()["task_id"], task["task_id"])
            prepare.assert_called_once()
            self.assertEqual(prepare.call_args.args[2], task["identity"]["slot_id"])
        original = self.reference.read_bytes()
        prepare = generation_tasks.prepare

        def change_after_prepare(*args, **kwargs):
            checked = prepare(*args, **kwargs)
            self.reference.write_bytes(synthetic_png(13))
            return checked

        with mock.patch.object(generation_tasks, "prepare", side_effect=change_after_prepare):
            with self.assertRaisesRegex(provenance.ProvenanceError, "file_changed"):
                self.verify({**self.item, "generation_task": task["task_id"]})
        self.reference.write_bytes(original)
        self.reference.write_bytes(synthetic_png(11))
        self.assertTrue(generation_tasks.resume_status(self.root, task["task_id"])["outputs_valid"])
        with self.assertRaises(provenance.ProvenanceError) as caught:
            self.verify({**self.item, "generation_task": task["task_id"]})
        self.assertNotIsInstance(caught.exception, generation_tasks.TaskError)
        self.assertIsInstance(caught.exception.__cause__, generation_tasks.TaskError)

    def test_refs_without_declared_hash_still_bind_claimed_bytes_and_need_paths(self):
        self.item["compiled"] = {"prompt": self.item["prompt"], "bindings": [
            {"kind": "identity", "path": "inputs/synthetic-reference.png"}]}
        task = self.complete()
        self.assertEqual(self.verify()["task_id"], task["task_id"])
        for compiled in (None, task["request"]["compiled"]):
            item = {key: value for key, value in self.item.items() if key != "compiled"}
            if compiled is not None:
                item["compiled"] = compiled
            with self.subTest(compiled=compiled), self.assertRaisesRegex(provenance.ProvenanceError, "reference_paths_required"):
                self.verify({**item, "generation_task": task["task_id"]})
        self.reference.write_bytes(synthetic_png(12))
        with self.assertRaisesRegex(provenance.ProvenanceError, "request_mismatch"):
            self.verify({**self.item, "generation_task": task["task_id"]})

    def test_deleted_reference_and_unsafe_reference_paths_fail_closed(self):
        self.item["compiled"] = {"prompt": self.item["prompt"], "references": ["inputs/synthetic-reference.png"]}
        task = self.complete()
        self.reference.unlink()
        with self.assertRaises(provenance.ProvenanceError):
            self.verify()
        self.reference.write_bytes(synthetic_png(10))
        for path in ("../outside.png", str(self.home / "outside.png"), "inputs/missing.png"):
            item = {**self.item, "compiled": {"prompt": self.item["prompt"], "references": [path]}}
            with self.subTest(path=path), self.assertRaises(provenance.ProvenanceError):
                self.verify({**item, "generation_task": task["task_id"]})

    def test_project_id_is_checked_against_disk_and_actual_task(self):
        task = self.complete()
        with self.assertRaisesRegex(provenance.ProvenanceError, "project_mismatch"):
            self.verify(project={**self.project, "project_id": "foreign"})
        foreign = {**self.project, "project_id": "foreign"}
        self.write_json("project.json", foreign)
        with self.assertRaises(provenance.ProvenanceError) as caught:
            self.verify(project=foreign, item={**self.item, "generation_task": task["task_id"]})
        self.assertNotIsInstance(caught.exception, generation_tasks.TaskError)
        self.assertIsInstance(caught.exception.__cause__, generation_tasks.TaskError)

    def test_changed_or_deleted_output_cannot_reuse_an_old_pass(self):
        self.complete()
        self.item["generation_task"] = self.verify()
        path = self.root / self.relative_image()
        for content in (synthetic_png(200), None):
            if content is None:
                path.unlink()
            else:
                path.write_bytes(content)
            with self.subTest(content=content), self.assertRaises(provenance.ProvenanceError):
                self.verify()

    def test_round_image_traversal_and_absolute_paths_are_rejected(self):
        self.complete()
        for field, value in (("dir", "rounds/../rounds/01-scene"), ("dir", str(self.root / self.entry["dir"])),
                             ("image", "../../inputs/synthetic-reference.png"),
                             ("image", str(self.root / self.relative_image())), ("image", "")):
            with self.subTest(field=field, value=value), self.assertRaises(provenance.ProvenanceError):
                self.verify(entry={**self.entry, field: value} if field == "dir" else self.entry,
                            item={**self.item, field: value} if field == "image" else self.item)

    def test_symlink_files_parents_tasks_project_and_root_are_rejected(self):
        task = self.complete()
        paths = [self.root / self.relative_image(), self.root / self.entry["dir"],
                 self.root / "tasks" / (task["task_id"] + ".json"), self.root / "tasks", self.root / "project.json"]
        for number, path in enumerate(paths):
            backup = self.home / f"backup-{number}"
            path.rename(backup)
            path.symlink_to(backup, target_is_directory=backup.is_dir())
            try:
                with self.subTest(path=path), self.assertRaises(provenance.ProvenanceError):
                    self.verify({**self.item, "generation_task": task["task_id"]})
            finally:
                path.unlink()
                backup.rename(path)
        linked = self.home / "linked-project"
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(provenance.ProvenanceError):
            provenance.verify_generated(linked, self.project, self.entry, self.item)

    def test_output_or_ledger_change_during_status_read_fails_final_reread(self):
        task = self.complete()
        read_status = generation_tasks.resume_status
        targets = [self.root / self.relative_image(), self.root / "tasks" / (task["task_id"] + ".json"), self.root / "project.json"]
        for target in targets:
            original = target.read_bytes()

            def change_after_status(*args, **kwargs):
                result = read_status(*args, **kwargs)
                target.write_bytes(synthetic_png(33) if target.suffix == ".png" else original + b"\n")
                return result

            try:
                with self.subTest(target=target), mock.patch.object(generation_tasks, "resume_status", side_effect=change_after_status):
                    with self.assertRaisesRegex(provenance.ProvenanceError, "file_changed"):
                        self.verify({**self.item, "generation_task": task["task_id"]})
            finally:
                target.write_bytes(original)

    def test_reread_error_is_wrapped_and_never_returns_a_previous_receipt(self):
        self.complete()
        self.item["generation_task"] = self.verify()
        original = provenance._Files.read
        reads = {}

        def fail_second_read(files, relative):
            reads[relative] = reads.get(relative, 0) + 1
            if relative == self.relative_image() and reads[relative] > 1:
                raise OSError("Synthetic re-read failure")
            return original(files, relative)

        with mock.patch.object(provenance._Files, "read", fail_second_read):
            with self.assertRaisesRegex(provenance.ProvenanceError, "Synthetic re-read failure"):
                self.verify()

    def test_derive_compose_bind_existing_sources_without_claiming_generation(self):
        target = self.root / self.relative_image()
        target.parent.mkdir(parents=True)
        target.write_bytes(synthetic_png())
        for method in ("derive", "compose"):
            item = {**self.item, "method": method, "prompt": "", "sources": ["inputs/synthetic-reference.png"]}
            with self.subTest(method=method):
                receipt = self.verify(item)
                self.assertEqual(receipt["method"], method)
                self.assertEqual(receipt["scope"], "local-files")
                self.assertEqual(receipt["sources"][0]["sha256"], hashlib.sha256(self.reference.read_bytes()).hexdigest())
                self.assertNotIn("task_id", receipt)
                self.assertNotIn("outputs_valid", receipt)
                self.assertEqual(receipt, self.verify({**item, "sources": receipt["sources"]}))
        self.assertFalse((self.root / "tasks").exists())
        invalid = [None, [], ["inputs/missing.png"], ["../outside.png"], [str(self.reference)],
                   [{"path": "inputs/synthetic-reference.png", "sha256": "0" * 64}], ["inputs"]]
        for sources in invalid:
            with self.subTest(sources=sources), self.assertRaises(provenance.ProvenanceError):
                self.verify({**self.item, "method": "derive", "sources": sources})
        actual = self.reference.read_bytes()
        self.reference.unlink()
        outside = self.home / "outside.png"
        outside.write_bytes(actual)
        self.reference.symlink_to(outside)
        with self.assertRaises(provenance.ProvenanceError):
            self.verify({**self.item, "method": "compose", "sources": ["inputs/synthetic-reference.png"]})

    def test_ambiguous_discovery_requires_explicit_choice(self):
        first = self.complete(intent="first")
        second = self.complete(intent="second")
        with self.assertRaisesRegex(provenance.ProvenanceError, "ambiguous_generation_task"):
            self.verify()
        for task in (first, second):
            self.assertEqual(self.verify({**self.item, "generation_task": task["task_id"]})["task_id"], task["task_id"])

    def test_tampered_claim_or_request_is_rejected_as_own_value_error(self):
        task = self.complete()
        relative = "tasks/" + task["task_id"] + ".json"
        original = (self.root / relative).read_bytes()
        for change in ({"claim_id": ""}, {"request_sha256": "0" * 64}, {"status": "not-a-status"}):
            self.mutate_task(task, **change)
            try:
                with self.subTest(change=change), self.assertRaises(provenance.ProvenanceError) as caught:
                    self.verify({**self.item, "generation_task": task["task_id"]})
                self.assertIsInstance(caught.exception, ValueError)
                self.assertNotIsInstance(caught.exception, generation_tasks.TaskError)
            finally:
                (self.root / relative).write_bytes(original)

    def test_read_only_stdlib_python310_and_no_network_or_mutating_protocol_calls(self):
        self.complete()
        before = self.snapshot()
        with mock.patch.object(socket, "socket", side_effect=AssertionError("Network forbidden")), \
                mock.patch.object(generation_tasks, "begin", side_effect=AssertionError("Claim forbidden")), \
                mock.patch.object(generation_tasks, "complete", side_effect=AssertionError("Completion forbidden")), \
                mock.patch.object(generation_tasks, "task_lock", side_effect=AssertionError("Write lock forbidden")):
            first = self.verify()
            self.assertEqual(first, self.verify())
        self.assertEqual(before, self.snapshot())
        source = (SCRIPTS / "generation_provenance.py").read_text()
        tree = ast.parse(source, feature_version=(3, 10))
        imports = {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imports.update(alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
        self.assertTrue(imports <= sys.stdlib_module_names | {"generation_tasks"})


if __name__ == "__main__":
    unittest.main()
