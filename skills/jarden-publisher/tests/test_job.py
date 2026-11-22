import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from test_package import write_json, write_png


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
EDITOR_URLS = {
    "xiaohongshu": "https://creator.xiaohongshu.com/publish/publish?target=image",
    "douyin": "https://creator.douyin.com/creator-micro/content/post/image?enter_from=draft",
    "bilibili": "https://member.bilibili.com/platform/upload/text/new-edit",
    "tiktok": "https://www.tiktok.com/tiktokstudio/upload",
    "instagram": "https://www.instagram.com/",
}
READY_CHECKS = ("mode", "images", "title", "body", "topics", "settings", "final_control")
WORK_ID = "0123456789abcdef01234567"
WORK_URL = f"https://www.xiaohongshu.com/explore/{WORK_ID}"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class JobTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.job = self.root / "job"
        self.state_file = self.job / "job.json"
        self.sessions = {
            platform: {
                "space": str(index),
                "tab": f"p{index}",
                "account": f"test-account-{platform}",
                "url": url,
            }
            for index, (platform, url) in enumerate(EDITOR_URLS.items(), start=1)
        }
        cover = self.root / "02-cover.png"
        scene = self.root / "01-scene.png"
        write_png(cover, (210, 180, 150))
        write_png(scene, (50, 110, 170))
        body = self.root / "body.txt"
        body.write_text("第一段  保留空格。\n\n第二段保留末尾换行。 #DesignDiary\n", encoding="utf-8")
        placements = self.root / "placements.json"
        write_json(placements, [{"image": 1, "after": 0}, {"image": 2, "after": 2}])
        self.package = self.succeed(self.run_cli(
            "package.py", "build", "--out", self.job,
            "--platform", "xiaohongshu", "--platform", "douyin", "--platform", "bilibili",
            "--platform", "tiktok", "--platform", "instagram",
            "--title", "毛绒设计", "--body-file", body,
            "--article-file", body, "--placements-file", placements,
            "--image", cover, "--image", scene, "--topic", "设计记录",
        ))
        self.assertFalse(any(self.package["blockers"].values()), self.package["blockers"])

    def run_cli(self, script, *args):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / script), *map(str, args)],
            cwd=self.root,
            capture_output=True,
            encoding="utf-8",
            timeout=20,
        )

    def succeed(self, result):
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def reject(self, result, previous_state):
        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertTrue(json.loads(result.stdout)["error"])
        self.assertEqual(self.state_file.read_bytes(), previous_state)

    def status(self):
        return self.succeed(self.run_cli("job.py", "status", self.job))

    def begin(self, platform):
        flags = []
        for key, value in self.sessions[platform].items():
            flags.extend((f"--{key}", value))
        return self.run_cli("job.py", "begin", self.job, platform, *flags)

    def record(self, command, platform, evidence, *args):
        evidence_file = self.root / "evidence.json"
        write_json(evidence_file, evidence)
        if command == "authorize" and platform in ("tiktok", "instagram") and "--review-id" not in args:
            review = self.status()["platforms"][platform].get("review_id")
            if review:
                args += ("--review-id", review)
        return self.run_cli("job.py", command, self.job, platform, "--file", evidence_file, *args)

    def evidence(self, platform, status, **fields):
        return {
            **self.sessions[platform],
            "status": status,
            "observed_at": now_iso(),
            "fingerprint": self.package["fingerprint"],
            "summary": "本地测试模拟页面回读",
            **fields,
        }

    def ready_evidence(self, platform, **settings):
        target = self.package["platforms"][platform]
        readback = {key: target[key] for key in ("title", "body", "topics", "settings")}
        readback["image_sha256"] = [image["sha256"] for image in target["images"]]
        platform_settings = {
            "xiaohongshu": {"original": False, "allow_copy": True},
            "douyin": {"allow_save": True, "cross_posting": False},
            "bilibili": {"original": False, "comments_enabled": True, "featured_comments": False},
        }
        if platform in ("tiktok", "instagram"):
            del readback["body"]
            readback.update(mode=target["kind"], caption=target["caption"], topics=["DesignDiary", *target["topics"]])
            if platform == "instagram":
                del readback["title"]
            platform_settings[platform] = {
                "comments_enabled": True, "branded_content": "not_available", "cross_posting": False,
            }
        readback["settings"] = {
            "visibility": "public", "scheduled": False, "ai_generated": False,
            "cover": "text_preview" if platform == "bilibili" else "first_image",
            **platform_settings[platform], **settings,
        }
        if platform == "bilibili":
            readback["placements"] = target["placements"]
        return self.evidence(
            platform, "ready",
            checks={**dict.fromkeys(READY_CHECKS, True), "durable_draft": False},
            readback=readback,
        )

    def make_ready(self, platform, **settings):
        self.succeed(self.begin(platform))
        self.succeed(self.record("observe", platform, self.ready_evidence(platform, **settings)))
        self.assertEqual(self.status()["platforms"][platform]["status"], "ready")

    def authorize(self, platform, instruction="确认发布这篇到小红书"):
        return self.record(
            "authorize", platform, self.ready_evidence(platform),
            "--instruction", instruction,
        )

    def test_frozen_package_starts_each_platform_once(self):
        package_file = self.job / "package.json"
        frozen_package = package_file.read_bytes()
        self.assertEqual(self.status(), {"schema_version": 2, "platforms": {}})
        self.assertFalse(self.state_file.exists())
        self.succeed(self.begin("xiaohongshu"))
        first = self.status()["platforms"]["xiaohongshu"]
        self.succeed(self.begin("douyin"))
        state = self.status()
        self.assertEqual(state["schema_version"], 2)
        self.assertEqual(set(state["platforms"]), {"xiaohongshu", "douyin"})
        self.assertEqual(state["platforms"]["xiaohongshu"], first)
        for platform, entry in state["platforms"].items():
            self.assertEqual(entry["status"], "preparing")
            self.assertEqual(entry["fingerprint"], self.package["fingerprint"])
            for key, value in self.sessions[platform].items():
                self.assertEqual(entry[key], value)
        before = self.state_file.read_bytes()
        self.reject(self.begin("xiaohongshu"), before)
        self.reject(self.begin("douyin"), before)
        self.assertEqual(package_file.read_bytes(), frozen_package)

    def test_legacy_job_keeps_existing_platform_when_a_second_begins(self):
        platform = "xiaohongshu"
        write_json(self.state_file, {
            "schema_version": 1,
            "status": "preparing",
            "platform": platform,
            "fingerprint": self.package["fingerprint"],
            "started_at": now_iso(),
            **self.sessions[platform],
        })
        before = self.state_file.read_bytes()
        self.assertEqual(self.status()["platforms"][platform]["status"], "preparing")
        self.assertEqual(self.state_file.read_bytes(), before)
        self.reject(self.begin(platform), before)
        self.succeed(self.begin("douyin"))
        self.succeed(self.record("observe", platform, self.ready_evidence(platform)))
        envelope = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(envelope["schema_version"], 2)
        entries = envelope["platforms"]
        self.assertEqual(entries[platform]["status"], "ready")
        self.assertEqual(entries[platform]["account"], self.sessions[platform]["account"])
        self.assertEqual(entries["douyin"]["status"], "preparing")

    def test_upload_observation_does_not_make_the_job_ready(self):
        platform = "xiaohongshu"
        self.succeed(self.begin(platform))
        observation = self.evidence(platform, "editing", summary="传图后仍在填写正文")
        self.succeed(self.record("observe", platform, observation))
        state = self.status()["platforms"][platform]
        self.assertEqual(state["status"], "editing")
        self.assertEqual(state["evidence"], observation)

    def test_ready_requires_matching_account_and_complete_readback(self):
        platform = "xiaohongshu"
        self.succeed(self.begin(platform))
        before = self.state_file.read_bytes()
        ready = self.ready_evidence(platform)
        cases = {
            "account": {**ready, "account": "another-test-account"},
            "fingerprint": {**ready, "fingerprint": "0" * 64},
            "checks": {**ready, "checks": {**ready["checks"], "final_control": False}},
            "missing readback": {key: value for key, value in ready.items() if key != "readback"},
        }
        wrong_values = {
            "title": "另一篇测试稿",
            "body": "正文被替换。",
            "topics": ["其他话题"],
            "image_sha256": list(reversed(ready["readback"]["image_sha256"])),
            "settings": {**ready["readback"]["settings"], "original": True},
        }
        for field, incorrect in wrong_values.items():
            cases[field] = {**ready, "readback": {**ready["readback"], field: incorrect}}
        for mismatch, invalid in cases.items():
            with self.subTest(mismatch=mismatch):
                self.reject(self.record("observe", platform, invalid), before)
        self.succeed(self.record("observe", platform, self.ready_evidence(platform)))
        self.assertEqual(self.status()["platforms"][platform]["status"], "ready")

    def test_bilibili_ready_requires_matching_article_placements(self):
        platform = "bilibili"
        self.succeed(self.begin(platform))
        before = self.state_file.read_bytes()
        invalid = self.ready_evidence(platform)
        invalid["readback"]["placements"] = [{"image": 1, "after": 1}, {"image": 2, "after": 2}]
        self.reject(self.record("observe", platform, invalid), before)
        self.succeed(self.record("observe", platform, self.ready_evidence(platform)))
        self.assertEqual(self.status()["platforms"][platform]["status"], "ready")

    def test_ready_requires_explicit_per_platform_options(self):
        platform = "douyin"
        self.succeed(self.begin(platform))
        before = self.state_file.read_bytes()
        for key in self.ready_evidence(platform)["readback"]["settings"]:
            with self.subTest(missing=key):
                evidence = self.ready_evidence(platform)
                del evidence["readback"]["settings"][key]
                self.reject(self.record("observe", platform, evidence), before)
        for wrong_page in ("https://creator.douyin.com/creator-micro/content/upload", "https://www.douyin.com/creator-micro/content/post/image", "https://creator.douyin.com/creator-micro/content/post/video"):
            with self.subTest(wrong_page=wrong_page):
                evidence = {**self.ready_evidence(platform), "url": wrong_page}
                self.reject(self.record("observe", platform, evidence), before)
        self.succeed(self.record("observe", platform, self.ready_evidence(platform)))

    def test_custom_cover_requires_source_identity(self):
        platform = "bilibili"
        self.succeed(self.begin(platform))
        before = self.state_file.read_bytes()
        unverified = self.ready_evidence(platform, cover="custom_image")
        self.reject(self.record("observe", platform, unverified), before)
        verified = self.ready_evidence(platform, cover="custom_image", cover_source_sha256=unverified["readback"]["image_sha256"][0])
        self.succeed(self.record("observe", platform, verified))

    def test_authorization_refuses_scheduled_or_cross_posted_submission(self):
        for platform, instruction, settings in (
            ("xiaohongshu", "确认发布这篇到小红书", {"scheduled": True}),
            ("douyin", "确认发布这篇到抖音", {"cross_posting": True}),
            ("instagram", "确认发布这篇到 Instagram", {"cross_posting": True}),
            ("tiktok", "确认发布这篇到 TikTok", {"scheduled": True}),
        ):
            with self.subTest(platform=platform):
                self.make_ready(platform, **settings)
                before = self.state_file.read_bytes()
                evidence = self.ready_evidence(platform, **settings)
                self.reject(self.record("authorize", platform, evidence, "--instruction", instruction), before)

    def test_authorization_refuses_stale_or_changed_options(self):
        platform = "xiaohongshu"
        self.make_ready(platform)
        before = self.state_file.read_bytes()
        changed = self.ready_evidence(platform, visibility="private")
        stale = self.ready_evidence(platform)
        stale["observed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
        previous = self.status()["platforms"][platform]["evidence"]
        for evidence in (changed, stale, previous):
            self.reject(self.record("authorize", platform, evidence, "--instruction", "确认发布这篇到小红书"), before)

    def test_single_platform_authorization_still_names_target(self):
        platform = "xiaohongshu"
        write_json(self.job / "package.json", self.succeed(self.run_cli(
            "package.py", "build", "--dry-run", "--title", "毛绒设计",
            "--image", self.root / "02-cover.png", "--body-file", self.root / "body.txt", "--topic", "设计记录",
        )))
        self.package = json.loads((self.job / "package.json").read_text())
        self.make_ready(platform)
        before = self.state_file.read_bytes()
        self.reject(self.authorize(platform, "确认发布这篇"), before)
        self.succeed(self.authorize(platform, "确认发布这篇到小红书"))

    def test_authorization_reserves_one_attempt_and_keeps_unknown_without_receipt(self):
        platform = "xiaohongshu"
        self.make_ready(platform)
        before = self.state_file.read_bytes()
        for unauthorized in ("请准备草稿", "确认发布这篇", "确认发布这篇到抖音"):
            with self.subTest(instruction=unauthorized):
                self.reject(self.authorize(platform, unauthorized), before)
        instruction = "确认发布这篇到小红书"
        self.succeed(self.authorize(platform, instruction))
        authorized = self.status()["platforms"][platform]
        self.assertEqual(authorized["status"], "publish_unknown")
        self.assertEqual(authorized["authorization"]["instruction"], instruction)
        self.succeed(self.record(
            "observe", platform, self.evidence(platform, "blocked", summary="尚未看到提交回执"),
        ))
        observed = self.status()["platforms"][platform]
        self.assertEqual(observed["status"], "publish_unknown")
        self.assertEqual(observed["authorization"], authorized["authorization"])
        self.reject(self.authorize(platform, instruction), self.state_file.read_bytes())

    def test_cannot_confirm_publication_without_authorization(self):
        platform = "xiaohongshu"
        self.make_ready(platform)
        before = self.state_file.read_bytes()
        receipt = self.evidence(platform, "confirmed", work_url=WORK_URL, publication="published")
        self.reject(self.record("observe", platform, receipt), before)
        self.assertEqual(self.status()["platforms"][platform]["status"], "ready")

    def test_overseas_readback_requires_photo_mode_and_exact_caption(self):
        for platform, wrong_mode, wrong_url in (
            ("tiktok", "video", "https://www.tiktok.com/login"),
            ("instagram", "reel", "https://www.instagram.com/accounts/login/"),
        ):
            with self.subTest(platform=platform):
                self.succeed(self.begin(platform))
                before = self.state_file.read_bytes()
                ready = self.ready_evidence(platform, visibility="followers")
                invalid = self.ready_evidence(platform)
                del invalid["readback"]["settings"]["branded_content"]
                self.reject(self.record("observe", platform, invalid), before)
                self.reject(self.record("observe", platform, {**ready, "url": wrong_url}), before)
                for field, wrong in (
                    ("mode", wrong_mode), ("caption", "Wrong caption"),
                    ("topics", self.package["platforms"][platform]["topics"]),
                    ("image_sha256", ready["readback"]["image_sha256"][::-1]),
                ):
                    invalid = {**ready, "readback": {**ready["readback"], field: wrong}}
                    self.reject(self.record("observe", platform, invalid), before)
                self.succeed(self.record("observe", platform, ready))

    def test_tiktok_cover_must_match_one_of_the_frozen_photos(self):
        platform = "tiktok"
        self.succeed(self.begin(platform))
        before = self.state_file.read_bytes()
        invalid = self.ready_evidence(platform, cover="selected_image", cover_source_sha256="0" * 64)
        self.reject(self.record("observe", platform, invalid), before)
        second = self.package["platforms"][platform]["images"][1]["sha256"]
        ready = self.ready_evidence(platform, cover="selected_image", cover_source_sha256=second)
        self.succeed(self.record("observe", platform, ready))

    def test_instagram_authorization_requires_the_effective_sharing_destination(self):
        platform = "instagram"
        self.make_ready(platform, cross_posting="not_available")
        before = self.state_file.read_bytes()
        evidence = self.ready_evidence(platform, cross_posting="not_available")
        self.reject(
            self.record("authorize", platform, evidence, "--instruction", "确认发布这篇到 Instagram"),
            before,
        )

    def test_instagram_audience_change_requires_a_new_review(self):
        platform = "instagram"
        self.make_ready(platform, visibility="followers")
        reviewed = self.status()["platforms"][platform]["review_id"]
        updated = self.succeed(self.record("observe", platform, self.ready_evidence(platform, visibility="public")))
        self.assertNotEqual(updated["review_id"], reviewed)
        before = self.state_file.read_bytes()
        instruction = "确认发布这篇到 Instagram"
        for unused_review in ("", reviewed):
            with self.subTest(review=unused_review):
                self.reject(self.record(
                    "authorize", platform, self.ready_evidence(platform, visibility="public"),
                    "--instruction", instruction, "--review-id", unused_review,
                ), before)
        self.succeed(self.record(
            "authorize", platform, self.ready_evidence(platform, visibility="public"),
            "--instruction", instruction, "--review-id", updated["review_id"],
        ))

    def test_overseas_authorization_and_receipts_are_platform_specific(self):
        for platform, instruction, wrong_instruction, identifier, work_url, wrong_url in (
            ("tiktok", "确认发布这篇到 TikTok", "确认发布这篇到抖音", "12345",
             "https://www.tiktok.com/@test_account/photo/12345", "https://www.tiktok.com/@test_account/video/12345"),
            ("instagram", "Publish this post on Instagram", "确认发布这篇到 TikTok", "AbCdef12_3-",
             "https://www.instagram.com/p/AbCdef12_3-/", "https://www.instagram.com/reel/AbCdef12_3-/"),
        ):
            with self.subTest(platform=platform):
                self.make_ready(platform)
                before = self.state_file.read_bytes()
                self.reject(self.authorize(platform, wrong_instruction), before)
                self.reject(self.authorize(platform, f"发到 {platform} 了"), before)
                self.succeed(self.authorize(platform, instruction))
                attempt = self.state_file.read_bytes()
                self.reject(self.authorize(platform, instruction), attempt)
                wrong_media = self.evidence(platform, "confirmed", work_url=wrong_url, publication="published")
                self.reject(self.record("observe", platform, wrong_media), attempt)
                wrong_page = self.evidence(
                    platform, "confirmed", url=wrong_url, work_id=identifier, publication="published",
                )
                self.reject(self.record("observe", platform, wrong_page), attempt)
                if platform == "instagram":
                    wrong_case = self.evidence(
                        platform, "confirmed", work_id=identifier.lower(), work_url=work_url,
                        publication="published",
                    )
                    self.reject(self.record("observe", platform, wrong_case), attempt)
                receipt = self.evidence(
                    platform, "confirmed", work_id=identifier, work_url=work_url, publication="published",
                )
                result = self.succeed(self.record("observe", platform, receipt))
                self.assertEqual(result["work_id"], identifier)
                self.assertEqual(result["work_url"], work_url)

    def test_manual_phone_publication_reconciles_the_original_package(self):
        self.succeed(self.begin("tiktok"))
        self.succeed(self.record("observe", "tiktok", self.evidence("tiktok", "blocked")))
        for platform, url, work_id in (
            ("tiktok", "https://www.tiktok.com/@test_account/photo/12345", "12345"),
            ("instagram", "https://www.instagram.com/p/AbCdef12_3-/", "AbCdef12_3-"),
        ):
            with self.subTest(platform=platform):
                readback = self.ready_evidence(platform)["readback"]
                del readback["settings"]
                receipt = self.evidence(
                    platform, "confirmed", url=url, publication="published", readback=readback,
                )
                before = self.state_file.read_bytes()
                wrong_copy = {**receipt, "readback": {**readback, "caption": "Someone else's post"}}
                self.reject(self.record("reconcile", platform, wrong_copy), before)
                if platform == "tiktok":
                    wrong_account = {**receipt, "account": "different-test-account"}
                    self.reject(self.record("reconcile", platform, wrong_account), before)
                result = self.succeed(self.record("reconcile", platform, receipt))
                self.assertEqual(result["status"], "confirmed")
                self.assertEqual(result["work_id"], work_id)
                self.assertEqual(result["work_url"], url)
                self.assertNotIn("authorization", result)
                self.assertEqual(self.status()["platforms"][platform], result)
                self.reject(self.authorize(platform, f"确认发布这篇到 {platform}"), self.state_file.read_bytes())

    def test_confirmation_requires_a_consistent_platform_receipt_and_publication(self):
        platform = "xiaohongshu"
        self.make_ready(platform)
        self.succeed(self.authorize(platform))
        before = self.state_file.read_bytes()
        invalid_receipts = {
            "missing work identity": {"publication": "published"},
            "wrong platform": {"work_url": "https://www.douyin.com/note/12345", "publication": "published"},
            "disagreeing identity": {"work_id": "f" * 24, "work_url": WORK_URL, "publication": "published"},
            "missing publication": {"work_id": WORK_ID},
            "unknown publication": {"work_id": WORK_ID, "publication": "unknown"},
        }
        for mismatch, receipt in invalid_receipts.items():
            with self.subTest(mismatch=mismatch):
                evidence = self.evidence(platform, "confirmed", **receipt)
                self.reject(self.record("observe", platform, evidence), before)
        for publication, identifier in (
            ("submitted", {"work_id": WORK_ID}),
            ("in_review", {"work_id": WORK_ID, "work_url": WORK_URL}),
            ("published", {"work_url": WORK_URL}),
        ):
            with self.subTest(publication=publication):
                evidence = self.evidence(platform, "confirmed", publication=publication, **identifier)
                self.succeed(self.record("observe", platform, evidence))
                state = self.status()["platforms"][platform]
                self.assertEqual(state["status"], "confirmed")
                self.assertEqual(state["work_id"], WORK_ID)
                self.assertEqual(state["publication"], publication)
        before = self.state_file.read_bytes()
        other_work = self.evidence(platform, "confirmed", work_id="f" * 24, publication="published")
        self.reject(self.record("observe", platform, other_work), before)


if __name__ == "__main__":
    unittest.main()
