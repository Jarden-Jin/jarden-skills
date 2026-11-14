"""Synthetic local protocol fixtures; never evidence of real generation/review."""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import asset_plan
import asset_workflow as assets
import generation_tasks
import project as state


class AssetWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="synthetic-assets-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = state.new_project("Synthetic protocol project", [])
        self.project["schema_version"] = 4
        assets.initialize(self.project)
        self.persist()

    def persist(self):
        state.atomic_json(self.root / "project.json", self.project)

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return relative

    def json_file(self, relative, data):
        self.write(relative, json.dumps(data, ensure_ascii=False, indent=2))
        return {"path": relative, "sha256": hashlib.sha256((self.root / relative).read_bytes()).hexdigest()}

    def image(self, relative, color=90, alpha=False, metadata=True):
        from PIL import Image, ImageDraw, PngImagePlugin
        image = Image.new("RGBA" if alpha else "RGB", (64, 64), (0, 0, 0, 0) if alpha else (color, 80, 190))
        if alpha:
            ImageDraw.Draw(image).rectangle((12, 12, 52, 52), fill=(color, 80, 190, 255))
        buffer = io.BytesIO()
        information = PngImagePlugin.PngInfo()
        if metadata:
            information.add_text("Description", "SYNTHETIC TEST FIXTURE; NOT EXTERNAL GENERATION")
        image.save(buffer, "PNG", pnginfo=information)
        return self.write(relative, buffer.getvalue())

    def choose(self, identifiers, **options):
        return assets.choose(self.root, self.project, identifiers, evidence="Synthetic user-scope protocol fixture", **options)

    def candidate(self, identifier, **options):
        result = assets.new(self.root, self.project, identifier, source_kind=options.pop("source_kind", "user-input"), **options)
        return assets._instance(self.project, result["instances"][0]["id"])

    def record_input(self, instance, color=90, alpha=False, path=None):
        path = path or "inputs/" + instance["id"] + ".png"
        if not (self.root / path).exists():
            self.image(path, color=color, alpha=alpha)
        self.project["inputs"].append({"path": path, "kind": "flat-design", "origin": "synthetic-test-fixture"})
        return assets.record(self.root, self.project, {"instance_id": instance["id"], "files": [path],
            "source": {"kind": "user-input", "producer": "synthetic-fixture-producer", "evidence": "Synthetic imported input, not real user artwork"}})

    def report(self, instance, verdict="pass", **updates):
        report = assets.review_template(self.project, instance)
        report.update(verdict=verdict, reviewer={"id": "synthetic-independent-reviewer", "independent": True, "viewed_files": True})
        report["checks"] = {criterion: {"verdict": verdict, "evidence": "Synthetic protocol evidence only, not actual visual inspection"}
                            for criterion in report["checks"]}
        report["physical_applicability"]["reason"] = "Synthetic flat/document fixture declares no real-world product relationships"
        report.update(updates)
        relative = "reviews/assets/" + instance["id"] + "-" + verdict + ".json"
        return self.json_file(relative, report)

    def approve(self, instance, **options):
        return assets.approve(self.root, self.project, instance["id"], evidence="Synthetic user adoption; not an actual review",
                              reviews={"design": self.report(instance)}, **options)

    def approved_input(self, identifier, **options):
        instance = self.candidate(identifier, **options)
        self.record_input(instance, color=int(identifier) + 50, alpha=identifier == "19")
        self.approve(instance)
        return instance

    def payload(self, action, **fields):
        return {"schema_version": 1, "request_id": "synthetic-request-01", "project_id": self.project["project_id"],
                "expected_revision": self.project["revision"], "action": action, **fields}

    def test_catalog_has_21_stable_ids_four_groups_and_truthful_boundaries(self):
        catalog = asset_plan.catalog()
        self.assertEqual(len(catalog["groups"]), 4)
        self.assertEqual([item["id"] for item in catalog["assets"]], [f"{number:02d}" for number in range(1, 22)])
        self.assertEqual(asset_plan.definition("06")["support"], "unsupported")
        for identifier in ("13", "14", "15"):
            item = asset_plan.definition(identifier)
            self.assertEqual(item["support"], "design-reference")
            self.assertFalse(item["manufacturing_ready"])

    def test_choice_is_required_before_new_and_preserves_known_inputs(self):
        self.assertEqual(assets.next_action(self.root, self.project)["action"], "choose-assets")
        with self.assertRaisesRegex(assets.AssetWorkflowError, "choose_assets_first"):
            self.candidate("02")
        self.assertEqual(self.project["rounds"], [])

    def test_pure_flat_plan_has_no_material_cutout_scene_or_unrequested_post(self):
        self.choose(["02", "04"])
        plan = assets.view(self.root, self.project)["plan"]
        self.assertEqual([row["asset_id"] for row in plan["execution"]], ["02", "04"])
        self.assertEqual(plan["execution"][1]["missing_dependencies"], ["02"])
        self.assertNotIn("material", json.dumps(plan))
        self.assertNotIn("scene", json.dumps(plan))
        self.assertNotIn("post", self.project)

    def test_document_independent_markdown_closure_without_pillow_or_product_questions(self):
        self.choose(["08"])
        instance = self.candidate("08", source_kind="local-authoring", quality="final")
        relative = self.write("assets-documents/setting.md", "# Synthetic character\n\nUnknown manufacturing details remain unknown.\n")
        original_import = __import__

        def no_pillow(name, *arguments, **keywords):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("Synthetic missing Pillow environment")
            return original_import(name, *arguments, **keywords)

        with mock.patch("builtins.__import__", side_effect=no_pillow):
            result = assets.record(self.root, self.project, {"instance_id": instance["id"], "files": [relative],
                "source": {"kind": "local-authoring", "producer": "synthetic-author", "evidence": "Synthetic document authoring fixture"}})
            self.assertFalse(result["eligibility"]["approved"])
            self.approve(instance)
            result = assets.export(self.root, self.project)
        self.assertEqual(result["manifest"], "assets-output/manifest.json")
        manifest = json.loads((self.root / result["manifest"]).read_text())
        self.assertEqual(manifest["selected"], ["08"])
        self.assertEqual(manifest["artifacts"][0]["kind"], "document")
        self.assertEqual(assets.next_action(self.root, self.project)["action"], "done")
        self.assertFalse((self.root / "post").exists())
        self.assertEqual(self.project["answers"], {})
        self.assertEqual(self.project["asset_workflow"]["instances"][0]["upstream"], [])

    def test_flat_02_and_04_complete_and_export_exact_approved_bytes(self):
        self.choose(["02", "04"])
        line = self.approved_input("02", quality="final")
        flat = self.approved_input("04", quality="final")
        self.assertEqual(flat["upstream"][0]["instance_id"], line["id"])
        self.assertEqual(assets.next_action(self.root, self.project)["action"], "export-assets")
        result = assets.export(self.root, self.project)
        for artifact in result["artifacts"]:
            for file in artifact["files"]:
                self.assertEqual((self.root / file["path"]).read_bytes(), (self.root / file["source"]).read_bytes())
        self.assertTrue(assets.view(self.root, self.project)["completion"]["complete"])
        self.assertFalse(line["requires_physics"])
        self.assertFalse(flat["requires_physics"])

    def test_exploration_defaults_two_and_explicit_count_overrides(self):
        self.choose(["01", "02"])
        result = assets.new(self.root, self.project, "01", prompt="Synthetic protocol prompt, not actual generation")
        self.assertEqual(len(result["instances"]), 2)
        self.assertFalse(result["should_call"])
        result = assets.new(self.root, self.project, "02", count=1, prompt="Synthetic protocol prompt, not actual generation")
        self.assertEqual(len(result["instances"]), 1)
        self.assertFalse((self.root / "tasks").exists())

    def test_A_qualified_B_missing_or_failed_does_not_block_A(self):
        self.choose(["01"])
        result = assets.new(self.root, self.project, "01", count=2, source_kind="user-input")
        first, second = [assets._instance(self.project, item["id"]) for item in result["instances"]]
        self.record_input(first, color=30)
        self.record_input(second, color=200)
        assets.record(self.root, self.project, {"instance_id": second["id"], "reviews": {"design": self.report(second, "needs_revision")}})
        self.approve(first)
        self.assertTrue(assets.eligibility(self.root, self.project, first)["approved"])
        self.assertFalse(assets.eligibility(self.root, self.project, second)["approvable"])
        self.assertEqual(second["status"], "needs_revision")
        self.assertEqual(assets.next_action(self.root, self.project)["action"], "export-assets")

    def test_A_qualified_B_unrecorded_can_be_adopted(self):
        self.choose(["01"])
        result = assets.new(self.root, self.project, "01", count=2, source_kind="user-input")
        first, second = [assets._instance(self.project, item["id"]) for item in result["instances"]]
        self.record_input(first)
        self.approve(first)
        self.assertEqual(second["status"], "planned")
        self.assertEqual(second["disposition"], "not-selected")
        self.assertEqual(assets.next_action(self.root, self.project)["action"], "export-assets")

    def test_unsupported06_blocks_without_generating_or_preparing_its_prerequisites(self):
        self.choose(["06"])
        view = assets.view(self.root, self.project)
        self.assertEqual([row["asset_id"] for row in view["plan"]["execution"]], ["06"])
        self.assertEqual(view["next"]["action"], "blocked-assets")
        with self.assertRaisesRegex(assets.AssetWorkflowError, "unsupported_asset"):
            self.candidate("06")
        with self.assertRaisesRegex(assets.AssetWorkflowError, "assets_incomplete"):
            assets.export(self.root, self.project)
        self.assertFalse((self.root / "asset-plans").exists())

    def test_unknown_and_requesting_recover_without_new_intents_or_claims(self):
        self.choose(["01"])
        first = assets.new(self.root, self.project, "01", count=1, prompt="Synthetic protocol generation prompt", authorization={"approved": True, "evidence": "Synthetic authorization fixture"})
        instance = assets._instance(self.project, first["instances"][0]["id"])
        plan = json.loads((self.root / instance["generation_plan"]).read_text())
        claim = generation_tasks.begin(self.root, plan, tool_request_id="synthetic-no-real-call")
        self.assertTrue(claim["should_call"])
        for task_state in ("requesting", "unknown"):
            if task_state == "unknown":
                generation_tasks.unknown(self.root, instance["generation_task"], "Synthetic interrupted protocol fixture", claim_id=claim["claim_id"])
            result = assets.new(self.root, self.project, "01", prompt="A changed synthetic prompt", intent_id="do-not-resend", evidence="Synthetic request")
            self.assertFalse(result["should_call"])
            self.assertEqual(result["action"], "reconcile-generation")
            self.assertEqual(len(self.project["asset_workflow"]["instances"]), 1)
            self.assertEqual(assets.next_action(self.root, self.project)["action"], "reconcile-generation")

    def test_generated_fixture_requires_completed_ledger_and_does_not_claim_external_truth(self):
        self.choose(["01"])
        planned = assets.new(self.root, self.project, "01", count=1, prompt="Synthetic ledger fixture", authorization={"approved": True, "evidence": "Synthetic authorized fixture"})
        instance = assets._instance(self.project, planned["instances"][0]["id"])
        image = self.image("assets-images/synthetic-output.png")
        payload = {"instance_id": instance["id"], "files": [image],
                   "source": {"kind": "builtin-generation", "producer": "synthetic-tool-driver", "evidence": "Synthetic tool protocol only; no external tool called"}}
        with self.assertRaisesRegex(assets.AssetWorkflowError, "provenance_blocked"):
            assets.record(self.root, self.project, payload)
        plan = json.loads((self.root / instance["generation_plan"]).read_text())
        claim = generation_tasks.begin(self.root, plan, tool_request_id="synthetic-local-only")
        generation_tasks.complete(self.root, instance["generation_task"], image, claim_id=claim["claim_id"])
        result = assets.record(self.root, self.project, payload)
        receipt = result["instance"]["source"]["receipts"][0]
        self.assertFalse(receipt["external_generation_verified"])
        self.assertFalse(receipt["visual_quality_verified"])
        self.assertFalse(result["eligibility"]["approved"])

    def test_formal_approval_needs_hashed_independent_file_report(self):
        self.choose(["02"])
        instance = self.candidate("02")
        self.record_input(instance)
        for reviews in ({}, {"design": {"approved": True}}, {"approved": True}):
            with self.subTest(reviews=reviews), self.assertRaises(assets.AssetWorkflowError):
                assets.approve(self.root, self.project, instance["id"], evidence="Synthetic choice", reviews=reviews)
        descriptor = self.report(instance, reviewer={"id": "synthetic-fixture-producer", "independent": True, "viewed_files": True})
        with self.assertRaisesRegex(assets.AssetWorkflowError, "independent_review_required"):
            assets.approve(self.root, self.project, instance["id"], evidence="Synthetic choice", reviews={"design": descriptor})

    def test_report_file_changed_or_scope_mismatch_cannot_approve(self):
        self.choose(["02"])
        instance = self.candidate("02")
        self.record_input(instance)
        descriptor = self.report(instance)
        self.write(descriptor["path"], "{}")
        with self.assertRaisesRegex(assets.AssetWorkflowError, "file_changed"):
            assets.approve(self.root, self.project, instance["id"], evidence="Synthetic choice", reviews={"design": descriptor})
        descriptor = self.report(instance, upstream_signature="0" * 64)
        with self.assertRaisesRegex(assets.AssetWorkflowError, "review_scope_mismatch"):
            assets.approve(self.root, self.project, instance["id"], evidence="Synthetic choice", reviews={"design": descriptor})

    def test_prefer_direction_is_not_formal_approval_or_generation_authorization(self):
        self.choose(["02"])
        instance = self.candidate("02")
        self.record_input(instance)
        result = assets.submit(self.root, self.project, self.payload("asset-intent", instance_id=instance["id"], intent="prefer-direction", note="Synthetic user preference only"))
        self.assertFalse(result["intent"]["formal_approval"])
        self.assertFalse(result["intent"]["generation_authorized"])
        self.assertFalse(assets.eligibility(self.root, self.project, instance)["approved"])
        with self.assertRaises(assets.AssetWorkflowError):
            assets.export(self.root, self.project)

    def test_stale_page_submission_and_unknown_fields_do_not_mutate(self):
        snapshot = copy.deepcopy(self.project)
        for fields in ({"expected_revision": 100}, {"approved": True}, {"project_id": "wrong"}):
            payload = self.payload("choose-assets", asset_ids=["08"], evidence="Synthetic user scope", **fields)
            with self.subTest(fields=fields), self.assertRaises(assets.AssetWorkflowError):
                assets.submit(self.root, self.project, payload)
            self.assertEqual(self.project, snapshot)

    def test_exact_UI_payloads_choose_approve_and_requests_use_registered_report(self):
        chosen = assets.submit(self.root, self.project, self.payload("choose-assets", asset_ids=["02"], items=[]))
        self.assertEqual(chosen["selected"], ["02"])
        instance = self.candidate("02")
        self.record_input(instance)
        assets.record(self.root, self.project, {"instance_id": instance["id"], "reviews": {"design": self.report(instance)}})
        selected = {"asset_id": "02", "instance_id": instance["id"], "round_id": instance["round_id"],
                    "items": [{"id": instance["id"], "sha256": instance["files"][0]["sha256"]}]}
        result = assets.submit(self.root, self.project, self.payload("approve-asset", **selected))
        self.assertEqual(result["status"], "approved")
        result = assets.submit(self.root, self.project, self.payload("asset-intent", intent="request-slots", **selected))
        self.assertFalse(result["intent"]["generation_authorized"])
        self.assertEqual(assets.view(None, self.project)["submit_actions"], ["choose-assets", "approve-asset", "asset-intent"])

    def test_UI_approval_without_report_or_changed_snapshot_is_rejected(self):
        self.choose(["02"])
        instance = self.candidate("02")
        self.record_input(instance)
        selected = {"asset_id": "02", "instance_id": instance["id"], "items": [{"id": instance["id"], "sha256": instance["files"][0]["sha256"]}]}
        with self.assertRaisesRegex(assets.AssetWorkflowError, "design_review_required"):
            assets.submit(self.root, self.project, self.payload("approve-asset", **selected))
        selected["items"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(assets.AssetWorkflowError, "changed_asset"):
            assets.submit(self.root, self.project, self.payload("approve-asset", **selected))

    def test_scene_retyping_cannot_erase_product_gate_for_same_bytes(self):
        self.choose(["01", "02"])
        scene = self.candidate("01", content_kind="scene")
        self.record_input(scene)
        line = self.candidate("02")
        copied = self.write("inputs/scene-retyped.png", (self.root / scene["files"][0]["path"]).read_bytes())
        self.record_input(line, path=copied)
        with self.assertRaisesRegex(assets.AssetWorkflowError, "g1_binding_required"):
            self.approve(line)

    def test_repeated_original_intent_recovers_original_round_not_new_generation(self):
        self.choose(["01"])
        first = assets.new(self.root, self.project, "01", prompt="Synthetic first intent", intent_id="original", count=1)
        assets.new(self.root, self.project, "01", prompt="Synthetic second intent", intent_id="another", count=1, evidence="Synthetic user asked for a new direction")
        recovered = assets.new(self.root, self.project, "01", prompt="Must never create new work", intent_id="original", count=1)
        self.assertTrue(recovered["unchanged"])
        self.assertFalse(recovered["should_call"])
        self.assertEqual(first["round"]["id"], recovered["round"]["id"])
        self.assertEqual(len(self.project["asset_workflow"]["rounds"]), 2)

    def test_manifest_tamper_and_exported_bytes_tamper_remove_completion(self):
        self.choose(["02"])
        self.approved_input("02")
        assets.export(self.root, self.project)
        manifest = json.loads((self.root / assets.MANIFEST).read_text())
        manifest["claim_boundary"] = "Synthetic tampering"
        self.json_file(assets.MANIFEST, manifest)
        self.assertFalse(assets.view(self.root, self.project)["completion"]["complete"])
        assets.export(self.root, self.project)
        manifest = json.loads((self.root / assets.MANIFEST).read_text())
        self.write(manifest["artifacts"][0]["files"][0]["path"], b"Changed exported fixture bytes")
        self.assertFalse(assets.view(self.root, self.project)["completion"]["complete"])

    def test_real_G1_G2_status_adapters_accept_only_current_distinct_reviewers(self):
        import contract_fixtures
        import contract_review
        import physics_review

        self.choose(["01"])
        instance = self.candidate("01", content_kind="scene")
        relative = self.image("inputs/synthetic-clean-scene.png", metadata=False)
        self.record_input(instance, path=relative)
        self.project["answers"]["product_form"] = "plush"
        self.project["product_contract"] = {
            "schema_version": 1, "enabled": True,
            "subjects": [{"id": "fixture", "identity": {"eyes": "Synthetic fixture eye positions"}}],
            "specs": [{"id": "fixture-spec", "subject_id": "fixture", "scale": {"status": "design-target", "height": 12, "unit": "cm"}}],
        }
        self.project["scene_plan"] = {"schema_version": 1, "slots": [
            {"id": "fixture-slot", "subject_id": "fixture", "spec_id": "fixture-spec", "view": "front", "role": "cover", "path": relative}
        ], "required_slots": ["fixture-slot"]}
        mappings = [{"slot_id": "fixture-slot", "path": relative, "source": relative}]
        instance["review_slots"] = mappings
        self.persist()
        contract_fixtures.review(self.root, [relative], mappings=mappings)
        self.project = state.load_project(self.root)
        instance = assets._instance(self.project, instance["id"])
        g1 = contract_review.status(self.root, self.project, [relative], ordered_slots=mappings)
        self.assertTrue(g1["applicable"])
        self.assertFalse(g1["blocking"])
        self.assertEqual(g1["verdict"], "pass")
        with self.assertRaisesRegex(assets.AssetWorkflowError, "g2_blocked"):
            self.approve(instance)
        packet = physics_review.prepare(self.root, self.project, [relative], "synthetic-fixture-producer")
        request = state.read_json(Path(packet["packet"]) / "request.json")
        report = {"schema_version": 1, "request_id": packet["request_id"],
                  "reviewer": {"id": "synthetic-g2-reviewer", "zero_context": True, "viewed_images": True},
                  "results": [{"id": image["id"], "sha256": image["sha256"], "verdict": "pass",
                               "summary": "Synthetic protocol fixture only, not actual physical review",
                               "checks": {criterion: {"verdict": "pass", "evidence": "Synthetic protocol evidence only"} for criterion in physics_review.CHECKS},
                               "findings": []} for image in request["images"]]}
        incoming = self.json_file("synthetic-physics-report.json", report)
        physics_review.record(self.root, self.project, packet["request_id"], self.root / incoming["path"])
        self.approve(instance)
        self.assertTrue(assets.eligibility(self.root, self.project, instance)["approved"])
        self.project["product_contract"]["specs"][0]["scale"]["height"] = 25
        self.assertFalse(assets.eligibility(self.root, self.project, instance)["approved"])

    def test_append_keeps_unaffected_bytes_approvals_and_old_known_fields(self):
        self.project["unknown_future"] = {"preserve": True}
        self.choose(["02", "04"])
        line = self.approved_input("02")
        flat = self.approved_input("04")
        before = copy.deepcopy(line)
        self.choose(["05"], append=True, requirements={"05": {"palette": "synthetic-red"}})
        self.assertEqual(line, before)
        self.assertTrue(assets.eligibility(self.root, self.project, line)["approved"])
        self.assertTrue(assets.eligibility(self.root, self.project, flat)["approved"])
        self.assertEqual(self.project["unknown_future"], {"preserve": True})

    def test_requirement_and_upstream_changes_invalidate_only_actual_dependents(self):
        self.choose(["02", "04", "05"])
        line = self.approved_input("02")
        flat = self.approved_input("04")
        palette = self.approved_input("05")
        self.choose(["02", "04", "05"], requirements={"05": {"palette": "changed"}})
        self.assertTrue(assets.eligibility(self.root, self.project, line)["approved"])
        self.assertTrue(assets.eligibility(self.root, self.project, flat)["approved"])
        self.assertFalse(assets.eligibility(self.root, self.project, palette)["approved"])
        self.write(line["files"][0]["path"], "modified synthetic input")
        self.assertFalse(assets.eligibility(self.root, self.project, flat)["approved"])

    def test_invalid_image_and_opaque_sticker_rejected_not_magic_only(self):
        self.choose(["02", "04", "19"])
        instance = self.candidate("02")
        bad = self.write("inputs/bad.png", b"\x89PNG\r\n\x1a\nnot actually a PNG")
        self.project["inputs"].append({"path": bad, "kind": "flat-design"})
        with self.assertRaisesRegex(assets.AssetWorkflowError, "image_decode_failed"):
            assets.record(self.root, self.project, {"instance_id": instance["id"], "files": [bad]})
        self.record_input(instance)
        self.approve(instance)
        self.approved_input("04")
        sticker = self.candidate("19")
        with self.assertRaisesRegex(assets.AssetWorkflowError, "alpha_check_failed"):
            self.record_input(sticker, color=230)

    def test_true_alpha_sticker_passes_applicable_program_checks(self):
        self.choose(["19"])
        self.approved_input("02")
        self.approved_input("04")
        sticker = self.approved_input("19")
        self.assertTrue(sticker["files"][0]["inspection"]["alpha"]["ok"])
        self.assertTrue(assets.eligibility(self.root, self.project, sticker)["approved"])

    def test_product_and_scene_cannot_waive_current_g1_g2(self):
        self.choose(["01"])
        instance = self.candidate("01", content_kind="scene")
        self.record_input(instance)
        with self.assertRaisesRegex(assets.AssetWorkflowError, "g1_binding_required"):
            self.approve(instance)
        descriptor = self.report(instance, physical_applicability={"required": False, "content_kind": "flat", "reason": "Synthetic attempt to waive real-world relationships"})
        with self.assertRaisesRegex(assets.AssetWorkflowError, "review_contradiction"):
            assets.approve(self.root, self.project, instance["id"], evidence="Synthetic choice", reviews={"design": descriptor})

    def test_deferred_unsupported_request_remains_unfinished(self):
        self.choose(["06"])
        result = assets.submit(self.root, self.project, self.payload("asset-intent", asset_id="06", intent="defer-unsupported"))
        self.assertFalse(result["intent"]["formal_approval"])
        self.assertEqual(self.project["asset_workflow"]["selected"], ["06"])
        self.assertFalse(assets.view(self.root, self.project)["completion"]["complete"])

    def test_user_input_is_not_accepted_as_derived_or_generated_origin(self):
        self.choose(["02"])
        instance = self.candidate("02")
        path = self.image("inputs/origin.png")
        with self.assertRaisesRegex(assets.AssetWorkflowError, "unregistered_input"):
            assets.record(self.root, self.project, {"instance_id": instance["id"], "files": [path],
                "source": {"kind": "user-input", "producer": "synthetic-importer", "evidence": "Synthetic origin fixture"}})
        self.project["inputs"].append({"path": path, "kind": "flat-design"})
        with self.assertRaisesRegex(assets.AssetWorkflowError, "source_kind_mismatch"):
            assets.record(self.root, self.project, {"instance_id": instance["id"], "files": [path],
                "source": {"kind": "builtin-generation", "producer": "synthetic-tool", "evidence": "Synthetic wrong origin fixture"}})

    def test_local_derived_asset_keeps_actual_sources_without_external_claim(self):
        self.choose(["02"])
        instance = self.candidate("02", source_kind="local-derive", count=1)
        source = self.image("inputs/derive-source.png", color=50)
        output = self.image("assets-images/derived.png", color=70)
        result = assets.record(self.root, self.project, {"instance_id": instance["id"], "files": [output],
            "source": {"kind": "local-derive", "producer": "synthetic-local-operation", "sources": [source],
                       "evidence": "Synthetic derived-file protocol; not external generation"}})
        provenance = result["instance"]["source"]["receipts"][0]
        self.assertEqual(provenance["method"], "derive")
        self.assertFalse(provenance["external_generation_verified"])
        self.approve(instance)
        self.write(source, "Changed synthetic derivation source")
        self.assertFalse(assets.eligibility(self.root, self.project, instance)["approved"])

    def test_final_design_reference13_is_not_forced_physical_or_manufacturing_ready(self):
        self.choose(["13"])
        for identifier in ("02", "04", "07"):
            self.approved_input(identifier)
        instance = self.approved_input("13", quality="final")
        self.assertFalse(instance["requires_physics"])
        result = assets.export(self.root, self.project)
        self.assertFalse(result["artifacts"][0]["manufacturing_ready"])
        self.assertEqual(result["artifacts"][0]["support"], "design-reference")

    def test_asset_library_exports_each_reusable_file_in_one_bundle(self):
        self.choose(["11"])
        self.approved_input("02")
        self.approved_input("04")
        library = self.candidate("11")
        first = self.image("inputs/library-first.png", color=10)
        second = self.image("inputs/library-second.png", color=220)
        self.project["inputs"].extend({"path": path, "kind": "flat-design"} for path in (first, second))
        assets.record(self.root, self.project, {"instance_id": library["id"], "files": [first, second],
            "source": {"kind": "user-input", "producer": "synthetic-library-author", "evidence": "Synthetic reusable library files"}})
        self.approve(library)
        result = assets.export(self.root, self.project)
        self.assertEqual(result["artifacts"][0]["kind"], "bundle")
        self.assertEqual(len(result["artifacts"][0]["files"]), 2)

    def test_scope_review_independently_detects_product_even_if_declared_flat(self):
        self.choose(["01"])
        instance = self.candidate("01")
        self.record_input(instance)
        descriptor = self.report(instance, physical_applicability={"required": True, "content_kind": "product", "reason": "Synthetic independent finding of physical relationships"})
        with self.assertRaisesRegex(assets.AssetWorkflowError, "g1_binding_required"):
            assets.approve(self.root, self.project, instance["id"], evidence="Synthetic choice", reviews={"design": descriptor})

    def test_failed_evidence_cannot_be_erased_by_moving_or_changing_asset_type(self):
        self.choose(["01", "02"])
        failed = self.candidate("01")
        self.record_input(failed)
        assets.record(self.root, self.project, {"instance_id": failed["id"], "reviews": {"design": self.report(failed, "needs_revision")}})
        other = self.candidate("02")
        copied = self.write("inputs/renamed.png", (self.root / failed["files"][0]["path"]).read_bytes())
        self.record_input(other, path=copied)
        with self.assertRaisesRegex(assets.AssetWorkflowError, "prior_review_failed"):
            self.approve(other)
        self.assertEqual(failed["status"], "needs_revision")

    def test_state_only_view_reads_no_path_and_never_claims_verified_completion(self):
        self.choose(["02"])
        self.approved_input("02")
        with mock.patch.object(assets, "_file", side_effect=AssertionError("Must not read real files")):
            view = assets.view(None, self.project)
        self.assertFalse(view["completion"]["complete"])
        self.assertEqual(view["completion"]["verification"], "not_assessed")
        self.assertEqual(view["next"]["action"], "verify-assets")

    def test_paths_traversal_and_symlinks_blocked(self):
        self.choose(["02"])
        instance = self.candidate("02")
        target = self.image("inputs/actual.png")
        (self.root / "inputs/linked.png").symlink_to(self.root / target)
        for path in ("../outside.png", str(self.root / target), "inputs/linked.png"):
            with self.subTest(path=path), self.assertRaises(assets.AssetWorkflowError):
                assets.record(self.root, self.project, {"instance_id": instance["id"], "files": [path]})

    def test_saved_plan_signature_guard_rejects_relevant_change_not_unrelated(self):
        self.choose(["01", "08"])
        planned = assets.new(self.root, self.project, "01", prompt="Synthetic safety fixture", count=1)
        instance = assets._instance(self.project, planned["instances"][0]["id"])
        plan = json.loads((self.root / instance["generation_plan"]).read_text())
        self.assertTrue(assets.validate_generation_plan(self.root, self.project, plan)["valid"])
        self.choose(["01", "08"], requirements={"08": {"text": "unrelated"}})
        self.assertTrue(assets.validate_generation_plan(self.root, self.project, plan)["valid"])
        self.choose(["01", "08"], requirements={"01": {"identity": "changed"}})
        with self.assertRaisesRegex(assets.AssetWorkflowError, "stale_asset_plan"):
            assets.validate_generation_plan(self.root, self.project, plan)

    def test_presets_are_visible_asset_combinations_not_hidden_legacy_transition(self):
        for preset in assets.PRESETS:
            self.choose([], preset=preset)
            self.assertEqual(self.project["asset_workflow"]["selected"], assets.PRESETS[preset])
            self.assertEqual(self.project["rounds"], [])
            self.assertNotIn("note_type", self.project["answers"])

    def test_explicit_choose_can_enable_migrated_schema4_preserving_legacy_selections(self):
        del self.project["asset_workflow"]
        self.project["selections"] = {"concept": {"legacy": {"image": "original.png", "unknown": 1}}}
        previous = copy.deepcopy(self.project["selections"])
        self.choose(["08"])
        self.assertTrue(assets.is_enabled(self.project))
        self.assertEqual(previous, self.project["selections"])

    def test_parser_exact_assets_project_action_and_options(self):
        parser = argparse.ArgumentParser()
        assets.add_parser(parser.add_subparsers(dest="command", required=True))
        args = parser.parse_args(["assets", str(self.root), "choose", "02", "04", "--evidence", "Synthetic choice"])
        self.assertEqual(args.command, "assets")
        self.assertEqual(args.asset_ids, ["02", "04"])
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(args.func(args), 0)
        self.assertEqual(json.loads(output.getvalue())["selected"], ["02", "04"])
        self.assertEqual(state.load_project(self.root)["asset_workflow"]["selected"], ["02", "04"])

    def test_catalog_standalone_cli_needs_no_project_and_no_schema_mutation(self):
        output = subprocess.run([sys.executable, str(SCRIPTS / "asset_workflow.py"), "assets", str(self.root / "missing"), "catalog"],
                                capture_output=True, text=True, check=True)
        self.assertEqual(len(json.loads(output.stdout)["assets"]), 21)
        self.assertFalse((self.root / "missing").exists())

    def test_project_cli_document_asset_end_to_end(self):
        root = self.root / "cli-document"

        def command(*arguments):
            output = subprocess.run([sys.executable, str(SCRIPTS / "project.py"), *arguments],
                                    capture_output=True, text=True)
            self.assertEqual(output.returncode, 0, output.stdout + output.stderr)
            result = json.loads(output.stdout)
            self.assertTrue(result.get("ok"), result)
            if arguments[0] != "show":
                current = state.load_project(root)
                manifest = state.read_json(root / "deliveries" / current["presentations"]["latest_delivery_id"] / "manifest.json")
                self.assertEqual(manifest["project_revision"], current["revision"])
                self.assertEqual(result["workbench"]["status"], "synced")
            return result

        result = command("init", str(root), "--kind", "text", "--workflow", "assets")
        self.assertEqual(result["next"]["action"], "choose-assets")
        command("assets", str(root), "choose", "08", "--evidence", "Synthetic CLI scope fixture")
        result = command("assets", str(root), "new", "--asset", "08", "--quality", "final")
        instance = result["instances"][0]
        (root / "authored").mkdir()
        (root / "authored/setting.md").write_text("# Synthetic standalone document\n\nUnknowns remain unknown.\n", encoding="utf-8")
        payload = {"instance_id": instance["id"], "files": ["authored/setting.md"],
                   "source": {"kind": "local-authoring", "producer": "synthetic-cli-author", "evidence": "Synthetic authored Markdown, not generated image"}}
        state.atomic_json(root / "record.json", payload)
        result = command("assets", str(root), "record", "--file", str(root / "record.json"))
        report = result["review_template"]
        report["reviewer"] = {"id": "synthetic-cli-reviewer", "independent": True, "viewed_files": True}
        report["verdict"] = "pass"
        report["checks"] = {criterion: {"verdict": "pass", "evidence": "Synthetic local CLI review protocol, not real independent visual inspection"} for criterion in report["checks"]}
        report["physical_applicability"]["reason"] = "Synthetic text-only document; no real product relationship or size proof"
        state.atomic_json(root / "review.json", report)
        descriptor = {"path": "review.json", "sha256": hashlib.sha256((root / "review.json").read_bytes()).hexdigest()}
        state.atomic_json(root / "adopt.json", {"instance_id": instance["id"], "evidence": "Synthetic CLI adoption fixture", "reviews": {"design": descriptor}})
        command("assets", str(root), "approve", "--file", str(root / "adopt.json"))
        result = command("assets", str(root), "export")
        self.assertEqual(result["manifest"], assets.MANIFEST)
        self.assertEqual(command("show", str(root))["next"]["action"], "done")
        self.assertFalse((root / "post").exists())


if __name__ == "__main__":
    unittest.main()
