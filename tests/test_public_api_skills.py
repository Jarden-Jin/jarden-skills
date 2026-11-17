import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PublicSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = load_module("image_client", ROOT / "skills/jarden-image/scripts/client.py")
        cls.video = load_module("video_client", ROOT / "skills/jarden-video/scripts/client.py")
        cls.scanner = load_module("public_skill_check", ROOT / "tools/check_public_api_skills.py")

    def test_public_artifact_allowlist_and_secret_scan(self):
        self.assertEqual(self.scanner.validate(ROOT), [])

    def test_clients_are_identical(self):
        self.assertEqual(
            (ROOT / "skills/jarden-image/scripts/client.py").read_bytes(),
            (ROOT / "skills/jarden-video/scripts/client.py").read_bytes(),
        )

    def test_help_requires_no_credentials_or_network(self):
        for name in ("jarden-image", "jarden-video"):
            result = subprocess.run(
                [sys.executable, str(ROOT / "skills" / name / "scripts/client.py"), "--help"],
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("--personal-key-file", result.stdout)

    def test_cache_is_shared_between_skills_and_scoped_by_service(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"XDG_CONFIG_HOME": temporary, "APPDATA": temporary}
        ):
            first = self.image._credential_dir("https://api.example.test")
            second = self.video._credential_dir("https://api.example.test")
            alternate = self.video._credential_dir("https://other.example.test")
            self.assertEqual(first, second)
            self.assertNotEqual(first, alternate)
            self.assertFalse(first.is_relative_to(ROOT))

    def test_credentials_written_privately_outside_skill(self):
        fixture = "fixture-credential-not-valid-for-any-service"
        with tempfile.TemporaryDirectory() as temporary:
            filename = Path(temporary) / "credentials" / "fixture.key"
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.image._write_secret(filename, fixture)
            self.assertNotIn(fixture, output.getvalue())
            self.assertEqual(filename.read_text().strip(), fixture)
            if os.name != "nt":
                self.assertEqual(filename.stat().st_mode & 0o777, 0o600)

    def test_invalid_personal_key_is_not_cached(self):
        fixture = "fixture-invalid-personal-credential"

        class RejectedClient:
            def __init__(self, base_url, token):
                pass

            def request(self, path):
                raise urllib.error.HTTPError("https://api.example.test/v1/auth", 401, "invalid", {}, None)

        with tempfile.TemporaryDirectory() as temporary:
            supplied = Path(temporary) / "supplied.key"
            supplied.write_text(fixture)
            cache = Path(temporary) / "cache"
            with patch.object(self.image, "Client", RejectedClient), patch.object(
                self.image, "_credential_dir", return_value=cache
            ):
                with self.assertRaisesRegex(RuntimeError, "PERSONAL_KEY_INVALID"):
                    self.image.resolve_client("https://api.example.test", personal_key_file=str(supplied))
            self.assertFalse(cache.exists())

    def test_device_bootstrap_reused_without_printing_secrets(self):
        personal = "fixture-personal-credential-for-tests"
        device = "fixture-device-credential-for-tests"
        registrations = []

        class FixtureClient:
            def __init__(self, base_url, token):
                self.token = token

            def request(self, path, payload=None):
                if path == "/v1/auth":
                    return {"user_id": "fixture-user", "key_id": "fixture-id",
                            "key_kind": "personal" if self.token == personal else "device"}
                if path == "/v1/keys":
                    registrations.append(payload)
                    return {"id": "fixture-device-id", "key": device}
                raise AssertionError(path)

        with tempfile.TemporaryDirectory() as temporary:
            supplied = Path(temporary) / "supplied.key"
            supplied.write_text(personal)
            cache = Path(temporary) / "cache"
            output = io.StringIO()
            with patch.object(self.image, "Client", FixtureClient), patch.object(
                self.image, "_credential_dir", return_value=cache
            ), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                first = self.image.resolve_client("https://api.example.test", personal_key_file=str(supplied))
                second = self.image.resolve_client("https://api.example.test")
            self.assertEqual(first.token, device)
            self.assertEqual(second.token, device)
            self.assertEqual(len(registrations), 1)
            self.assertNotIn(personal, output.getvalue())
            self.assertNotIn(device, output.getvalue())
            metadata = (cache / "metadata.json").read_text()
            self.assertNotIn(personal, metadata)
            self.assertNotIn(device, metadata)

    def test_runtime_key_is_only_sent_to_configured_service(self):
        fixture = "fixture-explicit-device-credential"
        observations = []

        class Response(io.BytesIO):
            pass

        class Opener:
            def open(self, request, timeout):
                observations.append((request.full_url, request.get_header("Authorization")))
                return Response(b'{"data": []}')

        with tempfile.TemporaryDirectory() as temporary:
            filename = Path(temporary) / "device.key"
            filename.write_text(fixture)
            client = self.image.resolve_client("https://api.example.test", token_file=str(filename))
            client.opener = Opener()
            self.assertEqual(client.request("/v1/models"), {"data": []})
            self.assertEqual(observations, [("https://api.example.test/v1/models", "Bearer " + fixture)])

    def test_insecure_remote_url_rejected(self):
        for module in (self.image, self.video):
            with self.assertRaisesRegex(ValueError, "HTTPS_REQUIRED"):
                module.Client("http://api.example.test", "fixture-token")

    def test_redirect_does_not_forward_authorization(self):
        handler = self.image.NoRedirect()
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(
                type("Request", (), {"full_url": "https://api.example.test/v1/models"})(),
                None, 302, "redirect", {}, "https://unrelated.example.test/",
            )

    def test_download_checksum_mismatch_removes_invalid_file(self):
        job = {"job_id": "job_fixture", "status": "succeeded", "outputs": [{
            "output_id": "out_1", "media_type": "image/png",
            "download_path": "/v1/jobs/job_fixture/outputs/out_1",
            "sha256": hashlib.sha256(b"expected").hexdigest(),
        }]}
        client = self.image.Client("https://api.example.test", "fixture-token")

        def fake_request(path, destination):
            destination.write_bytes(b"wrong-content")
            return {"file": str(destination)}

        with tempfile.TemporaryDirectory() as temporary, patch.object(client, "request", fake_request):
            folder = Path(temporary)
            with self.assertRaisesRegex(ValueError, "OUTPUT_CHECKSUM_MISMATCH"):
                client.download(job, folder)
            self.assertEqual(list(folder.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
