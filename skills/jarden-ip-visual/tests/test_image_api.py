from __future__ import annotations

import base64
from collections import Counter
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

SKILL = Path(__file__).resolve().parents[1]
FAKE_KEY = "fake-image-api-for-local-tests"


class FakeImageServer(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.requests.append((self.path, body, self.headers["Content-Type"]))
        if self.headers.get("Authorization") != "Bearer " + FAKE_KEY:
            self.send_error(401)
            return
        self.server.post_started.set()
        self.server.release_post.wait(10)
        if self.server.post_error:
            self.send_error(self.server.post_error)
            return
        items = self.server.response_items or [{"b64_json": self.server.image_b64}]
        data = json.dumps({"id": "fake-accepted-request", "api_key": FAKE_KEY, "data": items}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.server.downloads.append(self.path)
        response_file = self.server.response_file
        if response_file:
            self.server.response_seen_on_get.append(json.loads(response_file.read_text()))
        if self.server.fail_downloads.get(self.path, 0):
            self.server.fail_downloads[self.path] -= 1
            self.send_error(503)
            return
        self.server.get_started.set()
        self.server.release_get.wait(10)
        data = base64.b64decode(self.server.image_b64)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


class ImageApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = dict(os.environ, JARDEN_IP_VISUAL_HOME=str(self.root / "settings"))
        self.environment.pop("JARDEN_IP_VISUAL_IMAGE_API_KEY", None)
        self.environment["PYTHONDONTWRITEBYTECODE"] = "1"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeImageServer)
        self.server.requests = []
        self.server.downloads = []
        self.server.fail_downloads = {}
        self.server.response_items = None
        self.server.response_file = None
        self.server.response_seen_on_get = []
        self.server.post_error = None
        self.server.post_started = threading.Event()
        self.server.release_post = threading.Event()
        self.server.release_post.set()
        self.server.get_started = threading.Event()
        self.server.release_get = threading.Event()
        self.server.release_get.set()
        buffer = io.BytesIO()
        Image.new("RGB", (64, 96), (230, 170, 80)).save(buffer, format="PNG")
        self.server.image_b64 = base64.b64encode(buffer.getvalue()).decode()
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.prompt = self.root / "prompt.txt"
        self.prompt.write_text("生成一个完整的原创毛绒角色商品图，短毛绒有细小纤维与刺绣针脚，保持原图的长耳轮廓与蓝色口袋，米白纯背景和自然接触阴影。")
        self.call("configure", "--base-url", self.base_url, "--model", "fake-image-model")
        self.plan = self.root / "plan.json"

    def call(self, *args, code=0, keyed=False):
        environment = dict(self.environment)
        if keyed:
            environment["JARDEN_IP_VISUAL_IMAGE_API_KEY"] = FAKE_KEY
        result = subprocess.run([sys.executable, "-B", str(SKILL / "scripts/image_api.py"), *map(str, args)], text=True, capture_output=True, env=environment, timeout=20)
        self.assertNotIn(FAKE_KEY, result.stdout + result.stderr)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def prepare(self, reference=None, count=1, plan=None, output=None):
        args = ["plan", "--prompt-file", self.prompt, "--out", output or self.root / "new image.png", "--plan", plan or self.plan, "--n", count]
        if reference:
            args += ["--reference", reference]
        return self.call(*args)

    def receipt(self, plan=None):
        path = plan or self.plan
        return json.loads(path.with_name(path.stem + ".receipt.json").read_text())

    def start_run(self, plan=None, action="run", keyed=True):
        environment = dict(self.environment)
        if keyed:
            environment["JARDEN_IP_VISUAL_IMAGE_API_KEY"] = FAKE_KEY
        process = subprocess.Popen(
            [sys.executable, "-B", str(SKILL / "scripts/image_api.py"), action, "--plan", str(plan or self.plan)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process

    def finish(self, process, code=0):
        stdout, stderr = process.communicate(timeout=20)
        self.assertNotIn(FAKE_KEY, stdout + stderr)
        self.assertEqual(process.returncode, code, stdout + stderr)
        return json.loads(stdout)

    def download_response(self, count=1):
        urls = [f"http://127.0.0.1:{self.server.server_port}/result-{index + 1}.png" for index in range(count)]
        self.server.response_items = [{"url": url} for url in urls]
        self.server.response_file = self.plan.with_name(self.plan.stem + ".response.json")
        return [f"/result-{index + 1}.png" for index in range(count)]

    def assert_no_saved_key(self):
        for path in self.root.rglob("*.json"):
            self.assertNotIn(FAKE_KEY, path.read_text())

    def test_dry_run_no_network_no_key_and_key_required(self):
        self.prepare()
        self.call("run", "--plan", self.plan, "--dry-run")
        self.assertEqual(len(self.server.requests), 0)
        self.assertFalse((self.root / "new image.png").exists())
        self.assertFalse((self.root / "settings/locks").exists())
        self.call("run", "--plan", self.plan, code=2)
        self.assertEqual(len(self.server.requests), 0)

    def test_generate_saved_and_duplicate_request_refused(self):
        self.prepare()
        self.call("run", "--plan", self.plan, keyed=True)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.server.requests[0][0], "/v1/images/generations")
        with Image.open(self.root / "new image.png") as image:
            self.assertEqual(image.size, (64, 96))
        self.call("run", "--plan", self.plan, keyed=True, code=2)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.receipt()["state"], "completed")
        self.assert_no_saved_key()

    def test_concurrent_cli_same_plan_sends_only_one_post(self):
        self.prepare()
        self.server.release_post.clear()
        first = self.start_run()
        try:
            self.assertTrue(self.server.post_started.wait(5))
            self.finish(self.start_run(), code=2)
            self.assertEqual(len(self.server.requests), 1)
        finally:
            self.server.release_post.set()
        self.finish(first)
        self.assertEqual(len(self.server.requests), 1)

    def test_concurrent_cli_different_plan_same_output_sends_only_one_post(self):
        self.prepare()
        other = self.root / "other-plan.json"
        self.prompt.write_text(self.prompt.read_text() + "第二个计划改变背景，但输出位置相同。")
        self.prepare(plan=other)
        self.server.release_post.clear()
        first = self.start_run()
        try:
            self.assertTrue(self.server.post_started.wait(5))
            self.finish(self.start_run(other), code=2)
        finally:
            self.server.release_post.set()
        self.finish(first)
        self.assertEqual(len(self.server.requests), 1)

    def test_concurrent_cli_same_request_different_outputs_is_exclusive(self):
        self.prepare()
        other = self.root / "same-request.json"
        self.prepare(plan=other, output=self.root / "different.png")
        self.server.release_post.clear()
        first = self.start_run()
        try:
            self.assertTrue(self.server.post_started.wait(5))
            self.finish(self.start_run(other), code=2)
        finally:
            self.server.release_post.set()
        self.finish(first)
        self.assertEqual(len(self.server.requests), 1)

    def test_success_response_saved_before_get_and_resume_needs_no_key(self):
        self.prepare()
        paths = self.download_response()
        self.server.fail_downloads[paths[0]] = 1
        self.call("run", "--plan", self.plan, keyed=True, code=2)
        self.assertEqual(self.receipt()["state"], "partial")
        received = self.server.response_seen_on_get[0]
        self.assertEqual(received["request_id"], "fake-accepted-request")
        self.assertEqual(received["data"], self.server.response_items)
        self.assertNotIn("api_key", received)
        self.call("run", "--plan", self.plan, keyed=True, code=2)
        self.call("resume", "--plan", self.plan)
        self.assertEqual(self.receipt()["state"], "completed")
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.server.downloads, paths + paths)
        self.assertTrue((self.root / "new image.png").is_file())
        self.call("resume", "--plan", self.plan)
        self.assertEqual(self.server.downloads, paths + paths)
        self.assert_no_saved_key()

    def test_multiple_images_resume_reuses_saved_outputs_without_post(self):
        self.prepare(count=3)
        paths = self.download_response(3)
        self.server.fail_downloads[paths[1]] = 1
        self.call("run", "--plan", self.plan, keyed=True, code=2)
        receipt = self.receipt()
        self.assertEqual([item["state"] for item in receipt["images"]], ["saved", "pending", "pending"])
        first = self.root / "new image-1.png"
        before = (first.stat().st_mtime_ns, hashlib.sha256(first.read_bytes()).hexdigest())
        self.call("resume", "--plan", self.plan)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(Counter(self.server.downloads), Counter({paths[0]: 1, paths[1]: 2, paths[2]: 1}))
        self.assertEqual(before, (first.stat().st_mtime_ns, hashlib.sha256(first.read_bytes()).hexdigest()))
        self.assertEqual(len(self.receipt()["outputs"]), 3)
        self.assertEqual([item["state"] for item in self.receipt()["images"]], ["saved"] * 3)

    def test_resume_decodes_saved_base64_without_network(self):
        self.prepare()
        self.call("run", "--plan", self.plan, keyed=True)
        (self.root / "new image.png").unlink()
        self.call("resume", "--plan", self.plan)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.server.downloads, [])
        self.assertTrue((self.root / "new image.png").is_file())

    def test_killed_cli_releases_lock_but_keeps_response_and_output_claim(self):
        self.prepare()
        paths = self.download_response()
        self.server.release_get.clear()
        process = self.start_run()
        try:
            self.assertTrue(self.server.get_started.wait(5))
            self.assertTrue(self.server.response_file.is_file())
            process.kill()
            stdout, stderr = process.communicate(timeout=10)
            self.assertNotIn(FAKE_KEY, stdout + stderr)
            self.assertNotEqual(process.returncode, 0)
        finally:
            self.server.release_get.set()
        other = self.root / "after-crash.json"
        self.prepare(plan=other)
        self.call("run", "--plan", other, keyed=True, code=2)
        self.call("resume", "--plan", self.plan)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.server.downloads, paths + paths)
        self.assertEqual(self.receipt()["state"], "completed")

    def test_resume_adopts_output_saved_before_per_image_status_commit(self):
        self.prepare()
        self.call("run", "--plan", self.plan, keyed=True)
        receipt_file = self.plan.with_name(self.plan.stem + ".receipt.json")
        receipt = self.receipt()
        receipt["state"] = "saving"
        receipt["images"][0]["state"] = "ready"
        receipt["outputs"] = []
        receipt_file.write_text(json.dumps(receipt))
        output = self.root / "new image.png"
        previous_time = output.stat().st_mtime_ns
        self.call("resume", "--plan", self.plan)
        self.assertEqual(output.stat().st_mtime_ns, previous_time)
        self.assertEqual(self.receipt()["images"][0]["state"], "saved")
        self.assertEqual(len(self.server.requests), 1)

    def test_modified_saved_response_refused_without_network(self):
        self.prepare()
        paths = self.download_response()
        self.server.fail_downloads[paths[0]] = 1
        self.call("run", "--plan", self.plan, keyed=True, code=2)
        response = json.loads(self.server.response_file.read_text())
        response["data"][0]["url"] += "?changed=1"
        self.server.response_file.write_text(json.dumps(response))
        self.call("resume", "--plan", self.plan, code=2)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.server.downloads, paths)

    def test_runtime_key_in_result_url_is_not_saved_or_logged(self):
        self.prepare()
        self.download_response()
        self.server.response_items[0]["url"] += "?token=" + FAKE_KEY
        self.call("run", "--plan", self.plan, keyed=True, code=2)
        self.assertFalse(self.server.response_file.exists())
        self.assertEqual(self.server.downloads, [])
        self.assert_no_saved_key()

    def test_resume_does_not_need_original_reference(self):
        reference = self.root / "reference.png"
        Image.new("RGB", (32, 32), "blue").save(reference)
        self.prepare(reference)
        paths = self.download_response()
        self.server.fail_downloads[paths[0]] = 1
        self.call("run", "--plan", self.plan, keyed=True, code=2)
        reference.unlink()
        self.call("resume", "--plan", self.plan)
        self.assertEqual(len(self.server.requests), 1)

    def test_changed_existing_output_is_not_reused_or_overwritten(self):
        self.prepare(count=2)
        paths = self.download_response(2)
        self.server.fail_downloads[paths[1]] = 1
        self.call("run", "--plan", self.plan, keyed=True, code=2)
        first = self.root / "new image-1.png"
        first.write_bytes(b"changed-by-user")
        self.call("resume", "--plan", self.plan, code=2)
        self.assertEqual(first.read_bytes(), b"changed-by-user")
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.server.downloads, paths)

    def test_failed_generation_is_not_retried_even_under_new_plan_name(self):
        self.prepare()
        other = self.root / "renamed-plan.json"
        self.prepare(plan=other)
        self.server.post_error = 503
        self.call("run", "--plan", self.plan, keyed=True, code=2)
        self.call("run", "--plan", self.plan, keyed=True, code=2)
        self.call("run", "--plan", other, keyed=True, code=2)
        self.call("resume", "--plan", self.plan, code=2)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.server.downloads, [])

    def test_concurrent_resume_uses_one_download_and_no_new_post(self):
        self.prepare()
        paths = self.download_response()
        self.server.fail_downloads[paths[0]] = 1
        self.call("run", "--plan", self.plan, keyed=True, code=2)
        self.server.release_get.clear()
        first = self.start_run(action="resume", keyed=False)
        try:
            self.assertTrue(self.server.get_started.wait(5))
            self.finish(self.start_run(action="resume", keyed=False), code=2)
        finally:
            self.server.release_get.set()
        self.finish(first)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.server.downloads, paths + paths)

    def test_edit_uploads_reference_and_detects_mutation(self):
        reference = self.root / "ref.png"
        Image.new("RGB", (32, 32), "blue").save(reference)
        self.prepare(reference)
        self.call("run", "--plan", self.plan, keyed=True)
        path, body, content_type = self.server.requests[0]
        self.assertEqual(path, "/v1/images/edits")
        self.assertTrue(content_type.startswith("multipart/form-data"))
        self.assertIn(b'name="image[]"', body)
        self.assertIn(reference.read_bytes(), body)

    def test_modified_plan_and_reference_refused_without_call(self):
        self.prepare()
        data = json.loads(self.plan.read_text())
        data["model"] = "other"
        self.plan.write_text(json.dumps(data))
        self.call("run", "--plan", self.plan, "--dry-run", code=2)
        self.assertEqual(len(self.server.requests), 0)

    def test_wrapper_uses_declared_business_environment(self):
        if not shutil.which("node"):
            self.skipTest("Node.js 未安装")
        self.prepare()
        result = subprocess.run(
            ["node", str(SKILL / "scripts/credential-ui/src/profile.ts"), "run", "image-api", "--", sys.executable,
             str(SKILL / "scripts/image_api.py"), "run", "--plan", str(self.plan)],
            env=dict(self.environment, JARDEN_IP_VISUAL_IMAGE_API_KEY=FAKE_KEY), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(FAKE_KEY, result.stdout + result.stderr)
        self.assertEqual(len(self.server.requests), 1)

    def test_wrapper_failure_can_resume_without_wrapper_or_credentials(self):
        if not shutil.which("node"):
            self.skipTest("Node.js 未安装")
        self.prepare()
        paths = self.download_response()
        self.server.fail_downloads[paths[0]] = 1
        result = subprocess.run(
            ["node", str(SKILL / "scripts/credential-ui/src/profile.ts"), "run", "image-api", "--", sys.executable,
             "-B", str(SKILL / "scripts/image_api.py"), "run", "--plan", str(self.plan)],
            env=dict(self.environment, JARDEN_IP_VISUAL_IMAGE_API_KEY=FAKE_KEY), capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn(FAKE_KEY, result.stdout + result.stderr)
        self.call("resume", "--plan", self.plan)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.receipt()["state"], "completed")
        self.assert_no_saved_key()

    @unittest.skipUnless(os.environ.get("JARDEN_NATIVE_TEST") == "1", "显式开启才运行原生假凭据测试")
    def test_native_page_save_to_actual_image_business(self):
        self.prepare()
        helper = self.root / "native-binding.ts"
        helper.write_text(r"""
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
const [component, directory, python, business, plan] = process.argv.slice(2);
const { nativeBackend } = await import(pathToFileURL(path.join(component, 'src/config.ts')).href);
const { loadProfile, prepareProfile, profileStatus } = await import(pathToFileURL(path.join(component, 'src/profile.ts')).href);
const { startServer } = await import(pathToFileURL(path.join(component, 'src/server.ts')).href);
const declaration = JSON.parse(await readFile(path.join(component, 'manifests/default.json'), 'utf8'));
const profile = JSON.parse(await readFile(path.join(component, 'manifests/profiles.json'), 'utf8'));
declaration.credential = 'test/jarden-image/' + randomUUID();
const backend = await nativeBackend(declaration.credential);
assert.equal(await backend.get(), undefined);
await mkdir(path.join(directory, 'manifests'), { recursive: true });
await writeFile(path.join(directory, 'manifests/default.json'), JSON.stringify(declaration));
await writeFile(path.join(directory, 'manifests/profiles.json'), JSON.stringify(profile));
const bindings = await loadProfile('image-api', directory);
const app = await startServer({ manifests: [declaration] });
try {
  const headers = { Origin: app.origin, Authorization: 'Bearer ' + app.bootstrap, 'X-Local-Request': '1', 'Content-Type': 'application/json', Cookie: '' };
  const session = await fetch(app.origin + '/api/session', { method: 'POST', headers });
  headers.Cookie = session.headers.get('set-cookie').split(';')[0];
  const meta = await (await fetch(app.origin + '/api/meta', { headers })).json();
  const saved = await fetch(app.origin + '/api/save', { method: 'POST', headers, body: JSON.stringify({ entries: [
    { credential: meta.fields[0].credential, revision: meta.fields[0].revision, value: 'fake-image-api-for-local-tests' }
  ] }) });
  assert.equal((await saved.json()).status, 'saved');
  const status = await profileStatus(bindings);
  assert.equal(status.configured, true);
  assert.equal(JSON.stringify(status).includes('fake-image-api'), false);
  const environment = { ...process.env };
  delete environment.JARDEN_IP_VISUAL_IMAGE_API_KEY;
  const command = await prepareProfile(bindings, [python, business, 'run', '--plan', plan], environment);
  await new Promise((resolve, reject) => execFile(command.command, command.args, { env: command.env, timeout: 15000 },
    (error, stdout, stderr) => error || (stdout + stderr).includes('fake-image-api') ? reject(Error('业务调用未通过')) : resolve()));
  process.stdout.write('原生页面保存、脱敏状态、真实图片业务读取均通过\n');
} finally {
  app.close();
  await backend.delete();
  assert.equal(await backend.get(), undefined);
}
""", encoding="utf-8")
        result = subprocess.run(
            ["node", str(helper), str(SKILL / "scripts/credential-ui"), str(self.root / "declarations"),
             sys.executable, str(SKILL / "scripts/image_api.py"), str(self.plan)],
            env=self.environment, text=True, capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(FAKE_KEY, result.stdout + result.stderr)
        self.assertEqual(len(self.server.requests), 1)


if __name__ == "__main__":
    unittest.main()
