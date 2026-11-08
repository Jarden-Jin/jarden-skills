from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

from PIL import Image

SKILL = Path(__file__).resolve().parents[1]
REPO = SKILL.parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

import runtime_activity
import update_runtime


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FixtureMixin:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.temp = Path(self.temporary.name).resolve()
        self.updates = self.temp / "updates"
        self.inst = self.temp / "installed"
        self.new = self.temp / "candidate"
        self.project = self.temp / "project"
        self.project.mkdir()
        (self.project / "project.json").write_text('{"schema_version": 2, "sentinel": true}\n')
        self.environment = mock.patch.dict(os.environ, {
            "JARDEN_UPDATE_HOME": str(self.updates),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def write_skill(self, root: Path, version: str) -> None:
        (root / "scripts").mkdir(parents=True)
        (root / "references").mkdir()
        (root / "tests").mkdir()
        for name in ("check_update.py", "update_runtime.py", "runtime_activity.py"):
            shutil.copy2(SKILL / "scripts" / name, root / "scripts" / name)
            (root / "scripts" / name).chmod(0o644)
        (root / "references" / "project-state.md").write_text("# Project state\n", encoding="utf-8")
        (root / "tests" / "fixture.txt").write_text(f"fixture {version}\n", encoding="utf-8")
        (root / "README.md").write_text(f"README {version}\n", encoding="utf-8")
        (root / "SKILL.md").write_text(
            f'---\nname: jarden-ip-visual\nmetadata:\n  version: "{version}"\n---\n# Fixture {version}\n',
            encoding="utf-8",
        )
        for path in root.rglob("*"):
            if path.is_file():
                path.chmod(0o644)

    def run_command(self, args: list[str], *, cwd: Path | None = None, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, timeout=40)

    def cli(self, root: Path, *args: str, extra_env: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess, dict]:
        result = self.run_command([sys.executable, str(root / "scripts" / "check_update.py"), *args], extra_env=extra_env)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        try:
            payload = json.loads(lines[-1]) if lines else {}
        except json.JSONDecodeError:
            payload = {"parse_error": result.stdout}
        return result, payload

    def make_release_files(self, root: Path, version: str) -> tuple[Path, Path, dict]:
        archive_path = self.temp / f"{root.name}-{version}.zip"
        files = []
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                relative = f"jarden-ip-visual/{path.relative_to(root).as_posix()}"
                data = path.read_bytes()
                info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (0o100000 | 0o644) << 16
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
                files.append({
                    "path": relative,
                    "size": len(data),
                    "sha256": digest(data),
                    "mode": "0644",
                })
        archive_bytes = archive_path.read_bytes()
        commit = "a" * 40
        public = {
            "schema_version": 1,
            "skill": "jarden-ip-visual",
            "repository": "Jarden-Jin/jarden-skills",
            "skill_path": "skills/jarden-ip-visual",
            "channel": "stable",
            "version": version,
            "tag": f"jarden-ip-visual/v{version}",
            "commit": commit,
            "archive": {
                "name": f"jarden-ip-visual-{version}.zip",
                "size": len(archive_bytes),
                "sha256": digest(archive_bytes),
            },
            "files": files,
            "project_schema": {"read": [1, 2], "write": [2]},
            "dependencies": {"python": ">=3.10", "pillow": "required-for-image-operations"},
            "change_type": "minor",
            "migration_notes": (
                f"https://github.com/Jarden-Jin/jarden-skills/blob/{commit}/"
                "skills/jarden-ip-visual/references/project-state.md"
            ),
        }
        release = dict(public)
        release["_download_url"] = "fixture"
        release["_asset_id"] = 1
        release["_index_sha256"] = digest((json.dumps(public, sort_keys=True, ensure_ascii=False) + "\n").encode())
        release_path = self.temp / f"release-{root.name}.json"
        release_path.write_text(json.dumps(release, ensure_ascii=False), encoding="utf-8")
        return release_path, archive_path, public

    def plan_command(self, version: str = "0.2.0", *, extra_env: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess, Path]:
        release_path, archive_path, _ = self.make_release_files(self.new, version)
        output = self.temp / "update-plan.json"
        result, _ = self.source_cli(
            self.inst, release_path, archive_path,
            "plan", "--version", version, "--out", str(output), "--json",
            extra_env=extra_env,
        )
        return result, output

    def source_cli(
        self, root: Path, release_path: Path, archive_path: Path,
        *args: str, extra_env: dict[str, str] | None = None,
    ) -> tuple[subprocess.CompletedProcess, dict]:
        helper = """
import json
import runpy
import sys
from pathlib import Path
from unittest import mock
from urllib.parse import quote
sys.path.insert(0, sys.argv[1])
import update_runtime

index = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
index = {key: value for key, value in index.items() if not key.startswith("_")}
index_bytes = (json.dumps(index, sort_keys=True, ensure_ascii=False) + "\\n").encode("utf-8")
archive = Path(sys.argv[3]).read_bytes()
tag = index["tag"]
base = update_runtime.RELEASE_DOWNLOAD_PREFIX + quote(tag, safe="") + "/"
index_url = base + "release.json"
archive_url = base + index["archive"]["name"]
release = {
    "tag_name": tag, "draft": False, "prerelease": False,
    "assets": [
        {"name": "release.json", "id": 1, "size": len(index_bytes), "browser_download_url": index_url},
        {"name": index["archive"]["name"], "id": 2, "size": len(archive), "browser_download_url": archive_url},
    ],
}

def request(source, url, accept="application/vnd.github+json", limit=None):
    responses = {
        update_runtime.API_ROOT + "/releases/tags/" + quote(tag, safe=""): json.dumps(release).encode(),
        update_runtime.API_ROOT + "/releases?per_page=100": json.dumps([release]).encode(),
        update_runtime.API_ROOT + "/releases/assets/1": index_bytes,
        update_runtime.API_ROOT + "/releases/assets/2": archive,
    }
    if url not in responses:
        raise AssertionError("unexpected source URL: " + url)
    return responses[url]

script = Path(sys.argv[1]) / "check_update.py"
sys.argv = [str(script), *sys.argv[4:]]
with mock.patch.object(update_runtime.Source, "_request", request):
    runpy.run_path(str(script), run_name="__main__")
"""
        result = self.run_command([
            sys.executable, "-c", helper, str(root / "scripts"),
            str(release_path), str(archive_path), *args,
        ], extra_env=extra_env)
        payload = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else {}
        return result, payload

    def plan(self, version: str = "0.2.0", *, extra_env: dict[str, str] | None = None) -> dict:
        result, output = self.plan_command(version, extra_env=extra_env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(output.read_text(encoding="utf-8"))

    def apply(self, plan: Path | dict, *, extra_env: dict[str, str] | None = None, yes: bool = True):
        plan_path = plan if isinstance(plan, Path) else self.temp / "tampered-plan.json"
        if not isinstance(plan, Path):
            plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        args = ["apply", "--plan", str(plan_path), "--json"]
        if yes:
            args.append("--yes")
        return self.cli(self.inst, *args, extra_env=extra_env)

    def snapshot(self, root: Path) -> dict[str, tuple[str, bytes]]:
        return {
            path.relative_to(root).as_posix(): (f"{path.stat().st_mode & 0o777:o}", path.read_bytes())
            for path in sorted(root.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }

    def version(self, root: Path | None = None) -> str:
        return update_runtime.installed_version(root or self.inst)

    def standard_fixture(self, *, candidate_changes: bool = True) -> None:
        self.write_skill(self.inst, "0.1.0")
        self.write_skill(self.new, "0.2.0")
        if candidate_changes:
            (self.new / "README.md").write_text("README 0.2.0 changed\n", encoding="utf-8")
            (self.new / "tests" / "new.txt").write_text("new file\n", encoding="utf-8")

    def inject_candidate_code(self, before_main: str = "", import_lines: str = "") -> None:
        path = self.new / "scripts" / "check_update.py"
        source = path.read_text(encoding="utf-8")
        if import_lines:
            source = source.replace("from pathlib import Path\n", f"from pathlib import Path\n{import_lines}\n", 1)
        if before_main:
            needle = "    args = parser().parse_args(argv)\n"
            self.assertIn(needle, source)
            source = source.replace(needle, needle + before_main, 1)
        path.write_text(source, encoding="utf-8")
        path.chmod(0o644)


class UpdateInstallTests(FixtureMixin, unittest.TestCase):
    def test_apply_yes_installs_verifies_and_rolls_back_without_project_migration(self):
        self.standard_fixture()
        (self.inst / "README.md").write_text("local modified readme\n", encoding="utf-8")
        (self.inst / "local-note.txt").write_text("keep me\n", encoding="utf-8")
        project_before = (self.project / "project.json").read_bytes()
        plan = self.plan()
        self.assertIn("README.md", plan["modified"])
        self.assertIn("local-note.txt", plan["remove"])
        self.assertTrue(Path(plan["backup"]).is_relative_to(self.updates))
        self.assertFalse(Path(plan["backup"]).is_relative_to(self.inst))

        result, payload = self.apply(self.temp / "update-plan.json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(payload["phase"], "committed")
        self.assertEqual(self.version(), "0.2.0")
        self.assertTrue((self.inst / ".jarden-install.json").is_file())
        self.assertEqual((self.project / "project.json").read_bytes(), project_before)
        self.assertTrue(Path(payload["backup"]).is_relative_to(self.updates))

        transaction = payload["transaction"]
        result, payload = self.cli(self.inst, "rollback", "--transaction", transaction, "--yes", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(payload["phase"], "rolled_back")
        self.assertEqual(self.version(), "0.1.0")
        self.assertEqual((self.inst / "README.md").read_text(encoding="utf-8"), "local modified readme\n")
        self.assertEqual((self.inst / "local-note.txt").read_text(encoding="utf-8"), "keep me\n")
        self.assertFalse((self.inst / ".jarden-install.json").exists())
        self.assertEqual((self.project / "project.json").read_bytes(), project_before)

    def test_apply_requires_explicit_yes(self):
        self.standard_fixture()
        plan_path = self.temp / "update-plan.json"
        self.plan()
        before = self.snapshot(self.inst)
        result, payload = self.apply(plan_path, yes=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["code"], "confirmation_required")
        self.assertEqual(self.snapshot(self.inst), before)

    def test_plan_output_inside_install_is_rejected_without_install_changes(self):
        self.standard_fixture()
        release_path, archive_path, _ = self.make_release_files(self.new, "0.2.0")
        before = self.snapshot(self.inst)
        result, payload = self.source_cli(
            self.inst, release_path, archive_path,
            "plan", "--version", "0.2.0", "--out", str(self.inst / "unexpected-plan.json"), "--json",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["code"], "unsafe_path")
        self.assertEqual(self.snapshot(self.inst), before)

    def test_partial_activation_process_exit_restores_full_old_install(self):
        self.standard_fixture()
        self.plan()
        before = self.snapshot(self.inst)
        helper = """
import os
import runpy
import shutil
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
original_copy = shutil.copy2
def interrupted_copy(source, destination, *args, **kwargs):
    if str(destination).endswith(".jarden-tmp"):
        Path(destination).write_bytes(b"interrupted-candidate")
        os._exit(97)
    return original_copy(source, destination, *args, **kwargs)
shutil.copy2 = interrupted_copy
script = str(Path(sys.argv[1]) / "check_update.py")
sys.argv = [script, "apply", "--plan", sys.argv[2], "--yes", "--json"]
runpy.run_path(script, run_name="__main__")
"""
        result = self.run_command([sys.executable, "-c", helper, str(self.inst / "scripts"), str(self.temp / "update-plan.json")])
        self.assertEqual(result.returncode, 97, result.stdout + result.stderr)
        result, payload = self.cli(self.inst, "status", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(payload["needs_recovery"])
        result, payload = self.cli(self.inst, "status", "--recover", "--yes", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(payload["needs_recovery"])
        self.assertEqual(payload["transactions"][0]["phase"], "rolled_back")
        self.assertEqual(self.snapshot(self.inst), before)
        result, payload = self.cli(self.inst, "status", "--recover", "--yes", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(self.inst), before)

    def test_failed_candidate_smoke_restores_old_install(self):
        self.standard_fixture()
        self.inject_candidate_code(before_main='    if args.command == "selfcheck":\n        raise SystemExit(5)\n')
        self.plan()
        before = self.snapshot(self.inst)
        result, payload = self.apply(self.temp / "update-plan.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["code"], "verification_failed")
        self.assertEqual(self.snapshot(self.inst), before)

    def test_rollback_does_not_overwrite_new_local_content_at_removed_path(self):
        self.standard_fixture()
        local = self.inst / "local-note.txt"
        local.write_text("old note approved for removal\n", encoding="utf-8")
        self.plan()
        result, payload = self.apply(self.temp / "update-plan.json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        transaction = payload["transaction"]
        local.write_text("new work created after update\n", encoding="utf-8")
        before = self.snapshot(self.inst)
        result, payload = self.cli(self.inst, "rollback", "--transaction", transaction, "--yes", "--json")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["code"], "local_modified")
        self.assertEqual(self.snapshot(self.inst), before)

    def test_corrupt_cached_archive_is_rejected_at_real_cli(self):
        self.standard_fixture()
        plan = self.plan()
        before = self.snapshot(self.inst)
        Path(plan["archive_path"]).write_bytes(b"not a zip")
        result, payload = self.apply(self.temp / "update-plan.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["code"], "digest_mismatch")
        self.assertEqual(self.snapshot(self.inst), before)

    def test_unknown_plan_fields_are_rejected_before_writes(self):
        self.standard_fixture()
        plan = self.plan()
        before = self.snapshot(self.inst)
        plan["unexpected_install_script"] = "do-not-run"
        result, payload = self.apply(plan)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["code"], "invalid_plan")
        self.assertEqual(self.snapshot(self.inst), before)

    def test_zip_traversal_is_rejected_at_real_cli(self):
        self.standard_fixture()
        plan = self.plan()
        source = Path(plan["archive_path"])
        original = source.read_bytes()
        buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original), "r") as old, zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as new:
            for info in old.infolist():
                data = old.read(info)
                copied = zipfile.ZipInfo(info.filename, date_time=(1980, 1, 1, 0, 0, 0))
                copied.external_attr = info.external_attr
                new.writestr(copied, data)
            evil = zipfile.ZipInfo("jarden-ip-visual/../escape.txt", date_time=(1980, 1, 1, 0, 0, 0))
            evil.external_attr = (0o100000 | 0o644) << 16
            new.writestr(evil, b"escape")
        tampered = buffer.getvalue()
        source.write_bytes(tampered)
        plan["source"]["archive"]["size"] = len(tampered)
        plan["source"]["archive"]["sha256"] = digest(tampered)
        unsigned = dict(plan)
        unsigned.pop("summary")
        plan["summary"] = digest(json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        before = self.snapshot(self.inst)
        result, payload = self.apply(plan)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(payload["code"], {"invalid_archive", "unsafe_path"})
        self.assertEqual(self.snapshot(self.inst), before)
        self.assertFalse((self.temp / "escape.txt").exists())

    def test_active_execution_lease_blocks_install_and_install_takes_no_reader_lease(self):
        self.standard_fixture()
        self.plan()
        holder = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from runtime_activity import execution_lease
with execution_lease(Path(sys.argv[2])):
    print("ready", flush=True)
    time.sleep(8)
"""
        process = subprocess.Popen(
            [sys.executable, "-c", holder, str(self.inst / "scripts"), str(self.inst)],
            env=os.environ.copy(), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            before = self.snapshot(self.inst)
            result, payload = self.apply(self.temp / "update-plan.json")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(payload["code"], "runtime_busy")
            self.assertEqual(self.snapshot(self.inst), before)
        finally:
            process.terminate()
            process.communicate(timeout=5)

    def test_plan_never_executes_candidate_and_apply_does_after_authorization(self):
        self.standard_fixture()
        marker = self.temp / "candidate-executed.txt"
        self.inject_candidate_code(
            import_lines=f'Path({str(marker)!r}).write_text("executed", encoding="utf-8")'
        )
        plan = self.plan()
        self.assertFalse(marker.exists())
        result, payload = self.apply(self.temp / "update-plan.json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(payload["version"], "0.2.0")
        self.assertTrue(marker.is_file())

    def test_local_symlink_is_detected_and_refused(self):
        self.standard_fixture()
        (self.inst / "linked.md").symlink_to(self.inst / "README.md")
        plan = self.plan()
        self.assertIn("linked.md", plan["modified"])
        before = self.snapshot(self.inst)
        result, payload = self.apply(self.temp / "update-plan.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["code"], "unsafe_local_change")
        self.assertEqual(self.snapshot(self.inst), before)
        self.assertTrue((self.inst / "linked.md").is_symlink())

    def test_install_crash_after_switch_recovers_to_verified_new_version(self):
        self.standard_fixture()
        kill_once = self.temp / "kill-once.fixture"
        kill_once.write_text("fault injection", encoding="utf-8")
        self.inject_candidate_code(
            import_lines="import os\nimport signal\nimport time",
            before_main=(
                f"    if args.command == \"selfcheck\" and Path({str(kill_once)!r}).exists():\n"
                f"        Path({str(kill_once)!r}).unlink()\n"
                "        os.kill(os.getppid(), signal.SIGKILL)\n"
                "        time.sleep(1)\n"
            ),
        )
        self.plan()
        first = self.run_command(
            [sys.executable, str(self.inst / "scripts" / "check_update.py"), "apply", "--plan", str(self.temp / "update-plan.json"), "--yes", "--json"],
        )
        self.assertNotEqual(first.returncode, 0)
        self.assertEqual(self.version(), "0.2.0")
        self.assertFalse((self.inst / ".jarden-install.json").exists())
        status, payload = self.cli(self.inst, "status", "--json")
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        self.assertTrue(payload["needs_recovery"])
        recovered, payload = self.cli(self.inst, "status", "--recover", "--yes", "--json")
        self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
        self.assertFalse(payload["needs_recovery"])
        self.assertEqual(payload["transactions"][0]["phase"], "recovered")
        self.assertTrue((self.inst / ".jarden-install.json").is_file())
        self.assertEqual(self.version(), "0.2.0")
        transaction = payload["transactions"][0]["transaction"]
        rolled_back, payload = self.cli(self.inst, "rollback", "--transaction", transaction, "--yes", "--json")
        self.assertEqual(rolled_back.returncode, 0, rolled_back.stdout + rolled_back.stderr)
        self.assertEqual(payload["phase"], "rolled_back")
        self.assertEqual(self.version(), "0.1.0")

    def test_two_installers_are_serialized_or_fail_fast(self):
        self.standard_fixture()
        updater = self.inst / "scripts" / "update_runtime.py"
        source = updater.read_text(encoding="utf-8")
        needle = "        require_idle(home, root)\n"
        self.assertIn(needle, source)
        source = source.replace(
            needle,
            needle + (
                "        if os.environ.get('JARDEN_TEST_HOLD_INSTALL_GATE') == '1':\n"
                "            Path(os.environ['JARDEN_TEST_HOLD_MARKER']).write_text('held', encoding='utf-8')\n"
                "            time.sleep(0.8)\n"
            ),
            1,
        )
        updater.write_text(source, encoding="utf-8")
        self.plan()
        command = [sys.executable, str(self.inst / "scripts" / "check_update.py"), "apply", "--plan", str(self.temp / "update-plan.json"), "--yes", "--json"]
        marker = self.temp / "gate-held.txt"
        first = subprocess.Popen(
            command,
            env={**os.environ, "JARDEN_TEST_HOLD_INSTALL_GATE": "1", "JARDEN_TEST_HOLD_MARKER": str(marker)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not marker.exists():
                time.sleep(0.02)
            self.assertTrue(marker.exists(), "第一个安装器没有持有实例 gate")
            second = self.run_command(command)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(json.loads(second.stdout.splitlines()[-1])["code"], "runtime_busy")
            stdout, stderr = first.communicate(timeout=30)
            self.assertEqual(first.returncode, 0, stdout + stderr)
            self.assertEqual(self.version(), "0.2.0")
        finally:
            if first.poll() is None:
                first.kill()
                first.wait(timeout=5)


class AssetSourceTests(FixtureMixin, unittest.TestCase):
    def test_release_index_and_archive_use_bound_asset_api_ids(self):
        self.write_skill(self.inst, "0.1.0")
        self.write_skill(self.new, "0.2.0")
        _, archive_path, public = self.make_release_files(self.new, "0.2.0")
        index_bytes = json.dumps(public).encode()
        archive = archive_path.read_bytes()
        release = {
            "tag_name": public["tag"], "draft": False, "prerelease": False,
            "assets": [
                {"name": "release.json", "id": 31, "size": len(index_bytes),
                 "browser_download_url": update_runtime.RELEASE_DOWNLOAD_PREFIX + "index"},
                {"name": public["archive"]["name"], "id": 32, "size": len(archive),
                 "browser_download_url": update_runtime.RELEASE_DOWNLOAD_PREFIX + "archive"},
            ],
        }
        source = update_runtime.Source()
        responses = [json.dumps(release).encode(), index_bytes, archive]
        with mock.patch.object(update_runtime, "real_root", return_value=self.inst), \
                mock.patch.object(source, "_request", side_effect=responses) as request:
            actual = source.release("0.2.0")
            self.assertEqual(source.asset(actual), archive)
        calls = request.call_args_list
        self.assertEqual(calls[1].args, (update_runtime.API_ROOT + "/releases/assets/31", "application/octet-stream", update_runtime.MAX_INDEX_BYTES))
        self.assertEqual(calls[2].args, (update_runtime.API_ROOT + "/releases/assets/32", "application/octet-stream", update_runtime.MAX_ARCHIVE_BYTES))
        self.assertEqual(actual["_asset_id"], 32)

    def test_asset_api_accepts_trusted_binary_redirect_but_json_api_does_not(self):
        class Response(io.BytesIO):
            headers = {"Content-Length": "7"}

            def geturl(self):
                return "https://release-assets.githubusercontent.com/example/asset"

        source = update_runtime.Source()
        source._gh_checked = True
        source._token = "synthetic-github-token"
        with mock.patch.object(source._opener, "open", side_effect=lambda *args, **kwargs: Response(b"archive")):
            self.assertEqual(source._request(update_runtime.API_ROOT + "/releases/assets/32", "application/octet-stream"), b"archive")
            for endpoint, accept in (
                ("/releases?per_page=100", "application/vnd.github+json"),
                ("/releases?per_page=100", "application/octet-stream"),
                ("/releases/assets/32", "application/vnd.github+json"),
            ):
                with self.subTest(endpoint=endpoint, accept=accept), self.assertRaises(update_runtime.SourceError):
                    source._request(update_runtime.API_ROOT + endpoint, accept)

    def test_asset_redirect_removes_authorization_and_rejects_other_hosts(self):
        request = urllib.request.Request(
            update_runtime.API_ROOT + "/releases/assets/32",
            headers={"Authorization": "Bearer synthetic-github-token", "Accept": "application/octet-stream"},
        )
        handler = update_runtime._TrustedRedirect()
        redirected = handler.redirect_request(
            request, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/example/asset",
        )
        self.assertIsNotNone(redirected)
        self.assertNotIn("Authorization", dict(redirected.header_items()))
        for destination in ("http://release-assets.githubusercontent.com/asset", "https://untrusted.example/asset"):
            with self.subTest(destination=destination), self.assertRaises(update_runtime.SourceError):
                handler.redirect_request(request, None, 302, "Found", {}, destination)

    def test_json_redirect_is_rejected_before_a_followup_request_is_created(self):
        handler = update_runtime._TrustedRedirect()
        for endpoint, accept in (
            ("/releases?per_page=100", "application/vnd.github+json"),
            ("/releases?per_page=100", "application/octet-stream"),
            ("/releases/assets/32", "application/vnd.github+json"),
        ):
            request = urllib.request.Request(update_runtime.API_ROOT + endpoint, headers={"Accept": accept})
            with self.subTest(endpoint=endpoint, accept=accept), \
                    mock.patch.object(urllib.request.HTTPRedirectHandler, "redirect_request") as follow:
                with self.assertRaises(update_runtime.SourceError):
                    handler.redirect_request(request, None, 302, "Found", {}, "https://github.com/asset")
                follow.assert_not_called()

    def test_binary_redirect_to_github_removes_auth_and_preserves_asset_chain(self):
        request = urllib.request.Request(
            update_runtime.API_ROOT + "/releases/assets/32",
            headers={"Authorization": "Bearer synthetic-github-token", "Accept": "application/octet-stream"},
        )
        handler = update_runtime._TrustedRedirect()
        first = handler.redirect_request(request, None, 302, "Found", {}, "https://github.com/asset")
        self.assertIsNotNone(first)
        self.assertNotIn("Authorization", dict(first.header_items()))
        second = handler.redirect_request(
            first, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/example/asset",
        )
        self.assertIsNotNone(second)
        self.assertNotIn("Authorization", dict(second.header_items()))
        self.assertEqual(second.get_header("Accept"), "application/octet-stream")
        with self.assertRaises(update_runtime.SourceError):
            handler.redirect_request(second, None, 302, "Found", {}, "https://untrusted.example/asset")


class CheckStateTests(FixtureMixin, unittest.TestCase):
    def test_maintenance_subprocesses_never_read_or_inherit_image_credentials(self):
        values = {"HOME": str(self.temp), "PATH": "/usr/bin:/bin", "JARDEN_UPDATE_HOME": str(self.updates)}
        values.update(dict.fromkeys(("JARDEN_IP_VISUAL_IMAGE_API_KEY", "OPENAI_API_KEY"), "synthetic-not-a-credential"))

        class GuardedEnvironment(dict):
            def __getitem__(self, key):
                if key.endswith("API_KEY"):
                    raise AssertionError("维护代码不应取值图片凭据")
                return super().__getitem__(key)

            def items(self):
                raise AssertionError("维护代码不能枚举全部环境值")

            def copy(self):
                raise AssertionError("维护代码不能复制全部环境值")

        self.write_skill(self.inst, "0.1.0")
        response = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with mock.patch.object(os, "environ", GuardedEnvironment(values)), mock.patch.object(update_runtime.subprocess, "run", return_value=response) as runner:
            headers = update_runtime.Source()._headers("application/json", time.monotonic() + 2)
            self.assertNotIn("Authorization", headers)
            self.assertEqual(runner.call_args.kwargs["env"], {key: value for key, value in values.items() if not key.endswith("API_KEY")})
            runner.return_value = subprocess.CompletedProcess([], 0, stdout='{"ok":true,"version":"0.1.0"}', stderr="")
            update_runtime.candidate_smoke(self.inst, "0.1.0")
            self.assertFalse(any(key.endswith("API_KEY") for key in runner.call_args.kwargs["env"]))

    def test_24_hour_cache_one_hour_failure_cooldown_and_two_second_budget(self):
        self.assertEqual(update_runtime.CHECK_TTL, 24 * 60 * 60)
        self.assertEqual(update_runtime.FAILURE_COOLDOWN, 60 * 60)
        self.assertEqual(update_runtime.NETWORK_BUDGET, 2.0)
        self.write_skill(self.inst, "0.1.0")
        home = runtime_activity.instance_home(self.inst)
        update_runtime.atomic_json(home / "check.json", {
            "current": "0.1.0", "latest": "0.2.0", "checked_at": time.time(), "update": True,
        })

        class NeverCalled:
            def latest(self):
                raise AssertionError("24 小时缓存内不应联网")

        with mock.patch.object(update_runtime, "real_root", return_value=self.inst):
            result = update_runtime.check_update(source=NeverCalled())
        self.assertTrue(result["cached"])
        self.assertEqual(result["latest"], "0.2.0")
        (home / "check.json").unlink()
        update_runtime.atomic_json(home / "failures.json", {"last_failure_at": time.time()})

        class AlsoNeverCalled(NeverCalled):
            pass

        with mock.patch.object(update_runtime, "real_root", return_value=self.inst):
            result = update_runtime.check_update(source=AlsoNeverCalled())
        self.assertTrue(result["deferred"])

        class Raising:
            def latest(self):
                raise update_runtime.SourceError("offline", "network_error")

        with mock.patch.object(update_runtime, "real_root", return_value=self.inst):
            with self.assertRaises(update_runtime.UpdateError):
                update_runtime.check_update(force=True, source=Raising())
        self.assertTrue((runtime_activity.instance_home(self.inst) / "failures.json").is_file())

    def test_unknown_index_fields_and_untrusted_repository_are_rejected(self):
        _, _, release = self.make_release_files(self.new, "0.2.0") if self.new.exists() else (None, None, None)
        if release is None:
            self.write_skill(self.new, "0.2.0")
            _, _, release = self.make_release_files(self.new, "0.2.0")
        release["install_script"] = "curl example"
        with self.assertRaises(update_runtime.UpdateError):
            update_runtime.validate_release(release)
        release.pop("install_script")
        release["repository"] = "attacker/repo"
        with self.assertRaises(update_runtime.SourceError):
            update_runtime.validate_release(release)


class PersistentReleaseTests(FixtureMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.standard_fixture()

    def record_path(self) -> Path:
        return self.updates / "releases" / "jarden-ip-visual" / "0.2.0.json"

    def test_plan_cli_persists_fingerprint_and_repeated_process_reuses_record(self):
        before = self.snapshot(self.inst)
        plan = self.plan()
        record = self.record_path()
        expected = {
            "version": "0.2.0", "tag": "jarden-ip-visual/v0.2.0",
            "commit": plan["source"]["commit"],
            "archive_sha256": plan["source"]["archive"]["sha256"],
        }
        self.assertEqual(json.loads(record.read_text(encoding="utf-8")), expected)
        recorded = record.read_bytes()
        modified_at = record.stat().st_mtime_ns
        release_path, archive_path, _ = self.make_release_files(self.new, "0.2.0")
        second = self.temp / "second-plan.json"
        result, payload = self.source_cli(
            self.inst, release_path, archive_path,
            "plan", "--version", "0.2.0", "--out", str(second), "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(payload["source"], plan["source"])
        self.assertEqual(record.read_bytes(), recorded)
        self.assertEqual(record.stat().st_mtime_ns, modified_at)
        self.assertEqual(self.snapshot(self.inst), before)

    def test_plan_cli_rejects_replaced_same_version_archive_and_keeps_old_evidence(self):
        first_plan = self.plan()
        record = self.record_path().read_bytes()
        cached_archive = Path(first_plan["archive_path"]).read_bytes()
        cached_index = Path(first_plan["archive_path"]).with_name("release.json").read_bytes()
        (self.new / "README.md").write_text("same-version replacement\n", encoding="utf-8")
        release_path, archive_path, _ = self.make_release_files(self.new, "0.2.0")
        rejected = self.temp / "replacement-plan.json"
        before = self.snapshot(self.inst)
        result, payload = self.source_cli(
            self.inst, release_path, archive_path,
            "plan", "--version", "0.2.0", "--out", str(rejected), "--json",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["code"], "version_mutated")
        self.assertFalse(rejected.exists())
        self.assertEqual(self.record_path().read_bytes(), record)
        self.assertEqual(Path(first_plan["archive_path"]).read_bytes(), cached_archive)
        self.assertEqual(Path(first_plan["archive_path"]).with_name("release.json").read_bytes(), cached_index)
        self.assertEqual(self.snapshot(self.inst), before)

    def test_plan_cli_rejects_changed_commit_without_archive_change(self):
        plan = self.plan()
        record = self.record_path().read_bytes()
        release_path, archive_path, public = self.make_release_files(self.new, "0.2.0")
        public["commit"] = "b" * 40
        public["migration_notes"] = public["migration_notes"].replace("a" * 40, "b" * 40)
        release_path.write_text(json.dumps(public), encoding="utf-8")
        self.assertEqual(public["archive"], plan["source"]["archive"])
        rejected = self.temp / "changed-commit-plan.json"
        result, payload = self.source_cli(
            self.inst, release_path, archive_path,
            "plan", "--version", "0.2.0", "--out", str(rejected), "--json",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["code"], "version_mutated")
        self.assertFalse(rejected.exists())
        self.assertEqual(self.record_path().read_bytes(), record)

    def test_persistent_release_record_is_shared_across_real_install_instances(self):
        self.plan()
        record = self.record_path().read_bytes()
        other = self.temp / "second-installed"
        self.write_skill(other, "0.1.0")
        self.assertNotEqual(runtime_activity.instance_home(other), runtime_activity.instance_home(self.inst))
        (self.new / "README.md").write_text("replacement for second installation\n", encoding="utf-8")
        release_path, archive_path, _ = self.make_release_files(self.new, "0.2.0")
        output = self.temp / "other-plan.json"
        before = self.snapshot(other)
        result, payload = self.source_cli(
            other, release_path, archive_path,
            "plan", "--version", "0.2.0", "--out", str(output), "--json",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["code"], "version_mutated")
        self.assertFalse(output.exists())
        self.assertEqual(self.record_path().read_bytes(), record)
        self.assertEqual(self.snapshot(other), before)

    def test_plan_cli_rejects_symlinked_release_record_without_following_it(self):
        self.plan()
        record = self.record_path()
        outside = self.temp / "external-record.json"
        recorded = record.read_bytes()
        record.rename(outside)
        record.symlink_to(outside)
        release_path, archive_path, _ = self.make_release_files(self.new, "0.2.0")
        output = self.temp / "linked-record-plan.json"
        result, payload = self.source_cli(
            self.inst, release_path, archive_path,
            "plan", "--version", "0.2.0", "--out", str(output), "--json",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["code"], "version_mutated")
        self.assertFalse(output.exists())
        self.assertEqual(outside.read_bytes(), recorded)
        self.assertTrue(record.is_symlink())


@unittest.skipUnless((REPO / "tools" / "build_release.py").is_file(), "作者发行构建测试只在源码仓库运行")
class ReleaseBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init")
        self.git("config", "user.email", "release-test@example.invalid")
        self.git("config", "user.name", "Release Test")
        skill = self.repo / "skills" / "jarden-ip-visual"
        (skill / "scripts").mkdir(parents=True)
        (skill / "evals").mkdir()
        (skill / "SKILL.md").write_text('---\nname: jarden-ip-visual\nmetadata:\n  version: "0.2.0"\n---\n', encoding="utf-8")
        (skill / "README.md").write_text("committed readme\n", encoding="utf-8")
        (skill / "scripts" / "check_update.py").write_text("print('ok')\n", encoding="utf-8")
        (skill / "evals" / "private.md").write_text("must not ship\n", encoding="utf-8")
        (skill / ".env").write_text("SECRET=must-not-ship\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-m", "fixture")
        self.commit = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "jarden-ip-visual/v0.2.0")
        (skill / "README.md").write_text("dirty worktree must not ship\n", encoding="utf-8")

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(self.repo), *args], text=True, capture_output=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout

    def build(self, name: str) -> subprocess.CompletedProcess:
        return subprocess.run([
            sys.executable, str(REPO / "tools" / "build_release.py"),
            "--repo", str(self.repo), "--version", "0.2.0", "--commit", self.commit,
            "--change-type", "minor", "--output-dir", str(self.root / name),
        ], text=True, capture_output=True, timeout=40)

    def test_deterministic_release_from_commit_excludes_evals_and_external_index(self):
        first = self.build("dist-a")
        second = self.build("dist-b")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        zip_a = self.root / "dist-a" / "jarden-ip-visual-0.2.0.zip"
        zip_b = self.root / "dist-b" / "jarden-ip-visual-0.2.0.zip"
        index_a = self.root / "dist-a" / "release.json"
        index_b = self.root / "dist-b" / "release.json"
        self.assertEqual(zip_a.read_bytes(), zip_b.read_bytes())
        self.assertEqual(index_a.read_bytes(), index_b.read_bytes())
        release = json.loads(index_a.read_text(encoding="utf-8"))
        self.assertEqual(release["tag"], "jarden-ip-visual/v0.2.0")
        self.assertEqual(release["commit"], self.commit)
        self.assertEqual(release["archive"]["sha256"], digest(zip_a.read_bytes()))
        with zipfile.ZipFile(zip_a) as archive:
            names = archive.namelist()
            self.assertNotIn("jarden-ip-visual/evals/private.md", names)
            self.assertNotIn("jarden-ip-visual/.env", names)
            self.assertNotIn("jarden-ip-visual/release.json", names)
            self.assertTrue(all(name.startswith("jarden-ip-visual/") for name in names))
            self.assertEqual(archive.read("jarden-ip-visual/README.md"), b"committed readme\n")
            self.assertEqual(sorted(names), sorted(item["path"] for item in release["files"]))
        self.assertFalse(any("evals" in item["path"] for item in release["files"]))
        self.assertTrue((self.root / "dist-a" / "release.json").is_file())

    def test_builder_refuses_missing_or_wrong_tag_and_never_overwrites(self):
        skill = self.repo / "skills" / "jarden-ip-visual"
        (skill / "SKILL.md").write_text('---\nname: jarden-ip-visual\nmetadata:\n  version: "0.2.1"\n---\n', encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-m", "next")
        next_commit = self.git("rev-parse", "HEAD").strip()
        missing = subprocess.run([
            sys.executable, str(REPO / "tools" / "build_release.py"),
            "--repo", str(self.repo), "--version", "0.2.1", "--commit", next_commit,
            "--change-type", "patch", "--output-dir", str(self.root / "missing"),
        ], text=True, capture_output=True, timeout=30)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("不存在", missing.stdout)
        built = self.build("stable")
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        again = self.build("stable")
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("拒绝覆盖", again.stdout)


class ReaderConcurrencyTests(FixtureMixin, unittest.TestCase):
    def test_parallel_reader_registration_via_project_cli_keeps_all_writes(self):
        job = self.temp / "reader-project"
        image = self.temp / "input.png"
        Image.new("RGB", (320, 400), (20, 160, 80)).save(image)
        project_script = SKILL / "scripts" / "project.py"
        init = self.run_command([sys.executable, str(project_script), "init", str(job), "--input", str(image), "--kind", "wireframe"])
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        initial = json.loads((job / "project.json").read_text(encoding="utf-8"))["revision"]
        processes = [
            subprocess.Popen(
                [sys.executable, str(project_script), "set", str(job), f"brief.field{number}=value{number}"],
                env=os.environ.copy(), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            for number in range(4)
        ]
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stdout + stderr)
        state = json.loads((job / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(state["revision"], initial + 4)
        for number in range(4):
            self.assertEqual(state["brief"][f"field{number}"], f"value{number}")


if __name__ == "__main__":
    unittest.main()
