from __future__ import annotations

import copy
import hashlib
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import scene_plan


class ScenePlanTests(unittest.TestCase):
    def setUp(self):
        self.project = {
            "schema_version": 3, "revision": 17,
            "answers": {"product_form": "plush", "subjects": ["小云"], "delivery": "full-post"},
            "product_spec": {"usage": "桌面摆件，也能挂包", "scale": {"target": "掌心大小", "status": "user-confirmed"}},
            "brief": {"bible": {"shape": "圆头短身", "eyes": "圆眼睛"}},
            "selections": {}, "finals": [], "feedback": [],
        }
        self.base = [{"id": "cover", "role": "cover"}, {"id": "scale", "role": "scale"},
                     {"id": "detail", "role": "detail"}, {"id": "angle", "role": "angle"}]
        self.project["scene_plan"] = scene_plan.default_plan(self.project, self.base)
        self.front_hash = hashlib.sha256(b"confirmed front image").hexdigest()
        self.angle_hash = hashlib.sha256(b"confirmed angled image").hexdigest()

    def slot(self, identifier="back-new", view="back", **values):
        return {"id": identifier, "role": "angle", "subject": "小云", "spec_ref": "/product_spec",
                "view": view, **values}

    def patch(self, operation="append", **values):
        patch = {"schema_version": 1, "op": operation, "scope": {"slots": ["back-new"]}}
        patch.update({"slots": [self.slot()]} if operation == "append" else {})
        patch.update(values)
        return patch

    def apply(self, patch, **preconditions):
        preconditions.setdefault("expected_revision", self.project["scene_plan"]["revision"])
        return scene_plan.apply_patch(self.project, patch, **preconditions)

    def test_default_projects_old_slots_without_changing_inputs_or_requiring_three_views(self):
        original = copy.deepcopy(self.project)
        legacy = copy.deepcopy(self.project)
        legacy.pop("scene_plan")
        plan = scene_plan.default_plan(legacy, self.base)
        self.assertEqual([slot["id"] for slot in plan["slots"]], [slot["id"] for slot in self.base])
        self.assertEqual(plan["coverage"], {"required_views": [], "preserve_slots": []})
        self.assertEqual(plan["slots"][2]["view"], "detail")
        self.assertEqual(plan["slots"][3]["view"], "three-quarter")
        self.assertEqual(self.project, original)
        self.assertNotIn("view", self.base[0])

    def test_existing_default_is_stable_and_does_not_renumber_ids(self):
        expected = copy.deepcopy(self.project["scene_plan"])
        self.assertEqual(scene_plan.default_plan(self.project, list(reversed(self.base))), expected)
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "existing_plan"):
            scene_plan.default_plan(self.project, self.base, coverage={"required_views": ["front"]})

    def test_legacy_refs_keep_their_types_and_do_not_need_product_spec(self):
        legacy = copy.deepcopy(self.project)
        legacy.pop("scene_plan")
        legacy.pop("product_spec")
        bases = [{"id": "cover", "role": "cover", "refs": ["cutout.png"]}]
        plan = scene_plan.default_plan(legacy, bases)
        self.assertEqual(plan["slots"][0]["refs"], ["cutout.png"])
        self.assertIsNone(plan["slots"][0]["spec_ref"])
        self.assertEqual(plan["slots"][0]["reference_bindings"], [])

    def test_validate_returns_independent_normalized_json(self):
        plan = scene_plan.validate_plan(self.project["scene_plan"], self.project)
        plan["slots"][0]["keep"].append("圆眼睛")
        self.assertEqual(self.project["scene_plan"]["slots"][0]["keep"], [])
        self.assertEqual(scene_plan.plan_hash(self.project["scene_plan"]),
                         scene_plan.plan_hash(copy.deepcopy(self.project["scene_plan"])))

    def test_invalid_fields_schemas_ids_views_and_ranges_are_rejected(self):
        mutations = [
            ("schema_version", True), ("schema_version", 2), ("revision", True), ("revision", -1),
        ]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                plan = copy.deepcopy(self.project["scene_plan"])
                plan[field] = value
                with self.assertRaises(scene_plan.ScenePlanError):
                    scene_plan.validate_plan(plan)
        for field, value in (("view", "45"), ("id", "../cover"), ("yaw", 181), ("yaw", True),
                             ("elevation", -91), ("elevation", float("nan")), ("yaw", float("inf")),
                             ("yaw", 10 ** 500),
                             ("reference_bindings", "a.png"), ("keep", ["same", "same"])):
            with self.subTest(field=field, value=value):
                plan = copy.deepcopy(self.project["scene_plan"])
                plan["slots"][0][field] = value
                with self.assertRaises(scene_plan.ScenePlanError):
                    scene_plan.validate_plan(plan)

    def test_duplicate_slots_and_unknown_preserve_are_rejected(self):
        plan = copy.deepcopy(self.project["scene_plan"])
        plan["slots"].append(copy.deepcopy(plan["slots"][0]))
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "duplicate_slot"):
            scene_plan.validate_plan(plan)
        plan = copy.deepcopy(self.project["scene_plan"])
        plan["coverage"]["preserve_slots"] = ["missing"]
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "unknown_preserved"):
            scene_plan.validate_plan(plan)

    def test_coverage_is_only_plan_fact_not_pixel_evidence(self):
        plan = self.project["scene_plan"]
        plan["coverage"]["required_views"] = ["front", "back"]
        status = scene_plan.coverage_status(plan)
        self.assertFalse(status["complete"])
        self.assertEqual(status["missing_views"], ["back"])
        updated, impact = self.apply(self.patch())
        self.assertTrue(impact["coverage"]["complete"])
        self.assertFalse(impact["coverage"]["visual_verified"])
        self.assertEqual(updated["finals"], [])

    def test_append_preserves_front_finals_selections_and_original_bytes_declarations(self):
        self.project["scene_plan"]["coverage"]["preserve_slots"] = ["cover"]
        self.project["finals"] = [{"slot": "cover", "path": "old/front.png", "sha256": self.front_hash}]
        self.project["selections"] = {"scene": {"_set": "old-round"}}
        patch = self.patch(baseline_hashes={"cover": self.front_hash})
        before = copy.deepcopy(self.project)
        original_patch = copy.deepcopy(patch)
        updated, impact = self.apply(patch)
        self.assertEqual(self.project, before)
        self.assertEqual(patch, original_patch)
        self.assertEqual(updated["finals"], before["finals"])
        self.assertEqual(updated["selections"], before["selections"])
        self.assertEqual(updated["revision"], 17)
        self.assertEqual(updated["scene_plan"]["revision"], 1)
        self.assertEqual(impact["added_slots"], ["back-new"])
        self.assertEqual(impact["invalidated_slots"], [])
        self.assertEqual(impact["preserved_hashes"]["cover"], self.front_hash)
        updated["finals"][0]["path"] = "different.png"
        self.assertEqual(self.project, before)

    def test_append_many_is_atomic_and_never_replaces_an_existing_id(self):
        patch = self.patch(slots=[self.slot(), self.slot("side-new", "left")],
                           scope={"slots": ["back-new", "side-new"]})
        updated, _ = self.apply(patch)
        self.assertEqual(len(updated["scene_plan"]["slots"]), 6)
        for additions in ([self.slot("cover")], [self.slot(), self.slot()]):
            with self.subTest(additions=additions):
                with self.assertRaisesRegex(scene_plan.ScenePlanError, "existing_slot_id"):
                    self.apply(self.patch(slots=additions))

    def test_full_grid_rejects_append_instead_of_replacing_or_dropping(self):
        self.project["answers"]["delivery"] = "grid-9"
        self.project["scene_plan"]["slots"].extend([
            self.slot("extra-" + str(number), "front") for number in range(5)
        ])
        before = copy.deepcopy(self.project)
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "grid_capacity"):
            self.apply(self.patch())
        self.assertEqual(self.project, before)

    def test_grid_allows_append_only_up_to_capacity(self):
        self.project["answers"]["delivery"] = "grid-9"
        updated, _ = self.apply(self.patch())
        self.assertEqual(len(updated["scene_plan"]["slots"]), 5)

    def test_edit_changes_only_target_and_never_discards_finals(self):
        patch = self.patch("edit", slot_id="angle", changes={"view": "left", "yaw": -90},
                           scope={"slots": ["angle"], "subjects": ["小云"]})
        before = copy.deepcopy(self.project)
        updated, impact = self.apply(patch)
        self.assertEqual(updated["scene_plan"]["slots"][:3], before["scene_plan"]["slots"][:3])
        self.assertEqual(updated["scene_plan"]["slots"][3]["view"], "left")
        self.assertEqual(impact["invalidated_slots"], ["angle"])
        self.assertTrue(impact["sequence_changed"])
        self.assertIn("cover", impact["preserved_slots"])
        self.assertEqual(self.project, before)

    def test_missing_target_scope_or_whole_plan_overwrite_is_refused(self):
        patches = [
            self.patch("edit", changes={"view": "back"}),
            self.patch("edit", slot_id="angle", changes={"view": "back"}, scope={"slots": []}),
            self.patch("edit", slot_id="angle", changes={"id": "replacement"}, scope={"slots": ["angle"]}),
            self.patch("edit", slot_id="angle", changes={"view": "back"}, slots=[]),
            self.patch("replace", slot_id="missing", slot=self.slot("missing")),
        ]
        for patch in patches:
            with self.subTest(patch=patch):
                before = copy.deepcopy(self.project)
                with self.assertRaises(scene_plan.ScenePlanError):
                    self.apply(patch)
                self.assertEqual(self.project, before)

    def test_replace_keeps_id_and_leaves_other_slots_untouched(self):
        patch = self.patch("replace", slot_id="angle", slot=self.slot("angle", "right"),
                           scope={"slots": ["angle"]})
        updated, impact = self.apply(patch)
        self.assertEqual(updated["scene_plan"]["slots"][3]["id"], "angle")
        self.assertEqual(updated["scene_plan"]["slots"][3]["view"], "right")
        self.assertEqual(impact["changed_slots"], ["angle"])
        patch["slot"]["id"] = "new-id"
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "immutable_slot_id"):
            self.apply(patch)

    def test_scope_must_cover_original_and_new_subjects(self):
        patch = self.patch("edit", slot_id="angle", changes={"subject": "雨滴"},
                           scope={"slots": ["angle"], "subjects": ["雨滴"]})
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "subject_scope_mismatch"):
            self.apply(patch)

    def test_preconditions_check_plan_revision_and_hash_not_outer_revision(self):
        patch = self.patch()
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "missing_precondition"):
            scene_plan.apply_patch(self.project, patch)
        for invalid in (17, True, -1):
            with self.subTest(revision=invalid):
                with self.assertRaisesRegex(scene_plan.ScenePlanError, "stale_plan_revision"):
                    scene_plan.apply_patch(self.project, patch, expected_revision=invalid)
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "stale_plan_hash"):
            self.apply(patch, expected_hash="0" * 64)
        updated, _ = scene_plan.apply_patch(self.project, patch,
                                            expected_hash=scene_plan.plan_hash(self.project["scene_plan"]))
        self.assertEqual(updated["scene_plan"]["revision"], 1)
        patch["expected_revision"] = 5
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "conflicting_expected_revision"):
            self.apply(patch)

    def test_declared_baseline_change_is_rejected_without_mutating_project(self):
        self.project["finals"] = [{"slot": "angle", "path": "angle.png", "sha256": self.angle_hash}]
        patch = self.patch("edit", slot_id="angle", changes={"view": "back"},
                           scope={"slots": ["angle"]}, baseline_hashes={"angle": "0" * 64})
        before = copy.deepcopy(self.project)
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "stale_baseline_hash"):
            self.apply(patch)
        self.assertEqual(self.project, before)
        patch["baseline_hashes"] = {"angle": self.angle_hash}
        updated, impact = self.apply(patch)
        self.assertEqual(updated["finals"], before["finals"])
        self.assertEqual(impact["baseline_evidence"], "caller-declared-digests")

    def test_conflicting_final_digests_cannot_choose_an_arbitrary_baseline(self):
        self.project["finals"] = [{"slot": "angle", "sha256": self.angle_hash},
                                  {"slot": "angle", "sha256": self.front_hash}]
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "conflicting_baseline_hash"):
            self.apply(self.patch())

    def test_legacy_final_requires_host_hash_snapshot_and_explicit_declaration(self):
        self.project["finals"] = [{"slot": "angle", "path": "legacy-angle.png"}]
        patch = self.patch("edit", slot_id="angle", changes={"view": "left"}, scope={"slots": ["angle"]})
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "missing_baseline_hash"):
            self.apply(patch)
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "missing_baseline_declaration"):
            self.apply(patch, baseline_hashes={"angle": self.angle_hash})
        patch["baseline_hashes"] = {"angle": self.angle_hash}
        updated, _ = self.apply(patch, baseline_hashes={"angle": self.angle_hash})
        self.assertEqual(updated["finals"], self.project["finals"])

    def test_preserve_and_keep_cannot_be_silently_removed(self):
        self.project["scene_plan"]["coverage"]["preserve_slots"] = ["cover"]
        patch = self.patch("edit", slot_id="cover", changes={"view": "back"}, scope={"slots": ["cover"]})
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "preserved_slot_conflict"):
            self.apply(patch)
        self.project["scene_plan"]["slots"][3]["keep"] = ["保留绿色"]
        patch = self.patch("replace", slot_id="angle", slot=self.slot("angle"), scope={"slots": ["angle"]})
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "keep_conflict"):
            self.apply(patch)

    def test_required_front_cannot_disappear_even_when_not_explicitly_preserved(self):
        for slot in self.project["scene_plan"]["slots"]:
            slot["view"] = "three-quarter"
        self.project["scene_plan"]["slots"][0]["view"] = "front"
        self.project["scene_plan"]["coverage"]["required_views"] = ["front", "back"]
        patch = self.patch("edit", slot_id="cover", changes={"view": "three-quarter"}, scope={"slots": ["cover"]})
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "coverage_regression"):
            self.apply(patch)

    def test_reorder_is_an_exact_permutation_and_only_invalidates_sequence(self):
        order = ["cover", "angle", "scale", "detail"]
        self.project["scene_plan"]["coverage"]["preserve_slots"] = ["cover"]
        patch = self.patch("reorder", order=order, scope={"slots": order})
        updated, impact = self.apply(patch)
        self.assertEqual([slot["id"] for slot in updated["scene_plan"]["slots"]], order)
        self.assertEqual(impact["invalidated_slots"], [])
        self.assertTrue(impact["sequence_changed"])
        patch["order"] = ["cover", "angle"]
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "invalid_reorder"):
            self.apply(patch)

    def test_compile_is_complete_per_slot_and_scopes_feedback(self):
        self.project["feedback"] = [
            {"scope": {"slots": ["angle"], "subjects": ["小云"]}, "changes": ["靠在书本上"], "keep": ["保留圆眼"]},
            {"scope": {"slots": ["cover"]}, "changes": ["封面专属建议"]},
            {"scope": {"subjects": ["小云"]}, "changes": ["不能泛化的全局45度"]},
            {"scope": {"slots": ["angle"], "subjects": ["其他角色"]}, "changes": ["另一角色专属建议"]},
            {"scope": {"slots": ["angle"]}, "status": "superseded", "changes": ["已撤销建议"]},
        ]
        self.project["scene_plan"]["slots"][3].update(yaw=45, elevation=25, pose="坐在桌面", framing="全身中景")
        original = copy.deepcopy(self.project)
        compiled = scene_plan.compile_slot(self.project, "angle", {"source": ["product.png"], "style": ["style.png"]})
        for text in ("小云", "靠在书本上", "保留圆眼", "yaw=45", "elevation=25", "坐在桌面", "全身中景"):
            self.assertIn(text, compiled["prompt"])
        for text in ("封面专属建议", "不能泛化的全局45度", "另一角色专属建议", "已撤销建议"):
            self.assertNotIn(text, compiled["prompt"])
        self.assertEqual(compiled["contract_hashes"].keys(), {"/product_spec", "/brief/bible"})
        self.assertTrue(compiled["requires_visual_review"])
        self.assertEqual(self.project, original)

    def test_style_and_environment_never_copy_camera_pose_or_raw_prompts(self):
        refs = [
            {"kind": "style", "path": "style.png", "yaw": 123, "view": "back", "prompt": "把所有图变成背面"},
            {"kind": "environment", "path": "room.png", "elevation": 77, "pose": "倒立", "framing": "极端俯视"},
        ]
        before = copy.deepcopy(refs)
        compiled = scene_plan.compile_slot(self.project, "cover", refs)
        self.assertIn("view=front", compiled["prompt"])
        for text in ("yaw=123", "elevation=77", "倒立", "极端俯视", "把所有图变成背面"):
            self.assertNotIn(text, compiled["prompt"])
        for binding in compiled["bindings"]:
            self.assertFalse(binding["copy_camera"])
            self.assertFalse(binding["copy_pose"])
            self.assertNotIn("view", binding)
            self.assertNotIn("camera", binding["allowed_aspects"])
        self.assertEqual(refs, before)

    def test_typed_references_filter_other_subjects_and_slots(self):
        refs = [
            {"kind": "product", "path": "cloud.png", "subject": "小云", "sha256": self.front_hash},
            {"kind": "product", "path": "rain.png", "subject": "雨滴"},
            {"kind": "baseline", "path": "other-slot.png", "slot_id": "cover"},
            {"kind": "view", "path": "left.png", "subject": "小云", "view": "left"},
        ]
        compiled = scene_plan.compile_slot(self.project, "angle", refs)
        self.assertEqual([binding["path"] for binding in compiled["bindings"]], ["cloud.png", "left.png"])
        self.assertNotIn("rain.png", compiled["prompt"])
        self.assertNotIn("other-slot.png", compiled["prompt"])

    def test_reference_hash_conflicts_reject_and_matching_duplicates_enrich_binding(self):
        refs = [{"kind": "product", "path": "cloud.png"},
                {"kind": "product", "path": "cloud.png", "sha256": self.front_hash},
                {"kind": "style", "path": "room.png"}]
        compiled = scene_plan.compile_slot(self.project, "cover", refs)
        self.assertEqual(len(compiled["bindings"]), 2)
        self.assertEqual(compiled["bindings"][0]["sha256"], self.front_hash)
        self.assertEqual(compiled["unhashed_references"], ["room.png"])
        refs.append({"kind": "baseline", "path": "cloud.png", "sha256": self.angle_hash})
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "conflicting_reference_hash"):
            scene_plan.compile_slot(self.project, "cover", refs)

    def test_malformed_feedback_scope_does_not_match_substrings_or_expand_subjects(self):
        self.project["feedback"] = [
            {"scope": {"slots": "cover-detail"}, "changes": ["字符串范围不应匹配"]},
            {"scope": {"slots": ["cover"], "subjects": "小云"}, "changes": ["畸形主体范围"]},
            {"scope": {"slots": ["cover"]}, "changes": ["畸形保持项"], "keep": "圆眼"},
        ]
        compiled = scene_plan.compile_slot(self.project, "cover", [])
        for text in ("字符串范围不应匹配", "畸形主体范围", "畸形保持项"):
            self.assertNotIn(text, compiled["prompt"])

    def test_binding_normalization_is_idempotent(self):
        plan = self.project["scene_plan"]
        plan["slots"][0]["reference_bindings"] = [{"kind": "product", "path": "product.png"}]
        normalized = scene_plan.validate_plan(plan)
        self.assertEqual(scene_plan.validate_plan(normalized), normalized)
        self.assertEqual(scene_plan.plan_hash(normalized), scene_plan.plan_hash(plan))

    def test_legacy_refs_are_not_mutated_and_scene_style_only_supplies_environment(self):
        self.project["selections"] = {"cutout": {"小云": {"image": "cloud.png"}},
                                      "scene": {"_": {"image": "old-style.png"}}}
        compiled = scene_plan.compile_slot(self.project, "cover")
        self.assertEqual([binding["kind"] for binding in compiled["bindings"]], ["product", "environment"])
        legacy_refs = {"source": ["identity.png"], "style": ["style.png"], "based_on": ["baseline.png"]}
        before = copy.deepcopy(legacy_refs)
        compiled = scene_plan.compile_slot(self.project, "angle", legacy_refs)
        self.assertEqual([binding["kind"] for binding in compiled["bindings"]], ["product", "style", "baseline"])
        self.assertEqual(legacy_refs, before)

    def test_slot_signature_is_not_invalidated_by_unrelated_append_or_feedback(self):
        compiled = scene_plan.compile_slot(self.project, "cover", [])
        updated, _ = self.apply(self.patch())
        updated["feedback"] = [{"scope": {"slots": ["back-new"]}, "changes": ["背面改线"]}]
        self.assertEqual(scene_plan.compile_slot(updated, "cover", []), compiled)
        updated["product_spec"]["scale"]["target"] = "书包挂件大小"
        changed = scene_plan.compile_slot(updated, "cover", [])
        self.assertNotEqual(changed["contract_hashes"], compiled["contract_hashes"])
        self.assertNotEqual(changed["signature"], compiled["signature"])

    def test_subject_bibles_and_json_pointer_contracts_are_scoped(self):
        self.project["brief"]["bible"] = {"subjects": {"小云": {"eyes": "云的圆眼"}, "雨滴": {"eyes": "雨的方眼"}},
                                          "shared": {"material": "短绒"}}
        self.project["specs"] = {"cloud/one": {"usage": "仅作摆件"}}
        self.project["scene_plan"]["slots"][0]["spec_ref"] = "/specs/cloud~1one"
        compiled = scene_plan.compile_slot(self.project, "cover", [])
        self.assertIn("云的圆眼", compiled["prompt"])
        self.assertNotIn("雨的方眼", compiled["prompt"])
        self.assertIn("/specs/cloud~1one", compiled["contract_hashes"])

    def test_missing_contract_or_slot_cannot_silently_fall_back_to_full_project(self):
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "unknown_target_slot"):
            scene_plan.compile_slot(self.project, "missing")
        self.project["scene_plan"]["slots"][0]["spec_ref"] = "/missing/contract"
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "missing_spec_ref"):
            scene_plan.compile_slot(self.project, "cover")


