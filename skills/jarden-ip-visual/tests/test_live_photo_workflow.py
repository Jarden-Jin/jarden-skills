"""Synthetic protocol fixtures only; no paid calls or real Photos observations."""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import plistlib
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import asset_plan
import asset_workflow
import live_photo_workflow as live
import project as state


def png_bytes(red=100):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes([0, red, 80, 170]))) + chunk(b"IEND", b""))


class LivePhotoWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="synthetic-live-sidecar-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = state.new_project("Synthetic optional Live project", [], workflow="assets")
        self.write("inputs/source.png", png_bytes())
        self.persist()
        self.native = SimpleNamespace(package=mock.Mock(side_effect=self.fake_package),
                                      verify=mock.Mock(return_value={"passed_local_validation": True,
                                                                     "photo_library_modified": False}))
        native_patch = mock.patch.dict(sys.modules, {"live_photo_native": self.native})
        native_patch.start()
        self.addCleanup(native_patch.stop)

    def persist(self):
        state.atomic_json(self.root / "project.json", self.project)

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        return relative

    def descriptor(self, relative, content=None):
        if content is not None:
            self.write(relative, content)
        return {"path": relative, "sha256": hashlib.sha256((self.root / relative).read_bytes()).hexdigest()}

    def json_file(self, relative, data):
        return self.descriptor(relative, json.dumps(data, ensure_ascii=False))

    def plan(self, **changes):
        options = {"source": "inputs/source.png", "prompt": "Keep the synthetic subject; gently move only its fur.",
                   "model": "synthetic-manual-model", "evidence": "Synthetic source adoption quote; no service authorization."}
        options.update(changes)
        result = live.plan(self.root, self.project, **options)
        return live._job(self.project, result["job_id"])

    def record_payload(self, job, verdict="pass"):
        self.write("external-output/result.mp4", b"SYNTHETIC NONEMPTY VIDEO FIXTURE; NOT REAL GENERATION")
        return {"job_id": job["id"], "video": "external-output/result.mp4",
                "provider_receipt": self.json_file("external-output/provider.json", {"provider": "synthetic-provider", "downloaded": True}),
                "motion_review": {"verdict": verdict, "reviewer": "synthetic-reviewer",
                                  "evidence": "Synthetic protocol statement, not a real visual review."},
                "quality": "draft", "watermark": "preserved"}

    def recorded_job(self, verdict="pass"):
        job = self.plan()
        live.record(self.root, self.project, self.record_payload(job, verdict))
        return job

    def reconciliation(self, job, status="failed", **changes):
        receipt = self.json_file("external-output/" + status + ".json", {
            "status": status, "reason": "Synthetic membership denial; not an actual provider response"})
        payload = {"job_id": job["id"], "status": status, "provider_receipt": receipt,
                   "evidence": "Synthetic official rejection fixture; no video returned."}
        payload.update(changes)
        return payload

    def fake_package(self, video_path, output_path, *, name="LIVE", duration=3.0):
        self.assertTrue(video_path.is_relative_to(self.root))
        self.assertFalse(output_path.exists())
        output_path.mkdir(parents=True)
        photo = output_path / (name + ".jpg")
        video = output_path / (name + ".mov")
        photo.write_bytes(b"SYNTHETIC JPEG PROTOCOL FIXTURE")
        video.write_bytes(b"SYNTHETIC MOV PROTOCOL FIXTURE")
        bundle = output_path / (name + ".pvt")
        bundle.mkdir()
        for source in (photo, video):
            (bundle / source.name).write_bytes(source.read_bytes())
        (bundle / "metadata.plist").write_bytes(plistlib.dumps({"PFVideoComplementMetadataVersionKey": 1}))
        (output_path / "verification.json").write_text(json.dumps({"passed_local_validation": True,
                                                                  "evidence": "Synthetic mock; not real Apple validation"}))
        return {"photo": str(photo), "video": str(video), "pvt_package": str(bundle),
                "passed_local_validation": True, "photo_library_modified": False}

    def packaged_job(self):
        job = self.recorded_job()
        self.assertTrue(live.package(self.root, self.project, job["id"])["ok"])
        return job

    def attestation(self, job, target="macos-photos", **changes):
        evidence = self.descriptor("observations/" + target + ".txt", "Synthetic observation fixture; not actual GUI evidence")
        payload = {"job_id": job["id"], "target": target, "evidence": "Synthetic Live badge and playback fixture.",
                   "observer": "synthetic-host-observer", "live_badge": True, "playback_confirmed": True,
                   "evidence_files": [evidence]}
        payload.update(changes)
        return payload

    def cli(self, arguments, *, wrapper=False):
        parser = argparse.ArgumentParser()
        live.add_parser(parser.add_subparsers(dest="command", required=True))
        args = parser.parse_args(["live", str(self.root), *arguments])
        output = io.StringIO()
        with redirect_stdout(output):
            code = state.execute_command(args) if wrapper else args.func(args)
        return code, json.loads(output.getvalue())

    def test_view_disabled_is_pure_and_does_not_enable_sidecar(self):
        before = copy.deepcopy(self.project)
        files = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        view = live.view(self.root, self.project)
        self.assertFalse(view["enabled"])
        self.assertIsNone(view["current"])
        self.assertIsNone(view["latest"])
        self.assertEqual(view["jobs"], [])
        self.assertEqual(self.project, before)
        self.assertEqual(files, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_plan_binds_quote_to_scope_and_hash_without_authorizing_service(self):
        before = (self.root / "inputs/source.png").read_bytes()
        job = self.plan()
        self.assertEqual(job["status"], "planned")
        self.assertFalse(job["generation_authorized"])
        confirmation = job["confirmed_source_evidence"]
        self.assertEqual(confirmation["scope"], "source-only")
        self.assertEqual(confirmation["source"], job["source"])
        self.assertIn("Synthetic source adoption quote", confirmation["evidence"])
        self.assertEqual(job["source_digest"], hashlib.sha256(before).hexdigest())
        self.assertEqual((self.root / job["input"]["path"]).read_bytes(), before)
        self.assertEqual((self.root / "inputs/source.png").read_bytes(), before)
        self.assertIn("live-photo/", job["input"]["path"])
        self.assertEqual(self.project["live_photo"]["schema_version"], 1)
        self.native.package.assert_not_called()

    def test_plan_requires_model_prompt_and_exact_source_confirmation_evidence(self):
        for field in ("model", "prompt", "evidence"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.plan(**{field: " "})
        self.assertNotIn("live_photo", self.project)
        self.assertFalse((self.root / "live-photo").exists())

    def test_paths_external_traversal_symlink_and_nonimage_rejected(self):
        self.write("inputs/fake.png", "not an image")
        self.write("inputs/unsupported.webp", png_bytes())
        (self.root / "inputs/link.png").symlink_to(self.root / "inputs/source.png")
        (self.root / "linked").symlink_to(self.root / "inputs", target_is_directory=True)
        for source in (str(self.root / "inputs/source.png"), "../source.png", "inputs/link.png",
                       "linked/source.png", "inputs/fake.png", "inputs/unsupported.webp", "inputs/missing.jpg"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.plan(source=source)
        self.assertNotIn("live_photo", self.project)

    def test_plan_idempotency_preserves_job_and_snapshot(self):
        job = self.plan()
        original = copy.deepcopy(job)
        result = live.plan(self.root, self.project, "inputs/source.png", prompt=job["prompt"], model=job["model"],
                           evidence="A second synthetic adoption quote does not authorize another paid call.")
        self.assertTrue(result["unchanged"])
        self.assertEqual(result["job_id"], job["id"])
        self.assertEqual(job, original)
        self.assertEqual(len(self.project["live_photo"]["jobs"]), 1)

    def test_begin_claims_once_and_unknown_does_not_resend(self):
        job = self.plan()
        first = live.begin(self.root, self.project, job["id"], evidence="Synthetic explicit service authorization")
        self.assertTrue(first["should_call"])
        self.assertTrue(first["claimed"])
        self.assertEqual(job["status"], "requesting")
        self.assertEqual(first["request"]["source"], job["input"])
        self.assertEqual(first["request"]["live_duration"], 3.0)
        self.assertNotIn("duration", first["request"])
        for status in ("requesting", "unknown", "failed"):
            job["status"] = status
            result = live.begin(self.root, self.project, job["id"], evidence="Synthetic repeated authorization")
            self.assertFalse(result["should_call"])
            self.assertTrue(result["unchanged"])
        self.native.package.assert_not_called()

    def test_begin_requires_its_own_service_evidence(self):
        job = self.plan()
        with self.assertRaisesRegex(ValueError, "service_authorization_evidence_required"):
            live.begin(self.root, self.project, job["id"], evidence="")
        self.assertEqual(job["status"], "planned")

    def test_cli_reconcile_failed_persists_denial_and_begin_never_resends(self):
        job = self.plan()
        self.persist()
        code, claimed = self.cli(["begin", "--job", job["id"], "--evidence", "Synthetic explicit service authorization"])
        self.assertEqual(code, 0)
        self.assertTrue(claimed["should_call"])
        payload = self.reconciliation(job)
        descriptor = self.json_file("records/denied.json", payload)
        code, result = self.cli(["reconcile", "--file", descriptor["path"]])
        self.assertEqual(code, 0)
        self.assertFalse(result["should_call"])
        self.assertEqual(result["job"]["status"], "failed")
        saved = state.load_project(self.root)["live_photo"]["jobs"][0]
        receipt = saved["reconciliation"]
        self.assertEqual(receipt["binding"], {"signature": saved["signature"], "claim_id": claimed["job"]["claim_id"],
                                              "source": saved["source"]})
        self.assertNotEqual(receipt["provider_receipt"]["path"], payload["provider_receipt"]["path"])
        self.assertEqual(self.cli(["status"])[1]["latest"]["status"], "failed")
        revision = state.load_project(self.root)["revision"]
        self.assertTrue(self.cli(["reconcile", "--file", descriptor["path"]])[1]["unchanged"])
        self.assertEqual(state.load_project(self.root)["revision"], revision)
        resumed = self.cli(["begin", "--job", job["id"], "--evidence", "Synthetic repeated authorization"])[1]
        self.assertFalse(resumed["should_call"])
        self.assertEqual(resumed["job"]["claim_id"], claimed["job"]["claim_id"])
        self.native.package.assert_not_called()

    def test_reconcile_idempotent_and_distinct_receipts_are_not_overwritten(self):
        job = self.plan()
        live.begin(self.root, self.project, job["id"], evidence="Synthetic service authorization")
        payload = self.reconciliation(job)
        live.reconcile(self.root, self.project, payload)
        first = copy.deepcopy(job["reconciliations"][0])
        original = (self.root / first["provider_receipt"]["path"]).read_bytes()
        self.assertTrue(live.reconcile(self.root, self.project, payload)["unchanged"])
        self.assertEqual(len(job["reconciliations"]), 1)
        result = live.reconcile(self.root, self.project, self.reconciliation(job, "unknown"))
        self.assertFalse(result["should_call"])
        self.assertEqual(job["status"], "unknown")
        self.assertEqual(len(job["reconciliations"]), 2)
        self.assertEqual(job["reconciliations"][0], first)
        self.assertEqual((self.root / first["provider_receipt"]["path"]).read_bytes(), original)
        self.assertNotEqual(first["provider_receipt"]["path"], job["reconciliation"]["provider_receipt"]["path"])
        self.assertFalse(live.begin(self.root, self.project, job["id"], evidence="Synthetic repeat")["should_call"])

    def test_unknown_reconciliation_can_record_original_successful_video(self):
        job = self.plan()
        live.begin(self.root, self.project, job["id"], evidence="Synthetic service authorization")
        result = live.reconcile(self.root, self.project, self.reconciliation(job, "unknown"))
        self.assertEqual(result["job"]["status"], "unknown")
        self.assertFalse(result["should_call"])
        result = live.record(self.root, self.project, self.record_payload(job))
        self.assertEqual(result["job"]["status"], "video-recorded")
        self.assertFalse(result["imported"])
        self.assertEqual(len(job["reconciliations"]), 1)
        self.assertTrue(result["job"]["files_current"])

    def test_reconcile_rejects_planned_and_already_recorded_states(self):
        job = self.plan()
        payload = self.reconciliation(job)
        with self.assertRaisesRegex(ValueError, "invalid_reconciliation_state"):
            live.reconcile(self.root, self.project, payload)
        live.begin(self.root, self.project, job["id"], evidence="Synthetic service authorization")
        live.record(self.root, self.project, self.record_payload(job))
        before = copy.deepcopy(job)
        with self.assertRaisesRegex(ValueError, "invalid_reconciliation_state"):
            live.reconcile(self.root, self.project, payload)
        self.assertEqual(before, job)

    def test_reconcile_rejects_stale_claim_signature_source_and_unsafe_receipts(self):
        job = self.plan()
        live.begin(self.root, self.project, job["id"], evidence="Synthetic service authorization")
        payload = self.reconciliation(job)
        other_source = self.descriptor("inputs/other.png", png_bytes(111))
        linked = self.root / "external-output/link.json"
        linked.symlink_to(self.root / payload["provider_receipt"]["path"])
        invalids = [{**payload, "claim_id": "obsolete-claim"}, {**payload, "signature": "obsolete-plan"},
                    {**payload, "source": other_source}, {**payload, "status": "planned"}, {**payload, "evidence": " "},
                    {**payload, "provider_receipt": {**payload["provider_receipt"], "sha256": "0" * 64}},
                    {**payload, "provider_receipt": {**payload["provider_receipt"], "token": "must-not-copy"}},
                    {**payload, "provider_receipt": {**payload["provider_receipt"], "path": str(self.root / payload["provider_receipt"]["path"])}},
                    {**payload, "provider_receipt": {**payload["provider_receipt"], "path": "../outside.json"}},
                    {**payload, "provider_receipt": {**payload["provider_receipt"], "path": "external-output/link.json"}}]
        before = copy.deepcopy(job)
        for invalid in invalids:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                live.reconcile(self.root, self.project, invalid)
        self.assertEqual(before, job)
        self.assertNotIn("must-not-copy", json.dumps(job))

    def test_reconcile_changed_source_and_saved_receipt_are_stale(self):
        job = self.plan()
        live.begin(self.root, self.project, job["id"], evidence="Synthetic service authorization")
        payload = self.reconciliation(job)
        self.write("inputs/source.png", png_bytes(112))
        with self.assertRaisesRegex(ValueError, "file_changed"):
            live.reconcile(self.root, self.project, payload)
        self.write("inputs/source.png", png_bytes())
        live.reconcile(self.root, self.project, payload)
        self.write(job["reconciliation"]["provider_receipt"]["path"], "Changed synthetic receipt")
        self.assertEqual(live.view(self.root, self.project)["latest"]["status"], "stale")
        with self.assertRaisesRegex(ValueError, "file_changed"):
            live.begin(self.root, self.project, job["id"], evidence="Synthetic repeat")

    def test_changed_source_marks_stale_and_blocks_begin_record_package(self):
        job = self.plan()
        payload = self.record_payload(job)
        self.write("inputs/source.png", png_bytes(101))
        before = copy.deepcopy(self.project)
        self.assertEqual(live.view(self.root, self.project)["latest"]["status"], "stale")
        self.assertEqual(self.project, before)
        for action in (lambda: live.begin(self.root, self.project, job["id"], evidence="Synthetic authorization"),
                       lambda: live.record(self.root, self.project, payload),
                       lambda: live.package(self.root, self.project, job["id"])):
            with self.assertRaisesRegex(ValueError, "file_changed"):
                action()
        self.native.package.assert_not_called()

    def test_changed_snapshot_is_not_a_current_source(self):
        job = self.plan()
        self.write(job["input"]["path"], png_bytes(101))
        result = live.view(self.root, self.project)["latest"]
        self.assertTrue(result["stale"])
        self.assertFalse(result["files_current"])
        self.assertFalse(result["passed_local_validation"])

    def test_planned_import_requires_receipt_and_keeps_source_readonly(self):
        job = self.plan()
        source = (self.root / "inputs/source.png").read_bytes()
        payload = self.record_payload(job)
        missing = {**payload, "provider_receipt": {"path": "missing.json", "sha256": "0" * 64}}
        with self.assertRaises(ValueError):
            live.record(self.root, self.project, missing)
        result = live.record(self.root, self.project, payload)
        self.assertTrue(result["imported"])
        self.assertEqual(job["status"], "video-recorded")
        self.assertFalse(job["generation_authorized"])
        self.assertEqual((self.root / "inputs/source.png").read_bytes(), source)
        self.assertNotEqual(job["video"]["path"], payload["video"])
        self.assertNotEqual(job["provider_receipt"]["path"], payload["provider_receipt"]["path"])
        self.assertEqual(job["watermark"], "preserved")
        self.assertIsNone(job["package"])

    def test_record_is_idempotent_and_cannot_rewrite_video(self):
        job = self.plan()
        payload = self.record_payload(job)
        live.record(self.root, self.project, payload)
        before = copy.deepcopy(job)
        self.assertTrue(live.record(self.root, self.project, payload)["unchanged"])
        self.assertEqual(before, job)
        self.write("external-output/result.mp4", b"SYNTHETIC CHANGED VIDEO")
        with self.assertRaisesRegex(ValueError, "immutable_video"):
            live.record(self.root, self.project, payload)

    def test_motion_fail_and_uncertain_do_not_package(self):
        for verdict in ("needs_revision", "uncertain"):
            with self.subTest(verdict=verdict):
                job = self.recorded_job(verdict)
                self.assertEqual(job["status"], "motion-review")
                with self.assertRaisesRegex(ValueError, "motion_pass_required"):
                    live.package(self.root, self.project, job["id"])
        self.native.package.assert_not_called()

    def test_motion_review_can_be_reconciled_without_new_generation(self):
        job = self.recorded_job("uncertain")
        live.record(self.root, self.project, self.record_payload(job, "pass"))
        self.assertEqual(job["status"], "video-recorded")
        self.assertTrue(job["imported"])

    def test_empty_video_bad_receipt_and_unknown_descriptors_rejected(self):
        job = self.plan()
        payload = self.record_payload(job)
        invalids = [{**payload, "provider_receipt": {**payload["provider_receipt"], "sha256": "0" * 64}},
                    {**payload, "provider_receipt": {**payload["provider_receipt"], "token": "must-never-copy"}},
                    {**payload, "quality": "commercial-1080p"},
                    {**payload, "motion_review": {"verdict": "pass"}}]
        for invalid in invalids:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                live.record(self.root, self.project, invalid)
        self.write(payload["video"], b"")
        with self.assertRaisesRegex(ValueError, "empty_file"):
            live.record(self.root, self.project, payload)
        self.assertNotIn("must-never-copy", json.dumps(self.project))

    def test_package_native_schema_produces_zip_report_and_pvt_files(self):
        job = self.packaged_job()
        bundle = job["package"]
        self.assertEqual(job["status"], "packaged")
        self.assertTrue(bundle["verification"]["passed_local_validation"])
        self.assertIsInstance(bundle["pvt_path"], str)
        self.assertNotIn("pvt", bundle)
        self.assertEqual(len(bundle["pvt_files"]), 3)
        for key in ("photo", "video", "report", "archive", "manifest"):
            self.assertIn("sha256", bundle[key])
        with zipfile.ZipFile(self.root / bundle["archive"]["path"]) as archive:
            self.assertEqual(set(archive.namelist()), {"LIVE.jpg", "LIVE.mov", "LIVE.pvt/LIVE.jpg",
                                                     "LIVE.pvt/LIVE.mov", "LIVE.pvt/metadata.plist", "verification.json", "manifest.json"})
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read("LIVE.mov"), (self.root / bundle["video"]["path"]).read_bytes())
        self.native.verify.assert_called_once()
        self.assertFalse(live.view(self.root, self.project)["latest"]["acceptance"]["macos-photos"]["verified"])

    def test_package_is_idempotent_and_never_imports_into_photos(self):
        job = self.packaged_job()
        before = copy.deepcopy(job)
        result = live.package(self.root, self.project, job["id"])
        self.assertTrue(result["unchanged"])
        self.assertEqual(before, job)
        self.native.package.assert_called_once()
        self.assertEqual(job["attestations"], [])

    def test_package_failure_preserves_attempt_error_and_explicit_recovery(self):
        job = self.recorded_job()
        self.native.verify.return_value = {"passed_local_validation": False}
        result = live.package(self.root, self.project, job["id"])
        self.assertFalse(result["ok"])
        self.assertEqual(job["status"], "package-failed")
        self.assertIsNone(job["package"])
        first = job["package_attempts"][0]
        self.assertEqual(first["status"], "failed")
        self.assertTrue((self.root / first["directory"] / "LIVE.jpg").is_file())
        self.assertIn("recovery", job["error"])
        self.native.verify.return_value = {"passed_local_validation": True}
        self.assertTrue(live.package(self.root, self.project, job["id"])["ok"])
        self.assertNotEqual(first["directory"], job["package_attempts"][1]["directory"])
        self.assertNotIn("error", job)

    def test_native_failed_flag_cannot_be_overridden_by_second_verifier(self):
        job = self.recorded_job()

        def failed_native(*arguments, **keywords):
            result = self.fake_package(*arguments, **keywords)
            result.update(passed_local_validation=False, pvt_package=None)
            return result

        self.native.package.side_effect = failed_native
        result = live.package(self.root, self.project, job["id"])
        self.assertFalse(result["ok"])
        self.assertEqual(job["status"], "package-failed")
        self.assertIsNone(job["package"])
        self.native.verify.assert_not_called()
        first_directory = job["package_attempts"][0]["directory"]
        self.assertTrue((self.root / first_directory / "verification.json").is_file())
        self.native.package.side_effect = self.fake_package
        result = live.package(self.root, self.project, job["id"])
        self.assertTrue(result["ok"])
        self.assertNotEqual(first_directory, job["package_attempts"][1]["directory"])
        self.assertTrue(all(not value["verified"] for value in result["job"]["acceptance"].values()))

    def test_native_success_requires_exact_boolean_true(self):
        job = self.recorded_job()
        for flag in (None, 1, "true"):
            def invalid_native(*arguments, **keywords):
                result = self.fake_package(*arguments, **keywords)
                result["passed_local_validation"] = flag
                return result

            self.native.package.side_effect = invalid_native
            with self.subTest(flag=flag):
                self.assertFalse(live.package(self.root, self.project, job["id"])["ok"])
                self.assertIsNone(job["package"])
        self.native.verify.assert_not_called()

    def test_native_success_without_complete_matching_pvt_is_recoverable_failure(self):
        job = self.recorded_job()
        for problem in ("missing-pvt", "missing-metadata", "missing-photo", "missing-video", "changed-photo", "changed-video"):
            def incomplete_native(*arguments, **keywords):
                result = self.fake_package(*arguments, **keywords)
                bundle = Path(result["pvt_package"])
                if problem == "missing-pvt":
                    result["pvt_package"] = None
                elif problem == "missing-metadata":
                    (bundle / "metadata.plist").unlink()
                else:
                    field = "photo" if problem.endswith("photo") else "video"
                    resource = bundle / Path(result[field]).name
                    if problem.startswith("missing"):
                        resource.unlink()
                    else:
                        resource.write_bytes(b"SYNTHETIC CHANGED PVT RESOURCE")
                return result

            self.native.package.side_effect = incomplete_native
            with self.subTest(problem=problem):
                result = live.package(self.root, self.project, job["id"])
                self.assertFalse(result["ok"])
                self.assertIsNone(job["package"])
                self.assertEqual(job["status"], "package-failed")
                self.assertFalse((self.root / job["package_attempts"][-1]["directory"] / "LIVE.zip").exists())

    def test_changed_manifest_marks_package_stale_and_invalidates_acceptance(self):
        job = self.packaged_job()
        live.attest(self.root, self.project, self.attestation(job))
        self.write(job["package"]["manifest"]["path"], "Changed synthetic manifest")
        latest = live.view(self.root, self.project)["latest"]
        self.assertEqual(latest["status"], "stale")
        self.assertFalse(latest["passed_local_validation"])
        self.assertFalse(latest["acceptance"]["macos-photos"]["verified"])
        with self.assertRaisesRegex(ValueError, "file_changed"):
            live.package(self.root, self.project, job["id"])

    def test_package_exception_is_sanitized_and_native_source_change_is_blocked(self):
        job = self.recorded_job()
        self.native.package.side_effect = RuntimeError("credentials-must-not-appear")
        result = live.package(self.root, self.project, job["id"])
        self.assertFalse(result["ok"])
        self.assertNotIn("credentials-must-not-appear", json.dumps(result))
        self.assertNotIn("credentials-must-not-appear", json.dumps(self.project))

    def test_packaged_file_changed_blocks_acceptance_and_is_stale(self):
        job = self.packaged_job()
        payload = self.attestation(job)
        self.write(job["package"]["video"]["path"], b"SYNTHETIC CHANGED PACKAGE")
        with self.assertRaisesRegex(ValueError, "file_changed"):
            live.attest(self.root, self.project, payload)
        latest = live.view(self.root, self.project)["latest"]
        self.assertEqual(latest["status"], "stale")
        self.assertFalse(latest["passed_local_validation"])

    def test_macos_and_iphone_and_publishing_app_are_separate(self):
        job = self.packaged_job()
        live.attest(self.root, self.project, self.attestation(job))
        latest = live.view(self.root, self.project)["latest"]
        self.assertEqual(latest["status"], "macos-verified")
        self.assertTrue(latest["acceptance"]["macos-photos"]["verified"])
        self.assertFalse(latest["acceptance"]["iphone-photos"]["verified"])
        self.assertFalse(latest["acceptance"]["publishing-app"]["verified"])
        self.assertFalse(latest["acceptance"]["macos-photos"]["programmatically_observed"])
        live.attest(self.root, self.project, self.attestation(job, "iphone-photos"))
        self.assertEqual(live.view(self.root, self.project)["latest"]["status"], "iphone-verified")
        live.attest(self.root, self.project, self.attestation(job, "publishing-app"))
        self.assertEqual(live.view(self.root, self.project)["latest"]["status"], "publishing-app-verified")
        self.assertEqual(live.view(self.root, self.project)["next"]["action"], "done")

    def test_attestation_idempotency_and_later_macos_does_not_erase_iphone(self):
        job = self.packaged_job()
        payload = self.attestation(job)
        live.attest(self.root, self.project, payload)
        self.assertTrue(live.attest(self.root, self.project, payload)["unchanged"])
        self.assertEqual(len(job["attestations"]), 1)
        live.attest(self.root, self.project, self.attestation(job, "iphone-photos"))
        live.attest(self.root, self.project, {**payload, "evidence": "A second synthetic Mac observation."})
        self.assertEqual(job["status"], "iphone-verified")

    def test_attestation_requires_real_evidence_and_boolean_observation(self):
        job = self.packaged_job()
        payload = self.attestation(job)
        invalids = [{**payload, "evidence_files": []}, {**payload, "observer": ""},
                    {**payload, "live_badge": 1}, {**payload, "playback_confirmed": False},
                    {**payload, "evidence": " "}, {**payload, "target": "all-devices"},
                    {**payload, "evidence_files": [{"path": "missing.png", "sha256": "0" * 64}]}]
        for invalid in invalids:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                live.attest(self.root, self.project, invalid)
        self.assertEqual(job["attestations"], [])

    def test_roundtrip_hashes_bind_current_pair_and_do_not_imply_iphone(self):
        job = self.packaged_job()
        roundtrip = {}
        for field in ("photo", "video"):
            descriptor = job["package"][field]
            roundtrip[field] = self.descriptor("observations/roundtrip/" + Path(descriptor["path"]).name,
                                                (self.root / descriptor["path"]).read_bytes())
        live.attest(self.root, self.project, self.attestation(job, roundtrip=roundtrip))
        self.assertFalse(job["acceptance"]["iphone-photos"]["verified"])
        roundtrip["video"] = self.descriptor(roundtrip["video"]["path"], b"SYNTHETIC MISMATCH")
        with self.assertRaisesRegex(ValueError, "roundtrip_changed"):
            live.attest(self.root, self.project, self.attestation(job, roundtrip=roundtrip))

    def test_changed_evidence_invalidates_only_its_own_target(self):
        job = self.packaged_job()
        mac = self.attestation(job)
        live.attest(self.root, self.project, mac)
        live.attest(self.root, self.project, self.attestation(job, "iphone-photos"))
        self.write(mac["evidence_files"][0]["path"], "Changed synthetic evidence")
        before = copy.deepcopy(self.project)
        latest = live.view(self.root, self.project)["latest"]
        self.assertFalse(latest["acceptance"]["macos-photos"]["verified"])
        self.assertTrue(latest["acceptance"]["iphone-photos"]["verified"])
        self.assertEqual(before, self.project)

    def test_rootless_view_does_not_claim_current_or_verified(self):
        job = self.packaged_job()
        live.attest(self.root, self.project, self.attestation(job))
        latest = live.view(None, self.project)["latest"]
        self.assertFalse(latest["files_current"])
        self.assertFalse(latest["passed_local_validation"])
        self.assertFalse(latest["acceptance"]["macos-photos"]["verified"])

    def test_static_next_catalog_and_project_fields_do_not_change(self):
        catalog = asset_plan.catalog()
        next_before = state.next_action(self.project, self.root)
        fields_before = copy.deepcopy(self.project)
        job = self.recorded_job("needs_revision")
        self.assertEqual(next_before, state.next_action(self.project, self.root))
        self.assertEqual(fields_before, {key: value for key, value in self.project.items() if key != "live_photo"})
        self.assertEqual(catalog, asset_plan.catalog())
        self.assertEqual(len(catalog["assets"]), 21)
        self.assertEqual(live.view(self.root, self.project)["latest"]["id"], job["id"])

    def test_cli_requires_action_and_dispatch_rejects_invalid_action(self):
        parser = argparse.ArgumentParser()
        live.add_parser(parser.add_subparsers(dest="command", required=True))
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            parser.parse_args(["live", str(self.root)])
        with self.assertRaisesRegex(ValueError, "action_required"):
            live._dispatch(argparse.Namespace(project=str(self.root), action=None))

    def test_cli_status_is_readonly_and_plan_refreshes_revision_once(self):
        self.assertEqual(self.cli(["status"])[1]["enabled"], False)
        self.assertFalse((self.root / ".project.lock").exists())
        args = ["plan", "--source", "inputs/source.png", "--prompt", "Synthetic micro-motion only", "--model", "synthetic",
                "--evidence", "Synthetic source adoption"]
        code, result = self.cli(args)
        self.assertEqual(code, 0)
        self.assertEqual(result["revision"], 1)
        self.assertEqual(state.load_project(self.root)["revision"], 1)
        self.assertTrue(self.cli(args)[1]["unchanged"])
        self.assertEqual(state.load_project(self.root)["revision"], 1)
        before = (self.root / "project.json").read_bytes()
        self.cli(["status"])
        self.assertEqual((self.root / "project.json").read_bytes(), before)

    def test_cli_record_file_is_project_scoped(self):
        job = self.plan()
        self.persist()
        descriptor = self.json_file("records/video.json", self.record_payload(job))
        code, result = self.cli(["record", "--file", descriptor["path"]])
        self.assertEqual(code, 0)
        self.assertEqual(result["job"]["status"], "video-recorded")
        with self.assertRaises(ValueError):
            self.cli(["record", "--file", str(self.root / descriptor["path"])])

    def test_cli_package_failure_is_saved_not_rolled_back(self):
        job = self.recorded_job()
        self.persist()
        self.native.package.side_effect = RuntimeError("Synthetic unavailable native framework")
        code, result = self.cli(["package", "--job", job["id"]])
        self.assertEqual(code, 2)
        self.assertEqual(result["job"]["status"], "package-failed")
        saved = state.load_project(self.root)
        self.assertEqual(saved["live_photo"]["jobs"][0]["status"], "package-failed")
        self.assertEqual(saved["revision"], 1)

    def test_project_status_integration_does_not_require_native_import(self):
        job = self.plan()
        with mock.patch.object(live.importlib, "import_module", side_effect=AssertionError("native import during status")):
            payload = state.status_payload(self.project, root=self.root)
        self.assertEqual(payload["live_photo"]["latest"]["id"], job["id"])


if __name__ == "__main__":
    unittest.main()
