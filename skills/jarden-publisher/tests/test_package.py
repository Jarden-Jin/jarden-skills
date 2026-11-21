import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path


CLI = Path(__file__).resolve().parents[1] / "scripts" / "package.py"
WIDTH, HEIGHT = 6, 8
TITLE = "毛绒角色设计"
LEGACY_BODY = "从照片出发做了一组设计。\n\nAI 生成的视觉概念，尚未打样。"
TOPICS = ("设计记录", "毛绒玩具", "AI设计")
POST_PLATFORM = ("--platform", "xiaohongshu")


def write_png(path, rgb):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0)
    scanlines = (b"\x00" + bytes(rgb) * WIDTH) * HEIGHT
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class PackageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-B", str(CLI), *map(str, args)],
            cwd=self.root,
            capture_output=True,
            encoding="utf-8",
            timeout=20,
        )

    def package_json(self, result):
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        package = json.loads(result.stdout)
        self.assertEqual(package["schema_version"], 1)
        self.assertIsInstance(package["fingerprint"], str)
        self.assertTrue(package["fingerprint"])
        return package

    def assert_images(self, images, paths):
        self.assertEqual([image["path"] for image in images], list(map(str, paths)))
        for image, path in zip(images, paths):
            with self.subTest(image=path.name):
                self.assertEqual(image["sha256"], sha256(path))
                self.assertEqual((image["width"], image["height"]), (WIDTH, HEIGHT))
                self.assertEqual(image["size_bytes"], path.stat().st_size)
                self.assertIsInstance(image["role"], str)
                self.assertIsInstance(image["slot"], str)

    def assert_blocker(self, package, platform):
        blockers = package["blockers"].get(platform)
        self.assertIsInstance(blockers, list)
        self.assertTrue(blockers)
        self.assertTrue(all(isinstance(message, str) and message.strip() for message in blockers))

    def legacy_post(self, directory):
        post = directory / "post"
        post.mkdir(parents=True)
        pictures = [post / "02-cover.png", post / "01-scene.png"]
        write_png(pictures[0], (210, 180, 150))
        write_png(pictures[1], (50, 110, 170))
        tags = [f"#{topic}" for topic in TOPICS]
        caption = post / "caption.md"
        caption.write_bytes(f"{TITLE}\n\n{LEGACY_BODY}\n\n{' '.join(tags)}\n".encode("utf-8"))
        manifest = {
            "schema_version": 1,
            "signature": "a" * 64,
            "requirements": "b" * 64,
            "title": TITLE,
            "body": LEGACY_BODY,
            "tags": tags,
            "images": [
                {"file": pictures[0].name, "source": "rounds/cover.png", "role": "cover", "slot": "cover"},
                {"file": pictures[1].name, "source": "rounds/scene.png", "role": "scene", "slot": "scene-1"},
            ],
            "size": [WIDTH, HEIGHT],
            "checklist": [],
            "warnings": [],
            "files": {path.name: sha256(path) for path in (caption, *reversed(pictures))},
        }
        for entry in manifest["images"]:
            entry["source_sha256"] = hashlib.sha256(entry["source"].encode("utf-8")).hexdigest()
            entry["source_size"] = [WIDTH * 2, HEIGHT * 2]
            entry["overlay"] = {}
            entry["fit"] = "cover"
        write_json(post / "manifest.json", manifest)
        return post, pictures, manifest

    def manual_images(self, count=2):
        images = []
        for index in range(count):
            path = self.root / f"插图 {count - index}.png"
            write_png(path, ((30 + index * 50) % 256, 140, 200))
            images.append(path)
        return images

    def manual_args(self, images):
        args = ["--title", TITLE]
        for image in images:
            args.extend(("--image", image))
        for topic in TOPICS:
            args.extend(("--topic", topic))
        return args

    def test_legacy_import_preserves_order_copy_and_absolute_image_paths(self):
        post, pictures, _ = self.legacy_post(self.root)
        job = self.root / "job"
        package = self.package_json(self.run_cli("build", "--post", post, "--out", job, *POST_PLATFORM))
        self.assertEqual(set(package["platforms"]), {"xiaohongshu"})
        draft = package["platforms"]["xiaohongshu"]
        self.assertEqual(draft["title"], TITLE)
        self.assertEqual(draft["body"], LEGACY_BODY)
        self.assertIs(draft["settings"]["ai_generated"], True)
        self.assertEqual([topic.removeprefix("#") for topic in draft["topics"]], list(TOPICS))
        self.assert_images(draft["images"], pictures)
        self.assertEqual(
            [(image["role"], image["slot"]) for image in draft["images"]],
            [("cover", "cover"), ("scene", "scene-1")],
        )
        self.assertEqual(self.package_json(self.run_cli("check", job)), package)

    def test_preflight_groups_platform_options_without_creating_a_job(self):
        post, pictures, _ = self.legacy_post(self.root)
        task = self.root / "preflight"
        self.package_json(self.run_cli(
            "build", "--post", post, *POST_PLATFORM, "--platform", "douyin", "--out", task,
        ))
        result = subprocess.run(
            [sys.executable, "-B", str(CLI.with_name("preflight.py")), str(task)],
            cwd=self.root, capture_output=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["space_status"], "not_checked")
        self.assertEqual(set(report["platforms"]), {"xiaohongshu", "douyin"})
        short_note = report["platforms"]["xiaohongshu"]
        self.assertEqual(short_note["cover"]["candidate"], str(pictures[0]))
        self.assertEqual(short_note["body_chars"], len(LEGACY_BODY))
        self.assertEqual(short_note["browser"]["session"], "not_checked")
        self.assertTrue(any("位置" in action for action in short_note["attention"]))
        self.assertIn("同时发布", report["platforms"]["douyin"]["review_options"])
        self.assertFalse((task / "job.json").exists())

    def test_legacy_ai_provenance_survives_copy_override(self):
        post, _, _ = self.legacy_post(self.root)
        revised = self.root / "new-caption.txt"
        revised.write_text("接下来看看角色的几个角度。", encoding="utf-8")
        package = self.package_json(self.run_cli(
            "build", "--post", post, "--body-file", revised, "--dry-run",
        ))
        draft = package["platforms"]["xiaohongshu"]
        self.assertEqual(draft["body"], revised.read_text(encoding="utf-8"))
        self.assertIs(draft["settings"]["ai_generated"], True)

    def test_legacy_rejects_tampered_or_incomplete_picture_set(self):
        for problem in ("changed_bytes", "missing_file", "missing_digest"):
            with self.subTest(problem=problem):
                post, pictures, manifest = self.legacy_post(self.root / problem)
                if problem == "changed_bytes":
                    write_png(pictures[1], (170, 50, 110))
                elif problem == "missing_file":
                    pictures[1].unlink()
                else:
                    del manifest["files"][pictures[1].name]
                    write_json(post / "manifest.json", manifest)
                result = self.run_cli("build", "--post", post, "--dry-run", *POST_PLATFORM)
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_legacy_rejects_wrong_caption_digest_and_rehashed_caption_drift(self):
        with self.subTest(problem="wrong caption digest"):
            post, _, manifest = self.legacy_post(self.root / "stale")
            manifest["files"]["caption.md"] = "0" * 64
            write_json(post / "manifest.json", manifest)
            result = self.run_cli("build", "--post", post, "--dry-run", *POST_PLATFORM)
            self.assertNotEqual(result.returncode, 0, result.stdout)

        with self.subTest(problem="caption hash matches but body differs"):
            post, _, manifest = self.legacy_post(self.root / "drift")
            caption = post / "caption.md"
            text = caption.read_text(encoding="utf-8").replace(LEGACY_BODY, "正文被替换。")
            caption.write_bytes(text.encode("utf-8"))
            manifest["files"]["caption.md"] = sha256(caption)
            write_json(post / "manifest.json", manifest)
            result = self.run_cli("build", "--post", post, "--dry-run", *POST_PLATFORM)
            self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_check_revalidates_current_source_bytes(self):
        post, pictures, _ = self.legacy_post(self.root)
        job = self.root / "job"
        self.package_json(self.run_cli("build", "--post", post, "--out", job, *POST_PLATFORM))
        write_png(pictures[1], (170, 50, 110))
        checked = self.run_cli("check", job)
        self.assertNotEqual(checked.returncode, 0, checked.stdout)

    def test_check_rejects_tampered_job_copy(self):
        post, _, _ = self.legacy_post(self.root)
        job = self.root / "job"
        package = self.package_json(self.run_cli("build", "--post", post, "--out", job, *POST_PLATFORM))
        package["platforms"]["xiaohongshu"]["body"] = "任务正文被替换。"
        write_json(job / "package.json", package)
        checked = self.run_cli("check", job)
        self.assertNotEqual(checked.returncode, 0, checked.stdout)

    def test_second_build_preserves_existing_package(self):
        post, _, _ = self.legacy_post(self.root)
        job = self.root / "job"
        build = ("build", "--post", post, "--out", job, *POST_PLATFORM)
        package = self.package_json(self.run_cli(*build))
        package_path = job / "package.json"
        original = package_path.read_bytes()

        rebuilt = self.run_cli(*build, "--title", "另一版标题")
        self.assertNotEqual(rebuilt.returncode, 0, rebuilt.stdout)
        self.assertEqual(package_path.read_bytes(), original)
        self.assertEqual(self.package_json(self.run_cli("check", job)), package)

    def test_manual_dry_run_preserves_arguments_for_each_platform(self):
        pictures = self.manual_images()
        body = "第一段保留两个  空格。\n\n第二段保留末尾换行 🐾。\n"
        body_file = self.root / "手动正文.txt"
        body_file.write_bytes(body.encode("utf-8"))
        package = self.package_json(self.run_cli(
            "build", *self.manual_args(pictures), "--body-file", body_file,
            *POST_PLATFORM, "--platform", "douyin", "--dry-run",
        ))
        self.assertEqual(set(package["platforms"]), {"xiaohongshu", "douyin"})
        for platform, draft in package["platforms"].items():
            with self.subTest(platform=platform):
                self.assertEqual(draft["title"], TITLE)
                self.assertEqual(draft["body"], body)
                self.assertEqual([topic.removeprefix("#") for topic in draft["topics"]], list(TOPICS))
                self.assert_images(draft["images"], pictures)

    def test_manual_rejects_a_partial_picture_set(self):
        pictures = self.manual_images()
        pictures[1].unlink()
        result = self.run_cli("build", *self.manual_args(pictures), *POST_PLATFORM, "--dry-run")
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_missing_manual_title_is_a_platform_blocker(self):
        picture, = self.manual_images(count=1)
        package = self.package_json(self.run_cli(
            "build", "--image", picture, "--topic", TOPICS[0], *POST_PLATFORM, "--dry-run",
        ))
        self.assert_blocker(package, "xiaohongshu")
        self.assert_images(package["platforms"]["xiaohongshu"]["images"], [picture])

    def test_intentional_blank_body_passes_xiaohongshu_and_blocks_bilibili(self):
        pictures = self.manual_images(count=1)
        placements = [{"image": 1, "after": 0}]
        placements_file = self.root / "placements.json"
        write_json(placements_file, placements)
        package = self.package_json(self.run_cli(
            "build", *self.manual_args(pictures), *POST_PLATFORM, "--platform", "bilibili",
            "--placements-file", placements_file, "--dry-run",
        ))
        note = package["platforms"]["xiaohongshu"]
        self.assertEqual(note["title"], TITLE)
        self.assertEqual(note["body"], "")
        self.assertEqual([topic.removeprefix("#") for topic in note["topics"]], list(TOPICS))
        self.assertFalse(package["blockers"].get("xiaohongshu"))

        article = package["platforms"]["bilibili"]
        self.assertEqual(article["body"], "")
        self.assertEqual(article["placements"], placements)
        self.assert_blocker(package, "bilibili")

    def test_preflight_limits_are_specific_to_the_target_platform(self):
        pictures = self.manual_images(count=19)
        topics = [argument for index in range(6) for argument in ("--topic", f"话题{index}")]
        package = self.package_json(self.run_cli(
            "build", "--title", TITLE, "--platform", "xiaohongshu", "--platform", "douyin",
            *(argument for path in pictures for argument in ("--image", path)), *topics, "--dry-run",
        ))
        self.assertEqual(len(package["blockers"]["xiaohongshu"]), 1)
        self.assertIn("18 张", package["blockers"]["xiaohongshu"][0])
        self.assertEqual(len(package["blockers"]["douyin"]), 1)
        self.assertIn("5 个话题", package["blockers"]["douyin"][0])
        self.assertEqual(len(package["platforms"]["douyin"]["topics"]), 6)

    def test_bilibili_preserves_article_and_orders_placements_by_paragraph(self):
        pictures = self.manual_images(count=3)
        article = "第一段第一行。\r\n第一段第二行。\r\n\r\n第二段  有两个空格。\r\n\r\n第三段。\r\n"
        article_file = self.root / "专栏正文.md"
        article_file.write_bytes(article.encode("utf-8"))
        placements_file = self.root / "placements.json"
        placements = [{"image": 1, "after": 3}, {"image": 2, "after": 0}, {"image": 3, "after": 1}]
        write_json(placements_file, placements)
        package = self.package_json(self.run_cli(
            "build", *self.manual_args(pictures), "--platform", "bilibili",
            "--article-file", article_file, "--placements-file", placements_file, "--dry-run",
        ))
        draft = package["platforms"]["bilibili"]
        self.assertEqual(draft["kind"], "article")
        self.assertEqual(draft["body"], article)
        self.assert_images(draft["images"], pictures)
        self.assertEqual(
            [(placement["image"], placement["after"]) for placement in draft["placements"]],
            [(2, 0), (3, 1), (1, 3)],
        )
        self.assertFalse(package["blockers"].get("bilibili"))

    def test_missing_placements_only_blocks_bilibili(self):
        pictures = self.manual_images()
        article_file = self.root / "专栏正文.md"
        article = "第一段。\n\n第二段。\n"
        article_file.write_text(article, encoding="utf-8")
        package = self.package_json(self.run_cli(
            "build", *self.manual_args(pictures), *POST_PLATFORM, "--platform", "bilibili",
            "--body-file", article_file, "--article-file", article_file, "--dry-run",
        ))
        self.assertEqual(set(package["platforms"]), {"xiaohongshu", "bilibili"})
        self.assertEqual(package["platforms"]["bilibili"]["body"], article)
        self.assertFalse(package["blockers"].get("xiaohongshu"))
        self.assert_blocker(package, "bilibili")

    def test_incomplete_or_out_of_range_placements_only_block_bilibili(self):
        pictures = self.manual_images()
        article_file = self.root / "专栏正文.md"
        article = "第一段。\n\n第二段。\n"
        article_file.write_text(article, encoding="utf-8")
        placements_file = self.root / "placements.json"
        plans = {
            "missing image": [{"image": 1, "after": 0}],
            "repeated image": [{"image": 1, "after": 0}, {"image": 1, "after": 1}],
            "zero image index": [{"image": 0, "after": 0}, {"image": 1, "after": 1}],
            "past final paragraph": [{"image": 1, "after": 0}, {"image": 2, "after": 3}],
        }
        for problem, placements in plans.items():
            with self.subTest(problem=problem):
                write_json(placements_file, placements)
                package = self.package_json(self.run_cli(
                    "build", *self.manual_args(pictures), *POST_PLATFORM, "--platform", "bilibili",
                    "--article-file", article_file, "--placements-file", placements_file, "--dry-run",
                ))
                self.assertFalse(package["blockers"].get("xiaohongshu"))
                self.assert_blocker(package, "bilibili")
                draft = package["platforms"]["bilibili"]
                self.assertEqual(draft["body"], article)
                self.assert_images(draft["images"], pictures)

    def test_bilibili_rejects_invalid_utf8_article(self):
        pictures = self.manual_images(count=1)
        article_file = self.root / "专栏正文.md"
        article_file.write_bytes("正文".encode("utf-8") + b"\xff\n")
        placements_file = self.root / "placements.json"
        write_json(placements_file, [{"image": 1, "after": 0}])
        result = self.run_cli(
            "build", *self.manual_args(pictures), "--platform", "bilibili",
            "--article-file", article_file, "--placements-file", placements_file, "--dry-run",
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
