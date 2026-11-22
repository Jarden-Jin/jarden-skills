#!/usr/bin/env python3

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from PIL import Image


KINDS = {
    "xiaohongshu": "image-note",
    "douyin": "image-post",
    "bilibili": "article",
    "tiktok": "photo-post",
    "instagram": "feed-carousel",
}
CAPTION_PLATFORMS = {"tiktok", "instagram"}
FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
SIZE_LIMITS = {"xiaohongshu": 32_000_000, "douyin": 50_000_000, "bilibili": 20_000_000}
IMAGE_LIMITS = {"xiaohongshu": 18, "douyin": 35, "tiktok": 35, "instagram": 20}
CAPTION_HASHTAG = re.compile(r"(?<![\w#])#\w+")
JARDEN_AI_DECLARATION = re.compile(
    r"^[ \t]*(?:本组为[ \t]*)?AI[ \t]*生成的(?:设计效果图|视觉概念)",
    re.IGNORECASE | re.MULTILINE,
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(data):
    return digest(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path):
    return path.read_bytes().decode("utf-8")


def save_package(path, payload):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.link(temporary, path)
        finally:
            temporary.unlink()


def describe_image(path, role, slot, expected=None):
    path = path.expanduser().resolve(strict=True)
    raw = path.read_bytes()
    checksum = digest(raw)
    if expected is not None and checksum != expected:
        raise ValueError(f"图片摘要不匹配 {path.name}")
    with Image.open(io.BytesIO(raw)) as image:
        if image.format != FORMATS.get(path.suffix.lower()) or getattr(image, "is_animated", False):
            raise ValueError(f"需要可解码的静态 PNG、JPEG 或 WebP {path.name}")
        image.verify()
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        width, height = image.size
    return {
        "path": str(path), "sha256": checksum, "role": role, "slot": slot,
        "width": width, "height": height, "size_bytes": len(raw),
    }


def unique_images(images):
    for key in ("path", "slot", "sha256"):
        if len({entry[key] for entry in images}) != len(images):
            raise ValueError(f"图片列表存在重复项 {key}")
    return images


def child_file(root, name):
    relative = Path(name)
    if relative == Path(".") or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"交付清单路径无效 {name}")
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"交付文件超出 post 目录 {name}")
    return path


def import_post(root):
    root = root.expanduser().resolve(strict=True)
    manifest = read_json(child_file(root, "manifest.json"))
    if manifest["schema_version"] != 1 or manifest.get("prepared_review"):
        raise ValueError("需要正式导出的 schema 1 发帖交付包")
    title, body, tags = (manifest[field] for field in ("title", "body", "tags"))
    if not isinstance(title, str) or not isinstance(body, str) or not isinstance(tags, list):
        raise ValueError("交付清单中的标题、正文或话题类型错误")
    files = manifest["files"]
    caption = child_file(root, "caption.md").read_bytes()
    if digest(caption) != files["caption.md"] or caption.decode("utf-8").rstrip("\n") != "\n\n".join((title, body, " ".join(tags))):
        raise ValueError("caption.md 与交付清单的内容或摘要不匹配")
    images = []
    for entry in manifest["images"]:
        name = entry["file"]
        expected = files[name]
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"交付清单缺少有效的成品摘要 {name}")
        if entry.get("output_sha256", expected) != expected:
            raise ValueError(f"交付清单的两处成品摘要冲突 {name}")
        image = describe_image(child_file(root, name), entry["role"], entry.get("slot") or entry["role"], expected)
        if [image["width"], image["height"]] != manifest["size"]:
            raise ValueError(f"图片尺寸与交付清单不匹配 {name}")
        images.append(image)
    if not images or images[0]["role"] != "cover" or any(image["role"] == "cover" for image in images[1:]):
        raise ValueError("交付图序需要把唯一的封面放在首位")
    return unique_images(images), title, body, tags