class ContractScenePlanTests(unittest.TestCase):
    def setUp(self):
        self.project = {
            "answers": {"subjects": ["小云", "雨滴"], "delivery": "full-post"},
            "product_spec": {"usage": "旧约定不应覆盖生产合同"},
            "product_contract": {
                "subjects": {"cloud": {"name": "小云", "active": True},
                             "rain": {"name": "雨滴", "active": True}},
                "specs": {"cloud-small": {"subject_id": "cloud", "structure": {"shape": "圆头短身"}},
                          "rain-small": {"subject_id": "rain", "structure": {"shape": "水滴轮廓"}},
                          "cloud-large": {"subject_id": "cloud", "structure": {"shape": "大号圆头"}}},
                "view_assets": {},
            },
            "selections": {"cutout": {"小云": {"image": "cloud-cutout.png"}}},
            "brief": {"bible": {"subjects": {"小云": {"eyes": "云的圆眼"},
                                           "雨滴": {"eyes": "雨的方眼"}}}},
            "feedback": [], "finals": [],
        }
        self.project["scene_plan"] = scene_plan.default_plan(self.project, [
            {"id": "cover", "role": "cover"},
            {"id": "cloud", "role": "character", "subject": "小云"},
            {"id": "rain", "role": "character", "subject": "rain"},
            {"id": "angle", "role": "angle", "subject": "cloud"},
        ])

    def asset(self, identifier, *, subject="cloud", spec="cloud-small", view="front",
              status="confirmed", **values):
        asset = {"subject_id": subject, "spec_id": spec, "view": view,
                 "path": identifier + ".png", "sha256": hashlib.sha256(identifier.encode()).hexdigest(),
                 "origin": "reference-derived", "confirmation": {"status": status, "evidence": "用户认可"}}
        asset.update(values)
        self.project["product_contract"]["view_assets"][identifier] = asset
        return asset

    def test_default_maps_names_and_stable_ids_to_production_spec_pointers(self):
        slots = self.project["scene_plan"]["slots"]
        self.assertEqual(slots[1]["subject"], "cloud")
        self.assertEqual(slots[1]["spec_ref"], "/product_contract/specs/cloud-small")
        self.assertEqual(slots[2]["subject"], "rain")
        self.assertEqual(slots[2]["spec_ref"], "/product_contract/specs/rain-small")
        self.assertEqual(slots[3]["view"], "three-quarter")
        compiled = scene_plan.compile_slot(self.project, "cloud", [])
        self.assertIn("/product_contract/specs/cloud-small", compiled["contract_hashes"])
        self.assertNotIn("/product_spec", compiled["contract_hashes"])
        self.assertIn("云的圆眼", compiled["prompt"])
        self.assertNotIn("雨的方眼", compiled["prompt"])

    def test_group_spec_refs_cover_actual_subjects_and_subset_uses_only_its_subject(self):
        group = self.project["scene_plan"]["slots"][0]
        self.assertEqual(group["subjects"], ["cloud", "rain"])
        self.assertEqual(group["spec_refs"], ["/product_contract/specs/cloud-small",
                                             "/product_contract/specs/rain-small"])
        self.assertIsNone(group["spec_ref"])
        project = copy.deepcopy(self.project)
        project.pop("scene_plan")
        before = copy.deepcopy(project)
        base = [{"id": "subset", "role": "group", "subjects": ["雨滴"]}]
        plan = scene_plan.default_plan(project, base)
        self.assertEqual(plan["slots"][0]["subjects"], ["rain"])
        self.assertEqual(plan["slots"][0]["spec_ref"], "/product_contract/specs/rain-small")
        self.assertEqual(project, before)
        self.assertEqual(base[0]["subjects"], ["雨滴"])

    def test_subject_resolution_accepts_names_ids_and_active_contract_fallback(self):
        for subject in ("小云", "cloud"):
            with self.subTest(subject=subject):
                self.assertEqual(scene_plan._subjects(self.project, {"subject": subject}), ["cloud"])
        self.assertEqual(scene_plan._subjects(self.project, {"subjects": ["小云", "cloud", "雨滴"]}),
                         ["cloud", "rain"])
        self.project["answers"]["subjects"] = []
        self.project["product_contract"]["subjects"]["old"] = {"name": "旧角色", "active": False}
        self.assertEqual(scene_plan._subjects(self.project, {}), ["cloud", "rain"])
        for subject in ("unknown", "old"):
            with self.subTest(subject=subject):
                with self.assertRaisesRegex(scene_plan.ScenePlanError, "unknown_subject"):
                    scene_plan._subjects(self.project, {"subject": subject})

    def test_explicit_variant_and_escaped_spec_ids_are_preserved(self):
        project = copy.deepcopy(self.project)
        project.pop("scene_plan")
        project["product_contract"]["specs"]["cloud/mini~one"] = {"subject_id": "cloud"}
        pointer = "/product_contract/specs/cloud~1mini~0one"
        project["scene_plan"] = scene_plan.default_plan(project, [
            {"id": "mini", "role": "cover", "subject": "小云", "spec_ref": pointer},
        ])
        self.asset("unused")
        project["product_contract"]["view_assets"]["mini"] = {
            "subject_id": "cloud", "spec_id": "cloud/mini~one", "view": "front",
            "path": "mini.png", "sha256": "a" * 64, "origin": "user-confirmed",
            "confirmation": {"status": "confirmed"},
        }
        compiled = scene_plan.compile_slot(project, "mini", [])
        self.assertEqual(list(compiled["contract_hashes"]), [pointer, "/brief/bible/subjects/小云"])
        self.assertEqual([binding["path"] for binding in compiled["bindings"]], ["mini.png"])

    def test_wrong_subject_spec_and_missing_subject_spec_fail_without_legacy_fallback(self):
        project = copy.deepcopy(self.project)
        project.pop("scene_plan")
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "subject_spec_mismatch"):
            scene_plan.default_plan(project, [
                {"id": "wrong", "role": "cover", "subject": "cloud",
                 "spec_ref": "/product_contract/specs/rain-small"},
            ])
        del project["product_contract"]["specs"]["rain-small"]
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "missing_subject_spec"):
            scene_plan.default_plan(project, [{"id": "rain", "role": "character", "subject": "雨滴"}])

    def test_compile_prefers_production_contract_for_existing_legacy_slot_pointer(self):
        self.project["scene_plan"]["slots"][1]["spec_ref"] = "/product_spec"
        compiled = scene_plan.compile_slot(self.project, "cloud", [])
        self.assertIn("/product_contract/specs/cloud-small", compiled["contract_hashes"])
        self.assertNotIn("/product_spec", compiled["contract_hashes"])

    def test_compile_rejects_explicit_spec_ref_for_another_subject(self):
        slot = self.project["scene_plan"]["slots"][1]
        for fields in ({"spec_ref": "/product_contract/specs/rain-small"},
                       {"spec_refs": ["/product_contract/specs/rain-small"]}):
            with self.subTest(fields=fields):
                project = copy.deepcopy(self.project)
                project["scene_plan"]["slots"][1] = {**slot, **fields}
                with self.assertRaisesRegex(scene_plan.ScenePlanError, "subject_spec_mismatch"):
                    scene_plan.compile_slot(project, "cloud", [])

    def test_compile_uses_only_explicit_nondefault_variant_and_its_view(self):
        self.project["scene_plan"]["slots"][1]["spec_ref"] = "/product_contract/specs/cloud-large"
        self.asset("small-front")
        self.asset("large-front", spec="cloud-large")
        compiled = scene_plan.compile_slot(self.project, "cloud", [])
        self.assertIn("/product_contract/specs/cloud-large", compiled["contract_hashes"])
        self.assertNotIn("/product_contract/specs/cloud-small", compiled["contract_hashes"])
        self.assertEqual([binding["path"] for binding in compiled["bindings"]], ["large-front.png"])

    def test_only_confirmed_subject_spec_and_target_view_assets_are_attached(self):
        accepted = self.asset("cloud-front")
        self.asset("wrong-subject", subject="rain", spec="rain-small")
        self.asset("wrong-spec", spec="cloud-large")
        self.asset("wrong-pair", subject="rain")
        self.asset("wrong-view", view="back")
        self.asset("proposed", status="proposed")
        self.asset("unknown", status="unknown")
        self.asset("unconfirmed", confirmation={})
        self.asset("stale", spec_signature="0" * 64)
        before = copy.deepcopy(self.project)
        compiled = scene_plan.compile_slot(self.project, "cloud")
        self.assertEqual([binding["path"] for binding in compiled["bindings"]],
                         ["cloud-cutout.png", "cloud-front.png"])
        binding = compiled["bindings"][1]
        self.assertEqual(binding["subject"], "cloud")
        self.assertEqual(binding["spec_id"], "cloud-small")
        self.assertEqual(binding["sha256"], accepted["sha256"])
        self.assertEqual(binding["confirmation"], {"status": "confirmed"})
        self.assertEqual(self.project, before)

    def test_front_identity_cutout_and_matched_back_view_can_coexist(self):
        self.asset("cloud-back", view="back")
        self.asset("cloud-front")
        self.project["scene_plan"]["slots"][1]["view"] = "back"
        compiled = scene_plan.compile_slot(self.project, "cloud", [
            {"kind": "identity", "path": "identity-front.png", "subject": "小云"},
            {"kind": "product", "path": "material-cutout.png", "subject": "cloud"},
        ])
        self.assertEqual([binding["kind"] for binding in compiled["bindings"]],
                         ["identity", "product", "view"])
        self.assertEqual([binding["path"] for binding in compiled["bindings"]],
                         ["identity-front.png", "material-cutout.png", "cloud-back.png"])
        self.assertIn("view=back", compiled["prompt"])

    def test_missing_hidden_view_rejects_front_other_subject_variant_and_proposed_evidence(self):
        self.asset("front")
        for view in ("back", "left", "right"):
            with self.subTest(view=view):
                self.project["scene_plan"]["slots"][1]["view"] = view
                self.asset("proposed-" + view, view=view, status="proposed")
                self.asset("other-" + view, subject="rain", spec="rain-small", view=view)
                self.asset("variant-" + view, spec="cloud-large", view=view)
                with self.assertRaisesRegex(scene_plan.ScenePlanError, "missing_view_reference"):
                    scene_plan.compile_slot(self.project, "cloud")
                self.asset("approved-" + view, view=view)
                compiled = scene_plan.compile_slot(self.project, "cloud")
                self.assertEqual(compiled["bindings"][-1]["view"], view)

    def test_group_requires_matching_hidden_view_for_each_actual_subject(self):
        self.project["scene_plan"]["slots"][0]["view"] = "back"
        self.asset("cloud-back", view="back")
        with self.assertRaisesRegex(scene_plan.ScenePlanError, "missing_view_reference"):
            scene_plan.compile_slot(self.project, "cover", [])
        self.asset("rain-back", subject="rain", spec="rain-small", view="back")
        compiled = scene_plan.compile_slot(self.project, "cover", [])
        self.assertEqual({binding["subject"] for binding in compiled["bindings"]}, {"cloud", "rain"})

    def test_three_quarter_can_use_confirmed_front_and_explicit_geometry(self):
        self.asset("front")
        compiled = scene_plan.compile_slot(self.project, "angle")
        self.assertIn("view=three-quarter", compiled["prompt"])
        self.assertIn("圆头短身", compiled["prompt"])
        self.assertEqual(compiled["bindings"][-1]["view"], "front")
        self.project["scene_plan"]["slots"][1]["view"] = "detail"
        compiled = scene_plan.compile_slot(self.project, "cloud", [])
        self.assertEqual(compiled["bindings"], [])

    def test_explicit_view_refs_require_matching_confirmed_subject_and_spec(self):
        self.project["scene_plan"]["slots"][1]["view"] = "back"
        base = {"kind": "view", "path": "back.png", "view": "back", "subject": "小云",
                "spec_id": "cloud-small", "confirmation": {"status": "confirmed"}}
        for changes in ({"confirmation": {"status": "proposed"}}, {"spec_id": "cloud-large"},
                        {"subject": "rain"}, {"view": "front"}, {"spec_id": None}):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(scene_plan.ScenePlanError, "missing_view_reference"):
                    scene_plan.compile_slot(self.project, "cloud", [{**base, **changes}])
        compiled = scene_plan.compile_slot(self.project, "cloud", [base])
        self.assertEqual(compiled["bindings"][0]["subject"], "cloud")

    def test_feedback_aliases_stay_within_explicit_slot_scope(self):
        self.project["feedback"] = [
            {"scope": {"slots": ["cloud"], "subjects": ["小云"]}, "changes": ["云的建议"]},
            {"scope": {"slots": ["cloud"], "subjects": ["rain"]}, "changes": ["雨的建议"]},
            {"scope": {"subjects": ["cloud"]}, "changes": ["没有图位不可传播"]},
            {"scope": {"slots": ["rain"], "subjects": ["cloud"]}, "changes": ["其他图位建议"]},
        ]
        compiled = scene_plan.compile_slot(self.project, "cloud", [])
        self.assertIn("云的建议", compiled["prompt"])
        for text in ("雨的建议", "没有图位不可传播", "其他图位建议"):
            self.assertNotIn(text, compiled["prompt"])
        for subject in ("小云", "cloud"):
            patch = {"schema_version": 1, "op": "edit", "slot_id": "cloud",
                     "changes": {"pose": "站立"}, "scope": {"slots": ["cloud"], "subjects": [subject]}}
            updated, impact = scene_plan.apply_patch(self.project, patch, expected_revision=0)
            self.assertEqual(impact["changed_slots"], ["cloud"])
            self.assertEqual(updated["scene_plan"]["slots"][0], self.project["scene_plan"]["slots"][0])

    def test_unrelated_asset_and_spec_changes_do_not_change_slot_signature(self):
        self.asset("front")
        before = scene_plan.compile_slot(self.project, "cloud")
        self.asset("rain-back", subject="rain", spec="rain-small", view="back")
        self.project["product_contract"]["specs"]["rain-small"]["structure"]["shape"] = "更圆的水滴"
        self.assertEqual(scene_plan.compile_slot(self.project, "cloud"), before)
        self.project["product_contract"]["view_assets"]["front"]["confirmation"]["status"] = "proposed"
        self.assertNotEqual(scene_plan.compile_slot(self.project, "cloud")["signature"], before["signature"])


if __name__ == "__main__":
    unittest.main()
