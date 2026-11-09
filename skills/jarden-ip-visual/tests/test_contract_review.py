from __future__ import annotations

import copy
import hashlib
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock


SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

import contract_review
import project as state


def png(path, color=(20, 140, 80), metadata=False):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 6, 8, 8, 2, 0, 0, 0)
    pixels = b"".join(b"\x00" + bytes(color) * 6 for _ in range(8))
    content = contract_review.PNG_SIGNATURE + chunk(b"IHDR", header)
    if metadata:
        content += chunk(b"tEXt", b"Prompt\x00author expected pass")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


class ContractReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="contract-review-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(state, "SCHEMA_VERSION", 3).start()
        png(self.root / "front.png", metadata=True)
        png(self.root / "back.png", color=(30, 130, 90))
        png(self.root / "reference.png", color=(40, 120, 100), metadata=True)
        self.project = {
            "schema_version": 3, "project_id": "g1-fixture", "revision": 1,
            "product_contract": {
                "enabled": True, "schema_version": 1,
                "subjects": [{"id": "green", "identity": {"eyes": "两只黑色椭圆眼"}}],
                "specs": [{"id": "small", "subject_id": "green", "scale": {"status": "design-target", "height": 12, "unit": "cm"},
                           "references": [{"path": "reference.png", "view": "front", "origin": "reference-derived"}]}],
            },
            "scene_plan": {"schema_version": 1, "slots": [
                {"id": "cover", "subject_id": "green", "spec_id": "small", "role": "cover", "camera": {"yaw": 0}, "path": "front.png"},
                {"id": "back", "subject_id": "green", "spec_id": "small", "role": "angle", "camera": {"yaw": 180}, "path": "back.png"},
            ], "required_slots": ["cover", "back"], "preserve_images": [{"slot_id": "cover", "sha256": self.sha("front.png")}]},
            "finals": [{"slot": "cover", "path": "front.png"}, {"slot": "back", "path": "back.png"}],
        }
        self.save()

    def save(self):
        state.atomic_json(self.root / "project.json", self.project)

    def sha(self, relative):
        return hashlib.sha256((self.root / relative).read_bytes()).hexdigest()

    def prepare(self, paths=None, stage="scene", ordered_slots=None):
        return contract_review.prepare(self.root, self.project, paths or ["front.png", "back.png"], "fixture-producer", stage, ordered_slots)

    def report(self, packet, verdict="pass", sequence_verdict=None):
        request = state.read_json(Path(packet["request"]))
        findings = [] if verdict == "pass" else [{"code": "fixture.uncertain", "evidence": "协议测试预置缺陷，不代表实际视觉审查", "fix": "重新独立看图核验"}]

        def check(result):
            return {"verdict": result, "basis": "visible_pixels", "evidence": "仅为程序契约测试预置证据，没有进行真实独立看图。"}

        results = [{"slot_id": image["slot_id"], "path": image["path"], "sha256": image["sha256"], "verdict": verdict,
                    "checks": {name: check(verdict) for name in contract_review.CHECKS},
                    "observed_views": ["front" if image["slot_id"] == "cover" else "back"], "findings": copy.deepcopy(findings)}
                   for image in request["scope"]["images"]]
        sequence_verdict = sequence_verdict or verdict
        sequence = {"verdict": sequence_verdict, "checks": {name: check(sequence_verdict) for name in contract_review.SEQUENCE_CHECKS},
                    "actual_order": request["scope"]["ordered_slots"], "findings": [] if sequence_verdict == "pass" else copy.deepcopy(findings or [{"code": "fixture.sequence", "evidence": "测试整套未通过", "fix": "复核实际集合"}]),
                    "observed_views": [{"slot_id": row["slot_id"], "sha256": row["sha256"], "views": row["observed_views"],
                                        "basis": "visible_pixels", "evidence": "程序测试观察声明，不是真实图像角度验收。"} for row in results],
                    "preserve_images": [{"slot_id": row["slot_id"], "sha256": row["sha256"], **check(sequence_verdict)}
                                        for row in request["scope"]["collection"]["preserve_images"]]}
        return {"schema_version": 1, "request_id": request["request_id"], "scope_signature": request["scope_signature"],
                "reviewer": {"id": "fixture-independent", "independence": "zero-context", "viewed_images": True},
                "verdict": "pass" if verdict == sequence_verdict == "pass" else "uncertain", "results": results, "sequence": sequence}

    def record(self, packet, report=None):
        report_file = self.root / "incoming-report.json"
        state.atomic_json(report_file, report if report is not None else self.report(packet))
        return contract_review.record(self.root, self.project, packet["request_id"], report_file)

    def status(self, paths=None, stage="scene", ordered_slots=None):
        return contract_review.status(self.root, self.project, paths or ["front.png", "back.png"], stage, ordered_slots)

    def test_disabled_legacy_and_non_scene_are_not_applicable_without_files(self):
        for schema, enabled, stage in ((2, True, "scene"), (3, False, "scene"), (3, True, "concept"), (3, True, "cutout")):
            with self.subTest(schema=schema, enabled=enabled, stage=stage):
                project = copy.deepcopy(self.project)
                project["schema_version"] = schema
                project["product_contract"]["enabled"] = enabled
                result = contract_review.status(self.root, project, ["missing.png"], stage)
                self.assertFalse(result["blocking"])
                self.assertEqual(result["verdict"], "not_applicable")
        self.assertFalse((self.root / "reviews").exists())

    def test_missing_review_blocks_without_creating_reports(self):
        before = (self.root / "project.json").read_bytes()
        result = self.status()
        self.assertTrue(result["blocking"])
        self.assertEqual([row["state"] for row in result["paths"]], ["needs_review", "needs_review"])
        self.assertEqual(result["sequence"]["state"], "needs_review")
        with self.assertRaises(contract_review.ContractReviewError):
            contract_review.require(self.root, self.project, ["front.png", "back.png"])
        self.assertEqual(before, (self.root / "project.json").read_bytes())
        self.assertFalse((self.root / "reviews").exists())

    def test_partial_batch_does_not_require_future_publication_views(self):
        self.project["scene_plan"]["coverage"] = {"required_views": ["front", "back", "detail"], "preserve_slots": ["cover"]}
        self.project["scene_plan"]["slots"][0]["view"] = "front"
        self.project["scene_plan"]["slots"][1]["view"] = "back"
        self.save()
        packet = self.prepare(["front.png"])
        request = state.read_json(Path(packet["request"]))
        collection = request["scope"]["collection"]
        self.assertEqual(collection["mode"], "batch")
        self.assertEqual(collection["plan"]["coverage"]["required_views"], ["front"])
        self.assertEqual(set(collection["slot_requirements"]), {"cover"})
        self.assertEqual(self.project["scene_plan"]["coverage"]["required_views"], ["front", "back", "detail"])
        with self.assertRaises(contract_review.ContractReviewError):
            self.prepare(["front.png"], stage="package")

    def test_same_reviewer_cannot_approve_g1_and_g2_for_the_same_pixels(self):
        import physics_review

        png(self.root / "front.png")
        self.project["scene_plan"]["preserve_images"][0]["sha256"] = self.sha("front.png")
        self.project["answers"] = {"product_form": "plush"}
        self.save()
        packet = self.prepare()
        self.record(packet)
        self.assertFalse(self.status()["blocking"])
        physical_packet = physics_review.prepare(self.root, self.project, ["front.png", "back.png"], "fixture-producer")
        physical_request = state.read_json(Path(physical_packet["packet"]) / "request.json")
        report = {
            "schema_version": 1, "request_id": physical_packet["request_id"],
            "reviewer": {"id": "fixture-independent", "zero_context": True, "viewed_images": True},
            "results": [
                {"id": row["id"], "sha256": row["sha256"], "verdict": "pass",
                 "summary": "协议夹具，非实际视觉验收",
                 "checks": {name: {"verdict": "pass", "evidence": "程序预置证据"} for name in physics_review.CHECKS},
                 "findings": []} for row in physical_request["images"]
            ]
        }
        incoming = self.root / "physical-fixture.json"
        state.atomic_json(incoming, report)
        physics_review.record(self.root, self.project, physical_packet["request_id"], incoming)
        result = self.status()
        self.assertTrue(result["blocking"])
        self.assertTrue(all(row["verdict"] == "invalid" for row in result["paths"]))
        self.assertIn("不同新上下文", result["paths"][0]["reason"])

    def test_prepare_cleans_metadata_binds_original_and_does_not_touch_g2(self):
        g2 = self.root / "reviews/physics/packet.json"
        state.atomic_json(g2, {"sentinel": "do not change"})
        before = (self.root / "project.json").read_bytes()
        packet = self.prepare()
        request = state.read_json(Path(packet["request"]))
        self.assertEqual(before, (self.root / "project.json").read_bytes())
        self.assertEqual(request["scope"]["images"][0]["sha256"], self.sha("front.png"))
        self.assertNotIn("producer", request)
        for row in request["images"] + request["reference_images"]:
            content = (Path(packet["packet"]) / row["image"]).read_bytes()
            self.assertNotIn(b"author expected pass", content)
            self.assertTrue(content.startswith(contract_review.PNG_SIGNATURE))
        self.assertEqual(state.read_json(g2), {"sentinel": "do not change"})
        self.assertIn("不是实际观察证据", request["task"])

    def test_png_invalid_stream_non_png_animation_and_author_hint_are_refused(self):
        (self.root / "front.png").write_bytes(b"not png")
        with self.assertRaises(contract_review.ContractReviewError):
            self.prepare()
        self.assertFalse((self.root / "reviews").exists())
        png(self.root / "front.png")
        self.project["scene_plan"]["preserve_images"][0]["sha256"] = self.sha("front.png")
        self.project["product_contract"]["specs"][0]["scale"]["description"] = "作者希望必须通过"
        with self.assertRaises(contract_review.ContractReviewError):
            self.prepare()

    def test_valid_record_is_idempotent_and_revision_is_not_applicability(self):
        packet = self.prepare()
        report = self.report(packet)
        recorded = self.record(packet, report)
        self.assertFalse(recorded["unchanged"])
        self.assertEqual(recorded["revision"], 2)
        self.assertTrue(self.record(packet, report)["unchanged"])
        self.project["revision"] += 9
        self.project["feedback"] = [{"raw": "unrelated notes"}]
        self.save()
        self.assertFalse(self.status()["blocking"])
        self.assertEqual([row["state"] for row in self.status()["paths"]], ["reused", "reused"])

    def test_self_approval_missing_observation_checks_and_metadata_evidence_fail(self):
        packet = self.prepare()
        baseline = self.report(packet)
        variants = []
        for field, value in (("id", " fixture-producer "), ("viewed_images", False), ("independence", "author")):
            report = copy.deepcopy(baseline)
            report["reviewer"][field] = value
            variants.append(report)
        report = copy.deepcopy(baseline)
        report["results"][0]["checks"].pop("scale")
        variants.append(report)
        report = copy.deepcopy(baseline)
        report["results"][0]["checks"]["view"]["evidence"] = "yaw = 180.000"
        variants.append(report)
        report = copy.deepcopy(baseline)
        report["results"][0]["checks"]["scale"]["basis"] = "metadata"
        variants.append(report)
        report = copy.deepcopy(baseline)
        report["sequence"]["observed_views"] = []
        variants.append(report)
        report = copy.deepcopy(baseline)
        report["results"] = [report["results"][0], report["results"][0]]
        variants.append(report)
        before = (self.root / "project.json").read_bytes()
        for report in variants:
            with self.subTest(report=report):
                with self.assertRaises(contract_review.ContractReviewError):
                    self.record(packet, report)
                self.assertEqual(before, (self.root / "project.json").read_bytes())

    def test_uncertain_or_failed_checks_cannot_be_hidden_by_total_pass(self):
        packet = self.prepare()
        for verdict in ("uncertain", "needs_revision"):
            report = self.report(packet)
            report["results"][0]["checks"]["scale"]["verdict"] = verdict
            with self.assertRaises(contract_review.ContractReviewError):
                self.record(packet, report)
        self.record(packet, self.report(packet, "uncertain"))
        self.assertTrue(self.status()["blocking"])
        self.assertTrue(all(row["state"] == "blocked" for row in self.status()["paths"]))

    def test_image_request_reference_preview_and_report_tampering_block(self):
        for target in ("image", "request", "reference", "preview", "report"):
            with self.subTest(target=target):
                packet = self.prepare()
                report = self.report(packet)
                if target == "report":
                    saved = self.record(packet, report)
                    Path(saved["report"]).write_text("{}", encoding="utf-8")
                    self.assertTrue(self.status()["blocking"])
                    self.assertEqual(self.status()["paths"][0]["verdict"], "invalid")
                    continue
                if target == "image":
                    png(self.root / "back.png", color=(1, 2, 3))
                elif target == "reference":
                    png(self.root / "reference.png", color=(4, 5, 6))
                elif target == "request":
                    Path(packet["request"]).write_text("{}", encoding="utf-8")
                else:
                    (Path(packet["packet"]) / "image-001.png").write_bytes(b"tamper")
                with self.assertRaises(contract_review.ContractReviewError):
                    self.record(packet, report)

    def test_contract_slot_reference_changes_invalidate_without_revision_change(self):
        packet = self.prepare()
        self.record(packet)
        original = copy.deepcopy(self.project)
        for field in ("contract", "slot", "reference"):
            self.project = copy.deepcopy(original)
            with self.subTest(field=field):
                if field == "contract":
                    self.project["product_contract"]["specs"][0]["scale"]["height"] = 20
                elif field == "slot":
                    self.project["scene_plan"]["slots"][1]["camera"]["yaw"] = 90
                else:
                    png(self.root / "reference.png", color=(90, 80, 70))
                self.assertTrue(self.status()["blocking"])
                self.assertEqual(self.status()["paths"][1]["state"], "needs_review")

    def test_unrelated_future_slot_does_not_invalidate_reviewed_batch(self):
        packet = self.prepare()
        self.record(packet)
        self.project["scene_plan"]["slots"].append({"id": "optional-detail", "required": False, "subject_id": "green", "spec_id": "small", "camera": {"yaw": 90}})
        result = self.status()
        self.assertEqual([row["state"] for row in result["paths"]], ["reused", "reused"])
        self.assertEqual(result["sequence"]["state"], "reused")
        self.assertFalse(result["blocking"])
        with self.assertRaises(contract_review.ContractReviewError):
            self.prepare(stage="package")

    def test_order_change_reuses_images_not_collection_and_old_record_is_rejected(self):
        packet = self.prepare()
        report = self.report(packet)
        self.record(packet, report)
        reversed_result = self.status(["back.png", "front.png"])
        self.assertTrue(all(row["state"] == "reused" for row in reversed_result["paths"]))
        self.assertEqual(reversed_result["sequence"]["state"], "needs_review")
        invalid = self.prepare()
        wrong_order = self.report(invalid)
        wrong_order["sequence"]["actual_order"].reverse()
        with self.assertRaises(contract_review.ContractReviewError):
            self.record(invalid, wrong_order)

    def test_collection_failure_does_not_erase_per_image_pass(self):
        packet = self.prepare()
        self.record(packet, self.report(packet, sequence_verdict="uncertain"))
        result = self.status()
        self.assertTrue(all(row["state"] == "reused" for row in result["paths"]))
        self.assertEqual(result["sequence"]["state"], "blocked")
        self.assertTrue(result["blocking"])

    def test_new_failure_blocks_old_pass_replay_and_sibling_change_does_not_clear(self):
        old = self.prepare()
        old_report = self.report(old)
        self.record(old, old_report)
        new = self.prepare()
        self.record(new, self.report(new, "needs_revision"))
        with self.assertRaises(contract_review.ContractReviewError):
            self.record(old, old_report)
        png(self.root / "back.png", color=(44, 55, 66))
        result = self.status()
        self.assertEqual(result["paths"][0]["state"], "blocked")
        self.assertEqual(result["paths"][1]["state"], "needs_review")

    def test_rendered_paths_need_explicit_source_mapping_and_final_order(self):
        png(self.root / "post/front.png", color=(22, 33, 44))
        png(self.root / "post/back.png", color=(55, 66, 77))
        paths = ["post/front.png", "post/back.png"]
        for mapping in (None, ["cover", "back"]):
            with self.assertRaises(contract_review.ContractReviewError):
                self.prepare(paths, "package", mapping)
        mapping = [{"slot_id": "cover", "source": "front.png"}, {"slot_id": "back", "source": "back.png"}]
        packet = self.prepare(paths, "package", mapping)
        self.record(packet)
        self.assertFalse(self.status(paths, "done", mapping)["blocking"])
        self.assertTrue(self.status()["blocking"])
        with self.assertRaises(contract_review.ContractReviewError):
            self.prepare(["post/back.png"], "package", [mapping[1]])

    def test_same_bytes_new_path_reuses_current_applicability_not_changed_image(self):
        packet = self.prepare()
        self.record(packet)
        (self.root / "alias.png").write_bytes((self.root / "front.png").read_bytes())
        result = self.status(["alias.png", "back.png"])
        self.assertFalse(result["blocking"])
        png(self.root / "alias.png", color=(50, 60, 70))
        self.assertTrue(self.status(["alias.png", "back.png"])["blocking"])

    def test_preservation_original_hash_and_path_traversal_are_enforced(self):
        png(self.root / "front.png", color=(3, 4, 5))
        with self.assertRaises(contract_review.ContractReviewError):
            self.prepare()
        with self.assertRaises(contract_review.ContractReviewError):
            self.prepare(["../outside.png"])


