"""Product invariants; Pillow images are fixtures, never visual review evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, PngImagePlugin, UnidentifiedImageError


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import product_contract


def numeric_size(**changes):
    value = {
        "mode": "numeric", "height": 12, "unit": "cm",
        "measurement_basis": "本体站高，不含挂扣", "status": "confirmed",
        "origin": "user-confirmed", "evidence_kind": "design_target",
    }
    value.update(changes)
    return value


def fixture_png(path, color="white"):
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Description", "Programmatic contract fixture; NOT visual review evidence.")
    with Image.new("RGB", (360, 100), color) as image:
        ImageDraw.Draw(image).text(
            (12, 12), "CONTRACT TEST FIXTURE\nNOT VISUAL REVIEW EVIDENCE", fill="black",
        )
        image.save(path, pnginfo=metadata)


class NormalizedSizeTests(unittest.TestCase):
    def test_cm_mm_and_m_describe_the_same_target_without_losing_measurement_basis(self):
        for unit, factor in (("mm", 1), ("cm", 10), ("m", 1000)):
            with self.subTest(unit=unit):
                value = numeric_size(
                    unit=unit, height=120 / factor, width=75 / factor,
                    depth=30 / factor, total_length=150 / factor,
                )
                before = copy.deepcopy(value)
                result = product_contract.normalized_size(value)
                self.assertEqual(result["normalized_mm"], {
                    "height_mm": 120, "width_mm": 75, "depth_mm": 30, "total_length_mm": 150,
                })
                self.assertEqual(result["measurement_basis"], "本体站高，不含挂扣")
                self.assertEqual(result["unit"], unit)
                self.assertEqual(result["evidence_kind"], "design_target")
                self.assertEqual(result["origin"], "user-confirmed")
                self.assertEqual(result["status"], "confirmed")
                result["normalized_mm"]["height_mm"] = 999
                self.assertEqual(value, before)

    def test_each_numeric_dimension_requires_a_finite_positive_number_not_bool(self):
        invalid = (0, -0.0, -1, float("nan"), float("inf"), -float("inf"),
                   True, False, "12", [], {}, 10 ** 500)
        for dimension in ("height", "width", "depth", "total_length"):
            for value in invalid:
                with self.subTest(dimension=dimension, value=value):
                    size = numeric_size(**{dimension: value})
                    before = json.dumps(size, ensure_ascii=False, sort_keys=True)
                    with self.assertRaises(product_contract.ContractError):
                        product_contract.normalized_size(size)
                    self.assertEqual(json.dumps(size, ensure_ascii=False, sort_keys=True), before)

    def test_normalization_cannot_overflow_into_an_infinite_target(self):
        for mode, height in (("numeric", 1e308), ("range", [1, 1e308])):
            with self.subTest(mode=mode):
                with self.assertRaises(product_contract.ContractError):
                    product_contract.normalized_size(numeric_size(mode=mode, height=height))

    def test_numeric_size_needs_at_least_one_dimension_but_does_not_invent_others(self):
        with self.assertRaises(product_contract.ContractError):
            product_contract.normalized_size(numeric_size(height=None))
        result = product_contract.normalized_size(numeric_size(height=None, width=2))
        self.assertEqual(result["normalized_mm"], {"width_mm": 20})

    def test_range_keeps_both_endpoints_and_normalizes_every_supplied_dimension(self):
        size = numeric_size(
            mode="range", height=[10, 12], width=[6.5, 8], depth=[3, 3], total_length=[13, 15],
        )
        before = copy.deepcopy(size)
        result = product_contract.normalized_size(size)
        self.assertEqual(result["normalized_mm"], {
            "height_mm": [100, 120], "width_mm": [65, 80],
            "depth_mm": [30, 30], "total_length_mm": [130, 150],
        })
        result["height"][0] = 999
        result["normalized_mm"]["height_mm"][0] = 999
        self.assertEqual(size, before)

    def test_ranges_reject_reversed_malformed_boolean_and_nonfinite_bounds(self):
        invalid = ([12, 10], [], [1], [1, 2, 3], (1, 2), "1-2", 2,
                   [0, 2], [-1, 2], [True, 2], [1, False], [1, "2"],
                   [float("nan"), 2], [1, float("nan")], [1, float("inf")],
                   [-float("inf"), 2], [1, 10 ** 500])
        for bounds in invalid:
            with self.subTest(bounds=bounds):
                with self.assertRaises(product_contract.ContractError):
                    product_contract.normalized_size(numeric_size(mode="range", height=bounds))

    def test_numeric_range_and_qualitative_targets_require_an_observable_measurement_basis(self):
        sizes = [numeric_size(), numeric_size(mode="range", height=[10, 12]),
                 {"mode": "qualitative", "description": "能放进成人外套口袋"}]
        for size in sizes:
            for basis in (None, "", " \n\t", True, 12):
                with self.subTest(mode=size["mode"], basis=basis):
                    candidate = {**size, "measurement_basis": basis}
                    before = copy.deepcopy(candidate)
                    with self.assertRaises(product_contract.ContractError):
                        product_contract.normalized_size(candidate)
                    self.assertEqual(candidate, before)
        for unit in (None, "inch", "厘米", "", True):
            with self.subTest(unit=unit):
                with self.assertRaises(product_contract.ContractError):
                    product_contract.normalized_size(numeric_size(unit=unit))

    def test_unknown_size_stays_unknown_and_cannot_be_approved(self):
        value = {}
        result = product_contract.normalized_size(value)
        self.assertEqual(value, {})
        self.assertEqual(result["mode"], "unknown")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["origin"], "agent-inferred")
        self.assertEqual(result["evidence_kind"], "design_target")
        self.assertNotIn("normalized_mm", result)
        with self.assertRaises(product_contract.ContractError):
            product_contract.normalized_size({"mode": "unknown", "status": "confirmed"})

    def test_qualitative_scale_retains_observable_description_without_fabricating_mm(self):
        size = {
            "mode": "qualitative", "description": "成人单手托住，挂在日常背包侧面不拖地",
            "measurement_basis": "含挂扣的整体使用尺度", "status": "confirmed",
            "origin": "user-confirmed",
        }
        result = product_contract.normalized_size(size)
        self.assertEqual(result["description"], size["description"])
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["evidence_kind"], "design_target")
        self.assertNotIn("normalized_mm", result)
        for description in (None, "", " \t\n ", "小", True):
            with self.subTest(description=description):
                with self.assertRaises(product_contract.ContractError):
                    product_contract.normalized_size({**size, "description": description})

    def test_measured_sample_requires_nonempty_evidence(self):
        for evidence in (None, "", [], {}):
            with self.subTest(evidence=evidence):
                with self.assertRaises(product_contract.ContractError):
                    product_contract.normalized_size(numeric_size(
                        evidence_kind="measured_sample", evidence=evidence,
                    ))
        result = product_contract.normalized_size(numeric_size(
            evidence_kind="measured_sample", evidence="fixture sample log A: ruler measurement 120 mm",
        ))
        self.assertEqual(result["evidence_kind"], "measured_sample")
        self.assertEqual(result["evidence"], "fixture sample log A: ruler measurement 120 mm")

    def test_whitespace_is_not_measurement_evidence(self):
        for evidence in (" ", "\n\t"):
            with self.subTest(evidence=repr(evidence)):
                with self.assertRaises(product_contract.ContractError):
                    product_contract.normalized_size(numeric_size(
                        evidence_kind="measured_sample", evidence=evidence,
                    ))

    def test_user_specification_does_not_become_a_measurement(self):
        result = product_contract.normalized_size(numeric_size(evidence_kind="user_specification"))
        self.assertEqual(result["evidence_kind"], "user_specification")
        self.assertNotIn("evidence", result)

    def test_invalid_shapes_modes_statuses_and_sources_are_refused(self):
        invalid = [None, [], "12 cm", {"mode": "automatic"},
                   numeric_size(status="approved"), numeric_size(origin="generated"),
                   numeric_size(evidence_kind="real-photo")]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(product_contract.ContractError):
                    product_contract.normalized_size(value)


class ProductContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="product-contract-fixture-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = {
            "schema_version": 3, "project_id": "product-contract-test-fixture", "revision": 7,
            "answers": {"subjects": ["小云", "小雨"], "product_form": "plush"},
            "selections": {}, "finals": [], "feedback": [],
            "reviews": {"fixture": "No real visual review has been performed."},
        }
        product_contract.initialize(self.project)
        self.subjects = product_contract.sync_subjects(self.project)
        fixture_png(self.root / "fixtures/front.png")
        fixture_png(self.root / "fixtures/back.png", "lightblue")
        (self.root / "project.json").write_text(
            json.dumps(self.project, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    def files(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def assert_rejected_without_writes(self, operation, project=None, error=product_contract.ContractError):
        target = self.project if project is None else project
        before = copy.deepcopy(target)
        files = self.files()
        try:
            with self.assertRaises(error):
                operation()
        finally:
            self.assertEqual(self.files(), files, "失败操作修改了磁盘文件")
            self.assertEqual(target, before, "失败操作部分修改了原项目")

    def record(self, project=None, subject="小云", **changes):
        options = {"view": "front", "path": "fixtures/front.png", "origin": "reference-derived"}
        options.update(changes)
        return product_contract.record_view(
            self.root, self.project if project is None else project, subject, **options,
        )

    def confirm(self, asset, project=None, **changes):
        options = {
            "origin": "agent-inferred",
            "evidence": "程序合同 fixture 的确认回执；没有进行真实视觉审查，也不代表真人批准。",
        }
        options.update(changes)
        return product_contract.confirm_view(
            self.root, self.project if project is None else project, asset["id"], **options,
        )

    def test_subject_ids_and_own_specs_survive_reordering_removing_first_and_readding(self):
        product_contract.set_spec(self.project, "小云", {"name": "小云挂件款"})
        product_contract.set_spec(self.project, "小雨", {"name": "小雨桌面款"})
        cloud_id, cloud_spec = product_contract.spec_for(self.project, "小云")
        rain_id, rain_spec = product_contract.spec_for(self.project, "小雨")
        originals = {cloud_id: copy.deepcopy(cloud_spec), rain_id: copy.deepcopy(rain_spec)}
        signature = product_contract.signature(self.project)
        self.project["answers"]["subjects"] = ["小雨", "小云"]
        self.assertEqual(product_contract.sync_subjects(self.project), self.subjects)
        self.assertEqual(product_contract.signature(self.project), signature)
        self.project["answers"]["subjects"] = ["小雨"]
        self.assertEqual(product_contract.sync_subjects(self.project), {"小雨": self.subjects["小雨"]})
        self.assertEqual(product_contract.spec_for(self.project, "小雨"), (rain_id, originals[rain_id]))
        self.assertEqual(self.project["product_contract"]["specs"][cloud_id], originals[cloud_id])
        self.assertFalse(self.project["product_contract"]["subjects"][self.subjects["小云"]]["active"])
        for subject in ("小云", self.subjects["小云"]):
            with self.subTest(subject=subject):
                with self.assertRaises(product_contract.ContractError):
                    product_contract.spec_for(self.project, subject)
        self.project["answers"]["subjects"] = ["小雨", "小云"]
        self.assertEqual(product_contract.sync_subjects(self.project), self.subjects)
        self.assertEqual(product_contract.spec_for(self.project, "小云"), (cloud_id, originals[cloud_id]))
        self.assertEqual(len(self.project["product_contract"]["specs"]), 2)

    def test_name_and_stable_id_resolve_the_same_subject_without_accepting_another_spec(self):
        for name, identifier in self.subjects.items():
            with self.subTest(name=name):
                self.assertEqual(product_contract.subject_id(self.project, name), identifier)
                self.assertEqual(product_contract.spec_for(self.project, name),
                                 product_contract.spec_for(self.project, identifier))
                self.assertEqual(product_contract.signature(self.project, name),
                                 product_contract.signature(self.project, identifier))
        rain_spec, _ = product_contract.spec_for(self.project, "小雨")
        for subject, spec_id in (("不存在", None), ("小云", rain_spec), ("小云", "missing-spec")):
            with self.subTest(subject=subject, spec_id=spec_id):
                with self.assertRaises(product_contract.ContractError):
                    product_contract.spec_for(self.project, subject, spec_id)

    def test_free_text_dual_use_is_preserved_exactly_and_not_forced_into_an_enum(self):
        text = "  通勤挂在包上，回家拆下挂扣作桌面陪伴；也可做拍照搭子。\n保留可拆连接。  "
        values = {"usage": {"text": text, "status": "confirmed", "origin": "user-confirmed"}}
        before = copy.deepcopy(values)
        result = product_contract.set_spec(self.project, "小云", values)
        spec_id, spec = product_contract.spec_for(self.project, "小云")
        self.assertEqual(spec["usage"], values["usage"])
        self.assertEqual(result["spec_id"], spec_id)
        self.assertEqual(result["subject_id"], self.subjects["小云"])
        self.assertEqual(result["signature"], product_contract.signature(self.project, "小云"))
        self.assertEqual(product_contract.missing_requirements(self.project, "concept", "小云"), [])
        values["usage"]["text"] = "caller changed its own input"
        self.assertEqual(spec["usage"], before["usage"])

    def test_usage_confirmation_cannot_be_inferred_from_nonempty_text(self):
        product_contract.set_spec(self.project, "小云", {
            "usage": {"text": "挂包与桌面双用途", "status": "proposed", "origin": "agent-inferred"},
        })
        self.assertEqual([row["field"] for row in product_contract.missing_requirements(
            self.project, "concept", "小云",
        )], ["usage"])

    def test_scene_requires_confirmed_scale_but_a_qualitative_target_is_sufficient(self):
        product_contract.set_spec(self.project, "小云", {
            "usage": {"text": "桌面摆件，也能挂包", "status": "confirmed", "origin": "user-confirmed"},
        })
        for stage in ("scene", "package", "done"):
            with self.subTest(stage=stage):
                self.assertEqual([row["field"] for row in product_contract.missing_requirements(
                    self.project, stage, "小云",
                )], ["size"])
        product_contract.set_spec(self.project, "小云", {"size": {
            "mode": "qualitative", "description": "成人单手托起，放入外套口袋",
            "measurement_basis": "不含挂扣的本体", "status": "confirmed", "origin": "user-confirmed",
        }})
        self.assertEqual(product_contract.missing_requirements(self.project, "scene", "小云"), [])
        self.assertEqual(self.project["finals"], [])
        legacy = {"schema_version": 2}
        self.assertEqual(product_contract.missing_requirements(legacy, "scene"), [])
        self.assertEqual(legacy, {"schema_version": 2})

    def test_set_spec_rejects_invalid_input_without_partial_in_memory_or_disk_changes(self):
        invalid = [None, [], {"subject_id": self.subjects["小雨"]}, {"structure": []},
                   {"usage": {"text": True}}, {"usage": {"text": " \n", "status": "confirmed"}},
                   {"usage": {"text": "桌面", "origin": "guessed"}},
                   {"usage": {"text": "桌面", "status": "approved"}},
                   {"size": numeric_size(height=float("nan"))}, {"size": numeric_size(height=True)},
                   {"size": numeric_size(height=0)}, {"size": numeric_size(measurement_basis="")},
                   {"size": numeric_size(evidence_kind="measured_sample")}]
        baseline = copy.deepcopy(self.project)
        for values in invalid:
            with self.subTest(values=values):
                candidate = copy.deepcopy(baseline)
                self.assert_rejected_without_writes(
                    lambda: product_contract.set_spec(candidate, "小云", values), candidate,
                )

    def test_bad_size_cannot_commit_an_otherwise_valid_usage_change(self):
        values = {"usage": {"text": "新用途必须与尺寸一起提交", "status": "confirmed"},
                  "size": numeric_size(mode="range", height=[12, 10])}
        self.assert_rejected_without_writes(lambda: product_contract.set_spec(self.project, "小云", values))

    def test_invalid_existing_contract_cannot_partially_commit_a_valid_update(self):
        baseline = copy.deepcopy(self.project)
        rain_id, _ = product_contract.spec_for(self.project, "小雨")
        for invalid in ("schema", "usage", "size", "subject"):
            with self.subTest(invalid=invalid):
                candidate = copy.deepcopy(baseline)
                contract = candidate["product_contract"]
                if invalid == "schema":
                    contract["schema_version"] = True
                elif invalid == "usage":
                    contract["specs"][rain_id]["usage"] = {"text": 123}
                elif invalid == "size":
                    contract["specs"][rain_id]["size"] = numeric_size(height=-1)
                else:
                    contract["specs"][rain_id]["subject_id"] = "unknown-subject"
                self.assert_rejected_without_writes(lambda: product_contract.set_spec(
                    candidate, "小云", {"usage": {"text": "这项有效更新也不能部分落入无效合同"}},
                ), candidate)

    def test_rejected_update_does_not_initialize_a_missing_contract(self):
        self.project.pop("product_contract")
        self.assert_rejected_without_writes(lambda: product_contract.set_spec(
            self.project, "小云", {"size": numeric_size(height=-1)},
        ))

    def test_rejected_update_does_not_enable_a_disabled_contract(self):
        self.project["product_contract"]["enabled"] = False
        self.assert_rejected_without_writes(lambda: product_contract.set_spec(
            self.project, "小云", {"size": numeric_size(height=-1)},
        ))

    def test_rejected_update_does_not_partially_sync_pending_subject_changes(self):
        self.project["answers"]["subjects"] = ["小雨", "小芽"]
        self.assert_rejected_without_writes(lambda: product_contract.set_spec(
            self.project, "小雨", {"size": numeric_size(height=-1)},
        ))

    def test_set_spec_rejects_unknown_subject_and_foreign_spec_without_writes(self):
        rain_spec, _ = product_contract.spec_for(self.project, "小雨")
        for subject, spec_id in (("不存在", None), ("小云", "../unsafe"), ("小云", rain_spec)):
            with self.subTest(subject=subject, spec_id=spec_id):
                self.assert_rejected_without_writes(lambda: product_contract.set_spec(
                    self.project, subject, {"size": numeric_size()}, spec_id,
                ))

    def test_new_explicit_variant_keeps_default_spec_and_does_not_inherit_approval(self):
        before = copy.deepcopy(self.project["product_contract"]["specs"])
        result = product_contract.set_spec(self.project, "小云", {"size": numeric_size(height=24)}, "large-design")
        self.assertEqual(result["spec_id"], "large-design")
        for identifier, specification in before.items():
            self.assertEqual(self.project["product_contract"]["specs"][identifier], specification)
        _, large = product_contract.spec_for(self.project, "小云", "large-design")
        self.assertEqual(large["size"]["normalized_mm"]["height_mm"], 240)
        self.assertEqual(large["usage"]["status"], "unknown")

    def test_valid_update_changes_only_its_subject_and_keeps_noncontract_project_state(self):
        before = copy.deepcopy(self.project)
        rain_spec, _ = product_contract.spec_for(self.project, "小雨")
        product_contract.set_spec(self.project, "小云", {
            "size": numeric_size(), "structure": {"attachment": {"detachable": True}},
        })
        self.assertEqual({key: value for key, value in self.project.items() if key != "product_contract"},
                         {key: value for key, value in before.items() if key != "product_contract"})
        self.assertEqual(self.project["product_contract"]["specs"][rain_spec],
                         before["product_contract"]["specs"][rain_spec])
        self.assertEqual(self.project["product_contract"]["view_assets"], {})

    def test_contract_validation_is_pure_and_returns_independent_normalized_data(self):
        spec_id, spec = product_contract.spec_for(self.project, "小云")
        spec["size"] = numeric_size()
        before = copy.deepcopy(self.project)
        result = product_contract.validate_contract(self.project["product_contract"])
        self.assertEqual(result["specs"][spec_id]["size"]["normalized_mm"], {"height_mm": 120})
        result["specs"][spec_id]["size"]["height"] = 999
        result["subjects"][self.subjects["小云"]]["name"] = "changed copy"
        self.assertEqual(self.project, before)

    def test_signatures_ignore_outer_revision_feedback_and_selection(self):
        before = product_contract.signature(self.project)
        self.project["revision"] += 1
        self.project["feedback"].append({"raw": "改一下工作台说明"})
        self.project["selections"]["concept"] = {"fixture": "unrelated choice"}
        self.assertEqual(product_contract.signature(self.project), before)

    def test_signatures_are_scoped_to_subject_and_contract_domain(self):
        whole = product_contract.signature(self.project)
        cloud = product_contract.signature(self.project, "小云")
        rain = product_contract.signature(self.project, "小雨")
        specs = product_contract.signature(self.project, "小云", ["specs"])
        views = product_contract.signature(self.project, "小云", ["view_assets"])
        product_contract.set_spec(self.project, "小云", {"size": numeric_size()})
        self.assertNotEqual(product_contract.signature(self.project), whole)
        self.assertNotEqual(product_contract.signature(self.project, "小云"), cloud)
        self.assertNotEqual(product_contract.signature(self.project, "小云", ["specs"]), specs)
        self.assertEqual(product_contract.signature(self.project, "小雨"), rain)
        self.assertEqual(product_contract.signature(self.project, "小云", ["view_assets"]), views)
        specs = product_contract.signature(self.project, "小云", ["specs"])
        asset = self.record()
        recorded = product_contract.signature(self.project, "小云", ["view_assets"])
        self.assertNotEqual(recorded, views)
        self.confirm(asset)
        self.assertNotEqual(product_contract.signature(self.project, "小云", ["view_assets"]), recorded)
        self.assertEqual(product_contract.signature(self.project, "小云", ["specs"]), specs)
        self.assertEqual(product_contract.signature(self.project, "小雨"), rain)

    def test_record_binds_real_image_bytes_to_subject_spec_view_and_source_without_approval(self):
        spec_id, _ = product_contract.spec_for(self.project, "小云")
        files = self.files()
        outer = {key: copy.deepcopy(value) for key, value in self.project.items() if key != "product_contract"}
        asset = self.record(spec_id=spec_id)
        self.assertEqual(asset["subject_id"], self.subjects["小云"])
        self.assertEqual(asset["spec_id"], spec_id)
        self.assertEqual(asset["view"], "front")
        self.assertEqual(asset["path"], "fixtures/front.png")
        self.assertEqual(asset["sha256"], hashlib.sha256((self.root / asset["path"]).read_bytes()).hexdigest())
        self.assertEqual(len(asset["spec_signature"]), 64)
        self.assertEqual(asset["origin"], "reference-derived")
        self.assertEqual(asset["confirmation"]["status"], "proposed")
        self.assertEqual(product_contract.view_assets(self.project, "小云"), [])
        self.assertEqual({key: value for key, value in self.project.items() if key != "product_contract"}, outer)
        self.assertEqual(self.files(), files)

    def test_source_label_alone_never_confirms_a_view(self):
        baseline = copy.deepcopy(self.project)
        for origin in ("user-confirmed", "agent-inferred", "reference-derived"):
            with self.subTest(origin=origin):
                candidate = copy.deepcopy(baseline)
                asset = self.record(candidate, origin=origin)
                self.assertEqual(asset["origin"], origin)
                self.assertEqual(asset["confirmation"]["status"], "proposed")
                self.assertEqual(product_contract.view_assets(candidate, "小云"), [])

    def test_confirmation_preserves_original_source_and_records_separate_approval_evidence(self):
        asset = self.record()
        before = copy.deepcopy(asset)
        files = self.files()
        confirmed = self.confirm(asset)
        for field in ("id", "subject_id", "spec_id", "view", "path", "sha256", "spec_signature", "origin"):
            with self.subTest(field=field):
                self.assertEqual(confirmed[field], before[field])
        self.assertEqual(confirmed["confirmation"]["status"], "confirmed")
        self.assertEqual(confirmed["confirmation"]["origin"], "agent-inferred")
        self.assertIn("fixture", confirmed["confirmation"]["evidence"])
        visible = product_contract.view_assets(self.project, "小云", "front")
        self.assertEqual([row["id"] for row in visible], [asset["id"]])
        self.assertEqual(product_contract.view_assets(self.project, "小云", "back"), [])
        self.assertEqual(product_contract.view_assets(self.project, "小雨"), [])
        visible[0]["confirmation"]["evidence"] = "changed result copy"
        self.assertEqual(asset["confirmation"], confirmed["confirmation"])
        self.assertIn("fixture", asset["confirmation"]["evidence"])
        self.assertEqual(self.project["selections"], {})
        self.assertEqual(self.project["finals"], [])
        self.assertEqual(self.project["reviews"], {"fixture": "No real visual review has been performed."})
        self.assertEqual(self.files(), files)

    def test_same_file_in_different_subjects_or_views_cannot_overwrite_another_binding(self):
        front = self.record()
        back = self.record(view="back")
        rain = self.record(subject="小雨")
        self.assertEqual(len({front["id"], back["id"], rain["id"]}), 3)
        self.assertEqual(front["sha256"], rain["sha256"])
        self.assertEqual(len(self.project["product_contract"]["view_assets"]), 3)
        self.confirm(front)
        self.assertEqual(product_contract.view_assets(self.project, "小雨"), [])

    def test_record_rejects_invalid_view_origin_missing_external_and_absolute_paths_without_writes(self):
        variants = [
            {"view": "sideways"}, {"origin": "generated"}, {"path": "missing.png"},
            {"path": str(self.root / "fixtures/front.png")}, {"path": "../outside.png"},
        ]
        for changes in variants:
            with self.subTest(changes=changes):
                self.assert_rejected_without_writes(lambda: self.record(**changes))

    def test_record_rejects_symlink_escape_and_nonimage_files_without_writes(self):
        outside = self.root.parent / (self.root.name + "-external.png")
        fixture_png(outside)
        self.addCleanup(outside.unlink)
        (self.root / "fixtures/escape.png").symlink_to(outside)
        self.assert_rejected_without_writes(lambda: self.record(path="fixtures/escape.png"))
        (self.root / "fixtures/not-image.png").write_text("fixture only, not a PNG", encoding="utf-8")
        self.assert_rejected_without_writes(
            lambda: self.record(path="fixtures/not-image.png"), error=UnidentifiedImageError,
        )

    def test_record_rejects_unknown_subject_and_foreign_spec_without_writes(self):
        rain_spec, _ = product_contract.spec_for(self.project, "小雨")
        self.assert_rejected_without_writes(lambda: self.record(subject="不存在"))
        self.assert_rejected_without_writes(lambda: self.record(spec_id=rain_spec))

    def test_failed_record_does_not_create_a_contract_as_a_side_effect(self):
        self.project.pop("product_contract")
        self.assert_rejected_without_writes(lambda: self.record(path="missing.png"))

    def test_confirm_rejects_unknown_assets_blank_evidence_and_invalid_approval_source_without_writes(self):
        asset = self.record()
        for changes in ({"origin": "generated"}, {"evidence": ""}, {"evidence": " \n\t"}):
            with self.subTest(changes=changes):
                self.assert_rejected_without_writes(lambda: self.confirm(asset, **changes))
        self.assert_rejected_without_writes(lambda: product_contract.confirm_view(
            self.root, self.project, "unknown-asset", "agent-inferred", "fixture-only receipt",
        ))

    def test_confirm_rejects_changed_image_bytes_even_when_the_file_is_still_a_valid_png(self):
        asset = self.record()
        fixture_png(self.root / asset["path"], "lightgreen")
        self.assertNotEqual(hashlib.sha256((self.root / asset["path"]).read_bytes()).hexdigest(), asset["sha256"])
        self.assert_rejected_without_writes(lambda: self.confirm(asset))

    def test_confirm_rejects_deleted_image_and_mismatched_recorded_hash_without_writes(self):
        asset = self.record()
        original = copy.deepcopy(self.project)
        asset["sha256"] = "0" * 64
        self.assert_rejected_without_writes(lambda: self.confirm(asset))
        self.project = original
        (self.root / asset["path"]).unlink()
        self.assert_rejected_without_writes(lambda: self.confirm(asset))

    def test_confirm_rejects_path_escape_even_when_external_bytes_match(self):
        asset = self.record()
        outside = self.root.parent / (self.root.name + "-external.png")
        outside.write_bytes((self.root / asset["path"]).read_bytes())
        self.addCleanup(outside.unlink)
        (self.root / asset["path"]).unlink()
        (self.root / asset["path"]).symlink_to(outside)
        self.assert_rejected_without_writes(lambda: self.confirm(asset))

    def test_changed_own_spec_invalidates_a_confirmed_view_and_refuses_stale_reconfirmation(self):
        asset = self.record()
        self.confirm(asset)
        product_contract.set_spec(self.project, "小云", {"size": numeric_size()})
        self.assertEqual(product_contract.view_assets(self.project, "小云"), [])
        self.assert_rejected_without_writes(lambda: self.confirm(asset))
        fresh = self.record()
        self.assertNotEqual(fresh["spec_signature"], asset["spec_signature"])
        self.assertEqual(fresh["confirmation"]["status"], "proposed")
        self.confirm(fresh)
        self.assertEqual(len(product_contract.view_assets(self.project, "小云")), 1)

    def test_another_subjects_spec_change_does_not_invalidate_or_block_this_view(self):
        asset = self.record()
        product_contract.set_spec(self.project, "小雨", {"size": numeric_size(height=20)})
        self.confirm(asset)
        self.assertEqual([row["id"] for row in product_contract.view_assets(self.project, "小云")], [asset["id"]])

    def test_confirm_rejects_mismatched_spec_signature_without_writes(self):
        asset = self.record()
        asset["spec_signature"] = "0" * 64
        self.assert_rejected_without_writes(lambda: self.confirm(asset))

    def test_contract_rejects_a_view_whose_subject_and_spec_belong_to_different_products(self):
        asset = self.record()
        asset["subject_id"] = self.subjects["小雨"]
        self.assert_rejected_without_writes(lambda: product_contract.validate_contract(
            self.project["product_contract"],
        ))

    def test_confirm_rejects_a_view_whose_subject_and_spec_belong_to_different_products(self):
        asset = self.record()
        asset["subject_id"] = self.subjects["小雨"]
        self.assert_rejected_without_writes(lambda: self.confirm(asset))


if __name__ == "__main__":
    unittest.main()
