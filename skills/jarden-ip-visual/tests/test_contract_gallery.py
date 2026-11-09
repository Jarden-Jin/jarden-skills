"""Gallery wiring through real CLI gates; reports are synthetic protocol fixtures."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

import build_gallery
import contract_review
import contract_fixtures
import physics_review
import product_contract
import project
import scene_plan
import test_deliveries


EVIDENCE = "自动化协议夹具：验证报告接线，没有执行真实独立视觉验收。"


class ContractGalleryTests(unittest.TestCase):
    run_page = test_deliveries.DeliveryTests.run_page

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="contract-gallery-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.root = self.directory / "双用途 项目"
        self.source = self.directory / "原始图片.png"
        image = Image.new("RGBA", (120, 160))
        ImageDraw.Draw(image).ellipse((20, 10, 100, 145), fill=(210, 190, 150, 255))
        image.save(self.source)
        self.cli("project.py", "init", self.root, "--input", self.source, "--kind", "wireframe")
        self.cli("project.py", "set", self.root, "answers.product_form=plush", "answers.subject_scope=single",
                 'answers.subjects=["小云"]', "answers.concept_count=1", "answers.note_type=single-multiview",
                 "answers.delivery=full-post", "image_backend.kind=builtin")
        self.specification = {
            "usage": {"text": "放桌上陪伴，也可以挂在包上", "origin": "user-confirmed", "status": "confirmed"},
            "size": {"mode": "numeric", "height": 12, "width": 8, "unit": "cm", "measurement_basis": "本体站高，不含挂扣",
                     "origin": "user-confirmed", "status": "confirmed", "evidence_kind": "design_target"},
        }
        self.set_spec(self.specification)
        for stage in ("concept", "material", "cutout"):
            if stage == "cutout":
                contract_fixtures.identity(self.root, ["inputs/原始图片.png"])
            self.cli("project.py", "accept-input", self.root, "--stage", stage, "--subject", "小云",
                     "--path", "inputs/原始图片.png", "--origin", "agent-inferred", "--evidence", EVIDENCE)
        self.cli("project.py", "scene-plan", self.root)
        self.round = self.cli("project.py", "new-round", self.root, "--stage", "scene", "--count", "2")
        self.round_file = self.root / self.round["round_file"]
        data = project.read_json(self.round_file)
        for index, item in enumerate(data["items"]):
            image = Image.new("RGB", (120, 160), (90 + index * 50, 160, 110))
            image.save(self.round_file.parent / item["image"])
            item.update(title="测试方向 " + item["id"], prompt="协议测试使用的预置场景图，保留完整主体与可观察尺度，透明资料仅用于核对身份和材质。" + str(index))
        contract_fixtures.generation(self.root, project.find_round(self.current(), self.round["round"]), data)
        project.atomic_json(self.round_file, data)
        self.paths = [str((self.round_file.parent / item["image"]).relative_to(self.root)) for item in data["items"]]

    def cli(self, script, *arguments, code=0):
        result = subprocess.run([sys.executable, str(SKILL / "scripts" / script), *map(str, arguments)],
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "JARDEN_UPDATE_HOME": str(self.directory / "updates")},
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def set_spec(self, specification):
        path = self.directory / "spec.json"
        project.atomic_json(path, specification)
        self.cli("project.py", "product", self.root, "--subject", "小云", "--file", path)

    def current(self):
        return project.load_project(self.root, writable=False)

    def present(self):
        result = self.cli("present_round.py", self.root, "--no-open")
        manifest = project.read_json(Path(result["manifest"]))
        return Path(result["gallery"]), manifest

    def record_g1(self, paths=None, stage="scene", verdict="pass"):
        paths = paths or self.paths
        mappings = project.review_mappings(self.root, self.current(), paths, stage)
        mapping_file = self.directory / "mappings.json"
        mapping_file.write_text(json.dumps(mappings), encoding="utf-8")
        packet = self.cli("contract_review.py", "prepare", self.root, "--images", *paths,
                          "--stage", stage, "--producer", "protocol-author", "--ordered-slots", mapping_file)
        request = project.read_json(Path(packet["request"]))
        findings = [] if verdict == "pass" else [{"code": "fixture.scale", "evidence": EVIDENCE, "fix": "补充独立实际图证据"}]

        def check(value):
            return {"verdict": value, "basis": "visible_pixels", "evidence": EVIDENCE}

        rows = [{"slot_id": row["slot_id"], "path": row["path"], "sha256": row["sha256"], "verdict": verdict,
                 "checks": {name: check(verdict if name == "scale" else "pass") for name in contract_review.CHECKS},
                 "observed_views": ["协议测试观察声明"], "findings": copy.deepcopy(findings)} for row in request["scope"]["images"]]
        report = {"schema_version": 1, "request_id": request["request_id"], "scope_signature": request["scope_signature"],
                  "reviewer": {"id": "protocol-g1-reviewer", "independence": "zero-context", "viewed_images": True},
                  "verdict": verdict, "results": rows,
                  "sequence": {"verdict": verdict, "checks": {name: check(verdict) for name in contract_review.SEQUENCE_CHECKS},
                               "findings": copy.deepcopy(findings), "actual_order": request["scope"]["ordered_slots"],
                               "observed_views": [{"slot_id": row["slot_id"], "sha256": row["sha256"], "views": row["observed_views"],
                                                   "basis": "visible_pixels", "evidence": EVIDENCE} for row in rows],
                               "preserve_images": [{"slot_id": row["slot_id"], "sha256": row["sha256"], **check(verdict)}
                                                   for row in request["scope"]["collection"]["preserve_images"]]}}
        report_file = self.directory / "g1-report.json"
        project.atomic_json(report_file, report)
        self.cli("contract_review.py", "record", self.root, "--request", packet["request_id"], "--report", report_file)
        return packet

    def record_g2(self, paths):
        packet = self.cli("physics_review.py", "prepare", self.root, "--producer", "protocol-author", "--images", *paths)
        request = project.read_json(Path(packet["packet"]) / "request.json")
        report = {"schema_version": 1, "request_id": packet["request_id"],
                  "reviewer": {"id": "protocol-g2-reviewer", "zero_context": True, "viewed_images": True},
                  "results": [{"id": row["id"], "sha256": row["sha256"], "verdict": "pass", "summary": EVIDENCE,
                               "checks": {name: {"verdict": "pass", "evidence": EVIDENCE} for name in physics_review.CHECKS},
                               "findings": []} for row in request["images"]]}
        report_file = self.directory / "g2-report.json"
        project.atomic_json(report_file, report)
        self.cli("physics_review.py", "record", self.root, "--request", packet["request_id"], "--report", report_file)

    def approve_round(self):
        self.record_g1()
        self.record_g2(self.paths)
        self.cli("project.py", "record-round", self.root, self.round["round"])
        self.cli("project.py", "select", self.root, self.round["round"], "A")

    def scene_set(self, slots="cover"):
        info = self.cli("project.py", "new-round", self.root, "--stage", "scene", "--kind", "set", "--slots", slots)
        round_file = self.root / info["round_file"]
        data = project.read_json(round_file)
        paths = []
        for index, item in enumerate(data["items"]):
            Image.new("RGB", (120, 160), (80 + index * 12, 150, 90)).save(round_file.parent / item["image"])
            item.update(title="图位 " + item["id"], prompt="协议测试预置场景图片，用于验证图位接线和保持声明；保持完整产品和稳定背景，实际独立验收仍需另行完成。")
            paths.append(str((round_file.parent / item["image"]).relative_to(self.root)))
        contract_fixtures.generation(self.root, project.find_round(self.current(), info["round"]), data)
        project.atomic_json(round_file, data)
        self.record_g1(paths)
        self.record_g2(paths)
        self.cli("project.py", "record-round", self.root, info["round"])
        self.cli("project.py", "select", self.root, info["round"], "--approve")
        return paths

    def test_manifest_freezes_products_plan_and_missing_g1_without_fake_commit(self):
        before = self.current()
        page, manifest = self.present()
        view = manifest["view"]
        self.assertEqual(self.current()["revision"], before["revision"])
        spec = view["product_contract"]["subjects"][0]["specs"][0]
        self.assertEqual(spec["usage"], self.specification["usage"])
        self.assertEqual(spec["size"]["description"], "高 12、宽 8 cm")
        self.assertEqual(spec["size"]["measurement_basis"], "本体站高，不含挂扣")
        self.assertFalse(view["scene_plan"]["visual_verified"])
        self.assertEqual(len(view["scene_plan"]["slots"]), len(before["scene_plan"]["slots"]))
        actual = project.review_mappings(self.root, before, self.paths, "scene")
        group = view["g1"][0]
        self.assertEqual([row["slot_id"] for row in group["items"]], [row["slot_id"] for row in actual])
        self.assertEqual([row["original_path"] for row in group["items"]], self.paths)
        self.assertTrue(all(row["status"] == "needs_review" for row in group["items"]))
        rendered = self.run_page(page)
        for phrase in ("产品约定", self.specification["usage"]["text"], "视角计划", "G0 仅记录计划", "G1 实际图验收", "缺审", "未提交"):
            self.assertIn(phrase, rendered["text"])
        self.assertEqual(rendered["requests"], [])
        self.assertNotIn("已采用 · 回执已读回", rendered["text"])
        self.assertNotIn("已写入并读回真实回执", rendered["text"])
        self.assertNotIn("scope_signature", rendered["text"])
        self.assertNotIn('"schema_version"', rendered["text"])
        blocked = self.cli("project.py", "record-round", self.root, self.round["round"], code=2)
        self.assertFalse(blocked["ok"])
        frozen = Path(self.root / f"deliveries/{manifest['delivery_id']}/manifest.json").read_bytes()
        changed = copy.deepcopy(self.specification)
        changed["usage"]["text"] = "改为只放书架上"
        self.set_spec(changed)
        self.cli("build_gallery.py", self.root, "--delivery", manifest["delivery_id"], "--out", self.directory / "old.html")
        old = self.run_page(self.directory / "old.html")
        self.assertIn(self.specification["usage"]["text"], old["text"])
        self.assertNotIn(changed["usage"]["text"], old["text"])
        self.assertEqual(frozen, (self.root / f"deliveries/{manifest['delivery_id']}/manifest.json").read_bytes())

    def test_g1_details_and_real_gate_for_uncertain_revision_pass_and_invalid(self):
        for verdict in ("uncertain", "needs_revision", "pass"):
            with self.subTest(verdict=verdict):
                packet = self.record_g1(verdict=verdict)
                page, manifest = self.present()
                group = manifest["view"]["g1"][0]
                self.assertEqual([row["status"] for row in group["items"]], [verdict] * 2)
                self.assertEqual(group["sequence"]["status"], verdict)
                self.assertEqual(group["items"][0]["checks"][1]["reason"], EVIDENCE)
                rendered = self.run_page(page)
                self.assertIn(EVIDENCE, rendered["text"])
                self.assertIn("设计尺度：" + {"uncertain": "不确定", "needs_revision": "需修改", "pass": "通过"}[verdict], rendered["text"])
                self.assertFalse(any(row["selectable"] for row in manifest["items"]))
        self.record_g2(self.paths)
        self.cli("project.py", "record-round", self.root, self.round["round"])
        _, manifest = self.present()
        self.assertTrue(all(row["selectable"] for row in manifest["items"]))
        report = Path(packet["packet"]).parent / "report.json"
        report.write_text("{}", encoding="utf-8")
        page, manifest = self.present()
        self.assertTrue(all(row["status"] == "invalid" for row in manifest["view"]["g1"][0]["items"]))
        self.assertIn("已失效", self.run_page(page)["text"])
        self.assertFalse(any(row["selectable"] for row in manifest["items"]))

    def test_byte_and_contract_changes_stale_g1_but_old_delivery_stays_frozen(self):
        self.record_g1()
        _, old = self.present()
        Image.new("RGB", (120, 160), (15, 20, 25)).save(self.root / self.paths[0])
        _, changed = self.present()
        self.assertEqual(changed["view"]["g1"][0]["items"][0]["status"], "stale")
        self.assertEqual(changed["view"]["g1"][0]["items"][1]["status"], "pass")
        self.assertEqual(changed["view"]["g1"][0]["sequence"]["status"], "stale")
        self.record_g1()
        updated = copy.deepcopy(self.specification)
        updated["size"]["height"] = 18
        self.set_spec(updated)
        _, changed = self.present()
        self.assertTrue(all(row["status"] == "stale" for row in changed["view"]["g1"][0]["items"]))
        data = build_gallery.build_gallery(self.root, delivery_id=old["delivery_id"])
        self.assertTrue(all(row["status"] == "pass" for row in data["g1"][0]["items"]))

    def test_missing_bindings_files_and_broken_records_render_pending(self):
        current = self.current()
        current["rounds"][-1]["review_paths"] = {}
        project.atomic_json(self.root / "project.json", current)
        page, manifest = self.present()
        self.assertEqual(manifest["view"]["g1"][0]["sequence"]["status"], "needs_review")
        self.assertIn("待补", self.run_page(page)["text"])
        self.assertFalse(any(row["selectable"] for row in manifest["items"]))
        current["contract_reviews"] = {"broken": None}
        project.atomic_json(self.root / "project.json", current)
        (self.root / self.paths[0]).unlink()
        page, manifest = self.present()
        self.assertEqual(manifest["view"]["g1"][0]["items"][0]["status"], "pending")
        self.assertIn("待补", self.run_page(page)["text"])

    def test_known_natural_text_is_preserved_without_repeat_question_unknown_not_invented(self):
        known = copy.deepcopy(self.specification)
        known["usage"]["status"] = "proposed"
        known["size"] = {"mode": "unknown", "status": "unknown", "origin": "agent-inferred", "evidence_kind": "design_target"}
        self.set_spec(known)
        page, manifest = self.present()
        self.assertEqual(manifest["next"]["action"], "confirm-contract")
        self.assertEqual(manifest["view"]["product_contract"]["subjects"][0]["specs"][0]["size"]["description"], "")
        rendered = self.run_page(page)
        self.assertEqual(rendered["initial"]["text"], "")
        self.assertIn(known["usage"]["text"], rendered["text"])
        self.assertIn("尺度：未知，待补", rendered["text"])
        self.assertNotIn("12 cm", rendered["text"])
        known["usage"]["status"] = "confirmed"
        self.set_spec(known)
        page, manifest = self.present()
        self.assertTrue(manifest["next"]["field"].startswith("product.size:"))
        self.assertTrue(self.run_page(page)["initial"]["text"])

    def test_append_angle_keeps_front_snapshot_and_view_provenance_after_move(self):
        self.approve_round()
        front = self.scene_set()[0]
        recorded = self.cli("project.py", "view", self.root, "record", "--subject", "小云", "--view", "front",
                            "--path", front, "--origin", "agent-inferred")
        self.cli("project.py", "view", self.root, "confirm", "--asset", recorded["view_asset"]["id"],
                 "--origin", "user-confirmed", "--evidence", "仅确认设计目标，推定来源保持原样")
        before = self.current()
        patch = {"schema_version": 1, "op": "append", "expected_revision": before["scene_plan"]["revision"],
                 "scope": {"slots": ["back-new"]}, "preserve_slots": ["cover"],
                 "baseline_hashes": {"cover": hashlib.sha256((self.root / front).read_bytes()).hexdigest()},
                 "slots": [{"id": "back-new", "role": "angle", "subject": "小云", "view": "back",
                            "pose": "自然站立", "framing": "完整本体，不含额外文字"}]}
        patch_file = self.directory / "patch.json"
        project.atomic_json(patch_file, patch)
        self.cli("project.py", "scene-patch", self.root, "--file", patch_file)
        page, manifest = self.present()
        plan = manifest["view"]["scene_plan"]
        self.assertEqual(len(plan["slots"]), len(before["scene_plan"]["slots"]) + 1)
        cover = next(row for row in plan["slots"] if row["id"] == "cover")
        self.assertTrue(cover["preserve"])
        self.assertEqual(cover["image"]["original_path"], front)
        self.assertTrue(cover["image"]["display_path"].startswith("artifacts/sha256/"))
        self.assertEqual(next(row for row in plan["slots"] if row["id"] == "back-new")["image_state"], "pending")
        asset = manifest["view"]["product_contract"]["subjects"][0]["views"][0]
        self.assertEqual((asset["origin"], asset["status"], asset["confirmation_origin"]), ("agent-inferred", "confirmed", "user-confirmed"))
        frozen = (self.root / cover["image"]["display_path"]).read_bytes()
        Image.new("RGB", (120, 160), "black").save(self.root / front)
        moved = self.directory / "moved"
        shutil.move(self.root, moved)
        self.root = moved
        self.cli("build_gallery.py", self.root, "--delivery", manifest["delivery_id"])
        rendered = self.run_page(self.root / "gallery.html")
        self.assertIn("cover · 正面", rendered["text"])
        self.assertIn("原图已保留", rendered["text"])
        self.assertIn("back-new · 背面", rendered["text"])
        self.assertIn("原始来源：设计推定 · 确认来源：用户说明", rendered["text"])
        self.assertEqual((self.root / cover["image"]["display_path"]).read_bytes(), frozen)
        (self.root / cover["image"]["display_path"]).unlink()
        data = build_gallery.build_gallery(self.root, delivery_id=manifest["delivery_id"])
        self.assertFalse(next(row for row in data["scene_plan"]["slots"] if row["id"] == "cover")["image"]["exists"])
        self.assertIn("图片待补", self.run_page(self.root / "gallery.html")["text"])

    def test_actual_publication_g1_uses_output_paths_in_actual_order(self):
        self.approve_round()
        all_slots = ",".join(slot["id"] for slot in self.current()["scene_plan"]["slots"])
        self.scene_set(all_slots)
        project.atomic_json(self.root / "copy.json", {"title": "小云设计效果图", "body": "效果图，实物以打样为准。", "tags": ["#原创", "#设计", "#毛绒"]})
        prepared = self.cli("package_post.py", self.root, "--prepare-review", "--size", "120x160")
        self.record_g1(prepared["images"], "package")
        self.record_g2(prepared["images"])
        self.cli("package_post.py", self.root, "--size", "120x160")
        page, manifest = self.present()
        group = next(group for group in manifest["view"]["g1"] if group["stage"] == "package")
        output = project.read_json(self.root / "post/manifest.json")
        paths = ["post/" + image["file"] for image in output["images"]]
        expected = project.review_mappings(self.root, self.current(), paths, "package")
        self.assertEqual([row["original_path"] for row in group["items"]], paths)
        self.assertEqual([row["slot_id"] for row in group["items"]], [row["slot_id"] for row in expected])
        self.assertTrue(all(row["status"] == "pass" for row in group["items"]))
        self.assertEqual(group["sequence"]["status"], "pass")
        self.assertIn("完整实际发布集合", self.run_page(page)["text"])
        self.assertNotIn("applicable_signature", self.run_page(page)["text"])

    def test_legacy_readonly_and_old_manifest_do_not_borrow_current_new_fields(self):
        _, manifest = self.present()
        old_file = self.root / f"deliveries/{manifest['delivery_id']}/manifest.json"
        old = copy.deepcopy(manifest)
        for key in ("product_contract", "scene_plan", "g1"):
            old["view"].pop(key)
        project.atomic_json(old_file, old)
        frozen = old_file.read_bytes()
        before = (self.root / "project.json").read_bytes()
        self.cli("build_gallery.py", self.root)
        rendered = self.run_page(self.root / "gallery.html")
        self.assertIn("本交付未记录产品约定", rendered["text"])
        self.assertNotIn(self.specification["usage"]["text"], rendered["text"])
        self.assertEqual((self.root / "project.json").read_bytes(), before)
        self.assertEqual(old_file.read_bytes(), frozen)
        for schema in (1, 2):
            current = self.current()
            current["schema_version"] = schema
            current.pop("product_contract", None)
            current.pop("scene_plan", None)
            current["presentations"] = {}
            project.atomic_json(self.root / "project.json", current)
            before = (self.root / "project.json").read_bytes()
            self.cli("build_gallery.py", self.root)
            self.assertIn("旧项目只读", self.run_page(self.root / "gallery.html")["text"])
            self.assertEqual((self.root / "project.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