class ProductionContractReviewTests(unittest.TestCase):
    save = ContractReviewTests.save
    sha = ContractReviewTests.sha
    prepare = ContractReviewTests.prepare
    report = ContractReviewTests.report
    record = ContractReviewTests.record
    status = ContractReviewTests.status

    def setUp(self):
        ContractReviewTests.setUp(self)
        self.project["answers"] = {"subjects": ["小绿", "小蓝"]}
        self.project["product_contract"] = {
            "schema_version": 1, "enabled": True,
            "subjects": {
                "subject-green": {"name": "小绿", "active": True, "identity": {"eyes": "两只黑色椭圆眼"}},
                "subject-blue": {"name": "小蓝", "active": True},
            },
            "specs": {
                "spec-green": {
                    "subject_id": "subject-green",
                    "usage": {"text": "桌面摆件", "status": "confirmed", "origin": "user-confirmed"},
                    "size": {"mode": "numeric", "height": 12, "unit": "cm", "measurement_basis": "本体站高",
                             "status": "confirmed", "origin": "user-confirmed", "evidence_kind": "design_target"},
                    "structure": {"attachment": "身体左侧有挂环"},
                },
                "spec-green-large": {"subject_id": "subject-green", "size": {"height": 40}},
                "spec-blue": {"subject_id": "subject-blue", "size": {"height": 30}},
            },
            "view_assets": {
                "view-green": {"id": "view-green", "subject_id": "subject-green", "spec_id": "spec-green",
                               "path": "reference.png", "sha256": self.sha("reference.png"), "view": "front",
                               "origin": "agent-inferred", "confirmation": {"status": "confirmed", "origin": "user-confirmed",
                                                                           "evidence": "认可设计目标，不代表已有实物"}},
                "view-large": {"subject_id": "subject-green", "spec_id": "spec-green-large", "path": "unrelated-large.png",
                               "view": "back", "origin": "reference-derived", "confirmation": {"status": "confirmed"}},
                "view-blue": {"subject_id": "subject-blue", "spec_id": "spec-blue", "path": "unrelated-blue.png",
                              "view": "front", "origin": "user-confirmed", "confirmation": {"status": "confirmed"}},
            },
            "requirements": {},
        }
        self.project["scene_plan"] = {
            "schema_version": 1, "revision": 0,
            "slots": [
                {"id": "cover", "role": "cover", "subject": "小绿", "spec_ref": "/product_contract/specs/spec-green",
                 "view": "front", "yaw": 0, "elevation": 10, "pose": "站立", "framing": "完整主体",
                 "reference_bindings": [{"kind": "identity", "path": "reference.png", "subject": "小绿"}]},
                {"id": "back", "role": "angle", "subject": "subject-green", "spec_ref": "/product_contract/specs/spec-green",
                 "view": "back", "yaw": 180, "elevation": 0, "pose": "站立", "framing": "完整主体", "reference_bindings": []},
            ],
            "coverage": {"required_views": ["front", "back"], "preserve_slots": ["cover"]},
        }
        self.save()

    def alternatives(self, status="planned"):
        self.project["finals"] = []
        entry = {"id": "01-scene", "stage": "scene", "kind": "alternatives", "status": status,
                 "dir": "rounds/01-scene", "review_slots": [], "review_paths": {}}
        paths = []
        for index, item_id in enumerate(("A", "B", "C")):
            slot_id = entry["id"] + "-" + item_id
            slot = {**copy.deepcopy(self.project["scene_plan"]["slots"][0]), "id": slot_id, "base_slot": "cover"}
            relative = entry["dir"] + "/" + item_id + ".png"
            png(self.root / relative, color=(20 + index, 40, 80))
            entry["review_slots"].append(slot)
            entry["review_paths"][slot_id] = relative
            paths.append(relative)
        self.project["rounds"] = [entry]
        self.save()
        return paths, entry

    def test_map_contract_scopes_subject_spec_and_assets_and_preserves_provenance(self):
        packet = self.prepare()
        for image in packet["scope"]["images"]:
            contract = image["contract"]
            self.assertEqual(set(contract["subjects"]), {"subject-green"})
            self.assertEqual(set(contract["specs"]), {"spec-green"})
            self.assertEqual(set(contract["view_assets"]), {"view-green"})
            self.assertEqual(contract["view_assets"]["view-green"]["origin"], "agent-inferred")
            self.assertEqual(contract["view_assets"]["view-green"]["confirmation"]["status"], "confirmed")
            self.assertEqual(contract["specs"]["spec-green"]["size"]["evidence_kind"], "design_target")
            self.assertEqual(image["reference_images"], [{"path": "reference.png", "sha256": self.sha("reference.png")}])
        self.assertEqual(packet["scope"]["collection"]["preserve_images"],
                         [{"slot_id": "cover", "sha256": self.sha("front.png"), "source_sha256": self.sha("front.png")}])
        self.record(packet)
        self.assertFalse(self.status()["blocking"])

    def test_unrelated_specs_subjects_assets_do_not_invalidate_current_review(self):
        self.record(self.prepare())
        self.project["product_contract"]["specs"]["spec-green-large"]["size"]["height"] = 50
        self.project["product_contract"]["subjects"]["subject-blue"]["identity"] = {"eyes": "单眼"}
        self.project["product_contract"]["view_assets"]["view-blue"]["origin"] = "agent-inferred"
        self.assertFalse(self.status()["blocking"])

    def test_changes_invalidate_only_slots_using_the_referenced_spec(self):
        png(self.root / "unrelated-blue.png", color=(25, 60, 80))
        back = self.project["scene_plan"]["slots"][1]
        back.update(subject="小蓝", spec_ref="/product_contract/specs/spec-blue")
        self.save()
        self.record(self.prepare())
        self.project["product_contract"]["specs"]["spec-green"]["size"]["height"] = 15
        result = self.status()
        self.assertEqual([row["state"] for row in result["paths"]], ["needs_review", "reused"])
        self.assertEqual(result["sequence"]["state"], "needs_review")

    def test_pointer_escaping_missing_reference_and_subject_mismatch(self):
        contract = self.project["product_contract"]
        contract["specs"]["spec/green~small"] = contract["specs"].pop("spec-green")
        contract["view_assets"]["view-green"]["spec_id"] = "spec/green~small"
        for slot in self.project["scene_plan"]["slots"]:
            slot["spec_ref"] = "/product_contract/specs/spec~1green~0small"
        packet = self.prepare()
        self.assertEqual(set(packet["scope"]["images"][0]["contract"]["specs"]), {"spec/green~small"})
        for pointer in ("/product_contract/specs/missing", "/product_contract/specs/spec~2green", "/product_contract/specs/spec-blue"):
            with self.subTest(pointer=pointer):
                self.project["scene_plan"]["slots"][0]["spec_ref"] = pointer
                with self.assertRaises(contract_review.ContractReviewError):
                    self.prepare()

    def test_project_spec_pointer_is_bound_and_list_spec_pointer_stays_compatible(self):
        self.project["product_spec"] = {"size": {"height": 12}, "origin": "agent-inferred"}
        self.project["scene_plan"]["slots"][0]["spec_ref"] = "/product_spec"
        projection = contract_review._contract_for(self.project, self.project["scene_plan"]["slots"][0])
        self.assertEqual(projection["resolved_spec_refs"], {"/product_spec": self.project["product_spec"]})
        contract = self.project["product_contract"]
        contract["specs"] = [{"id": identifier, **spec} for identifier, spec in contract["specs"].items()]
        self.project["scene_plan"]["slots"][0]["spec_ref"] = "/product_contract/specs/0"
        projection = contract_review._contract_for(self.project, self.project["scene_plan"]["slots"][0])
        self.assertEqual([spec["id"] for spec in projection["specs"]], ["spec-green"])
        with self.assertRaises(contract_review.ContractReviewError):
            contract_review._resolve(self.project, "/product_contract/specs/00")

    def test_scale_view_source_confirmation_and_slot_changes_invalidate(self):
        self.record(self.prepare())
        original = copy.deepcopy(self.project)
        for target in ("size", "origin", "confirmation", "view", "yaw", "elevation", "pose", "framing", "reference_bytes"):
            with self.subTest(target=target):
                self.project = copy.deepcopy(original)
                if target == "size":
                    self.project["product_contract"]["specs"]["spec-green"]["size"]["height"] = 15
                elif target == "origin":
                    self.project["product_contract"]["view_assets"]["view-green"]["origin"] = "reference-derived"
                elif target == "confirmation":
                    self.project["product_contract"]["view_assets"]["view-green"]["confirmation"]["status"] = "proposed"
                elif target == "reference_bytes":
                    png(self.root / "reference.png", color=(2, 3, 4))
                else:
                    self.project["scene_plan"]["slots"][0][target] = 30 if target in ("yaw", "elevation") else "changed"
                self.assertTrue(self.status()["blocking"])

    def test_set_scene_review_uses_round_paths_before_finals(self):
        self.project["finals"] = []
        self.project["rounds"] = [{"id": "02-scene-set", "stage": "scene", "kind": "set", "status": "planned",
                                   "review_paths": {"cover": "front.png", "back": "back.png"}}]
        self.save()
        with self.assertRaises(contract_review.ContractReviewError):
            contract_review.require(self.root, self.project, ["front.png", "back.png"], stage="scene")
        self.record(self.prepare())
        self.assertFalse(contract_review.require(self.root, self.project, ["front.png", "back.png"], stage="scene")["blocking"])

    def test_three_alternatives_have_distinct_stable_slots_without_changing_plan(self):
        paths, entry = self.alternatives()
        plan = copy.deepcopy(self.project["scene_plan"])
        packet = self.prepare(paths)
        self.assertEqual(packet["scope"]["ordered_slots"], [slot["id"] for slot in entry["review_slots"]])
        self.record(packet)
        entry = self.project["rounds"][0]
        entry["status"] = "recorded"
        self.assertFalse(self.status(paths)["blocking"])
        self.assertEqual(plan, self.project["scene_plan"])
        self.assertTrue(self.status(paths, "package")["blocking"])

    def test_partial_candidate_review_does_not_require_unproduced_sibling_files(self):
        paths, entry = self.alternatives()
        for relative in paths[1:]:
            (self.root / relative).unlink()
        packet = self.prepare(paths[:1])
        self.assertEqual(packet["scope"]["ordered_slots"], [entry["review_slots"][0]["id"]])
        self.record(packet)
        self.assertFalse(self.status(paths[:1])["blocking"])

    def test_main_mappings_keep_identical_candidate_bytes_in_separate_review_slots(self):
        paths, entry = self.alternatives()
        for relative in paths[1:]:
            (self.root / relative).write_bytes((self.root / paths[0]).read_bytes())
        mapping = state.review_mappings(self.root, self.project, paths, "scene")
        self.assertEqual([row["slot_id"] for row in mapping], list(entry["review_paths"]))
        packet = self.prepare(paths, ordered_slots=mapping)
        self.record(packet)
        self.assertFalse(self.status(paths, ordered_slots=mapping)["blocking"])

    def test_actual_round_output_filename_is_resolved_without_guessing_or_mutating_project(self):
        paths, entry = self.alternatives()
        actual = []
        items = []
        for index, (identifier, previous) in enumerate(entry["review_paths"].items()):
            filename = f"candidate-{index + 1}-revised.png"
            relative = entry["dir"] + "/" + filename
            (self.root / relative).write_bytes((self.root / previous).read_bytes())
            items.append({"id": chr(65 + index), "review_slot_id": identifier, "image": filename})
            actual.append(relative)
        state.atomic_json(self.root / entry["dir"] / "round.json", {"items": items})
        before = copy.deepcopy(self.project)
        mapping = state.review_mappings(self.root, self.project, actual, "scene")
        self.assertEqual([row["slot_id"] for row in mapping], list(entry["review_paths"]))
        packet = self.prepare(actual, ordered_slots=mapping)
        self.record(packet)
        self.assertFalse(self.status(actual, ordered_slots=mapping)["blocking"])
        self.assertEqual(entry["review_paths"], before["rounds"][0]["review_paths"])

    def test_inactive_review_slots_and_duplicate_ids_are_rejected(self):
        paths, entry = self.alternatives()
        for status in ("superseded", "selected", "cancelled"):
            with self.subTest(status=status):
                entry["status"] = status
                self.assertTrue(self.status(paths)["blocking"])
        entry["status"] = "planned"
        entry["review_slots"][0]["id"] = "cover"
        with self.assertRaises(contract_review.ContractReviewError):
            self.prepare(paths)

    def test_publication_requires_every_plan_slot_and_ignores_review_candidates(self):
        self.alternatives()
        self.project["finals"] = [{"slot": "cover", "path": "front.png"}, {"slot": "back", "path": "back.png"}]
        self.project["scene_plan"]["required_slots"] = ["cover"]
        self.project["scene_plan"]["slots"][1]["required"] = False
        self.save()
        mapping = [{"slot": "cover", "source": "front.png"}, {"slot": "back", "source": "back.png"}]
        with self.assertRaises(contract_review.ContractReviewError):
            self.prepare(["front.png"], "package", mapping[:1])
        packet = self.prepare(stage="package", ordered_slots=mapping)
        self.assertEqual(packet["scope"]["collection"]["required_slots"], ["cover", "back"])
        self.assertEqual(set(packet["scope"]["collection"]["slot_requirements"]), {"cover", "back"})
        self.record(packet)
        self.assertFalse(self.status(stage="done", ordered_slots=mapping)["blocking"])

    def test_package_shape_mapping_binds_arbitrary_staging_outputs_sources_and_order(self):
        import package_post

        paths = ["reviews/post/staging/arbitrary-one.png", "reviews/post/staging/arbitrary-two.png"]
        for index, relative in enumerate(paths):
            png(self.root / relative, color=(10 + index, 80, 90))
        entries = []
        for final, relative, role in zip(self.project["finals"], paths, ("cover", "angle")):
            final.update(role=role, subject="小绿")
            entries.append({"slot": final["slot"], "file": Path(relative).name, "source": final["path"],
                            "source_sha256": self.sha(final["path"]), "output_sha256": self.sha(relative),
                            "role": role, "subject": "小绿"})
        state.atomic_json(self.root / "reviews/post/staging/manifest.json", {"images": entries})
        self.save()
        mapping = package_post.actual_output_slots(self.root, self.project, paths)
        self.assertEqual([row["slot"] for row in mapping], ["cover", "back"])
        with self.assertRaises(contract_review.ContractReviewError):
            self.prepare(paths, "package")
        packet = self.prepare(paths, "package", mapping)
        self.record(packet)
        self.assertFalse(self.status(paths, "done", mapping)["blocking"])
        self.assertEqual(self.status(list(reversed(paths)), "done", list(reversed(mapping)))["sequence"]["state"], "needs_review")
        invalid = copy.deepcopy(mapping)
        invalid[0]["source"] = "back.png"
        invalid[0]["source_sha256"] = self.sha("back.png")
        self.assertTrue(self.status(paths, "package", invalid)["blocking"])
        png(self.root / "back.png", color=(70, 80, 90))
        self.assertTrue(self.status(paths, "done", mapping)["blocking"])

    def test_metadata_copies_are_not_accepted_as_actual_view_evidence(self):
        packet = self.prepare()
        copies = ["view=front yaw=0 elevation=10 pose=站立 framing=完整主体",
                  json.dumps({key: self.project["scene_plan"]["slots"][0][key]
                              for key in ("view", "yaw", "elevation", "pose", "framing")}, ensure_ascii=False)]
        for evidence in copies:
            for target in ("check", "sequence"):
                with self.subTest(evidence=evidence, target=target):
                    report = self.report(packet)
                    destination = report["results"][0]["checks"]["view"] if target == "check" else report["sequence"]["observed_views"][0]
                    destination["evidence"] = evidence
                    with self.assertRaises(contract_review.ContractReviewError):
                        self.record(packet, report)

    def test_latest_failure_cannot_be_overwritten_by_old_pass_with_production_shape(self):
        old = self.prepare()
        self.record(old)
        new = self.prepare()
        self.record(new, self.report(new, "needs_revision"))
        with self.assertRaises(contract_review.ContractReviewError):
            self.record(old)
        self.assertTrue(all(row["state"] == "blocked" for row in self.status()["paths"]))

    def test_invalid_latest_failure_does_not_fall_back_to_an_old_pass(self):
        self.record(self.prepare())
        latest = self.prepare()
        saved = self.record(latest, self.report(latest, "needs_revision"))
        Path(saved["report"]).write_text("{}", encoding="utf-8")
        result = self.status()
        self.assertTrue(result["blocking"])
        self.assertEqual([row["verdict"] for row in result["paths"]], ["invalid", "invalid"])
        self.assertEqual(result["sequence"]["verdict"], "invalid")


if __name__ == "__main__":
    unittest.main()
