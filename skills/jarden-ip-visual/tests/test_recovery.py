from __future__ import annotations

import json
import unittest

from PIL import Image

import test_workflow
import package_post
import project


class RecoveryTests(test_workflow.WorkflowTests):
    def accept_design(self):
        for stage in ("concept", "material"):
            self.call("project.py", "accept-input", self.job, "--stage", stage, "--path", "inputs/original.png")

    def complete(self, note="single-multiview"):
        self.init()
        self.known(note_type=note)
        self.accept_design()
        round_id, _ = self.round("cutout")
        self.call("project.py", "select", self.job, round_id, "--approve")
        round_id, _ = self.round("scene")
        self.call("project.py", "select", self.job, round_id, "A")
        round_id, _ = self.round("scene")
        self.call("project.py", "select", self.job, round_id, "--approve")
        (self.job / "copy.json").write_text(json.dumps({"title": "原创小云", "body": "设计效果图，实物以打样为准。", "tags": ["#原创IP", "#云", "#毛绒玩具"]}))
        self.package_review()
        self.call("package_post.py", self.job)

    def test_early_delivery_or_note_change_never_skips_material(self):
        self.init()
        self.known()
        self.call("project.py", "accept-input", self.job, "--stage", "concept", "--path", "inputs/original.png")
        for setting in ("answers.note_type=design-manuscript", "answers.delivery=grid-9"):
            info = self.call("project.py", "set", self.job, setting)
            self.assertEqual(info["stage"], "material")
            self.assertEqual(info["next"]["stage"], "material")

    def test_early_round_survives_unrelated_delivery_change(self):
        self.init()
        self.known()
        round_id, _ = self.round("concept")
        self.call("project.py", "set", self.job, "answers.delivery=grid-9", "answers.note_type=design-manuscript")
        info = self.call("project.py", "select", self.job, round_id, "A")
        self.assertEqual(info["stage"], "material")

    def test_accept_material_requires_all_concept_confirmations(self):
        self.init()
        self.known(["云", "雨"], "series-collection")
        self.call("project.py", "accept-input", self.job, "--stage", "concept", "--subject", "云", "--path", "inputs/original.png")
        self.call("project.py", "accept-input", self.job, "--stage", "material", "--subject", "云", "--path", "inputs/original.png", code=2)
        self.call("project.py", "accept-input", self.job, "--stage", "concept", "--subject", "雨", "--path", "inputs/original.png")
        self.call("project.py", "accept-input", self.job, "--stage", "material", "--subject", "云", "--path", "inputs/original.png")
        self.call("project.py", "accept-input", self.job, "--stage", "material", "--subject", "雨", "--path", "inputs/original.png")
        self.assertEqual(self.call("project.py", "show", self.job)["stage"], "cutout")

    def test_upstream_regeneration_invalidates_post_and_downstream(self):
        self.complete()
        old_round = json.loads((self.job / "project.json").read_text())["rounds"][0]["id"]
        round_id, _ = self.round("concept")
        self.call("project.py", "select", self.job, round_id, "A")
        info = self.call("project.py", "show", self.job)
        self.assertEqual(info["stage"], "material")
        data = json.loads((self.job / "project.json").read_text())
        self.assertEqual(data["finals"], [])
        self.assertNotIn("post", data)
        self.assertFalse(self.call("build_gallery.py", self.job)["has_post"])
        html = (self.job / "gallery.html").read_text()
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", html)
        self.call("project.py", "record-round", self.job, old_round, code=2)

    def test_other_product_detail_invalidates(self):
        self.complete()
        self.call("project.py", "set", self.job, "answers.product_form=other", "brief.product_detail=亚克力挂件")
        data = json.loads((self.job / "project.json").read_text())
        data["stage"] = "done"
        project.save_project(self.job, data)
        info = self.call("project.py", "set", self.job, "brief.product_detail=金属徽章")
        self.assertEqual(info["stage"], "concept")

    def test_subject_count_conflicts_rejected_and_shrinking_works(self):
        self.init()
        self.known(["云", "雨", "风"], "series-collection")
        self.call("project.py", "set", self.job, "answers.concept_count=1", code=2)
        self.call("project.py", "set", self.job, 'answers.subjects=["云","云"]', code=2)
        info = self.call("project.py", "set", self.job, "answers.subject_scope=single", 'answers.subjects=["云"]')
        self.assertEqual(info["subjects"], ["云"])
        self.assertEqual(info["answers"]["concept_count"], 1)

    def test_same_subject_keeps_multiple_designs_and_approvals(self):
        self.init()
        self.known()
        self.call("project.py", "set", self.job, "answers.concept_count=3")
        subjects = self.call("project.py", "show", self.job)["subjects"]
        for stage in ("concept", "material"):
            for subject in subjects:
                self.call("project.py", "accept-input", self.job, "--stage", stage, "--subject", subject, "--path", "inputs/original.png")
                before = project.load_project(self.job)
                self.call("project.py", "set", self.job, 'answers.subjects=[" 小云 "]')
                after = project.load_project(self.job)
                self.assertEqual(after["answers"]["concept_count"], 3)
                self.assertEqual(before["selections"], after["selections"])
        round_id, _ = self.round("cutout")
        self.call("project.py", "select", self.job, round_id, "--approve")
        self.call("project.py", "set", self.job, 'answers.subjects=["小云"]')
        self.assertEqual(self.call("project.py", "show", self.job)["stage"], "scene")

    def test_automatic_subject_names_do_not_collide(self):
        self.init()
        self.known(["角色2"])
        self.call("project.py", "set", self.job, "answers.concept_count=3")
        subjects = self.call("project.py", "show", self.job)["subjects"]
        self.assertEqual(len(subjects), len(set(subjects)))
        for stage in ("concept", "material"):
            for index, subject in enumerate(subjects):
                info = self.call("project.py", "accept-input", self.job, "--stage", stage, "--subject", subject, "--path", "inputs/original.png")
                self.assertEqual(info["stage"], stage if index < 2 else ("material" if stage == "concept" else "cutout"))
        round_id, _ = self.round("cutout")
        self.call("project.py", "select", self.job, round_id, "--approve")
        self.assertEqual(len(project.load_project(self.job)["selections"]["cutout"]), 3)

    def test_legacy_finals_local_replacement_clears_missing_and_duplicates(self):
        for delivery, slot in (("full-post", "cover"), ("full-post", "scene-1"), ("grid-9", "cover")):
            with self.subTest(delivery=delivery, slot=slot):
                self.job = self.root / f"legacy-{delivery}-{slot}"
                self.complete()
                if delivery == "grid-9":
                    self.call("project.py", "set", self.job, "answers.delivery=grid-9")
                    round_id, _ = self.round("scene")
                    self.call("project.py", "select", self.job, round_id, "A")
                    round_id, _ = self.round("scene")
                    self.call("project.py", "select", self.job, round_id, "--approve")
                data = project.load_project(self.job)
                originals = [dict(final) for final in data["finals"]]
                for final in originals:
                    legacy = self.job / ("legacy-" + final["slot"] + ".png")
                    legacy.write_bytes((self.job / final["path"]).read_bytes())
                    self.call("project.py", "final", self.job, "--role", final["role"], "--slot", final["slot"], "--path", legacy.name)
                data = project.load_project(self.job)
                data["finals"] = [{key: value for key, value in final.items() if key != "slot"} for final in data["finals"]]
                project.save_project(self.job, data)
                required_count = len(data["finals"])
                first = originals[0]
                self.call("project.py", "final", self.job, "--role", first["role"], "--slot", first["slot"], "--path", first["path"])
                self.assertEqual(len(project.load_project(self.job)["finals"]), required_count)
                data = project.load_project(self.job)
                data["finals"] = [{key: value for key, value in final.items() if key != "slot"} for final in data["finals"]]
                project.save_project(self.job, data)
                round_id, path = self.round("scene", slots=slot)
                plan = json.loads(path.read_text())
                (path.parent / plan["items"][0]["image"]).unlink()
                self.call("project.py", "record-round", self.job, round_id, "--allow-missing")
                info = self.call("project.py", "select", self.job, round_id, "--approve", "--skip", slot)
                self.assertEqual(info["stage"], "scene")
                self.assertIn(slot, info["next"]["slots"])
                self.call("package_post.py", self.job, "--dry-run", code=2)
                round_id, _ = self.round("scene", slots=slot)
                self.call("project.py", "select", self.job, round_id, "--approve")
                self.assertEqual(len(project.load_project(self.job)["finals"]), required_count)
                self.package_review()
                self.call("package_post.py", self.job, "--force")

    def test_identical_input_filenames_are_preserved(self):
        first = self.root / "first"
        second = self.root / "second"
        first.mkdir()
        second.mkdir()
        Image.new("RGB", (32, 32), "red").save(first / "image.png")
        Image.new("RGB", (32, 32), "blue").save(second / "image.png")
        self.call("project.py", "init", self.job, "--input", first / "image.png", "--input", second / "image.png")
        data = json.loads((self.job / "project.json").read_text())
        self.assertEqual(len({item["path"] for item in data["inputs"]}), 2)
        self.assertNotEqual((self.job / data["inputs"][0]["path"]).read_bytes(), (self.job / data["inputs"][1]["path"]).read_bytes())

    def test_nine_grid_never_silently_drops_required_subjects(self):
        self.init()
        self.known([f"朋友{index}" for index in range(8)], "series-collection", "grid-9")
        info = self.call("project.py", "show", self.job)
        self.assertEqual(info["next"]["issue"], "grid-capacity")
        data = json.loads((self.job / "project.json").read_text())
        with self.assertRaises(project.ProjectError):
            project.scene_slots(data)

    def test_partial_initial_scene_does_not_complete_post(self):
        self.init()
        self.known(note_type="design-manuscript")
        self.accept_design()
        round_id, _ = self.round("cutout")
        self.call("project.py", "select", self.job, round_id, "--approve")
        round_id, _ = self.round("scene")
        self.call("project.py", "select", self.job, round_id, "A")
        round_id, _ = self.round("scene", slots="cover")
        info = self.call("project.py", "select", self.job, round_id, "--approve")
        self.assertEqual(info["stage"], "scene")
        self.assertIn("turnaround", info["next"]["slots"])
        (self.job / "copy.json").write_text(json.dumps({"title": "测试", "body": "效果图", "tags": ["#一", "#二", "#三"]}))
        self.call("package_post.py", self.job, "--dry-run", code=2)

    def test_cutout_partial_approval_defaults_only_missing(self):
        self.init()
        self.known(["云", "雨"], "series-collection")
        for stage in ("concept", "material"):
            for subject in ("云", "雨"):
                self.call("project.py", "accept-input", self.job, "--stage", stage, "--subject", subject, "--path", "inputs/original.png")
        self.call("project.py", "new-round", self.job, "--stage", "cutout", "--kind", "alternatives", code=2)
        round_id, _ = self.round("cutout")
        info = self.call("project.py", "select", self.job, round_id, "--approve", "--skip", "cutout-2")
        self.assertEqual(info["next"]["slots"], ["cutout-2"])
        next_round = self.call("project.py", "new-round", self.job, "--stage", "cutout")
        self.assertEqual(next_round["items"], ["cutout-2"])

    def test_material_shared_constraints_are_not_rejected(self):
        self.init()
        self.known()
        self.call("project.py", "accept-input", self.job, "--stage", "concept", "--path", "inputs/original.png")
        info = self.call("project.py", "new-round", self.job, "--stage", "material")
        path = self.job / info["round_file"]
        data = json.loads(path.read_text())
        shared = "保留原图角色的轮廓五官和配色，保持完全相同的机位构图与光照，完整居中全身，主体比例不变，不添加任何其他角色文字水印，突出真实纤维和接触阴影。" * 4
        for item, material in zip(data["items"], ("细密短毛绒", "羊羔卷绒", "舒展长毛绒")):
            item.update(title=material, prompt=shared + "材质为" + material)
        path.write_text(json.dumps(data, ensure_ascii=False))
        self.call("project.py", "record-round", self.job, info["round"], "--allow-missing")
        gallery = self.call("build_gallery.py", self.job)
        self.assertEqual(gallery["missing_images"], 3)
        html = (self.job / "gallery.html").read_text()
        self.assertIn("文字方向已经可以", html)

    def test_post_commit_failure_is_recovered_without_repackaging(self):
        self.complete()
        data = project.load_project(self.job)
        data["stage"] = "package"
        data.pop("post")
        project.save_project(self.job, data)
        info = self.call("package_post.py", self.job)
        self.assertTrue(info["unchanged"])
        data = project.load_project(self.job)
        self.assertEqual(data["stage"], "done")
        self.assertIn("post", data)

    def test_grid_preview_not_in_publish_image_inspection(self):
        self.init()
        self.known(delivery="grid-9")
        data = project.load_project(self.job)
        for slot in project.scene_slots(data):
            path = self.job / f"{slot['id']}.png"
            Image.new("RGB", (120, 160), "teal").save(path)
            self.review([path.name])
            self.call("project.py", "final", self.job, "--role", slot["role"], "--slot", slot["id"], "--path", path.name)
        (self.job / "copy.json").write_text(json.dumps({"title": "测试九宫格", "body": "AI设计效果图", "tags": ["#一", "#二", "#三"]}))
        self.package_review()
        self.call("package_post.py", self.job)
        self.assertTrue((self.job / "post/previews/grid-preview.png").is_file())
        self.call("check_images.py", self.job / "post", "--ratio", "3:4", "--min-side", "1080")


if __name__ == "__main__":
    unittest.main()


def load_tests(loader, tests, pattern):
    names = [name for name in RecoveryTests.__dict__ if name.startswith("test_")]
    return unittest.TestSuite(RecoveryTests(name) for name in sorted(names))
