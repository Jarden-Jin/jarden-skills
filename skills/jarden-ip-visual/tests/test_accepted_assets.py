"""Real CLI gates with synthetic images/reports, never real visual or user approval."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


SKILL = Path(__file__).resolve().parents[1]
SYNTHETIC_EVIDENCE = "synthetic 协议夹具：模拟用户授权决策与完整主体身份审查，不代表真人批准或真实独立视觉验收。"


class AcceptedAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="synthetic-accepted-assets-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "project with spaces"
        source = self.directory / "synthetic-reference.png"
        image = Image.new("RGB", (256, 320), "white")
        ImageDraw.Draw(image).ellipse((50, 30, 210, 295), fill=(170, 150, 210))
        image.save(source)
        self.call("project.py", "init", self.root, "--input", source, "--kind", "wireframe")
        self.call("project.py", "set", self.root,
                  "answers.product_form=plush", "answers.subject_scope=single",
                  'answers.subjects=["小云"]', "answers.concept_count=1",
                  "answers.note_type=single-multiview", "answers.delivery=full-post",
                  "image_backend.kind=builtin")
        for stage in ("concept", "material"):
            self.call("project.py", "accept-input", self.root, "--stage", stage,
                      "--path", "inputs/synthetic-reference.png", "--origin", "agent-inferred")
        self.candidate = "inputs/synthetic-cutout.png"
        self.make_image(self.candidate)

    def call(self, script, *arguments, code=0):
        result = subprocess.run(
            [sys.executable, str(SKILL / "scripts" / script), *map(str, arguments)],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def read_project(self):
        return json.loads((self.root / "project.json").read_text(encoding="utf-8"))

    def make_image(self, relative, *, mode="RGBA", background=(0, 0, 0, 0), color=(170, 150, 210, 255)):
        image = Image.new(mode, (256, 320), background)
        ImageDraw.Draw(image).ellipse((50, 30, 210, 295), fill=color)
        image.save(self.root / relative)

    def prepare_report(self, *images, verdict="pass", reviewer="synthetic-independent-reviewer"):
        packet = self.call("review_checks.py", "prepare", self.root, "--category", "identity",
                           "--producer", "synthetic-producer", "--stage", "cutout",
                           "--images", *(images or (self.candidate,)))
        request = json.loads(Path(packet["request"]).read_text(encoding="utf-8"))
        scope = request["scope"]
        report = {
            "schema_version": 2,
            "review_id": packet["review_id"],
            "category": "identity",
            "scope": scope,
            "image_hashes": [item["sha256"] for item in scope["paths"]],
            "decision_signature": scope.get("decision_signature"),
            "template_version": scope.get("template_version"),
            "reviewer": {"id": reviewer, "independence": "zero-context", "viewed_images": True},
            "verdict": verdict,
            "findings": [] if verdict == "pass" else [{
                "code": "identity.synthetic_incomplete_subject",
                "severity": "blocking",
                "detail": "synthetic 夹具模拟主体轮廓不完整或身份无法确认。",
                "evidence": SYNTHETIC_EVIDENCE,
                "fix": "synthetic 夹具模拟补齐主体后重新独立审查。",
            }],
            "evidence": SYNTHETIC_EVIDENCE,
            "paths": [{**item, "verdict": verdict, "evidence": SYNTHETIC_EVIDENCE}
                      for item in scope["paths"]],
        }
        report_path = self.directory / (packet["review_id"] + "-synthetic-report.json")
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        return packet, report_path

    def record_report(self, packet, report_path, *, code=0):
        return self.call("review_checks.py", "record", self.root, "--category", "identity",
                         "--request", packet["review_id"], "--report", report_path, code=code)

    def review(self, *images, verdict="pass"):
        packet, report_path = self.prepare_report(*images, verdict=verdict)
        self.record_report(packet, report_path)

    def accept(self, *arguments, path=None, code=0):
        return self.call("project.py", "accept-input", self.root, "--stage", "cutout",
                         "--path", path or self.candidate, "--origin", "agent-inferred",
                         *arguments, code=code)

    def assert_rejected(self, *arguments, reason):
        before = (self.root / "project.json").read_bytes()
        result = self.accept(*arguments, code=2)
        self.assertFalse(result["ok"])
        self.assertIn(reason, result["error"])
        self.assertEqual(before, (self.root / "project.json").read_bytes())

    def test_cutout_requires_nonempty_evidence_even_after_identity_pass(self):
        self.review()
        for arguments in ((), ("--evidence", ""), ("--evidence", " \t ")):
            with self.subTest(arguments=arguments):
                self.assert_rejected(*arguments, reason="非空 --evidence")

    def test_alpha_and_author_evidence_do_not_replace_independent_identity(self):
        result = self.call("check_images.py", self.root / self.candidate, "--alpha")
        self.assertTrue(result["ok"])
        self.assert_rejected("--evidence", SYNTHETIC_EVIDENCE, reason="not_assessed")

    def test_alpha_gate_still_applies_to_reviewed_assets(self):
        cases = (
            {"mode": "RGB", "background": "white", "color": (170, 150, 210)},
            {"background": (255, 255, 255, 255)},
            {"color": (0, 0, 0, 0)},
        )
        for options in cases:
            with self.subTest(options=options):
                self.make_image(self.candidate, **options)
                self.review()
                self.assert_rejected("--evidence", SYNTHETIC_EVIDENCE, reason="alpha")

    def test_reviewed_cutout_preserves_evidence_and_agent_origin(self):
        self.review()
        result = self.accept("--evidence", "  " + SYNTHETIC_EVIDENCE + "  ",
                             path="./inputs/../inputs/synthetic-cutout.png")
        current = self.read_project()
        selection = current["selections"]["cutout"]["小云"]
        self.assertEqual(selection, {
            "round": "input", "item": "accepted", "image": self.candidate,
            "origin": "agent-inferred", "evidence": SYNTHETIC_EVIDENCE,
        })
        self.assertEqual(result["stage"], "scene")
        self.assertNotIn("用户明确确认", result["note"])
        self.assertEqual({record["category"] for record in current["review_records"].values()}, {"identity"})

    def test_uncertain_or_failed_identity_cannot_be_accepted(self):
        for verdict in ("uncertain", "needs_revision"):
            with self.subTest(verdict=verdict):
                self.review(verdict=verdict)
                self.assert_rejected("--evidence", SYNTHETIC_EVIDENCE, reason=verdict)

    def test_another_paths_identity_pass_does_not_cover_the_candidate(self):
        other = "inputs/synthetic-other-cutout.png"
        self.make_image(other, color=(80, 170, 200, 255))
        self.review(other)
        self.assert_rejected("--evidence", SYNTHETIC_EVIDENCE, reason="not_assessed")

    def test_candidate_bytes_changed_after_review_invalidate_pass(self):
        self.review()
        self.make_image(self.candidate, color=(90, 180, 200, 255))
        self.assert_rejected("--evidence", SYNTHETIC_EVIDENCE, reason="images_changed")

    def test_reference_bytes_changed_after_review_invalidate_pass(self):
        self.review()
        self.make_image("inputs/synthetic-reference.png", color=(90, 180, 200, 255))
        self.assert_rejected("--evidence", SYNTHETIC_EVIDENCE, reason="references_changed")

    def test_replacing_material_reference_invalidates_identity_pass(self):
        self.review()
        replacement = "inputs/synthetic-new-material.png"
        self.make_image(replacement, color=(90, 180, 200, 255))
        self.call("project.py", "accept-input", self.root, "--stage", "material",
                  "--path", replacement, "--origin", "agent-inferred")
        self.assert_rejected("--evidence", SYNTHETIC_EVIDENCE, reason="references_changed")

    def test_self_authored_report_cannot_supply_identity_pass(self):
        packet, report_path = self.prepare_report(reviewer="synthetic-producer")
        result = self.record_report(packet, report_path, code=2)
        self.assertIn("不能自行批准", result["error"])
        self.assert_rejected("--evidence", SYNTHETIC_EVIDENCE, reason="not_assessed")

    def test_report_must_cover_every_requested_path(self):
        other = "inputs/synthetic-other-cutout.png"
        self.make_image(other, color=(80, 170, 200, 255))
        packet, report_path = self.prepare_report(self.candidate, other)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["paths"] = report["paths"][1:]
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        result = self.record_report(packet, report_path, code=2)
        self.assertIn("逐张覆盖", result["error"])
        self.assert_rejected("--evidence", SYNTHETIC_EVIDENCE, reason="not_assessed")


if __name__ == "__main__":
    unittest.main()
