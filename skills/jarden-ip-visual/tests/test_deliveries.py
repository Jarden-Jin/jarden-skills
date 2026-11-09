"""Synthetic delivery fixtures exercise protocols without actual visual acceptance."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from PIL import Image, ImageDraw

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

import build_gallery
import present_round
import project
import runtime_activity
import update_runtime
import contract_fixtures


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "可搬动 项目"
        self.image = Path(self.temporary.name) / "source.png"
        picture = Image.new("RGB", (180, 240), (238, 226, 195))
        ImageDraw.Draw(picture).ellipse((38, 30, 142, 190), fill=(245, 241, 223), outline=(117, 92, 69), width=3)
        picture.save(self.image)
        self.make_current()

    def make_current(self):
        (self.root / "inputs").mkdir(parents=True)
        shutil.copyfile(self.image, self.root / "inputs" / "source.png")
        directory = self.root / "rounds" / "01-concept-s1"
        directory.mkdir(parents=True)
        for identifier in ("A", "B", "C"):
            shutil.copyfile(self.image, directory / f"{identifier}.png")
        project_state = {
            "schema_version": project.SCHEMA_VERSION,
            "project_id": "project-delivery-fixture",
            "revision": 9,
            "requirements_revision": 9,
            "project": "小米团",
            "created_at": "2026-11-08T10:00:00+08:00",
            "updated_at": "2026-11-08T10:00:00+08:00",
            "brief": {"ip_name": "小米团", "selling_points": ["柔软"], "bible": {"配色": "米白和浅棕"}, "decisions": []},
            "inputs": [{"path": "inputs/source.png", "kind": "wireframe", "original_name": "source.png"}],
            "answers": {"product_form": "plush", "subject_scope": "single", "subjects": ["小米团"],
                        "concept_count": 1, "note_type": "single-multiview", "delivery": "full-post"},
            "preferences": {"directions_per_round": 3},
            "image_backend": {"kind": "builtin", "tool": "image_gen.imagegen"},
            "stage": "concept",
            "rounds": [{"id": "01-concept-s1", "stage": "concept", "subject": "小米团", "kind": "alternatives",
                        "dir": "rounds/01-concept-s1", "status": "recorded", "missing": []}],
            "selections": {},
            "finals": [],
            "deliveries": [],
            "presentations": {},
            "pending_intents": [],
            "decision_receipts": {},
            "feedback": [],
        }
        project.atomic_json(self.root / "project.json", project_state)
        contract_fixtures.configure(self.root)
        project_state = self.data()
        round_data = {
            "schema_version": project.SCHEMA_VERSION,
            "id": "01-concept-s1",
            "stage": "concept",
            "subject": "小米团",
            "kind": "alternatives",
            "based_on": "",
            "note": "只调整毛感，保留圆眼睛与左耳缺口",
            "created_at": "2026-11-08T10:01:00+08:00",
            "requirements": project.requirement_signature(project_state, "concept", "小米团"),
            "items": [
                {"id": identifier, "title": title, "idea": idea, "prompt": prompt, "image": f"{identifier}.png", "status": "generated"}
                for identifier, title, idea, prompt in (
                    ("A", "轻短绒", "保留蓬松感，缩短表面毛长。", "圆润柔软的兔子挂件，米白与浅棕配色，圆眼睛，左耳小缺口，轻短绒材质，正面完整全身，干净背景"),
                    ("B", "短绒定型", "让缝线和五官更清楚。", "圆润柔软的兔子挂件，米白与浅棕配色，圆眼睛，左耳小缺口，短绒定型的整齐表面，正面完整全身，干净背景"),
                    ("C", "分区毛感", "身体短绒，耳缘保留长毛。", "圆润柔软的兔子挂件，米白与浅棕配色，圆眼睛，左耳小缺口，身体短绒耳缘长毛，正面完整全身，干净背景"),
                )
            ],
        }
        project.atomic_json(directory / "round.json", round_data)

    def make_legacy(self, schema):
        state = self.data()
        state["schema_version"] = schema
        state["revision"] = 0
        state.pop("product_contract", None)
        state.pop("scene_plan", None)
        if schema == 1:
            state.pop("project_id", None)
        else:
            state.setdefault("project_id", "project-delivery-fixture")
        project.atomic_json(self.root / "project.json", state)
        entry = state["rounds"][0]
        round_file = self.root / entry["dir"] / "round.json"
        data = project.read_json(round_file)
        data["schema_version"] = schema
        data["requirements"] = project.requirement_signature(state, entry["stage"], entry.get("subject"))
        project.atomic_json(round_file, data)

    def data(self):
        return project.load_project(self.root, writable=False)

    def test_maintenance_snapshot_reads_local_cache_without_network_or_project_write(self):
        update_directory = Path(self.temporary.name) / "maintenance"
        project_before = (self.root / "project.json").read_bytes()
        with mock.patch.dict(os.environ, {"JARDEN_UPDATE_HOME": str(update_directory)}), \
                mock.patch.object(update_runtime.Source, "_request", side_effect=AssertionError("页面不能联网")):
            self.assertEqual(build_gallery.maintenance_summary()["status"], "unchecked")
            self.assertFalse(update_directory.exists())
            current = update_runtime.installed_version(update_runtime.real_root())
            major, minor, patch = update_runtime.version_key(current)
            latest = f"{major}.{minor}.{patch + 1}"
            self.assertGreater(update_runtime.version_key(latest), update_runtime.version_key(current))
            update_runtime.atomic_json(update_runtime.check_state_path(), {
                "current": current,
                "latest": latest, "checked_at": time.time(), "update": True,
            })
            result = present_round.present_round(self.root, open_browser=False)
            manifest = project.read_json(Path(result["manifest"]))
            self.assertEqual(manifest["view"]["maintenance"]["status"], "available")
            self.assertEqual(manifest["view"]["maintenance"]["current"], current)
            self.assertEqual(manifest["view"]["maintenance"]["latest"], latest)
            update_runtime.atomic_json(update_runtime.failure_state_path(), {"last_failure_at": time.time() + 1})
            summary = build_gallery.maintenance_summary()
            self.assertEqual(summary["status"], "failed")
            self.assertIsNone(summary["latest"])
        after = self.data()
        before = json.loads(project_before)
        for key in before:
            if key not in {"deliveries", "presentations"}:
                self.assertEqual(after[key], before[key])

    def test_stale_maintenance_cache_and_controls_cannot_install(self):
        with mock.patch.dict(os.environ, {"JARDEN_UPDATE_HOME": str(Path(self.temporary.name) / "maintenance")}):
            current = update_runtime.installed_version(update_runtime.real_root())
            major, minor, patch = update_runtime.version_key(current)
            update_runtime.atomic_json(update_runtime.check_state_path(), {
                "current": current,
                "latest": f"{major}.{minor}.{patch + 1}", "checked_at": time.time() - update_runtime.CHECK_TTL - 1,
                "update": True,
            })
            self.assertEqual(build_gallery.maintenance_summary()["status"], "stale")
            result = present_round.present_round(self.root, open_browser=False)
            page = Path(result["gallery"]).read_text()
            self.assertIn("维护请求（尚未执行）", page)
            self.assertIn("此请求不授权安装", page)
            self.assertIn("maintenance.status !== \"available\"", page)

    def cli(self, script, *arguments):
        result = subprocess.run([sys.executable, str(SKILL / "scripts" / script), *map(str, arguments)],
                                env={**os.environ, "JARDEN_UPDATE_HOME": str(Path(self.temporary.name) / "updates"), "PYTHONDONTWRITEBYTECODE": "1"},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def question_page(self, answers, offer=None, stage="concept"):
        state = self.data()
        state["answers"] = answers
        state["rounds"] = []
        state["stage"] = stage
        project.atomic_json(self.root / "project.json", state)
        contract_fixtures.configure(self.root)
        if offer is None:
            result = present_round.present_round(self.root, open_browser=False)
        else:
            with mock.patch.object(project, "next_action", return_value=offer):
                result = present_round.present_round(self.root, open_browser=False)
        return Path(result["gallery"])

    def run_page(self, page, **scenario):
        if not shutil.which("node"):
            self.skipTest("Node.js is unavailable for the generated HTML binding regression")
        runner = r"""
