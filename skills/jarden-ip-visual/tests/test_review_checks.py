from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

import physics_review
import project
import review_checks
import runtime_activity
import test_workflow


class ReviewChecksTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.job = self.root / "project with spaces"
        self.source = self.root / "original.png"
        image = Image.new("RGB", (320, 400), (10, 180, 70))
        ImageDraw.Draw(image).ellipse((80, 60, 240, 350), fill=(248, 245, 230))
        image.save(self.source)
        self.init()

    def call(self, script, *args, code=0):
        result = subprocess.run(
            [sys.executable, str(SKILL / "scripts" / script), *map(str, args)],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def init(self):
        self.call("project.py", "init", self.job, "--input", self.source, "--kind", "wireframe")
        self.call(
            "project.py", "set", self.job,
            "answers.product_form=plush", "answers.subject_scope=single",
            'answers.subjects=["小云"]', "answers.concept_count=1",
            "answers.note_type=single-multiview", "answers.delivery=full-post",
            "image_backend.kind=builtin",
        )

    def packet(self, category, *images, producer="producer-maker"):
        args = ["prepare", self.job, "--category", category, "--producer", producer]
        if images:
            args += ["--images", *images]
        return self.call("review_checks.py", *args)

    def make_report(self, packet, verdict="pass", findings=None, facts=None):
        request = project.read_json(Path(packet["request"]))
        scope = request["scope"]
        rows = [
            {
                "path": item["path"],
                "sha256": item["sha256"],
                "verdict": verdict if verdict in ("pass", "needs_revision", "uncertain") else "pass",
                "evidence": f"程序回归模拟 {item['path']} 的逐图证据；不代表真实视觉验收。",
            }
            for item in scope.get("paths") or []
        ]
        report = {
            "schema_version": 2,
            "review_id": request["review_id"],
            "category": request["category"],
            "scope": scope,
            "image_hashes": [item["sha256"] for item in scope.get("paths") or []],
            "decision_signature": scope.get("decision_signature"),
            "template_version": scope.get("template_version"),
            "reviewer": {
                "id": "independent-reviewer",
                "independence": "zero-context",
                "viewed_images": True,
            },
            "verdict": verdict,
            "findings": findings or [],
            "evidence": "程序回归预置的审查证据，仅验证 CLI 契约；没有执行真实独立看图或用户审美批准。",
            "paths": rows,
        }
        if request["category"] == "creative":
            report["criteria"] = {name: {"verdict": "pass", "evidence": "程序回归预置的代表方案依据，不代表真实创作验收。"} for name in review_checks.CREATIVE_CRITERIA}
        if request["category"] in {"sequence", "copy"}:
            names = review_checks.SEQUENCE_CRITERIA if request["category"] == "sequence" else review_checks.COPY_CRITERIA
            report["criteria"] = {
                name: {
                    "verdict": "pass",
                    "evidence": "程序回归预置的整组素材依据，不代表真实传播或事实验收。",
                }
                for name in names
            }
        if request["category"] == "copy":
            report["facts"] = facts if facts is not None else []
        if request["category"] == "ux":
            report["reviewer"]["viewed_page"] = True
            report["tests"] = ["程序回归模拟静态页面走查；没有执行真实浏览器验收"]
            report["criteria"] = {
                name: {"verdict": "pass", "evidence": "程序回归预置的交互依据，不代表真实浏览器验收。"}
                for name in review_checks.UX_CRITERIA
            }
        return report

    def record_report(self, packet, report, name="report.json"):
        path = self.root / name
        project.write_json(path, report)
        return self.call(
            "review_checks.py", "record", self.job,
            "--category", packet["category"],
            "--request", packet["review_id"],
            "--report", path,
        )

    def review_category(self, category, verdict="pass", *images):
        images = images or ("inputs/original.png",)
        packet = self.packet(category, *images)
        findings = None
        if verdict != "pass":
            findings = [{
                "code": f"{category}.visible_issue",
                "severity": "blocking",
                "detail": "实际画面有需要修正的问题",
                "evidence": "完整图里可见的触点、差异或文字问题",
                "fix": "只修对应图位后重新检查",
            }]
        result = self.record_report(packet, self.make_report(packet, verdict=verdict, findings=findings))
        return packet, result

    def register_all_finals(self):
        data = project.load_project(self.job)
        data["finals"] = [
            {"role": slot["role"], "slot": slot["id"], "path": "inputs/original.png"}
            for slot in project.scene_slots(data)
        ]
        project.save_project(self.job, data)

    def write_copy(self):
        project.write_json(self.job / "copy.json", {
            "title": "小云设计分享",
            "body": "保留圆眼与蓝色围巾的设计效果图。",
            "tags": ["#原创IP", "#毛绒", "#角色设计"],
            "ai_disclosure": "AI 设计效果图",
            "image_text": "none",
            "overlays": {},
        })

    def test_status_never_turns_missing_identity_or_creative_into_pass(self):
        before = (self.job / "project.json").read_bytes()
        state = self.call("review_checks.py", "status", self.job, "--category", "identity", "--images", "inputs/original.png")
        self.assertEqual(state["verdict"], "not_assessed")
        self.assertFalse(state["blocking"])
        self.assertTrue(state["not_assessed"])
        self.assertEqual(before, (self.job / "project.json").read_bytes())
        self.assertFalse((self.job / "reviews/categories").exists())

        before_revision = project.load_project(self.job)["revision"]
        packet = self.packet("identity")
        self.assertEqual(before_revision, project.load_project(self.job)["revision"])
        report = self.make_report(packet)
        recorded = self.record_report(packet, report)
        self.assertEqual(recorded["verdict"], "pass")
        self.assertEqual(recorded["revision"], before_revision + 1)
        replayed = self.record_report(packet, report)
        self.assertTrue(replayed["unchanged"])
        self.assertEqual(replayed["revision"], before_revision + 1)
        state = self.call("review_checks.py", "status", self.job, "--category", "identity", "--images", "inputs/original.png")
        self.assertEqual(state["verdict"], "pass")
        creative = self.call("review_checks.py", "status", self.job, "--category", "creative", "--images", "inputs/original.png")
        self.assertEqual(creative["verdict"], "not_assessed")
        self.assertFalse(creative["blocking"])

        gate = self.call("review_checks.py", "check", self.job, "--stage", "concept", "--images", "inputs/original.png")
        self.assertFalse(gate["blocking"])
        self.assertFalse(any(item["category"] == "creative" for item in gate["findings"]))
        self.assertTrue(any(item["category"] == "creative" for item in gate["not_assessed"]))
        missing_allowed = self.call("review_checks.py", "check", self.job, "--stage", "concept")
        self.assertFalse(missing_allowed["blocking"])
        self.assertEqual(missing_allowed["inspections"]["files"]["verdict"], "pass")

    def test_decision_and_copy_changes_invalidate_only_their_categories(self):
        project.write_json(self.job / "copy.json", {
            "title": "小云设计分享",
            "body": "保留圆眼与蓝色围巾的设计效果图。",
            "tags": ["#原创IP", "#毛绒", "#角色设计"],
            "ai_disclosure": "AI 设计效果图",
            "image_text": "none",
            "overlays": {},
        })
        self.review_category("identity")
        self.review_category("copy")
        self.register_all_finals()
        self.review_category("sequence")

        self.call(
            "project.py", "decision", self.job,
            "--rule", "保留左耳缺口与圆眼睛",
            "--origin", "user-confirmed",
            "--scope", '{"subject":"小云","stages":["material","cutout"]}',
            "--evidence", '{"source":"user"}',
        )
        identity = self.call("review_checks.py", "status", self.job, "--category", "identity", "--images", "inputs/original.png")
        copy = self.call("review_checks.py", "status", self.job, "--category", "copy", "--images", "inputs/original.png")
        sequence = self.call("review_checks.py", "status", self.job, "--category", "sequence", "--images", "inputs/original.png")
        self.assertEqual(identity["verdict"], "not_assessed")
        self.assertFalse(identity["blocking"])
        self.assertEqual(copy["verdict"], "pass")
        self.assertEqual(sequence["verdict"], "pass")

        project.write_json(self.job / "copy.json", {
            "title": "小云换了标题",
            "body": "保留圆眼与蓝色围巾的设计效果图。",
            "tags": ["#原创IP", "#毛绒", "#角色设计"],
            "ai_disclosure": "AI 设计效果图",
            "image_text": "none",
            "overlays": {},
        })
        copy = self.call("review_checks.py", "status", self.job, "--category", "copy", "--images", "inputs/original.png")
        sequence = self.call("review_checks.py", "status", self.job, "--category", "sequence", "--images", "inputs/original.png")
        self.assertEqual(copy["verdict"], "pass")
        self.assertTrue(copy["not_assessed"])
        self.assertFalse(copy["blocking"])
        self.assertEqual(sequence["verdict"], "pass")

    def test_copy_fact_requires_verified_basis_and_cannot_fake_pass(self):
        project.write_json(self.job / "copy.json", {
            "title": "小云新品",
            "body": "这次售价 99 元，已经开始发售。",
            "tags": ["#原创IP", "#毛绒", "#角色设计"],
            "ai_disclosure": "AI 设计效果图",
            "image_text": "none",
            "overlays": {},
        })
        packet = self.packet("copy", "inputs/original.png")
        unsupported = self.make_report(packet, facts=[])
        report_path = self.root / "unsupported-copy.json"
        project.write_json(report_path, unsupported)
        result = self.call(
            "review_checks.py", "record", self.job, "--category", "copy",
            "--request", packet["review_id"], "--report", report_path, code=2,
        )
        self.assertIn("事实主张", result["error"])
        state = self.call("review_checks.py", "status", self.job, "--category", "copy", "--images", "inputs/original.png")
        self.assertEqual(state["verdict"], "pass")
        self.assertTrue(state["not_assessed"])
        self.assertFalse(state["blocking"])

        verified = self.make_report(packet, facts=[{
            "claim": "售价 99 元并已发售",
            "basis": "用户在项目回复中确认并提供商品链接",
            "status": "verified",
        }])
        self.record_report(packet, verified, "verified-copy.json")
        state = self.call("review_checks.py", "status", self.job, "--category", "copy", "--images", "inputs/original.png")
        self.assertEqual(state["verdict"], "pass")

    def test_tampering_or_replaying_a_recorded_report_cannot_clear_failure(self):
        packet, recorded = self.review_category("identity")
        report_path = Path(recorded["report"])
        report_path.write_text(report_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        state = self.call("review_checks.py", "status", self.job, "--category", "identity", "--images", "inputs/original.png")
        self.assertEqual(state["verdict"], "blocked")
        self.assertTrue(state["blocking"])
        self.assertTrue(any("索引不一致" in (row.get("reason") or "") for row in state["findings"]))

        replay = self.make_report(packet, verdict="needs_revision", findings=[{
            "code": "identity.changed_eye",
            "severity": "blocking",
            "detail": "眼睛形状改变",
            "evidence": "与确认参考并排可见",
            "fix": "恢复原眼形",
        }])
        path = self.root / "replay.json"
        project.write_json(path, replay)
        result = self.call(
            "review_checks.py", "record", self.job, "--category", "identity",
            "--request", packet["review_id"], "--report", path, code=2,
        )
        self.assertIn("另一份报告", result["error"])

    def test_explicit_identity_or_copy_failure_report_blocks_but_missing_does_not(self):
        missing = self.call("review_checks.py", "check", self.job, "--stage", "concept", "--images", "inputs/original.png")
        self.assertFalse(missing["blocking"])
        self.assertTrue(missing["not_assessed"])

        packet = self.packet("identity", "inputs/original.png")
        report = self.make_report(packet, verdict="needs_revision", findings=[{
            "code": "identity.eye_shape",
            "severity": "blocking",
            "detail": "眼形与确认参考不同",
            "evidence": "并排原图可见眼形被拉长",
            "fix": "恢复确认参考的圆眼",
        }])
        self.record_report(packet, report)
        identity = self.call("review_checks.py", "status", self.job, "--category", "identity", "--images", "inputs/original.png")
        self.assertEqual(identity["verdict"], "blocked")
        gate = self.call("review_checks.py", "check", self.job, "--stage", "concept", "--images", "inputs/original.png")
        self.assertTrue(gate["blocking"])
        self.assertTrue(any(item["category"] == "identity" for item in gate["findings"]))

    def test_template_change_only_invalidates_ux(self):
        (self.job / "assets").mkdir()
        (self.job / "assets/gallery.html").write_bytes((SKILL / "assets/gallery.html").read_bytes())
        self.review_category("identity")
        ux_packet = self.packet("ux")
        self.record_report(ux_packet, self.make_report(ux_packet), "ux.json")
        ux = self.call("review_checks.py", "status", self.job, "--category", "ux")
        identity = self.call("review_checks.py", "status", self.job, "--category", "identity", "--images", "inputs/original.png")
        self.assertEqual(ux["verdict"], "pass")
        self.assertEqual(identity["verdict"], "pass")
        with (self.job / "assets/gallery.html").open("a", encoding="utf-8") as stream:
            stream.write("\n<!-- template changed -->\n")
        ux = self.call("review_checks.py", "status", self.job, "--category", "ux")
        identity = self.call("review_checks.py", "status", self.job, "--category", "identity", "--images", "inputs/original.png")
        self.assertEqual(ux["verdict"], "not_assessed")
        self.assertFalse(ux["blocking"])
        self.assertEqual(identity["verdict"], "pass")

    def test_original_or_reference_bytes_changed_after_prepare_cannot_be_approved(self):
        candidate = "inputs/candidate.png"
        Image.new("RGB", (320, 400), (20, 130, 240)).save(self.job / candidate)
        before_revision = project.load_project(self.job)["revision"]
        packet = self.packet("identity", candidate)
        report = self.make_report(packet)
        Image.new("RGB", (320, 400), (30, 140, 240)).save(self.job / candidate)
        path = self.root / "changed-candidate.json"
        project.write_json(path, report)
        result = self.call("review_checks.py", "record", self.job, "--category", "identity",
                           "--request", packet["review_id"], "--report", path, code=2)
        self.assertIn("已变化", result["error"])
        self.assertEqual(before_revision, project.load_project(self.job)["revision"])

        packet = self.packet("identity", candidate)
        project.write_json(path, self.make_report(packet))
        Image.new("RGB", (320, 400), (40, 150, 240)).save(self.job / "inputs/original.png")
        result = self.call("review_checks.py", "record", self.job, "--category", "identity",
                           "--request", packet["review_id"], "--report", path, code=2)
        self.assertIn("已变化", result["error"])
        self.assertEqual(before_revision, project.load_project(self.job)["revision"])
        self.assertFalse(project.load_project(self.job).get("review_records"))

    def test_newer_failure_blocks_old_pass_replay_and_new_review_resolves(self):
        old_packet, _ = self.review_category("identity")
        self.review_category("identity", "needs_revision")
        state = self.call("review_checks.py", "status", self.job, "--category", "identity", "--images", "inputs/original.png")
        self.assertTrue(state["blocking"])
        self.assertEqual(state["paths"][0]["verdict"], "needs_revision")
        self.assertEqual(state["review"], "blocked")
        path = self.root / "old-pass.json"
        project.write_json(path, self.make_report(old_packet))
        before_revision = project.load_project(self.job)["revision"]
        result = self.call("review_checks.py", "record", self.job, "--category", "identity",
                           "--request", old_packet["review_id"], "--report", path, code=2)
        self.assertIn("更新", result["error"])
        self.assertEqual(before_revision, project.load_project(self.job)["revision"])
        self.review_category("identity")
        state = self.call("review_checks.py", "status", self.job, "--category", "identity", "--images", "inputs/original.png")
        self.assertFalse(state["blocking"])
        self.assertEqual(state["verdict"], "pass")

    def test_creative_packet_has_neutral_bible_references_and_clean_previews(self):
        data = project.load_project(self.job)
        data.setdefault("brief", {})["bible"] = {
            "identity": {"eyes": "圆眼睛", "nested": {"color": "蓝色", "author_preference": "LEAK-PREFERENCE"}},
            "prompt": "LEAK-PROMPT",
            "conclusion": "LEAK-CONCLUSION",
        }
        data["notes"] = "LEAK-PROJECT-NOTE"
        data["preferences"] = {"winner": "LEAK-WINNER"}
        project.save_project(self.job, data)
        metadata = PngInfo()
        metadata.add_text("Author", "LEAK-IMAGE-AUTHOR")
        metadata.add_text("Prompt", "LEAK-IMAGE-PROMPT")
        Image.new("RGB", (320, 400), (20, 50, 160)).save(self.job / "inputs/original.png", pnginfo=metadata)
        candidate = "inputs/candidate.png"
        Image.new("RGB", (320, 400), (20, 90, 160)).save(self.job / candidate, pnginfo=metadata)
        original_hash = hashlib.sha256((self.job / candidate).read_bytes()).hexdigest()
        before_revision = project.load_project(self.job)["revision"]
        packet = self.packet("creative", candidate)
        request = project.read_json(Path(packet["request"]))
        reference = request["reference"]["confirmed_bible"]
        self.assertEqual(reference["identity"]["eyes"], "圆眼睛")
        self.assertEqual(reference["identity"]["nested"], {"color": "蓝色"})
        self.assertNotIn("LEAK-", json.dumps(request, ensure_ascii=False))
        self.assertTrue(request["reference_images"])
        self.assertEqual(request["scope"]["paths"][0]["sha256"], original_hash)
        for item in request["images"] + request["reference_images"]:
            with Image.open(Path(packet["packet"]) / item["image"]) as image:
                self.assertNotIn("Author", image.info)
                self.assertNotIn("Prompt", image.info)
        self.assertEqual(before_revision, project.load_project(self.job)["revision"])

    def test_sequence_reorder_does_not_invalidate_identity_or_copy(self):
        self.write_copy()
        self.register_all_finals()
        self.review_category("identity")
        self.review_category("copy")
        self.review_category("sequence")
        data = project.load_project(self.job)
        data["finals"] = list(reversed(data["finals"]))
        project.save_project(self.job, data)
        sequence = self.call("review_checks.py", "status", self.job, "--category", "sequence", "--images", "inputs/original.png")
        self.assertEqual(sequence["verdict"], "pass")
        self.assertEqual(sequence["review"], "not_assessed")
        self.assertEqual(sequence["stale"][0]["reason"], "sequence_changed")
        self.assertFalse(sequence["blocking"])
        for category in ("identity", "copy"):
            state = self.call("review_checks.py", "status", self.job, "--category", category, "--images", "inputs/original.png")
            self.assertEqual(state["review"], "pass")

    def test_builtin_inspections_need_no_reports_and_ratio_is_publish_only(self):
        self.write_copy()
        self.register_all_finals()
        before = (self.job / "project.json").read_bytes()
        for category in ("files", "copy", "sequence"):
            state = self.call("review_checks.py", "status", self.job, "--category", category, "--images", "inputs/original.png")
            self.assertEqual(state["verdict"], "pass")
            self.assertEqual(state["review"], "not_assessed")
            self.assertFalse(state["blocking"])
        self.assertFalse((self.job / "reviews/categories").exists())
        self.assertEqual(before, (self.job / "project.json").read_bytes())
        for stage in ("concept", "material", "cutout", "scene"):
            gate = self.call("review_checks.py", "check", self.job, "--stage", stage, "--images", "inputs/original.png")
            self.assertFalse(any(item.get("code") == "files.ratio" for item in gate["findings"]))
            self.assertNotIn("files", gate["categories"])
        gate = self.call("review_checks.py", "check", self.job, "--stage", "package")
        self.assertTrue(gate["blocking"])
        self.assertTrue(any(item.get("code") == "files.ratio" for item in gate["findings"]))
        (self.job / "inputs/broken.png").write_bytes(b"not-an-image")
        for stage in ("concept", "material", "cutout", "scene", "package", "done"):
            gate = self.call("review_checks.py", "check", self.job, "--stage", stage, "--images", "inputs/broken.png")
            self.assertTrue(gate["blocking"])
            self.assertTrue(any(item.get("code") == "files.decode_failed" for item in gate["findings"]))

    def test_empty_scene_registration_is_not_physics_pass_but_actual_image_is_blocked(self):
        empty = self.call("review_checks.py", "check", self.job, "--stage", "scene", "--images")
        self.assertFalse(empty["blocking"])
        self.assertEqual(empty["categories"]["physics"]["verdict"], "not_applicable")
        self.assertEqual(empty["inspections"]["files"]["verdict"], "not_applicable")
        self.assertEqual(empty["inspections"]["files"]["checked"], 0)
        actual = self.call("review_checks.py", "check", self.job, "--stage", "scene", "--images", "inputs/original.png")
        self.assertTrue(actual["blocking"])
        self.assertEqual(actual["categories"]["physics"]["paths"][0]["verdict"], "missing")

    def test_identity_failure_is_reachable_from_record_and_select_cli(self):
        round_id, path = test_workflow.WorkflowTests.round(self, "concept", register=False)
        data = project.read_json(path)
        images = [(path.parent / item["image"]).relative_to(self.job).as_posix() for item in data["items"]]
        self.call("project.py", "record-round", self.job, round_id)
        self.review_category("identity", "needs_revision", *images)
        before = (self.job / "project.json").read_bytes()
        result = self.call("project.py", "record-round", self.job, round_id, code=2)
        self.assertIn("审查", result["error"])
        self.assertEqual(before, (self.job / "project.json").read_bytes())
        result = self.call("project.py", "select", self.job, round_id, "A", code=2)
        self.assertIn("审查", result["error"])
        self.assertEqual(before, (self.job / "project.json").read_bytes())

    def test_record_is_visible_and_idempotent_inside_core_transaction(self):
        packet = self.packet("identity", "inputs/original.png")
        report_path = self.root / "in-transaction.json"
        project.write_json(report_path, self.make_report(packet))
        with project.project_lock(self.job):
            before = project.load_project(self.job, writable=True)
            first = review_checks.record(self.job, before, "identity", packet["review_id"], report_path)
            current = project.load_project(self.job, writable=True)
            state = review_checks.status(self.job, current, "identity", ["inputs/original.png"])
            self.assertEqual(state["verdict"], "pass")
            retry = review_checks.record(self.job, current, "identity", packet["review_id"], report_path)
            self.assertTrue(retry["unchanged"])
            self.assertEqual(first["revision"], retry["revision"])
        state = self.call("review_checks.py", "status", self.job, "--category", "identity", "--images", "inputs/original.png")
        self.assertEqual(state["verdict"], "pass")

    def test_ux_requires_page_observation_and_does_not_gate_customer_selection(self):
        packet = self.packet("ux")
        findings = [{"code": "ux.recovery", "severity": "blocking", "detail": "提交失败后没有恢复路径",
                     "evidence": "测试页按钮持续禁用且没有重试提示", "fix": "增加恢复入口与状态说明"}]
        report = self.make_report(packet, verdict="needs_revision", findings=findings)
        report["reviewer"].pop("viewed_page")
        report_path = self.root / "ux-unviewed.json"
        project.write_json(report_path, report)
        result = self.call("review_checks.py", "record", self.job, "--category", "ux", "--request", packet["review_id"],
                           "--report", report_path, code=2)
        self.assertIn("实际查看页面", result["error"])
        report["reviewer"]["viewed_page"] = True
        self.record_report(packet, report)
        state = self.call("review_checks.py", "status", self.job, "--category", "ux")
        self.assertTrue(state["blocking"])
        gate = self.call("review_checks.py", "check", self.job, "--stage", "concept", "--images", "inputs/original.png")
        self.assertFalse(gate["blocking"])
        self.assertNotIn("ux", gate["categories"])

    def test_review_write_cli_holds_execution_lease_before_project_lock(self):
        environment = mock.patch.dict(os.environ, {"JARDEN_UPDATE_HOME": str(self.root / "updates")})
        with environment:
            home = runtime_activity.instance_home(SKILL)
            before_revision = project.load_project(self.job)["revision"]
            with project.project_lock(self.job):
                process = subprocess.Popen(
                    [sys.executable, str(SKILL / "scripts/review_checks.py"), "prepare", str(self.job),
                     "--category", "identity", "--producer", "producer-maker", "--images", "inputs/original.png"],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                self.addCleanup(lambda: process.kill() if process.poll() is None else None)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not list((home / "readers").glob("*.json")) and process.poll() is None:
                    time.sleep(0.02)
                self.assertIsNone(process.poll())
                with runtime_activity.instance_gate(SKILL) as gate_home:
                    with self.assertRaises(runtime_activity.RuntimeBusyError):
                        runtime_activity.require_idle(gate_home, SKILL)
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            packet = json.loads(stdout)
            self.assertFalse(list((home / "readers").glob("*.json")))
            self.assertEqual(before_revision, project.load_project(self.job)["revision"])
            report_path = self.root / "lease-report.json"
            project.write_json(report_path, self.make_report(packet))
            transaction = home / "transactions/test.json"
            project.write_json(transaction, {"phase": "preparing"})
            for arguments in (
                ("prepare", "--category", "identity", "--producer", "producer-maker"),
                ("record", "--category", "identity", "--request", packet["review_id"], "--report", report_path),
                ("status", "--category", "identity"),
                ("check", "--stage", "concept"),
            ):
                result = self.call("review_checks.py", arguments[0], self.job, *arguments[1:], code=2)
                self.assertIn("更新事务", result["error"])
            self.assertEqual(before_revision, project.load_project(self.job)["revision"])
            self.assertFalse(list((home / "readers").glob("*.json")))

    def test_copy_failure_report_blocks_without_turning_missing_report_into_failure(self):
        self.write_copy()
        self.register_all_finals()
        missing = self.call("review_checks.py", "status", self.job, "--category", "copy", "--images", "inputs/original.png")
        self.assertFalse(missing["blocking"])
        self.assertEqual(missing["review"], "not_assessed")
        self.review_category("copy", "needs_revision")
        reviewed = self.call("review_checks.py", "status", self.job, "--category", "copy", "--images", "inputs/original.png")
        self.assertTrue(reviewed["blocking"])
        self.assertEqual(reviewed["review"], "blocked")
        self.assertFalse(reviewed["not_assessed"])
        gate = self.call("review_checks.py", "check", self.job, "--stage", "package", "--images", "inputs/original.png")
        self.assertTrue(gate["blocking"])
        self.assertIn("copy", gate["categories"])
        self.assertTrue(any(item["category"] == "copy" for item in gate["findings"]))

    def test_packaging_cannot_bypass_valid_copy_failure_report(self):
        self.write_copy()
        self.register_all_finals()
        test_workflow.WorkflowTests.review(self, ["inputs/original.png"])
        self.review_category("copy", "needs_revision")
        before = (self.job / "project.json").read_bytes()
        result = self.call("package_post.py", self.job, "--dry-run", code=2)
        self.assertIn("copy 审查仍有阻断问题", result["error"])
        self.assertEqual((self.job / "project.json").read_bytes(), before)
        self.assertFalse((self.job / "post").exists())

    def test_packaging_cannot_bypass_valid_identity_failure_report(self):
        self.write_copy()
        self.register_all_finals()
        test_workflow.WorkflowTests.review(self, ["inputs/original.png"])
        self.review_category("identity", "needs_revision")
        before = (self.job / "project.json").read_bytes()
        result = self.call("package_post.py", self.job, "--dry-run", code=2)
        self.assertIn("分类审查仍有阻断问题", result["error"])
        self.assertEqual((self.job / "project.json").read_bytes(), before)
        self.assertFalse((self.job / "post").exists())

    def test_prepared_output_failures_bind_by_content_not_staging_directory(self):
        self.write_copy()
        self.register_all_finals()
        test_workflow.WorkflowTests.review(self, ["inputs/original.png"])
        prepared = self.call("package_post.py", self.job, "--prepare-review")
        test_workflow.WorkflowTests.review(self, prepared["images"])
        for category in ("identity", "copy"):
            with self.subTest(category=category):
                self.review_category(category, "needs_revision", *prepared["images"])
                before = (self.job / "project.json").read_bytes()
                result = self.call("package_post.py", self.job, code=2)
                self.assertIn("审查仍有阻断问题", result["error"])
                self.assertFalse((self.job / "post").exists())
                self.assertEqual(before, (self.job / "project.json").read_bytes())
                self.review_category(category, "pass", *prepared["images"])
        completed = self.call("package_post.py", self.job)
        self.assertTrue(completed["ok"])

    def test_alternate_copy_uses_actual_content_for_inspection_and_review(self):
        self.write_copy()
        self.register_all_finals()
        test_workflow.WorkflowTests.review(self, ["inputs/original.png"])
        self.review_category("copy", "needs_revision")
        alternate = self.job / "alternate-copy.json"
        alternate.write_bytes((self.job / "copy.json").read_bytes())
        replacement = project.read_json(self.job / "copy.json")
        replacement["body"] = "另一版尚未独立审查的设计说明。"
        project.write_json(self.job / "copy.json", replacement)
        default = self.call("review_checks.py", "status", self.job, "--category", "copy", "--images", "inputs/original.png")
        self.assertFalse(default["blocking"])
        result = self.call("package_post.py", self.job, "--copy", alternate, "--dry-run", code=2)
        self.assertIn("copy 审查仍有阻断问题", result["error"])
        packet = self.call("review_checks.py", "prepare", self.job, "--category", "copy", "--producer", "producer-maker",
                           "--images", "inputs/original.png", "--copy", alternate)
        self.record_report(packet, self.make_report(packet), "alternate-pass.json")
        accepted = self.call("package_post.py", self.job, "--copy", alternate, "--dry-run")
        self.assertTrue(accepted["ok"])
        invalid = project.read_json(alternate)
        invalid["title"] = ""
        project.write_json(alternate, invalid)
        result = self.call("review_checks.py", "status", self.job, "--category", "copy", "--copy", alternate)
        self.assertTrue(result["blocking"])

    def test_changing_other_image_does_not_erase_unchanged_negative_verdict(self):
        candidate = "inputs/candidate.png"
        Image.new("RGB", (320, 400), (20, 130, 240)).save(self.job / candidate)
        packet = self.packet("identity", "inputs/original.png", candidate)
        report = self.make_report(packet, verdict="needs_revision", findings=[{
            "code": "identity.fixture", "severity": "blocking", "detail": "第一张身份不符",
            "evidence": "程序协议测试预设第一张失败，不是真实视觉验收。", "fix": "只修第一张并重新送审",
        }])
        report["paths"][1]["verdict"] = "pass"
        self.record_report(packet, report)
        Image.new("RGB", (320, 400), (30, 140, 240)).save(self.job / candidate)
        blocked = self.call("review_checks.py", "status", self.job, "--category", "identity", "--images", "inputs/original.png")
        self.assertTrue(blocked["blocking"])
        self.assertEqual(blocked["paths"][0]["verdict"], "needs_revision")
        self.review_category("identity")
        accepted = self.call("review_checks.py", "status", self.job, "--category", "identity", "--images", "inputs/original.png")
        self.assertFalse(accepted["blocking"])

    def test_content_alias_reuses_valid_report_but_not_other_image_changes(self):
        self.review_category("identity")
        alias = "inputs/content-alias.png"
        (self.job / alias).write_bytes((self.job / "inputs/original.png").read_bytes())
        accepted = self.call("review_checks.py", "status", self.job, "--category", "identity", "--images", alias)
        self.assertEqual(accepted["verdict"], "pass")
        Image.new("RGB", (320, 400), (30, 140, 240)).save(self.job / alias)
        changed = self.call("review_checks.py", "status", self.job, "--category", "identity", "--images", alias)
        self.assertEqual(changed["verdict"], "not_assessed")

    def test_physics_failure_is_not_masked_by_creative_pass_and_packet_is_read_only(self):
        packet = self.call("physics_review.py", "prepare", self.job, "--producer", "producer", "--images", "inputs/original.png")
        request_file = Path(packet["packet"]) / "request.json"
        request = project.read_json(request_file)
        report = {
            "schema_version": 1,
            "request_id": packet["request_id"],
            "reviewer": {"id": "physics-reviewer", "zero_context": True, "viewed_images": True},
            "results": [{
                "id": request["images"][0]["id"],
                "sha256": request["images"][0]["sha256"],
                "verdict": "needs_revision",
                "summary": "挂环没有可信闭合与支撑",
                "checks": {
                    name: {
                        "verdict": "needs_revision" if name in ("connections_materials", "overall_realism") else "pass",
                        "evidence": "完整图中可见挂环断开，接触阴影与结构不一致。",
                    }
                    for name in physics_review.CHECKS
                },
                "findings": [{"region": "挂环", "issue": "断开", "fix": "恢复闭合连接"}],
            }],
        }
        report_path = self.root / "physics-report.json"
        project.write_json(report_path, report)
        self.call("physics_review.py", "record", self.job, "--request", packet["request_id"], "--report", report_path)

        def packet_digest():
            return {
                path.relative_to(self.job).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (self.job / "reviews/physics").rglob("*") if path.is_file()
            }

        before = packet_digest()
        self.review_category("creative")
        after = packet_digest()
        self.assertEqual(before, after)
        creative = self.call("review_checks.py", "status", self.job, "--category", "creative", "--images", "inputs/original.png")
        physics = self.call("review_checks.py", "status", self.job, "--category", "physics", "--images", "inputs/original.png")
        self.assertEqual(creative["verdict"], "pass")
        self.assertEqual(physics["verdict"], "blocked")
        gate = self.call("review_checks.py", "check", self.job, "--stage", "review", "--images", "inputs/original.png")
        self.assertTrue(gate["blocking"])
        self.assertTrue(any(item["category"] == "physics" and item["verdict"] == "needs_revision" for item in gate["findings"]))


if __name__ == "__main__":
    unittest.main()