def normalize_topics(tags):
    if not isinstance(tags, list):
        raise ValueError("话题必须是列表")
    topics = []
    for original in tags:
        topic = original.strip().removeprefix("#")
        if not topic or "#" in topic or "\n" in topic or "\r" in topic:
            raise ValueError(f"话题格式无效 {original!r}")
        if topic.casefold() in {known.casefold() for known in topics}:
            raise ValueError(f"话题重复 {original!r}")
        topics.append(topic)
    return topics


def compose_caption(platform, title, body, topics):
    sections = (title, body) if platform == "instagram" else (body,)
    text = "\n\n".join(section for section in sections if section.strip())
    hashtags = " ".join(
        f"#{topic}" for topic in topics
        if not re.search(rf"(?<!\w)#{re.escape(topic)}(?!\w)", text, re.IGNORECASE)
    )
    return "\n\n".join(section for section in (text, hashtags) if section)


def caption_topic_count(content):
    return max(len(content["topics"]), len(CAPTION_HASHTAG.findall(content["caption"])))


def article_placements(article, entries, image_count):
    paragraphs = len(re.split(r"\r?\n[ \t]*\r?\n", article.strip())) if article.strip() else 0
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("插图计划需要一个 JSON 对象数组")
    indexes = []
    for entry in entries:
        index, after = entry["image"], entry["after"]
        if type(index) is not int or type(after) is not int or not 0 <= after <= paragraphs:
            raise ValueError(f"插图位置无效 {entry}")
        indexes.append(index)
    if sorted(indexes) != list(range(1, image_count + 1)):
        raise ValueError("插图计划需要覆盖每张图且各放一次")
    return sorted(entries, key=lambda entry: entry["after"])


def platform_blockers(platform, content):
    title, body, topics = (content[field] for field in ("title", "body", "topics"))
    image_count = len(content["images"])
    issues = []
    if platform not in CAPTION_PLATFORMS and not title.strip():
        issues.append("标题缺失，需拟定或确认")
    if platform in ("xiaohongshu", "douyin") and not topics:
        issues.append("话题缺失，需按素材拟定或确认")
    if platform == "instagram" and image_count < 2:
        issues.append("Instagram 多图轮播至少需要两张图片")
    if platform in IMAGE_LIMITS and image_count > IMAGE_LIMITS[platform]:
        issues.append(f"{platform} 图文预检最多支持 {IMAGE_LIMITS[platform]} 张图片")
    if platform in CAPTION_PLATFORMS and any(re.search(r"\s", topic) for topic in topics):
        issues.append("话题含空白字符，请确认可用的单个 #标签")
    if platform == "instagram" and caption_topic_count(content) > 5:
        issues.append("Instagram 的说明超过 5 个 #标签，请确认保留项")
    if platform == "xiaohongshu":
        if sum(1 if ord(char) >= 128 else 0.5 for char in title) > 20:
            issues.append("标题超过小红书 20 加权字的预检预算")
        if any("." in topic for topic in topics):
            issues.append("小红书话题含半角点，需要确认替换话题")
    if platform == "douyin" and len(topics) > 5:
        issues.append("抖音图文预检按 5 个话题执行，请先选定要保留的话题")
    if platform == "bilibili":
        if not body.strip():
            issues.append("需要你或上游提供专栏正文")
        if not content["placements"]:
            issues.append("需要按正文安排插图后再上传")
    if platform in SIZE_LIMITS:
        issues.extend(f"图片超过预检体积预算 {Path(image['path']).name}"
                      for image in content["images"] if image["size_bytes"] > SIZE_LIMITS[platform])
    return issues


def current_blockers(package, platform):
    return list(dict.fromkeys([
        *package["blockers"].get(platform, []),
        *platform_blockers(platform, package["platforms"][platform]),
    ]))