const fs = require("node:fs");
const vm = require("node:vm");
const html = fs.readFileSync(process.argv[1], "utf8");
const scenario = JSON.parse(process.argv[2]);
const payload = html.match(/<script id="gallery-data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const data = JSON.parse(payload);
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
if (scripts.length !== 1) throw new Error("Expected one bound gallery script");
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.textContent = "";
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  getAttribute(key) { return this.attributes[key]; }
  addEventListener(event, handler) { this.listeners[event] = handler; }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; }
  showModal() { this.setAttribute("open", ""); }
  scrollIntoView() {}
}
const app = new Element("main");
const walk = node => [node, ...node.children.flatMap(walk)];
const visibleText = node => [node.textContent, ...node.children.map(visibleText)].join(" ");
const find = (tag, text) => walk(app).find(node => node.tagName === tag && visibleText(node).includes(text));
const storageKey = `jarden:draft:${data.project_id || data.title}:${data.delivery_id || data.snapshot_at}`;
const stored = new Map(scenario.draft ? [[storageKey, JSON.stringify(scenario.draft)]] : []);
const posts = [];
const requests = [];
let receipt = null;
const response = body => ({ ok: true, status: 200, json: async () => body });
const document = {
  activeElement: null,
  createElement: tag => new Element(tag),
  addEventListener() {},
  getElementById: id => id === "gallery-data" ? { textContent: payload } : id === "app" ? app : walk(app).find(node => node.attributes.id === id),
  querySelector: selector => selector.startsWith(".") ? walk(app).find(node => node.className === selector.slice(1)) : null,
  querySelectorAll: () => []
};
const context = {
  document,
  location: { hash: scenario.interactive ? "#token=fixture-token" : "", protocol: scenario.interactive ? "http:" : "file:" },
  localStorage: { getItem: key => stored.get(key) || null, setItem: (key, value) => stored.set(key, value) },
  URLSearchParams, Intl, Date, AbortController, setTimeout, clearTimeout,
  requestAnimationFrame: callback => callback(),
  crypto: { randomUUID: () => `generated-${data.delivery_id}` },
  fetch: async (url, options) => {
    requests.push(url);
    if (url === "/api/state") return response({ project_id: data.project_id, revision: data.project_revision, manifest: data, receipts: {} });
    if (url === "/api/decision") {
      const submitted = JSON.parse(options.body);
      posts.push(submitted);
      receipt = { committed: true, action: submitted.action, request_id: submitted.request_id,
        delivery_id: submitted.delivery_id, new_revision: data.project_revision + 1 };
      return response(receipt);
    }
    if (url.startsWith("/api/receipt/") && receipt) return response(receipt);
    throw new Error("Unexpected page request: " + url);
  }
};
context.window = context;
const settle = async () => { for (let turn = 0; turn < 30; turn++) await Promise.resolve(); };
(async () => {
  vm.runInNewContext(scripts[0][1], context, { timeout: 2000 });
  await settle();
  const question = walk(app).find(node => node.className === "question");
  const initial = {
    text: question ? visibleText(question) : "",
    selected: walk(app).filter(node => node.tagName === "INPUT" && node.attributes.name === "answer" && node.checked).map(node => JSON.parse(node.value))
  };
  if (Object.hasOwn(scenario, "choose")) {
    const radio = walk(app).find(node => node.attributes.name === "answer" && node.value === JSON.stringify(scenario.choose));
    if (!radio) throw new Error("Requested answer radio is missing");
    radio.listeners.change({ target: radio });
  }
  if (Object.hasOwn(scenario, "input")) {
    const input = walk(question).find(node => node.tagName === "INPUT" && node.attributes.type !== "radio");
    input.listeners.input({ target: { value: scenario.input } });
  }
  const draft = JSON.parse(stored.get(storageKey) || "null");
  find("BUTTON", "导出回复草稿").listeners.click();
  const exported = walk(app).find(node => node.tagName === "TEXTAREA" && node.attributes.readonly === "true").value;
  if (scenario.submit) {
    const button = find("BUTTON", "保存这个答案");
    if (!button || button.attributes.disabled === "true") throw new Error("Answer submit is unavailable");
    await button.listeners.click();
    await settle();
  }
  process.stdout.write(JSON.stringify({
    initial, text: visibleText(app), exported, draft, posts, requests,
    radios: question ? walk(question).filter(node => node.attributes.name === "answer").map(node => ({
      value: JSON.parse(node.value), label: visibleText(walk(question).find(label => label.tagName === "LABEL" && label.children.includes(node)))
    })) : [],
    inputs: question ? walk(question).filter(node => node.tagName === "INPUT" && node.attributes.type !== "radio").map(node => ({
      type: node.attributes.type, placeholder: node.attributes.placeholder, value: node.value
    })) : []
  }));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run(["node", "-e", runner, str(page), json.dumps(scenario, ensure_ascii=False)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_generated_questions_use_field_copy_instead_of_agent_instructions(self):
        cases = (
            ({}, "concept", "answers.product_form", "这次想把 IP 做成哪种周边"),
            ({"product_form": "plush"}, "concept", "answers.subject_scope", "这次想做参考图里的哪些角色"),
            ({"product_form": "plush", "subject_scope": "single"}, "concept",
             "answers.concept_count", "这次一共想做几个角色或设计"),
            ({"product_form": "plush", "subject_scope": "single", "subjects": ["小米团"], "concept_count": 1}, "scene",
             "answers.note_type", "这次想怎样展示作品"),
            ({"product_form": "plush", "subject_scope": "single", "subjects": ["小米团"], "note_type": "single-multiview", "concept_count": 1},
             "scene", "answers.delivery", "最后希望拿到哪种图片组合"),
        )
        for answers, stage, field, question in cases:
            with self.subTest(field=field):
                page = self.question_page(answers, stage=stage)
                rendered = self.run_page(page)
                self.assertIn(question, rendered["initial"]["text"])
                self.assertNotRegex(rendered["initial"]["text"], r"answers\.|先\s*set|直接\s*set|写入|intro|single|all|selected")
                self.assertNotIn("answers.", rendered["exported"])
                self.assertNotIn("answer-field", rendered["exported"])
                self.assertEqual(rendered["posts"], [])
                self.assertEqual(rendered["requests"], [])

    def test_generated_subject_scope_radios_save_raw_values_and_read_receipts(self):
        for value in ("single", "all", "selected"):
            with self.subTest(value=value):
                page = self.question_page({"product_form": "plush"})
                before = (self.root / "project.json").read_bytes()
                rendered = self.run_page(page, interactive=True, choose=value, submit=True)
                self.assertEqual([radio["value"] for radio in rendered["radios"]], ["single", "all", "selected"])
                self.assertEqual([radio["label"].strip() for radio in rendered["radios"]],
                                 ["只做一个角色", "全部角色都做", "挑选其中几个角色"])
                self.assertEqual(rendered["inputs"], [])
                self.assertEqual(rendered["initial"]["selected"], [])
                self.assertIn("回到聊天里", rendered["initial"]["text"])
                self.assertNotIn("已识别", rendered["initial"]["text"])
                self.assertEqual(rendered["draft"]["fieldValue"], value)
                self.assertEqual(rendered["draft"]["requestId"], None)
                self.assertEqual(len(rendered["posts"]), 1)
                payload = rendered["posts"][0]
                self.assertEqual(payload["field"], "answers.subject_scope")
                self.assertEqual(payload["value"], value)
                self.assertEqual(payload["action"], "answer-field")
                self.assertEqual(payload["items"], [])
                self.assertIsNone(payload["round_id"])
                self.assertIn(f"/api/receipt/{payload['request_id']}", rendered["requests"])
                self.assertIn("回答已保存", rendered["text"])
                self.assertEqual((self.root / "project.json").read_bytes(), before)
                request = self.root / "scope-request.json"
                request.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                receipt = self.cli("project.py", "submit", self.root, "--request", request)
                self.assertTrue(receipt["committed"])
                self.assertEqual(self.data()["answers"]["subject_scope"], value)

    def test_generated_question_reuses_draft_and_request_identity(self):
        page = self.question_page({"product_form": "plush"})
        manifest = project.read_json(self.root / "deliveries/D0001/manifest.json")
        payload = {"schema_version": 1, "request_id": "", "project_id": manifest["project_id"],
                   "delivery_id": manifest["delivery_id"], "expected_revision": manifest["project_revision"],
                   "round_id": None, "action": "answer-field", "items": [], "note": "保留圆眼睛",
                   "keep": ["配色"], "field": "answers.subject_scope", "value": "selected"}
        draft = {"action": "answer-field", "fieldValue": "selected", "note": payload["note"], "keep": "配色",
                 "requestId": "original-request", "requestPayload": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}
        rendered = self.run_page(page, interactive=True, draft=draft, submit=True)
        self.assertEqual(rendered["initial"]["selected"], ["selected"])
        self.assertEqual(rendered["posts"][0], {**payload, "request_id": "original-request"})
        self.assertIn("挑选其中几个角色", rendered["exported"])
        self.assertIn("主体范围", rendered["exported"])
        self.assertNotIn("answers.", rendered["exported"])
        self.assertNotIn("selected", rendered["exported"])
        changed = self.run_page(page, interactive=True, draft=draft, choose="all", submit=True)
        self.assertIsNone(changed["draft"]["requestId"])
        self.assertEqual(changed["draft"]["requestPayload"], "")
        self.assertEqual(changed["draft"]["note"], payload["note"])
        self.assertNotEqual(changed["posts"][0]["request_id"], "original-request")
        self.assertEqual(changed["posts"][0]["value"], "all")

    def test_generated_question_keeps_validation_and_neutral_recommendations(self):
        page = self.question_page({"product_form": "plush"})
        rendered = self.run_page(page, interactive=True, submit=True)
        self.assertEqual(rendered["posts"], [])
        self.assertIn("先选择或填写一个答案", rendered["text"])
        page = self.question_page({}, {"action": "ask", "field": "answers.concept_count", "options": [], "say": "先 set answers.concept_count"})
        rendered = self.run_page(page, interactive=True, input="1.5", submit=True)
        self.assertEqual(rendered["posts"], [])
        self.assertEqual(rendered["inputs"][0]["type"], "number")
        self.assertIn("概念数量必须是整数", rendered["text"])
        page = self.question_page({"product_form": "figure", "subject_scope": "single", "subjects": ["小米团"], "concept_count": 1}, stage="scene")
        rendered = self.run_page(page)
        self.assertEqual(rendered["initial"]["selected"], ["design-manuscript"])
        self.assertIn("建议，可改选", rendered["initial"]["text"])
        self.assertIn("仅供参考", rendered["initial"]["text"])
        self.assertNotRegex(rendered["initial"]["text"], "涨粉|带货|工厂接单|品牌上新")

    def test_generated_question_preserves_offered_values_and_capacity_explanation(self):
        page = self.question_page({}, {"action": "ask", "field": "answers.product_form", "recommended": "puzzle",
                                      "options": [{"value": "puzzle", "label": "定制拼图", "intro": "保留原选项说明"}]})
        rendered = self.run_page(page, interactive=True, submit=True)
        self.assertEqual(rendered["initial"]["selected"], ["puzzle"])
        self.assertIn("定制拼图", rendered["exported"])
        self.assertIn("保留原选项说明", rendered["initial"]["text"])
        self.assertEqual(rendered["posts"][0]["value"], "puzzle")
        page = self.question_page({"product_form": "plush", "subject_scope": "single", "note_type": "single-multiview"})
        rendered = self.run_page(page, interactive=True, choose=2, submit=True)
        self.assertIsInstance(rendered["posts"][0]["value"], int)
        self.assertEqual(rendered["posts"][0]["value"], 2)
        page = self.question_page({"product_form": "figure", "subject_scope": "all", "subjects": [f"角色{index}" for index in range(8)],
                                   "note_type": "series-collection", "concept_count": 8, "delivery": "grid-9"})
        rendered = self.run_page(page)
        self.assertIn("超过九宫格容量", rendered["initial"]["text"])
        self.assertIn("回到聊天里确认分组或调整角色范围", rendered["initial"]["text"])
        self.assertNotIn("answers.", rendered["initial"]["text"])

    def test_generated_question_fallback_and_non_question_say_remain_distinct(self):
        for field in ("answers.subjects", "answers.future_question", None):
            with self.subTest(field=field):
                page = self.question_page({}, {"action": "ask", "field": field, "say": "先 set answers.subjects=single；内部哨兵"})
                rendered = self.run_page(page)
                self.assertNotIn("内部哨兵", rendered["text"])
                self.assertNotIn("answers.", rendered["text"])
                self.assertEqual(rendered["inputs"][0]["type"], "text")
                self.assertNotIn("single", rendered["inputs"][0]["placeholder"])
        for action in ("review", "package", "done"):
            with self.subTest(action=action):
                page = self.question_page({}, {"action": action, "say": "保留这条非提问状态说明"})
                rendered = self.run_page(page)
                self.assertIn("保留这条非提问状态说明", rendered["text"])
                self.assertEqual(rendered["initial"]["text"], "")

    def add_round(self):
        project_state = self.data()
        source = self.root / "rounds" / "01-concept-s1"
        target = self.root / "rounds" / "02-concept-s1"
        target.mkdir()
        for path in source.glob("*.png"):
            shutil.copyfile(path, target / path.name)
        round_data = project.read_json(source / "round.json")
        round_data["id"] = "02-concept-s1"
        round_data["created_at"] = "2026-11-08T11:00:00+08:00"
        round_data["based_on"] = "01-concept-s1:A"
        for item in round_data["items"]:
            item["title"] += "·第二版"
            item["prompt"] += "，这次进一步压缩毛长，保持原有五官与配色"
        with project.project_lock(self.root):
            latest = project.load_project(self.root, writable=True)
            latest["rounds"].append({"id": "02-concept-s1", "stage": "concept", "subject": "小米团", "kind": "alternatives",
                                     "dir": "rounds/02-concept-s1", "status": "recorded", "missing": []})
            project.write_json(target / "round.json", round_data)
            project.save_project(self.root, latest)

    def test_snapshot_is_immutable_and_revision_does_not_change(self):
        project_state = self.data()
        revision = project_state["revision"]
        original = (self.root / "rounds/01-concept-s1/A.png").read_bytes()
        first = present_round.present_round(self.root, open_browser=False)
        self.assertEqual(first["delivery_id"], "D0001")
        self.assertEqual(self.data()["revision"], revision)
        manifest = project.read_json(self.root / "deliveries/D0001/manifest.json")
        self.assertEqual(manifest["schema_version"], 1)
        for key in ("project_id", "delivery_id", "project_revision", "stage", "subject", "round_id", "kind", "items",
                    "last_delivery_id", "comparison_baseline", "next", "decisions", "feedback"):
            self.assertIn(key, manifest)
        item = manifest["items"][0]
        for key in ("id", "slot", "original_path", "display_path", "image_sha256", "selectable", "disabled_reason",
                    "title", "idea", "prompt", "method", "sources"):
            self.assertIn(key, item)
        self.assertEqual(item["method"], "generate")
        manifest_bytes = (self.root / "deliveries/D0001/manifest.json").read_bytes()
        frozen = (self.root / item["display_path"]).read_bytes()
        self.assertEqual(frozen, original)
        (self.root / "rounds/01-concept-s1/A.png").write_bytes(self.image.read_bytes() + b"source-replaced")
        self.assertEqual((self.root / item["display_path"]).read_bytes(), original)
        self.assertEqual(hashlib.sha256(frozen).hexdigest(), item["image_sha256"])
        page = (self.root / "deliveries/D0001/index.html").read_text(encoding="utf-8")
        marker = '<script id="gallery-data" type="application/json">'
        page_data = json.loads(page.split(marker, 1)[1].split("</script>", 1)[0])
        self.assertTrue(page_data["current"]["items"][0]["path"].startswith("../../artifacts/sha256/"))
        self.assertEqual(self.data()["revision"], revision)
        self.assertEqual((self.root / "deliveries/D0001/manifest.json").read_bytes(), manifest_bytes)

    def test_latest_previous_and_comparison_are_distinct_real_deliveries(self):
        present_round.present_round(self.root, open_browser=False)
        self.add_round()
        second = present_round.present_round(self.root, open_browser=False)
        self.assertEqual(second["delivery_id"], "D0002")
        data = build_gallery.build_gallery(self.root)
        self.assertEqual(data["delivery_id"], "D0002")
        self.assertEqual(data["previous_delivery"]["delivery_id"], "D0001")
        self.assertEqual(data["comparison"]["delivery_id"], "D0001")
        self.assertEqual(data["comparison"]["basis"]["reason"], "明确的 based-on")
        self.assertEqual(len(data["comparison"]["basis"]["matches"]), 3)
        self.assertEqual(data["comparison"]["basis"]["matches"][0]["item_id"], "A")
        self.assertTrue(all(match["baseline_item_id"] == "A" for match in data["comparison"]["basis"]["matches"]))
        second_manifest = project.read_json(self.root / "deliveries/D0002/manifest.json")
        self.assertEqual(second_manifest["view"]["previous_delivery"]["delivery_id"], "D0001")
        self.assertEqual(second_manifest["view"]["comparison"]["delivery_id"], "D0001")
        self.assertEqual(data["project_revision"], project.load_project(self.root)["revision"])
        self.assertEqual(data["current"]["round_id"], "02-concept-s1")
        self.assertEqual(data["current"]["items"][0]["id"], "A")
        self.assertTrue(data["current"]["items"][0]["image_sha256"])
        self.assertTrue((self.root / "gallery.html").is_file())
        history = build_gallery.build_gallery(self.root, self.root / "old-gallery.html", delivery_id="D0001")
        self.assertEqual(history["delivery_id"], "D0001")
        self.assertEqual(history["previous_delivery"], None)

    def test_legacy_schema_is_read_only_and_old_build_data_surface_remains(self):
        for schema in (1, 2):
            with self.subTest(schema=schema):
                self.make_legacy(schema)
                before = (self.root / "project.json").read_bytes()
                output = self.root / f"legacy-{schema}.html"
                data = build_gallery.build_gallery(self.root, output)
                self.assertTrue(data["readonly"])
                self.assertIn("current", data)
                self.assertIn("history", data)
                self.assertIn("picks", data)
                self.assertIn("post", data)
                self.assertEqual(data["current"]["items"][0]["method"], "AI 生成")
                self.assertEqual(data["current"]["items"][0]["prompt_label"], "生图提示词")
                self.assertTrue(all("旧项目只读" in item["disabled_reason"] for item in data["current"]["items"]))
                self.assertEqual(before, (self.root / "project.json").read_bytes())
                self.assertFalse((self.root / "deliveries").exists())
                html = output.read_text(encoding="utf-8")
                self.assertIn("旧项目只读", html)
                self.assertIn('"readonly": true', html)

    def test_open_receipts_never_infer_verified_and_delivery_ids_stay_monotonic(self):
        first = present_round.present_round(self.root, open_browser=False)
        receipt = project.read_json(self.root / "deliveries/D0001/receipts.json")
        self.assertEqual(receipt["latest"]["status"], "unavailable")
        dispatched = present_round.record_open_result(self.root, first["delivery_id"], "open_dispatched", target=first["gallery"])
        self.assertEqual(dispatched["status"], "open_dispatched")
        with self.assertRaises(project.ProjectError):
            present_round.record_open_result(self.root, first["delivery_id"], "opened_verified")
        verified = present_round.record_open_result(
            self.root, first["delivery_id"], "opened_verified", target=first["gallery"],
            evidence={"delivery_id": "D0001", "images_decoded": True, "inspected_by": "browser-test"},
        )
        self.assertEqual(verified["status"], "opened_verified")
        orphan = self.root / "deliveries" / "D0007"
        orphan.mkdir()
        second = present_round.present_round(self.root, open_browser=False)
        self.assertEqual(second["delivery_id"], "D0008")
        self.assertEqual(self.data()["presentations"]["latest_delivery_id"], "D0008")

    def test_first_question_can_present_without_a_round(self):
        project_state = self.data()
        project_state["answers"] = {}
        project_state["rounds"] = []
        project.atomic_json(self.root / "project.json", project_state)
        shutil.rmtree(self.root / "rounds")
        revision = project_state["revision"]
        result = present_round.present_round(self.root, open_browser=False)
        self.assertEqual(result["delivery_id"], "D0001")
        self.assertEqual(self.data()["revision"], revision)
        manifest = project.read_json(self.root / "deliveries/D0001/manifest.json")
        self.assertIsNone(manifest["round_id"])
        self.assertEqual(manifest["kind"], "summary")
        self.assertEqual(manifest["items"], [])
        self.assertEqual(manifest["next"]["action"], "ask")
        self.assertEqual(manifest["next"]["field"], "answers.product_form")
        page = (self.root / "deliveries/D0001/index.html").read_text(encoding="utf-8")
        marker = '<script id="gallery-data" type="application/json">'
        page_data = json.loads(page.split(marker, 1)[1].split("</script>", 1)[0])
        self.assertIsNone(page_data["current"])
        self.assertEqual(page_data["next"]["field"], "answers.product_form")
        self.assertIn("还差一个确认", page)
        self.assertIn("answers.product_form", page)

    def test_whole_project_moves_without_losing_delivery_images(self):
        present_round.present_round(self.root, open_browser=False)
        moved = Path(self.temporary.name) / "搬到别处" / self.root.name
        moved.parent.mkdir()
        shutil.copytree(self.root, moved)
        original_root = self.root.as_posix()
        data = build_gallery.build_gallery(moved)
        item_path = data["current"]["items"][0]["path"]
        self.assertFalse(Path(item_path).is_absolute())
        self.assertTrue((moved / item_path).is_file())
        page = (moved / "gallery.html").read_text(encoding="utf-8")
        self.assertNotIn(original_root, page)
        self.assertNotIn(self.temporary.name, page)
        snapshot = moved / "deliveries" / "D0001" / "index.html"
        snapshot_text = snapshot.read_text(encoding="utf-8")
        snapshot_data = json.loads(snapshot_text.split('<script id="gallery-data" type="application/json">', 1)[1].split("</script>", 1)[0])
        self.assertTrue((snapshot.parent / snapshot_data["current"]["items"][0]["path"]).is_file())

    def test_changed_source_disables_adoption_but_keeps_old_snapshot(self):
        present_round.present_round(self.root, open_browser=False)
        frozen = project.read_json(self.root / "deliveries/D0001/manifest.json")["items"][0]
        original = self.root / frozen["original_path"]
        old = (self.root / frozen["display_path"]).read_bytes()
        original.write_bytes(old + b"changed-at-source")
        data = build_gallery.build_gallery(self.root)
        item = data["current"]["items"][0]
        self.assertTrue(item["exists"])
        self.assertFalse(item["selectable"])
        self.assertIn("源文件已被替换", item["disabled_reason"])
        self.assertEqual((self.root / item["path"]).read_bytes(), old)

    def test_corrupt_snapshot_is_explicit_and_does_not_use_original(self):
        present_round.present_round(self.root, open_browser=False)
        manifest = project.read_json(self.root / "deliveries/D0001/manifest.json")
        item = manifest["items"][0]
        (self.root / item["display_path"]).write_bytes(b"corrupt-snapshot")
        data = build_gallery.build_gallery(self.root)
        displayed = data["current"]["items"][0]
        self.assertFalse(displayed["exists"])
        self.assertFalse(displayed["selectable"])
        self.assertIsNone(displayed["path"])
        self.assertIn("历史快照缺失", displayed["disabled_reason"])
        self.assertTrue((self.root / item["original_path"]).is_file())

    def test_atomic_json_manifest_is_visible_inside_project_transaction(self):
        with project.project_lock(self.root):
            result = present_round.present_round(self.root, open_browser=False)
            manifest = project.read_json(Path(result["manifest"]))
            self.assertEqual(manifest["delivery_id"], "D0001")
            self.assertTrue(Path(result["manifest"]).is_file())
            self.assertEqual(project.load_project(self.root)["presentations"]["latest_delivery_id"], "D0001")
        self.assertEqual(self.data()["presentations"]["latest_delivery_id"], "D0001")

    def test_generated_page_is_self_contained_and_static_mode_has_no_api_success(self):
        present_round.present_round(self.root, open_browser=False)
        html = (self.root / "gallery.html").read_text(encoding="utf-8")
        self.assertNotIn('<script src="http', html)
        self.assertNotIn('<link rel="stylesheet" href="http', html)
        self.assertIn("Authorization: `Bearer ${token}`", html)
        self.assertIn('location.protocol.startsWith("http")', html)
        self.assertIn("尚未提交", html)
        self.assertIn("静态快照", html)

    def test_real_cli_refuses_unfinished_update_before_writing(self):
        environment = {**os.environ, "JARDEN_UPDATE_HOME": str(Path(self.temporary.name) / "updates"), "PYTHONDONTWRITEBYTECODE": "1"}
        with mock.patch.dict(os.environ, environment):
            home = runtime_activity.instance_home(SKILL)
        project.atomic_json(home / "transactions/pending.json", {"phase": "activating"})
        before = (self.root / "project.json").read_bytes()
        for script, options in (("present_round.py", ["--no-open"]), ("build_gallery.py", [])):
            with self.subTest(script=script):
                result = subprocess.run([sys.executable, str(SKILL / "scripts" / script), str(self.root), *options],
                                        env=environment, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                payload = json.loads(result.stdout)
                self.assertFalse(payload["ok"])
                self.assertIn("未结束的更新事务", payload["error"])
                self.assertEqual((self.root / "project.json").read_bytes(), before)
                self.assertFalse((self.root / "deliveries").exists())
                self.assertFalse((self.root / "gallery.html").exists())
                self.assertEqual(list((home / "readers").glob("*.json")), [])

    def test_real_cli_summary_snapshot_is_local_scene_baseline_after_source_replacement(self):
        source = self.root / "rounds/09-scene-set"
        source.mkdir()
        slots = project.scene_slots(self.data())
        for slot in slots:
            shutil.copyfile(self.image, source / f"{slot['id']}.png")
        state = self.data()
        state["stage"] = "done"
        state["rounds"] = [{"id": "09-scene-set", "stage": "scene", "subject": None, "kind": "set", "dir": "rounds/09-scene-set", "status": "approved", "missing": []}]
        state["selections"] = {stage: {"小米团": {"round": "input", "item": "accepted", "image": "inputs/source.png"}} for stage in ("concept", "material", "cutout")}
        state["selections"]["scene"] = {"_": {"round": "08-scene", "item": "A", "image": "inputs/source.png"}}
        state["finals"] = [{"slot": slot["id"], "role": slot["role"], "subject": slot.get("subject"),
                            "path": f"rounds/09-scene-set/{slot['id']}.png"} for slot in slots]
        project.atomic_json(self.root / "project.json", state)
        project.atomic_json(source / "round.json", {"schema_version": project.SCHEMA_VERSION, "id": "09-scene-set", "stage": "scene", "subject": None,
                            "kind": "set", "items": [{**slot, "image": f"{slot['id']}.png", "title": slot["id"], "status": "generated"} for slot in slots]})
        self.cli("present_round.py", self.root, "--no-open")
        old_file = self.root / "deliveries/D0001/manifest.json"
        old_bytes = old_file.read_bytes()
        old = project.read_json(old_file)
        self.assertEqual(old["kind"], "summary")
        self.assertEqual(old["items"], [])
        self.assertEqual(old["stage"], "done")
        self.assertEqual(old["next"]["action"], "review")
        old_scale = next(item for item in old["view"]["picks"] if item["id"] == "scale")
        old_snapshot = (self.root / old_scale["display_path"]).read_bytes()
        self.cli("project.py", "new-round", self.root, "--stage", "scene", "--kind", "set", "--slots", "scale", "--based-on", "09-scene-set:scale")
        current_state = self.data()
        current = current_state["rounds"][-1]
        current_file = self.root / current["dir"] / "round.json"
        current_data = project.read_json(current_file)
        image = Image.new("RGB", (180, 240), (69, 120, 98))
        image.save(current_file.parent / current_data["items"][0]["image"])
        image.save(source / "scale.png")
        result = self.cli("present_round.py", self.root, "--no-open")
        self.assertEqual(result["project_revision"], current_state["revision"])
        manifest = project.read_json(self.root / "deliveries/D0002/manifest.json")
        basis = manifest["comparison_baseline"]
        self.assertEqual(basis["delivery_id"], "D0001")
        self.assertEqual(basis["round_id"], "09-scene-set")
        self.assertEqual([(match["item_id"], match["baseline_item_id"]) for match in basis["matches"]], [("scale", "scale")])
        comparison = manifest["view"]["comparison"]
        self.assertEqual(comparison["delivery_id"], "D0001")
        self.assertTrue(comparison["items"])
        self.assertTrue(all(item["display_path"] == old_scale["display_path"] for item in comparison["items"]))
        self.assertTrue(all(item["image_sha256"] == old_scale["image_sha256"] for item in comparison["items"]))
        self.assertNotEqual(manifest["items"][0]["image_sha256"], old_scale["image_sha256"])
        self.assertEqual((self.root / old_scale["display_path"]).read_bytes(), old_snapshot)
        self.assertEqual(old_file.read_bytes(), old_bytes)
        self.assertTrue(manifest["view"]["previous_delivery"]["items"])
        self.cli("build_gallery.py", self.root)
        self.assertEqual(build_gallery.build_data(self.root, self.data())["comparison"]["delivery_id"], "D0001")

    def test_next_action_receives_root_and_preserves_business_state(self):
        state = self.data()
        state["stage"] = "done"
        state["rounds"] = []
        for action in ("review", "package"):
            with self.subTest(action=action), mock.patch.object(project, "next_action", return_value={"action": action}) as next_action:
                data = build_gallery.build_data(self.root, state, prefer_latest=False)
                self.assertEqual(data["next"]["action"], action)
                self.assertEqual(next_action.call_args.args[1], self.root.resolve())
                self.assertIsNot(next_action.call_args.args[0], state)
                self.assertEqual(state["stage"], "done")

    def test_real_cli_explicit_baseline_compares_every_alternative(self):
        self.cli("present_round.py", self.root, "--no-open")
        self.cli("project.py", "set", self.root, "preferences.directions_per_round=5")
        self.cli("project.py", "new-round", self.root, "--stage", "concept", "--based-on", "01-concept-s1:B")
        revision = self.data()["revision"]
        self.cli("present_round.py", self.root, "--no-open")
        manifest = project.read_json(self.root / "deliveries/D0002/manifest.json")
        basis = manifest["comparison_baseline"]
        self.assertEqual(basis["delivery_id"], "D0001")
        self.assertEqual({match["item_id"] for match in basis["matches"]}, {"A", "B", "C", "D", "E"})
        self.assertTrue(all(match["baseline_item_id"] == "B" for match in basis["matches"]))
        self.assertEqual(self.data()["revision"], revision)

    def test_real_cli_planned_missing_reason_is_factual(self):
        self.cli("project.py", "set", self.root, "brief.execution_status=透明协议待确认，尚未调用内置生图")
        self.cli("project.py", "new-round", self.root, "--stage", "concept")
        self.cli("present_round.py", self.root, "--no-open")
        manifest = project.read_json(self.root / "deliveries/D0001/manifest.json")
        self.assertTrue(manifest["items"])
        for item in manifest["items"]:
            self.assertFalse(item["exists"])
            self.assertFalse(item["selectable"])
            self.assertIn("图片尚未生成", item["reason"])
            self.assertIn("透明协议待确认，尚未调用内置生图", item["reason"])
            self.assertIn("提示词尚未填写", item["reason"])
            self.assertNotIn("没有可用的生图能力", item["reason"])
            self.assertNotIn("提示词已保存", item["reason"])
        page = build_gallery.build_data(self.root, self.data())
        for item in page["current"]["items"]:
            self.assertEqual(item["disabled_reason"], item["reason"])
            self.assertNotIn("历史快照缺失", item["disabled_reason"])

    def test_real_cli_holds_lease_through_html_and_open_receipt(self):
        environment = {**os.environ, "JARDEN_UPDATE_HOME": str(Path(self.temporary.name) / "updates"), "PYTHONDONTWRITEBYTECODE": "1"}
        runner = """
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
scripts, script, root, probe_path, *options = sys.argv[1:]
sys.path.insert(0, scripts)
import runtime_activity
events = []
def probe(frame, event, argument):
    if event not in {"call", "return"} or frame.f_code.co_name not in {"atomic_html", "record_open_result", "emit"}:
        return
    if Path(frame.f_code.co_filename).parent != Path(scripts):
        return
    readers = runtime_activity.active_leases(runtime_activity.instance_home(), runtime_activity.real_root())
    if not any(reader["pid"] == os.getpid() for reader in readers):
        raise AssertionError("CLI lease missing during " + frame.f_code.co_name + ":" + event)
    if event == "call" and frame.f_code.co_name == "atomic_html":
        status = subprocess.run([sys.executable, str(Path(scripts) / "check_update.py"), "status", "--json"],
                                text=True, capture_output=True, timeout=10, check=True)
        if not any(reader["pid"] == os.getpid() for reader in json.loads(status.stdout)["active_readers"]):
            raise AssertionError("updater CLI cannot see delivery lease")
    events.append(frame.f_code.co_name + ":" + event)
sys.argv = [str(Path(scripts) / script), root, *options]
sys.setprofile(probe)
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
finally:
    sys.setprofile(None)
    readers = runtime_activity.active_leases(runtime_activity.instance_home(), runtime_activity.real_root())
    if any(reader["pid"] == os.getpid() for reader in readers):
        raise AssertionError("CLI lease was not released")
    Path(probe_path).write_text(json.dumps(events), encoding="utf-8")
"""
        before_revision = self.data()["revision"]
        for script, options in (("present_round.py", ["--no-open"]), ("build_gallery.py", [])):
            with self.subTest(script=script):
                probe = Path(self.temporary.name) / f"{script}-probe.json"
                result = subprocess.run([sys.executable, "-c", runner, str(SKILL / "scripts"), script, str(self.root), str(probe), *options],
                                        env=environment, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(json.loads(result.stdout)["ok"])
                events = json.loads(probe.read_text(encoding="utf-8"))
                self.assertIn("atomic_html:call", events)
                self.assertIn("atomic_html:return", events)
                self.assertIn("emit:call", events)
                if script == "present_round.py":
                    self.assertIn("record_open_result:call", events)
                    self.assertIn("record_open_result:return", events)
                self.assertEqual(self.data()["revision"], before_revision)
        self.assertEqual(self.data()["presentations"]["latest_delivery_id"], "D0001")


if __name__ == "__main__":
    unittest.main()
