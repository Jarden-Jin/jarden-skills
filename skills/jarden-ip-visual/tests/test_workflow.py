from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

import package_post
import project
import physics_review
import contract_fixtures


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.job = self.root / "project with spaces"
        self.source = self.root / "original.png"
        image = Image.new("RGB", (320, 400), (10, 180, 70))
        ImageDraw.Draw(image).ellipse((80, 60, 240, 350), fill=(248, 245, 230))
        image.save(self.source)

    def call(self, script, *args, code=0):
        result = subprocess.run([sys.executable, str(SKILL / "scripts" / script), *map(str, args)], text=True, capture_output=True)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def init(self):
        return self.call("project.py", "init", self.job, "--input", self.source, "--kind", "wireframe")

    def known(self, subjects=None, note_type="single-multiview", delivery="full-post"):
        names = subjects or ["小云"]
        self.call("project.py", "set", self.job, "answers.product_form=plush", f"answers.subject_scope={'single' if len(names) == 1 else 'all'}",
                  "answers.subjects=" + json.dumps(names, ensure_ascii=False), f"answers.concept_count={len(names)}",
                  f"answers.note_type={note_type}", f"answers.delivery={delivery}", "image_backend.kind=builtin")
        contract_fixtures.configure(self.job)

    def review(self, paths, verdict="pass", reviewer="unit-test-reviewer"):
        packet = self.call("physics_review.py", "prepare", self.job, "--producer", "unit-test-producer", "--images", *paths)
        request = json.loads((Path(packet["packet"]) / "request.json").read_text())
        report = {"schema_version": 1, "request_id": packet["request_id"],
                  "reviewer": {"id": reviewer, "zero_context": True, "viewed_images": True}, "results": []}
        for image in request["images"]:
            report["results"].append({"id": image["id"], "sha256": image["sha256"], "verdict": verdict,
                "summary": "程序回归的模拟审查，不是真实图像验收", "checks": {name: {"verdict": verdict, "evidence": "单元测试预置证据"} for name in physics_review.CHECKS},
                "findings": [] if verdict == "pass" else [{"region": "测试区域", "issue": "测试缺陷", "fix": "测试返修"}]})
        report_path = Path(packet["packet"]).parent / "test-report.json"
        project.write_json(report_path, report)
        result = self.call("physics_review.py", "record", self.job, "--request", packet["request_id"], "--report", report_path)
        current = project.load_project(self.job)
        try:
            mappings = project.review_mappings(self.job, current, paths, "scene")
        except project.ProjectError:
            mappings = None
        if mappings and verdict == "pass":
            contract_fixtures.review(self.job, paths, mappings=mappings)
        return packet, report_path, result

    def package_review(self):
        prepared = self.call("package_post.py", self.job, "--prepare-review")
        self.review(prepared["images"])
        contract_fixtures.review(self.job, prepared["images"], "package", package_post.actual_output_slots(self.job, project.load_project(self.job), prepared["images"]))
        return prepared

    def round(self, stage, subject=None, slots=None, register=True):
        args = ["new-round", self.job, "--stage", stage]
        if subject:
            args += ["--subject", subject]
        if slots:
            args += ["--slots", slots]
        info = self.call("project.py", *args)
        path = self.job / info["round_file"]
        data = json.loads(path.read_text())
        topics = [
            "圆润柔软的轮廓，向下折的长耳朵和宽扁的身体，面部使用清楚的细线五官，蓝色是主色，肚子上有一个小口袋，干净的概念设计图",
            "利落的几何体块，有明显的上宽下窄结构，角部柔和倒圆，表情带一点困倦，暖黄作为主色，配件集中在侧面，完整的原创平面角色",
            "舒展修长的比例，脸颊略饱满，睁眼的神态与简洁配色，绿色点缀在领口和四肢，带大面积留白的角色设计图，重点展现清楚的身份记忆点",
        ]
        for index, item in enumerate(data["items"]):
            item.update(title=f"方向{index + 1}", idea=f"设计方案{index + 1}", prompt=topics[index % 3])
            if stage == "cutout":
                image = Image.new("RGBA", (320, 400))
                ImageDraw.Draw(image).ellipse((80, 60, 240, 350), fill=(230, 220, 205, 255))
            else:
                with Image.open(self.source) as original:
                    image = original.copy()
            image.save(path.parent / item["image"])
        contract_fixtures.generation(self.job, project.find_round(project.load_project(self.job), info["round"]), data)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        if register:
            if stage == "scene":
                self.review([(path.parent / item["image"]).relative_to(self.job).as_posix() for item in data["items"]])
            self.call("project.py", "record-round", self.job, info["round"])
        return info["round"], path

    def test_init_is_idempotent_and_questions_advance(self):
        info = self.init()
        self.assertEqual(info["next"]["field"], "answers.product_form")
        before = (self.job / "project.json").read_bytes()
        self.init()
        self.assertEqual(before, (self.job / "project.json").read_bytes())
        self.call("project.py", "set", self.job, "answers.product_form=plush")
        info = self.call("project.py", "show", self.job)
        self.assertEqual(info["next"]["field"], "answers.subject_scope")
        self.call("project.py", "set", self.job, "answers.subject_scope=single", 'answers.subjects=["小云"]')
        info = self.call("project.py", "show", self.job)
        self.assertEqual(info["next"]["field"], "answers.concept_count")
        self.call("project.py", "set", self.job, "answers.concept_count=1")
        info = self.call("project.py", "show", self.job)
        self.assertTrue(info["next"]["field"].startswith("product.usage:"))
        self.assertTrue(info["next"]["free_text"])
        self.assertEqual(info["next"]["options"], [])
        current = project.load_project(self.job)
        self.assertEqual(current["schema_version"], project.SCHEMA_VERSION)
        self.assertTrue(current["product_contract"]["enabled"])

    def test_unknown_scale_allows_concepts_and_publishing_questions_wait_for_scene(self):
        import product_contract

        self.init()
        self.call("project.py", "set", self.job, "answers.product_form=plush", "answers.subject_scope=single",
                  'answers.subjects=["小云"]', "answers.concept_count=1", "image_backend.kind=builtin")
        usage = "放在桌边陪我，也想偶尔挂在包上"
        specification = self.root / "usage.json"
        project.write_json(specification, {"usage": {"text": usage, "status": "confirmed", "origin": "user-confirmed"}})
        self.call("project.py", "product", self.job, "--subject", "小云", "--file", specification)
        current = project.load_project(self.job)
        _, spec = product_contract.spec_for(current, "小云")
        self.assertEqual(spec["usage"]["text"], usage)
        self.assertEqual(spec["size"]["mode"], "unknown")
        self.assertEqual(self.call("project.py", "show", self.job)["next"]["action"], "round")
        for stage in ("concept", "material"):
            round_id, _ = self.round(stage)
            self.call("project.py", "select", self.job, round_id, "A")
        round_id, _ = self.round("cutout")
        self.call("project.py", "select", self.job, round_id, "--approve")
        self.assertTrue(self.call("project.py", "show", self.job)["next"]["field"].startswith("product.size:"))
        self.call("project.py", "new-round", self.job, "--stage", "scene", code=2)
        project.write_json(specification, {"size": {"mode": "qualitative", "description": "本体约一只手掌大",
                           "measurement_basis": "仅本体，不含挂扣", "status": "confirmed", "origin": "user-confirmed",
                           "evidence_kind": "design_target"}})
        self.call("project.py", "product", self.job, "--subject", "小云", "--file", specification)
        self.assertEqual(self.call("project.py", "show", self.job)["next"]["field"], "answers.note_type")
        self.call("project.py", "set", self.job, "answers.note_type=single-multiview")
        self.assertEqual(self.call("project.py", "show", self.job)["next"]["field"], "answers.delivery")
        self.call("project.py", "set", self.job, "answers.delivery=full-post")
        self.assertEqual(self.call("project.py", "show", self.job)["next"]["action"], "scene-plan")
        self.call("project.py", "scene-plan", self.job)
        self.assertEqual(self.call("project.py", "show", self.job)["next"]["action"], "round")

    def test_invalid_setting_does_not_mutate(self):
        self.init()
        before = (self.job / "project.json").read_bytes()
        self.call("project.py", "set", self.job, "answers.product_form=[]", code=2)
        self.assertEqual(before, (self.job / "project.json").read_bytes())

    def test_nonplush_intake_and_gallery_use_product_language(self):
        self.init()
        self.call("project.py", "set", self.job, "answers.product_form=puzzle", "answers.subject_scope=single", 'answers.subjects=["雪夜"]')
        info = self.call("project.py", "show", self.job)
        self.assertEqual(info["next"]["field"], "answers.concept_count")
        options = project.ask_action(project.load_project(self.job), "note_type")["options"]
        self.assertEqual(len(options), 4)
        self.assertNotIn("玩偶", json.dumps(options, ensure_ascii=False))
        self.assertNotIn("挂包", json.dumps(options, ensure_ascii=False))
        self.known()
        self.call("project.py", "set", self.job, "answers.product_form=puzzle")
        self.round("concept")
        self.call("build_gallery.py", self.job)
        html = (self.job / "gallery.html").read_text()
        self.assertIn("单个产品多维展示", html)
        self.assertNotIn("耳朵再长一点", html)

    def test_multi_subject_acceptance_keeps_previous(self):
        self.init()
        self.known(["云", "雨"], "series-collection")
        for name in ("云", "雨"):
            self.call("project.py", "accept-input", self.job, "--stage", "concept", "--subject", name, "--path", "inputs/original.png")
        info = self.call("project.py", "show", self.job)
        self.assertEqual(info["stage"], "material")
        data = json.loads((self.job / "project.json").read_text())
        self.assertEqual(set(data["selections"]["concept"]), {"云", "雨"})

    def test_round_resume_and_gallery_never_generate_images(self):
        self.init()
        self.known()
        info = self.call("project.py", "new-round", self.job, "--stage", "concept")
        round_file = self.job / info["round_file"]
        data = json.loads(round_file.read_text())
        for index, item in enumerate(data["items"]):
            item["title"] = f"方向{index + 1}"
            item["prompt"] = ("蓝色圆润小兔角色设计完整侧耳清楚辨认平面效果色块阴影" if index == 0 else
                              "柔软温暖小熊的原创概念图短耳朵大口袋背带细节暖黄色" if index == 1 else
                              "几何风格绿色小猫完整的造型面部五官自然比例半闭眼小帽子") * 2
        round_file.write_text(json.dumps(data, ensure_ascii=False))
        recorded = self.call("project.py", "record-round", self.job, info["round"], "--allow-missing")
        self.assertIn("present_round.py", recorded["say"])
        self.assertNotIn("build_gallery.py", recorded["say"])
        before = sorted(self.job.rglob("*.png"))
        gallery = self.call("build_gallery.py", self.job)
        self.assertEqual(gallery["missing_images"], 3)
        self.assertEqual(before, sorted(self.job.rglob("*.png")))
        html = (self.job / "gallery.html").read_text()
        self.assertIn("方向3", html)
        self.assertIn("已经可以", html)
        self.call("project.py", "select", self.job, info["round"], "A", code=2)

    def test_stage_flow_partial_scene_regeneration_and_invalidation(self):
        self.init()
        self.known()
        for stage in ("concept", "material"):
            round_id, _ = self.round(stage)
            self.call("project.py", "select", self.job, round_id, "A")
        round_id, _ = self.round("cutout")
        self.call("project.py", "select", self.job, round_id, "--approve")
        info = self.call("project.py", "show", self.job)
        self.assertEqual(info["stage"], "scene")
        round_id, _ = self.round("scene")
        self.call("project.py", "select", self.job, round_id, "A")
        round_id, _ = self.round("scene")
        self.call("project.py", "select", self.job, round_id, "--approve", "--skip", "detail")
        info = self.call("project.py", "show", self.job)
        self.assertEqual(info["next"]["slots"], ["detail"])
        round_id, _ = self.round("scene", slots="detail")
        self.call("project.py", "select", self.job, round_id, "--approve")
        data = json.loads((self.job / "project.json").read_text())
        self.assertEqual(data["stage"], "package")
        self.assertEqual(len(data["finals"]), 6)
        self.call("project.py", "set", self.job, "answers.product_form=figure")
        data = json.loads((self.job / "project.json").read_text())
        self.assertEqual(data["stage"], "concept")
        self.assertEqual(data["finals"], [])

    def test_gallery_distinguishes_generation_composition_and_derivation(self):
        self.init()
        self.known()
        round_id, round_path = self.round("concept")
        data = json.loads(round_path.read_text())
        data["items"][1].update(method="compose", sources=["inputs/original.png"])
        data["items"][2].update(method="derive", sources=["inputs/original.png"])
        round_path.write_text(json.dumps(data, ensure_ascii=False))
        self.call("project.py", "record-round", self.job, round_id)
        self.call("build_gallery.py", self.job)
        html = (self.job / "gallery.html").read_text()
        marker = '<script id="gallery-data" type="application/json">'
        payload = json.loads(html.split(marker, 1)[1].split("</script>", 1)[0])
        items = payload["current"]["items"]
        self.assertEqual([item["method"] for item in items], ["AI 生成", "后期拼版", "已有图派生"])
        self.assertEqual([item["prompt_label"] for item in items], ["生图提示词", "制作说明", "制作说明"])
        self.assertEqual(items[0]["sources"], [])
        self.assertEqual(items[1]["sources"], ["inputs/original.png"])
        self.assertIn("来源图片", html)

    def test_cutout_cannot_be_approved_opaque(self):
        self.init()
        self.known()
        for stage in ("concept", "material"):
            self.call("project.py", "accept-input", self.job, "--stage", stage, "--subject", "小云", "--path", "inputs/original.png")
        round_id, path = self.round("cutout")
        data = json.loads(path.read_text())
        Image.open(self.source).save(path.parent / data["items"][0]["image"])
        data["items"][0].update(method="derive", sources=["inputs/original.png"])
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.call("project.py", "record-round", self.job, round_id)
        self.call("project.py", "select", self.job, round_id, "--approve", code=2)

    def test_packaging_order_ratio_recovery_and_copy_validation(self):
        self.init()
        self.known(["云", "雨"], "series-collection")
        for role, slot, name, color in (("scene", "scene-1", "scene.png", "orange"), ("character", "character-1", "one.png", "blue"),
                                      ("cover", "cover", "cover.png", "magenta"), ("character", "character-2", "two.png", "green"),
                                      ("scene", "scene-2", "scene-two.png", "purple")):
            Image.new("RGB", (300, 300), color).save(self.job / name)
            contract_fixtures.bind_final(self.job, name, slot)
            self.review([name])
            self.call("project.py", "final", self.job, "--role", role, "--slot", slot, "--path", name)
        data = project.load_project(self.job)
        paths = [final["path"] for final in package_post.ordered_finals(data)]
        contract_fixtures.review(self.job, paths)
        copy = {"title": "一群好朋友", "body": "云和雨的设计效果图，材质以打样为准。", "tags": ["#原创IP", "#天气", "#毛绒玩具"]}
        (self.job / "copy.json").write_text(json.dumps(copy, ensure_ascii=False))
        self.call("package_post.py", self.job, "--dry-run")
        self.assertFalse((self.job / "post").exists())
        self.package_review()
        self.call("package_post.py", self.job)
        manifest = json.loads((self.job / "post/manifest.json").read_text())
        self.assertEqual([entry["role"] for entry in manifest["images"]], ["cover", "character", "character", "scene", "scene"])
        with Image.open(self.job / "post/01-cover.png") as image:
            self.assertEqual(image.size, (1080, 1440))
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 255))
        self.assertTrue(self.call("package_post.py", self.job)["unchanged"])
        (self.job / "post/02-character.png").write_bytes(b"damaged")
        self.call("package_post.py", self.job, code=2)
        result = self.call("package_post.py", self.job, "--force")
        self.assertTrue(Path(result["previous"]).is_dir())
        self.call("build_gallery.py", self.job)
        self.assertIn("一群好朋友", (self.job / "gallery.html").read_text())
        copy["tags"] = ["#AI", "#ai", "#毛绒"]
        (self.job / "copy.json").write_text(json.dumps(copy))
        self.call("package_post.py", self.job, "--dry-run", code=2)

    def test_grid_requires_nine_actual_finals(self):
        self.init()
        self.known(delivery="grid-9")
        contract_fixtures.bind_final(self.job, "inputs/original.png", "cover")
        self.review(["inputs/original.png"])
        self.call("project.py", "final", self.job, "--role", "cover", "--slot", "cover", "--path", "inputs/original.png")
        (self.job / "copy.json").write_text(json.dumps({"title": "测试", "body": "测试效果图", "tags": ["#一", "#二", "#三"]}))
        self.call("package_post.py", self.job, "--dry-run", code=2)

    def test_character_budget_and_duplicate_tags(self):
        self.assertEqual(package_post.count_chars("小兔🐰\ufe0f"), 3)
        self.assertEqual(package_post.count_chars("a\u200db"), 2)
        with self.assertRaises(project.ProjectError):
            package_post.validate_copy({"title": "好" * 21, "body": "正文", "tags": ["#一", "#二", "#三"]}, "single-multiview")

    def test_remove_background_and_compose_actual_pixels(self):
        transparent = self.root / "cutout.png"
        self.call("remove_bg.py", self.source, transparent)
        self.call("check_images.py", transparent, "--alpha")
        with Image.open(transparent) as original:
            image = original.copy()
        self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertEqual(image.getpixel((160, 200))[3], 255)
        original_hash = hashlib.sha256(transparent.read_bytes()).hexdigest()
        for command, args in (
            ("grid", ["--images", str(transparent), str(transparent), "--labels", "云", "雨"]),
            ("lineup", ["--images", str(transparent), str(transparent), "--labels", "正面", "背面"]),
            ("poster", ["--subject", str(transparent), "--word", "CLOUD", "--caption", "DESIGN"]),
            ("info", ["--image", str(transparent), "--title", "云", "--field", "材质=短毛绒", "--field", "尺寸=待定"]),
        ):
            out = self.root / f"{command}.png"
            self.call("compose.py", command, *args, "--image-text", "user-approved", "--out", out)
            with Image.open(out) as image:
                self.assertEqual(image.size, (1080, 1440))
        self.assertEqual(hashlib.sha256(transparent.read_bytes()).hexdigest(), original_hash)
        self.call("compose.py", "overlay", transparent, "--text", "云", "--image-text", "user-approved", "--out", self.root / "overlay.png")
        self.call("remove_bg.py", self.source, transparent, code=2)

    def test_fake_checkerboard_rejected(self):
        image = Image.new("RGB", (256, 256))
        draw = ImageDraw.Draw(image)
        for row in range(16):
            for col in range(16):
                color = 220 if (row + col) % 2 else 250
                draw.rectangle((col * 16, row * 16, col * 16 + 15, row * 16 + 15), fill=(color,) * 3)
        path = self.root / "fake-alpha.png"
        image.save(path)
        info = self.call("check_images.py", path, "--alpha", code=1)
        self.assertIn("棋盘格", str(info["results"][0]["problems"]))


if __name__ == "__main__":
    unittest.main()