def build(args):
    source_ai_generated = None
    if args.post:
        images, title, body, tags = import_post(args.post)
        if JARDEN_AI_DECLARATION.search(body):
            source_ai_generated = True
    else:
        images = unique_images([
            describe_image(path, "cover" if index == 1 else "image", f"image-{index}")
            for index, path in enumerate(args.image, 1)
        ])
        title, body, tags = "", "", []
    if args.title is not None:
        title = args.title
    if args.body_file:
        body = read_text(args.body_file)
    if args.topic is not None:
        tags = args.topic
    selected = list(dict.fromkeys(args.platform or ["xiaohongshu"]))
    if (args.article_file or args.placements_file) and "bilibili" not in selected:
        raise ValueError("专栏正文和插图计划需要选择 bilibili")
    settings = {"ai_generated": args.ai_generated or source_ai_generated, "original": args.original}
    payload = {"schema_version": 1, "platforms": {}, "blockers": {}}
    for platform in selected:
        placement_error = None
        entry = {
            "kind": KINDS[platform], "title": title, "body": body,
            "topics": normalize_topics(tags), "images": images, "settings": settings,
        }
        if platform in CAPTION_PLATFORMS:
            entry["caption"] = compose_caption(platform, title, body, entry["topics"])
            entry["settings"] = {"ai_generated": settings["ai_generated"]}
        elif platform == "bilibili":
            entry["body"] = read_text(args.article_file) if args.article_file else body
            entry["placements"] = []
            if args.placements_file:
                try:
                    entry["placements"] = article_placements(
                        entry["body"], read_json(args.placements_file), len(images)
                    )
                except (KeyError, OSError, ValueError) as error:
                    placement_error = str(error)
        payload["platforms"][platform] = entry
        issues = platform_blockers(platform, entry)
        if placement_error:
            issues.append(placement_error)
        if issues:
            payload["blockers"][platform] = issues
    payload["fingerprint"] = fingerprint(payload)
    if not args.dry_run:
        if args.out is None:
            raise ValueError("请指定新的任务目录 --out")
        args.out.mkdir(mode=0o700, parents=True, exist_ok=True)
        save_package(args.out / "package.json", payload)
    return payload


def load_package(job):
    package_path = job / "package.json" if job.is_dir() else job
    payload = read_json(package_path)
    if payload["schema_version"] != 1 or payload["fingerprint"] != fingerprint({
        key: value for key, value in payload.items() if key != "fingerprint"
    }):
        raise ValueError("任务内容包版本或摘要不匹配")
    if not payload["platforms"] or not set(payload["platforms"]).issubset(KINDS):
        raise ValueError("任务内容包没有有效发布平台")
    checked = {}
    for entry in payload["platforms"].values():
        for image in entry["images"]:
            path = Path(image["path"])
            if path not in checked:
                if not path.is_absolute():
                    raise ValueError(f"图片必须使用绝对路径 {path}")
                checked[path] = digest(path.read_bytes())
            if checked[path] != image["sha256"]:
                raise ValueError(f"图片在定稿后发生变化 {path.name}")
    return payload


def main():
    parser = argparse.ArgumentParser(description="检查和冻结图文发布内容包")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("build")
    source = create.add_mutually_exclusive_group(required=True)
    source.add_argument("--post", type=Path)
    source.add_argument("--image", type=Path, action="append")
    create.add_argument("--title")
    create.add_argument("--body-file", type=Path)
    create.add_argument("--topic", action="append")
    create.add_argument("--platform", choices=KINDS, action="append")
    create.add_argument("--article-file", type=Path)
    create.add_argument("--placements-file", type=Path)
    create.add_argument("--ai-generated", action="store_true", default=None)
    create.add_argument("--original", action="store_true", default=None)
    create.add_argument("--out", type=Path)
    create.add_argument("--dry-run", action="store_true")
    commands.add_parser("check").add_argument("job", type=Path)
    args = parser.parse_args()
    try:
        result = build(args) if args.command == "build" else load_package(args.job)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, SyntaxError, Image.DecompressionBombError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
