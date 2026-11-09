"""Packaging protocol fixtures simulate review evidence, never actual visual approval."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from PIL import Image


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import contract_review
import contract_fixtures
import imaging
import package_post
import physics_review
import product_contract
import project as state
import scene_plan


EVIDENCE = "程序回归预置的协议证据，没有执行真实独立看图或视觉验收。"


class PreparedArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "project with spaces"
        self.root.mkdir()
        self.out = self.root / "post"
        self.args = argparse.Namespace(copy=None, out="post", size="48x64", fit="auto", font=None)
        self.copy = {"title": "小云效果图", "body": "设计效果图，实物以打样为准。", "tags": ["#原创", "#设计", "#毛绒"]}
        self.project = state.new_project("prepared-artifacts", [])
        self.project["answers"] = {"product_form": "plush", "subject_scope": "single", "subjects": ["小云"],
                                   "concept_count": 1, "note_type": "single-multiview", "delivery": "full-post"}
        self.project["stage"] = "package"
        product_contract.sync_subjects(self.project)
        slots = [{"id": "cover", "role": "cover"}, {"id": "back-new", "role": "angle"},
                 {"id": "scale", "role": "scale"}, {"id": "detail", "role": "detail"}]
        self.project["scene_plan"] = scene_plan.default_plan(self.project, slots)
        for index, slot in enumerate(slots):
            relative = slot["id"] + ".png"
            Image.new("RGB", (64, 64), (30 + index * 40, 130, 80)).save(self.root / relative)
            self.project["finals"].append({"slot": slot["id"], "role": slot["role"], "subject": "小云", "path": relative})
        self.project["finals"].reverse()
        self.write_copy()
        state.atomic_json(self.root / "project.json", self.project)
        contract_fixtures.configure(self.root)
        self.project = state.load_project(self.root)
        self.approve(self.source_paths(), "scene")

    def write_copy(self):
        state.atomic_json(self.root / "copy.json", self.copy)

    def source_paths(self):
        return [final["path"] for final in package_post.ordered_finals(self.project)]

    def plan(self):
        return package_post.make_plan(self.root, self.project, self.copy, self.args)

    def prepare(self):
        return package_post.prepare_review(self.root, self.plan(), self.args, self.project)

    def approve(self, paths, stage="package"):
        """Record synthetic protocol evidence without performing visual acceptance."""
        packet = physics_review.prepare(self.root, self.project, paths, "fixture-producer")
        request = state.read_json(Path(packet["packet"]) / "request.json")
        physics = {"schema_version": 1, "request_id": packet["request_id"],
                   "reviewer": {"id": "fixture-physics", "zero_context": True, "viewed_images": True},
                   "results": [{**{key: row[key] for key in ("id", "sha256")}, "verdict": "pass", "summary": EVIDENCE,
                                "checks": {name: {"verdict": "pass", "evidence": EVIDENCE} for name in physics_review.CHECKS},
                                "findings": []} for row in request["images"]]}
        physics_file = self.root / "fixture-physics.json"
        state.atomic_json(physics_file, physics)
        physics_review.record(self.root, self.project, packet["request_id"], physics_file)
        mappings = package_post.actual_output_slots(self.root, self.project, paths)
        contract_fixtures.review(self.root, paths, stage, mappings)
        self.project = state.load_project(self.root)

    def package(self):
        return package_post.execute(self.root, self.project, self.copy, self.plan(), self.out, self.args)

    def prepared_and_approved(self):
        prepared = self.prepare()
        self.approve(prepared["images"])
        return prepared

    def snapshot(self, directory):
        return {path.relative_to(directory).as_posix(): path.read_bytes() for path in directory.rglob("*") if path.is_file()}

    def old_post(self):
        self.out.mkdir()
        (self.out / "old.txt").write_text("previous delivery", encoding="utf-8")
        return self.snapshot(self.out)

    def assert_no_staging(self):
        self.assertFalse(list(self.root.glob(".post-*")))

    @contextmanager
    def after_call(self, function, callback):
        """Observe real function returns without replacing review gates."""
        previous = sys.getprofile()

        def observe(frame, event, value):
            if previous is not None:
                previous(frame, event, value)
            if event == "return" and frame.f_code is function.__code__:
                callback(frame.f_locals)

        sys.setprofile(observe)
        try:
            yield
        finally:
            sys.setprofile(previous)

    def test_scene_plan_order_keeps_new_angle_in_requested_position(self):
        self.assertEqual([entry["slot"] for entry in self.plan()["images"]], ["cover", "back-new", "scale", "detail"])
        self.assertEqual([entry["file"] for entry in self.plan()["images"]],
                         ["01-cover.png", "02-angle.png", "03-scale.png", "04-detail.png"])

    def test_scene_plan_rejects_unknown_and_duplicate_final_slots(self):
        for change in ("unknown", "duplicate"):
            with self.subTest(change=change):
                project = copy.deepcopy(self.project)
                final = copy.deepcopy(project["finals"][0])
                if change == "unknown":
                    final["slot"] = "stale-slot"
                project["finals"].append(final)
                with self.assertRaisesRegex(state.ProjectError, "图位重复或不属于"):
                    package_post.ordered_finals(project)

    def test_legacy_order_and_cover_checks_remain(self):
        legacy = copy.deepcopy(self.project)
        legacy["schema_version"] = 2
        legacy.pop("scene_plan")
        legacy["finals"] = []
        for slot in state.scene_slots(legacy):
            legacy["finals"].insert(0, {"slot": slot["id"], "role": slot["role"], "path": slot["id"] + ".png"})
        self.assertEqual([row["role"] for row in package_post.ordered_finals(legacy)],
                         ["cover", "scale", "detail", "angle", "scene", "info"])
        self.project["scene_plan"]["slots"].reverse()
        with self.assertRaisesRegex(state.ProjectError, "缺少 cover"):
            package_post.ordered_finals(self.project)

    def test_prepare_writes_hash_bound_manifest_and_ordered_mapping_without_commit(self):
        previous = self.old_post()
        project_bytes = (self.root / "project.json").read_bytes()
        prepared = self.prepare()
        manifest = state.read_json(self.root / prepared["manifest"])
        self.assertTrue(manifest["prepared_review"])
        self.assertEqual(manifest["render_signature"], prepared["render_signature"])
        self.assertEqual(prepared["output_slots"], json.loads((self.root / prepared["ordered_slots_file"]).read_text(encoding="utf-8")))
        self.assertEqual([row["slot_id"] for row in prepared["output_slots"]], ["cover", "back-new", "scale", "detail"])
        for path, entry, mapping in zip(prepared["images"], manifest["images"], prepared["output_slots"]):
            self.assertEqual(entry["output_sha256"], package_post.file_hash(self.root / path))
            self.assertEqual(mapping["source_sha256"], package_post.file_hash(self.root / entry["source"]))
            self.assertNotEqual(mapping["sha256"], mapping["source_sha256"])
        self.assertEqual(project_bytes, (self.root / "project.json").read_bytes())
        self.assertEqual(previous, self.snapshot(self.out))

    def test_public_mapping_preserves_actual_requested_order(self):
        prepared = self.prepare()
        paths = list(reversed(prepared["images"]))
        mappings = package_post.actual_output_slots(self.root, self.project, paths)
        self.assertEqual([row["path"] for row in mappings], paths)
        self.assertEqual([row["slot_id"] for row in mappings], ["detail", "scale", "back-new", "cover"])
        packet = contract_review.prepare(self.root, self.project, paths, "fixture-producer", "package", mappings)
        self.assertEqual(packet["scope"]["ordered_slots"], ["detail", "scale", "back-new", "cover"])

    def test_mapping_rejects_changed_output_source_duplicates_and_ambiguous_manifest(self):
        prepared = self.prepare()
        manifest_path = self.root / prepared["manifest"]
        original = state.read_json(manifest_path)
        first_path = self.root / prepared["images"][0]
        output = first_path.read_bytes()
        first_path.write_bytes(b"changed")
        with self.assertRaisesRegex(state.ProjectError, "输出哈希"):
            package_post.actual_output_slots(self.root, self.project, prepared["images"])
        first_path.write_bytes(output)
        source = self.root / original["images"][0]["source"]
        source_bytes = source.read_bytes()
        source.write_bytes(b"changed source")
        with self.assertRaisesRegex(state.ProjectError, "源图已变化"):
            package_post.actual_output_slots(self.root, self.project, prepared["images"])
        source.write_bytes(source_bytes)
        with self.assertRaisesRegex(state.ProjectError, "不重复"):
            package_post.actual_output_slots(self.root, self.project, [prepared["images"][0]] * 2)
        changed = copy.deepcopy(original)
        changed["images"].append(changed["images"][0])
        state.atomic_json(manifest_path, changed)
        with self.assertRaisesRegex(state.ProjectError, "图位重复"):
            package_post.actual_output_slots(self.root, self.project, prepared["images"])
        state.atomic_json(manifest_path, {"images": "invalid"})
        with self.assertRaisesRegex(state.ProjectError, "images 列表"):
            package_post.actual_output_slots(self.root, self.project, prepared["images"])

    def test_approved_prepared_bytes_are_copied_without_render_and_g1_sees_staging(self):
        prepared = self.prepared_and_approved()
        expected = [package_post.file_hash(self.root / relative) for relative in prepared["images"]]
        observed = []

        def capture(arguments):
            if arguments["stage"] == "package":
                root, paths, ordered_slots = (arguments[key] for key in ("root", "paths", "ordered_slots"))
                observed.append(copy.deepcopy(ordered_slots))
                self.assertTrue(all((root / relative).parent.name.startswith(".post-") for relative in paths))
                self.assertTrue((root / paths[0]).parent.joinpath("manifest.json").is_file())
                self.assertEqual([row["path"] for row in ordered_slots], paths)
                self.assertEqual([row["slot_id"] for row in ordered_slots], ["cover", "back-new", "scale", "detail"])

        with mock.patch.object(package_post, "render_images", side_effect=AssertionError("unexpected render")), \
                self.after_call(contract_review.require, capture):
            result = self.package()
        self.assertTrue(result["reused"])
        self.assertEqual(len(observed), 2)
        manifest = state.read_json(self.out / "manifest.json")
        self.assertEqual([package_post.file_hash(self.out / row["file"]) for row in manifest["images"]], expected)
        self.assertNotIn("manifest.json", manifest["files"])
        self.assertTrue(package_post.complete_existing(self.out, manifest["signature"]))
        self.assertEqual(state.load_project(self.root)["stage"], "done")
        self.assert_no_staging()

    def test_copy_only_body_change_reuses_prepared_and_existing_post_without_render(self):
        prepared = self.prepared_and_approved()
        original_plan = self.plan()
        self.copy["body"] = "更新正文，图片仍为设计效果图。"
        self.write_copy()
        changed_plan = self.plan()
        self.assertNotEqual(original_plan["signature"], changed_plan["signature"])
        self.assertEqual(original_plan["render_signature"], changed_plan["render_signature"])
        with mock.patch.object(package_post, "render_images", side_effect=AssertionError("copy edit rendered")):
            first = self.package()
            shutil.rmtree((self.root / prepared["manifest"]).parents[1])
            self.copy["body"] = "第二次修改正文，实物以打样为准。"
            self.write_copy()
            previous = self.snapshot(self.out)
            second = self.package()
        self.assertTrue(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(previous, self.snapshot(Path(second["previous"])))
        self.assertIn(self.copy["body"], (self.out / "caption.md").read_text(encoding="utf-8"))

    def test_repeated_preparation_reuses_identical_bytes(self):
        first = self.prepare()
        with mock.patch.object(package_post, "render_images", side_effect=AssertionError("repeat render")):
            second = self.prepare()
        self.assertTrue(second["reused"])
        self.assertEqual([row["sha256"] for row in first["output_slots"]], [row["sha256"] for row in second["output_slots"]])

    def test_renderer_pillow_font_and_source_changes_invalidate_prepared_cache(self):
        font = self.root / "fixture-font.ttf"
        font.write_bytes(b"font-a")
        self.args.font = str(font)
        self.copy["image_text"] = "user-approved"
        self.copy["overlays"] = {"cover": {"text": "fixture"}}
        self.write_copy()
        with mock.patch.object(imaging, "draw_overlay", side_effect=lambda image, overlay, explicit: image):
            self.prepare()
        original = self.plan()
        self.assertIsNotNone(package_post.prepared_artifacts(self.root, original))
        variants = [
            mock.patch.object(package_post, "RENDER_VERSION", "changed-renderer"),
            mock.patch("PIL.__version__", "changed-pillow"),
        ]
        for patch in variants:
            with patch:
                changed = self.plan()
                self.assertNotEqual(original["render_signature"], changed["render_signature"])
                self.assertIsNone(package_post.prepared_artifacts(self.root, changed))
                with self.assertRaises(state.ProjectError):
                    package_post.require_render_inputs(self.root, original, self.args)
        digest = package_post.file_hash
        with mock.patch.object(package_post, "file_hash", side_effect=lambda path: "changed-code" if path == Path(imaging.__file__) else digest(path)):
            changed = self.plan()
            self.assertNotEqual(original["render_signature"], changed["render_signature"])
            self.assertIsNone(package_post.prepared_artifacts(self.root, changed))
        font.write_bytes(b"font-b")
        changed = self.plan()
        self.assertNotEqual(original["render_signature"], changed["render_signature"])
        self.assertIsNone(package_post.prepared_artifacts(self.root, changed))
        font.write_bytes(b"font-a")
        Image.new("RGB", (64, 64), "purple").save(self.root / "cover.png")
        self.approve(self.source_paths(), "scene")
        changed = self.plan()
        self.assertNotEqual(original["render_signature"], changed["render_signature"])
        self.assertIsNone(package_post.prepared_artifacts(self.root, changed))
        with self.assertRaisesRegex(state.ProjectError, "定稿图在计划后变化"):
            package_post.require_render_inputs(self.root, original, self.args)

    def test_no_overlay_does_not_resolve_or_hash_unused_font(self):
        first = self.plan()
        self.args.font = "/missing/unused-font.ttf"
        with mock.patch.object(imaging, "find_font", side_effect=AssertionError("unused font lookup")):
            second = self.plan()
            self.prepare()
        self.assertEqual(first["render_signature"], second["render_signature"])
        self.assertIsNone(second["render_settings"]["font"])

    def test_corrupt_or_traversing_prepared_artifact_is_not_reused(self):
        prepared = self.prepare()
        manifest_path = self.root / prepared["manifest"]
        manifest = state.read_json(manifest_path)
        for field, value in (("file", "../cover.png"), ("output_sha256", "bad"), ("fit", "auto")):
            with self.subTest(field=field):
                changed = copy.deepcopy(manifest)
                changed["images"][0][field] = value
                state.atomic_json(manifest_path, changed)
                self.assertIsNone(package_post.prepared_artifacts(self.root, self.plan()))
        state.atomic_json(manifest_path, manifest)
        (self.root / prepared["images"][0]).write_bytes(b"corrupt")
        self.assertIsNone(package_post.prepared_artifacts(self.root, self.plan()))

    def test_valid_cache_copied_bytes_are_checked_again(self):
        self.prepare()
        previous = self.old_post()
        copyfile = shutil.copyfile

        def tamper(source, destination):
            result = copyfile(source, destination)
            Path(destination).write_bytes(b"tampered while copying")
            return result

        with mock.patch.object(package_post.shutil, "copyfile", side_effect=tamper):
            with self.assertRaisesRegex(state.ProjectError, "复用期间变化"):
                self.package()
        self.assertEqual(previous, self.snapshot(self.out))
        self.assert_no_staging()

    def test_unreviewed_prepared_outputs_block_and_keep_old_post(self):
        self.prepare()
        previous = self.old_post()
        project_bytes = (self.root / "project.json").read_bytes()
        with self.assertRaisesRegex(state.ProjectError, "物理审查未通过"):
            self.package()
        self.assertEqual(previous, self.snapshot(self.out))
        self.assertEqual(project_bytes, (self.root / "project.json").read_bytes())
        self.assert_no_staging()

    def test_g1_publication_review_is_required_separately_from_scene_review(self):
        prepared = self.prepare()
        self.approve(prepared["images"], "scene")
        previous = self.old_post()
        with self.assertRaisesRegex(state.ProjectError, "G1 合同视觉审查未通过"):
            self.package()
        self.assertEqual(previous, self.snapshot(self.out))
        self.assert_no_staging()

    def test_lock_rechecks_revision_copy_and_renderer_before_replacing_post(self):
        self.prepared_and_approved()
        previous = self.old_post()
        original_project = (self.root / "project.json").read_bytes()
        plan = self.plan()
        for change in ("revision", "copy", "renderer"):
            with self.subTest(change=change):
                calls = []
                active = []

                def mutate(arguments):
                    root = arguments["root"]
                    if arguments["stage"] == "package" and not calls:
                        calls.append(arguments["stage"])
                        if change == "revision":
                            updated = state.load_project(root)
                            updated["revision"] += 1
                            state.atomic_json(root / "project.json", updated)
                        elif change == "copy":
                            state.atomic_json(root / "copy.json", {**self.copy, "body": "changed during packaging"})
                        else:
                            patch = mock.patch.object(package_post, "RENDER_VERSION", "changed-at-lock")
                            patch.start()
                            active.append(patch)

                try:
                    with self.after_call(state.require_reviews, mutate):
                        with self.assertRaises(state.ProjectError):
                            package_post.execute(self.root, self.project, self.copy, copy.deepcopy(plan), self.out, self.args)
                finally:
                    for patch in active:
                        patch.stop()
                    (self.root / "project.json").write_bytes(original_project)
                    self.write_copy()
                self.assertEqual(previous, self.snapshot(self.out))
                self.assertFalse(list(self.root.glob("post-previous-*")))
                self.assert_no_staging()

    def test_final_lock_rechecks_actual_output_hash_after_review(self):
        self.prepared_and_approved()
        previous = self.old_post()
        calls = []

        def tamper(arguments):
            if arguments["stage"] == "package":
                calls.append(arguments["stage"])
                if len(calls) == 2:
                    (arguments["root"] / arguments["paths"][0]).write_bytes(b"changed after review")

        with self.after_call(state.require_reviews, tamper):
            with self.assertRaisesRegex(state.ProjectError, "输出哈希"):
                self.package()
        self.assertEqual(previous, self.snapshot(self.out))
        self.assert_no_staging()

    def test_commit_failure_rolls_back_previous_post(self):
        self.prepared_and_approved()
        previous = self.old_post()
        project_bytes = (self.root / "project.json").read_bytes()
        with mock.patch.object(state, "save_project", side_effect=OSError("commit failure")):
            with self.assertRaisesRegex(OSError, "commit failure"):
                self.package()
        self.assertEqual(previous, self.snapshot(self.out))
        self.assertEqual(project_bytes, (self.root / "project.json").read_bytes())
        self.assertFalse(list(self.root.glob("post-previous-*")))
        self.assert_no_staging()

    def test_existing_post_fast_path_still_passes_actual_mapping_and_lock_checks(self):
        self.prepared_and_approved()
        self.package()
        existing = self.snapshot(self.out)
        calls = []

        def capture(arguments):
            if arguments["stage"] == "package":
                paths, ordered_slots = (arguments[key] for key in ("paths", "ordered_slots"))
                calls.append(ordered_slots)
                self.assertTrue(all(path.startswith("post/") for path in paths))
                self.assertEqual([row["path"] for row in ordered_slots], paths)

        emitted = []
        with self.after_call(contract_review.require, capture), \
                mock.patch.object(state, "emit", side_effect=lambda value: emitted.append(value) or 0), \
                mock.patch.object(package_post, "render_images", side_effect=AssertionError("fast path render")):
            self.assertEqual(package_post.main([str(self.root), "--size", self.args.size]), 0)
        self.assertTrue(emitted[0]["unchanged"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(existing, self.snapshot(self.out))

    def test_existing_post_fast_path_detects_output_changes_during_review(self):
        self.prepared_and_approved()
        self.package()
        project_bytes = (self.root / "project.json").read_bytes()

        def tamper(arguments):
            if arguments["stage"] == "package":
                (arguments["root"] / arguments["paths"][0]).write_bytes(b"changed during existing post review")

        emitted = []
        with self.after_call(state.require_reviews, tamper), \
                mock.patch.object(state, "emit", side_effect=lambda value: emitted.append(value) or 2):
            self.assertEqual(package_post.main([str(self.root), "--size", self.args.size]), 2)
        self.assertFalse(emitted[0]["ok"])
        self.assertIn("实际输出在审查后变化", emitted[0]["error"])
        self.assertEqual(project_bytes, (self.root / "project.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
