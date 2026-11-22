import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit


PLATFORM_NAMES = {
    "xiaohongshu": "小红书|xiaohongshu",
    "douyin": "抖音|douyin",
    "bilibili": "B站|哔哩哔哩|bilibili",
    "tiktok": "tiktok|海外抖音|国际版抖音",
    "instagram": "instagram|ins|ig",
}
CREATOR_HOSTS = {
    "xiaohongshu": "creator.xiaohongshu.com",
    "douyin": "creator.douyin.com",
    "bilibili": "member.bilibili.com",
    "tiktok": "www.tiktok.com",
    "instagram": "www.instagram.com",
}
EDITOR_PATHS = {
    "xiaohongshu": "/publish/publish",
    "douyin": "/creator-micro/content/post/image",
    "bilibili": "/platform/upload/text/new-edit",
    "tiktok": "/tiktokstudio/upload",
    "instagram": "",
}
PLATFORM_SWITCHES = {
    "xiaohongshu": ("allow_copy",),
    "douyin": ("allow_save", "cross_posting"),
    "bilibili": ("comments_enabled", "featured_comments"),
    "tiktok": ("ai_generated", "comments_enabled", "branded_content", "cross_posting"),
    "instagram": ("ai_generated", "comments_enabled", "branded_content", "cross_posting"),
}
COVER_MODES = {
    "xiaohongshu": {"first_image"},
    "douyin": {"first_image", "custom_image"},
    "bilibili": {"text_preview", "custom_image"},
    "tiktok": {"first_image", "selected_image"},
    "instagram": {"first_image"},
}
WORK_IDS = {
    "xiaohongshu": r"[0-9a-f]{24}",
    "douyin": r"[1-9][0-9]*",
    "bilibili": r"[1-9][0-9]*",
    "tiktok": r"[1-9][0-9]*",
    "instagram": r"[A-Za-z0-9_-]+",
}
WORK_PATH_PREFIXES = {
    "xiaohongshu": r"/(?:explore|discovery/item)/",
    "douyin": r"/(?:note|video)/",
    "bilibili": r"/(?:read/cv|opus/)",
    "tiktok": r"/@[\w.]+/photo/",
    "instagram": r"/p/",
}
IDENTITY = ("space", "tab", "account")
READY_CHECKS = ("mode", "images", "title", "body", "topics", "settings", "final_control")
OBSERVATIONS = {"editing", "blocked", "ready", "confirmed"}
PUBLICATIONS = {"submitted", "in_review", "published"}
MAX_AGE = timedelta(minutes=5)


def read_object(path):
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return document


def read_state(path):
    if not os.path.lexists(path):
        return {"schema_version": 2, "platforms": {}}
    state = read_object(path)
    version = state.get("schema_version")
    if type(version) is not int:
        raise ValueError("unsupported job state format")
    if version == 1 and state.get("platform") in PLATFORM_NAMES:
        return {"schema_version": 2, "platforms": {state["platform"]: state}}
    if version != 2 or not isinstance(state.get("platforms"), dict):
        raise ValueError("unsupported job state format")
    for platform, record in state["platforms"].items():
        if platform not in PLATFORM_NAMES or not isinstance(record, dict) or record.get("platform") != platform:
            raise ValueError("invalid platform record in job state")
    return state


def save_state(path, state):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".job-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(state, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)


def platform_url(platform, value):
    if not isinstance(value, str) or "\\" in value or any(ord(char) <= 32 for char in value):
        raise ValueError("invalid platform URL")
    parsed = urlsplit(value)
    domain = f"{platform}.com"
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or not (host == domain or host.endswith(f".{domain}"))
        or parsed.username is not None
        or parsed.port not in (None, 443)
    ):
        raise ValueError(f"expected an HTTPS URL on {domain}")
    return parsed


def creator_url(platform, value):
    parsed = platform_url(platform, value)
    if parsed.hostname != CREATOR_HOSTS[platform]:
        raise ValueError("expected the official creator site")
    return parsed


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("observed_at must be an ISO 8601 timestamp with timezone")
    result = datetime.fromisoformat(value.removesuffix("Z") + "+00:00"
                                    if value.endswith("Z") else value)
    if result.utcoffset() is None:
        raise ValueError("observed_at needs a timezone")
    return result


def check_observation(platform, state, evidence, now):
    status = evidence["status"]
    if status not in OBSERVATIONS or not evidence["summary"]:
        raise ValueError("evidence needs a supported status and a summary")
    for field in IDENTITY:
        if state[field] != evidence.get(field):
            raise ValueError(f"evidence {field} does not match the job")
    platform_url(platform, evidence["url"])
    observed = timestamp(evidence["observed_at"])
    previous = state.get("evidence")
    if not now - MAX_AGE <= observed <= now or observed < timestamp(state["started_at"]):
        raise ValueError("evidence must be fresh and observed during this job")
    if previous and observed < timestamp(previous["observed_at"]):
        raise ValueError("evidence is older than the last observation")
    if status in {"ready", "confirmed"} or "fingerprint" in evidence:
        if evidence.get("fingerprint") != state["fingerprint"]:
            raise ValueError("evidence fingerprint does not match the job")
    checks = evidence.get("checks")
    draft_flags = [record["durable_draft"] for record in (evidence, checks)
                   if isinstance(record, dict) and "durable_draft" in record]
    if (status == "ready" and not draft_flags) or any(type(flag) is not bool for flag in draft_flags):
        raise ValueError("durable_draft must be a boolean")
    if len(set(draft_flags)) > 1:
        raise ValueError("durable_draft must be consistent")
    if True in draft_flags and "draft_id" not in evidence:
        raise ValueError("durable_draft requires a draft_id")
    if "draft_id" in evidence:
        draft_id = evidence["draft_id"]
        if not isinstance(draft_id, str) or not draft_id.strip():
            raise ValueError("draft_id must be a nonempty string")
        if state.get("draft_id", draft_id) != draft_id:
            raise ValueError("evidence draft_id does not match the job")


def expected_content(platform, content):
    from package import CAPTION_PLATFORMS, caption_topics

    expected = {"topics": content["topics"]}
    if platform in CAPTION_PLATFORMS:
        expected.update(mode=content["kind"], caption=content["caption"], topics=caption_topics(content))
        if platform == "tiktok":
            expected["title"] = content["title"]
    else:
        expected.update(title=content["title"], body=content["body"])
    expected["image_sha256"] = [image["sha256"] for image in content["images"]]
    if platform == "bilibili":
        expected["placements"] = content["placements"]
    return expected


def check_readback(readback, expected, *, with_settings=False):
    required_fields = set(expected)
    if with_settings:
        required_fields.add("settings")
    if not isinstance(readback, dict) or readback.keys() != required_fields:
        raise ValueError(f"readback must contain exactly {', '.join(sorted(required_fields))}")
    for field, value in expected.items():
        if json.dumps(readback[field], sort_keys=True) != json.dumps(value, sort_keys=True):
            raise ValueError(f"readback.{field} differs from the package")


def check_ready(platform, package, state, evidence):
    from package import CAPTION_PLATFORMS, current_blockers

    if current_blockers(package, platform):
        raise ValueError("the selected platform has unresolved package blockers")
    if creator_url(platform, evidence["url"]).path.rstrip("/") != EDITOR_PATHS[platform]:
        raise ValueError("readiness requires the verified image or article editor")
    checks = evidence.get("checks")
    if not isinstance(checks, dict) or any(checks.get(key) is not True for key in READY_CHECKS):
        raise ValueError("all seven readiness checks must be true")
    if "draft_id" in state and evidence.get("draft_id") != state["draft_id"]:
        raise ValueError("ready evidence must identify the known draft")

    content = package["platforms"][platform]
    expected = expected_content(platform, content)
    readback = evidence.get("readback")
    check_readback(readback, expected, with_settings=True)
    caption_platform = platform in CAPTION_PLATFORMS
    settings = readback["settings"]
    if not isinstance(settings, dict):
        raise ValueError("readback.settings must be an object")
    visibility = ("public", "followers", "close_friends") if platform == "instagram" else (
        "public", "private", "friends", "followers",
    )
    if settings.get("visibility") not in visibility:
        raise ValueError("readback.settings must include the observed visibility")
    if type(settings.get("scheduled")) is not bool:
        raise ValueError("readback.settings.scheduled must be an observed boolean")
    switch_fields = PLATFORM_SWITCHES[platform]
    if not caption_platform:
        switch_fields = ("ai_generated", *switch_fields)
    for field in switch_fields:
        if caption_platform and settings.get(field) == "not_available":
            continue
        if type(settings.get(field)) is not bool:
            raise ValueError(f"readback.settings.{field} must be an observed boolean")
    if platform in ("xiaohongshu", "bilibili") and type(settings.get("original")) is not bool:
        raise ValueError("readback.settings.original must be an observed boolean")
    if settings.get("cover") not in COVER_MODES[platform]:
        raise ValueError("readback.settings must include the observed platform cover mode")
    if settings["cover"] == "custom_image":
        source = settings.get("cover_source_sha256")
        if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{64}", source):
            raise ValueError("custom cover requires the verified source image checksum")
    if settings["cover"] == "selected_image":
        if settings.get("cover_source_sha256") not in expected["image_sha256"]:
            raise ValueError("selected cover must identify an image in the frozen photo post")
    package_settings = content["settings"]
    if package_settings is None:
        package_settings = {}
    if not isinstance(package_settings, dict):
        raise ValueError("package settings must be an object or null")
    if settings.get("original") is True and package_settings.get("original") is not True:
        raise ValueError("readback.settings.original requires explicit authorization in the package")
    for field, value in package_settings.items():
        if value is None:
            continue
        if json.dumps(settings.get(field), sort_keys=True) != json.dumps(value, sort_keys=True):
            raise ValueError(f"readback.settings.{field} differs from the package")


def work_id_from_url(platform, url):
    path = platform_url(platform, url).path.rstrip("/")
    pattern = rf"{WORK_PATH_PREFIXES[platform]}({WORK_IDS[platform]})"
    match = re.fullmatch(pattern, path, re.IGNORECASE | re.ASCII)
    if not match:
        return None
    return match[1].lower() if platform == "xiaohongshu" else match[1]


def work_receipt(platform, evidence):
    page_id = work_id_from_url(platform, evidence["url"])
    work_url = evidence.get("work_url")
    link_id = work_id_from_url(platform, work_url) if work_url is not None else None
    if work_url is not None and not link_id:
        raise ValueError("work_url must be a platform work permalink")
    if platform in ("tiktok", "instagram") and not (page_id or link_id):
        raise ValueError("confirmation requires a verified photo post permalink")
    work_id = evidence.get("work_id")
    if work_id is not None:
        if type(work_id) is int and platform not in ("xiaohongshu", "instagram"):
            work_id = str(work_id)
        if not isinstance(work_id, str):
            raise ValueError("invalid platform work_id")
        if platform == "xiaohongshu":
            work_id = work_id.lower()
        elif platform == "bilibili":
            work_id = work_id.lower().removeprefix("cv")
        if not re.fullmatch(WORK_IDS[platform], work_id):
            raise ValueError("invalid platform work_id")
    identifiers = {value for value in (page_id, link_id, work_id) if value is not None}
    if len(identifiers) != 1:
        raise ValueError("confirmation needs one consistent platform work identifier")
    return identifiers.pop(), work_url or (evidence["url"] if page_id else None)


def confirm_work(platform, state, evidence):
    authorization = state.get("authorization")
    if authorization and timestamp(evidence["observed_at"]) < timestamp(authorization["authorized_at"]):
        raise ValueError("confirmation predates the publish attempt")
    publication = evidence.get("publication")
    if publication not in PUBLICATIONS:
        raise ValueError("publication must be submitted, in_review, or published")
    work_id, work_url = work_receipt(platform, evidence)
    if state.get("work_id", work_id) != work_id:
        raise ValueError("confirmation refers to a different work")
    state.update(status="confirmed", publication=publication, work_id=work_id)
    if work_url:
        state["work_url"] = work_url


def reconcile_manual(platform, package, state, evidence, now):
    account = evidence.get("account")
    if not isinstance(account, str) or not account.strip():
        raise ValueError("manual confirmation requires an observed account")
    if evidence["status"] != "confirmed" or not evidence["summary"]:
        raise ValueError("manual confirmation requires a confirmed observation and summary")
    if evidence.get("fingerprint") != package["fingerprint"]:
        raise ValueError("evidence fingerprint does not match the job")
    observed_at = timestamp(evidence["observed_at"])
    if not now - MAX_AGE <= observed_at <= now:
        raise ValueError("manual confirmation requires a fresh observation")
    if state is None:
        state = {
            "schema_version": 1,
            "platform": platform,
            "account": account,
            "space": evidence.get("space"),
            "tab": evidence.get("tab"),
            "fingerprint": package["fingerprint"],
            "url": evidence["url"],
            "started_at": evidence["observed_at"],
        }
    if account != state["account"]:
        raise ValueError("evidence account does not match the job")
    previous = state.get("evidence")
    latest = timestamp(previous["observed_at"] if previous else state["started_at"])
    if observed_at < latest:
        raise ValueError("manual confirmation predates the job or latest observation")
    check_readback(evidence.get("readback"), expected_content(platform, package["platforms"][platform]))
    confirm_work(platform, state, evidence)
    state["evidence"] = evidence
    return state


def explicit_instruction(platform, instruction):
    text = instruction.strip().rstrip("。.!！")
    chinese = re.sub(r"\s+", "", text)
    target = rf"(?:{PLATFORM_NAMES[platform]})"
    prefix = r"(?:(?:我?(?:确认|同意|授权)|好)[，,]?|可以|请|就)?"
    reference = r"(?:这[篇条个]|本[篇条])"
    noun = r"(?:笔记|图文|专栏|文章|帖子|稿件|内容)"
    verb = r"(?:现在|立即)?(?:发布|发)"
    action = rf"(?:{verb}{reference}?{noun}?|把{reference}{noun}?{verb})"
    pattern = rf"{prefix}(?:在{target}{action}|{action}(?:到|至){target})吧?"
    english = (
        r"(?:please )?publish(?: (?:it|this|this post))?"
        rf" (?:to|on) {target}(?: now)?"
    )
    return bool(
        re.fullmatch(pattern, chinese, re.IGNORECASE)
        or re.fullmatch(english, " ".join(text.split()), re.IGNORECASE)
    )


def review_id(state, evidence):
    from package import fingerprint

    return fingerprint({
        "platform": state["platform"],
        "identity": {field: state[field] for field in IDENTITY},
        "fingerprint": state["fingerprint"],
        "url": evidence["url"],
        "draft_id": evidence.get("draft_id"),
        "readback": evidence["readback"],
    })


def update_state(args, package, state, evidence, now):
    from package import CAPTION_PLATFORMS

    platform, stage = args.platform, state["status"]
    check_observation(platform, state, evidence, now)
    observed = evidence["status"]
    if args.command == "authorize":
        if stage != "ready" or "authorization" in state or observed != "ready":
            raise ValueError("authorization requires a ready job without a publish attempt")
        check_ready(platform, package, state, evidence)
        previous = state["evidence"]
        if timestamp(evidence["observed_at"]) <= timestamp(previous["observed_at"]):
            raise ValueError("authorization requires a new editor observation")
        fields = ("fingerprint", "url", "draft_id")
        current_readback = json.dumps(evidence["readback"], sort_keys=True)
        previous_readback = json.dumps(previous.get("readback"), sort_keys=True)
        if current_readback != previous_readback or any(evidence.get(field) != previous.get(field) for field in fields):
            raise ValueError("authorization evidence must match the ready observation")
        if platform in CAPTION_PLATFORMS and (
            not args.review_id or args.review_id != state.get("review_id") or args.review_id != review_id(state, evidence)
        ):
            raise ValueError("authorization requires the review_id shown to the user before approval")
        settings = evidence["readback"]["settings"]
        if settings["scheduled"] or settings.get("cross_posting") is True:
            raise ValueError("scheduled or cross-posted publication requires the user to submit manually")
        if settings.get("cross_posting") == "not_available":
            raise ValueError("publication requires a verified single-platform destination")
        if not explicit_instruction(platform, args.instruction):
            raise ValueError("an explicit current publish instruction naming the platform is required")
        state["authorization"] = {
            "instruction": args.instruction,
            "authorized_at": now.isoformat(timespec="seconds"),
            "evidence": evidence,
        }
        state["status"] = "publish_unknown"
    elif observed == "confirmed":
        if stage not in {"publish_unknown", "confirmed"} or "authorization" not in state:
            raise ValueError("confirmation requires a recorded publish attempt")
        confirm_work(platform, state, evidence)
    elif stage == "confirmed" or (stage == "publish_unknown" and observed == "ready"):
        raise ValueError("a recorded publish attempt cannot return to editing or readiness")
    elif stage != "publish_unknown":
        if observed == "ready":
            check_ready(platform, package, state, evidence)
            if platform in CAPTION_PLATFORMS:
                state["review_id"] = review_id(state, evidence)
        else:
            state.pop("review_id", None)
        state["status"] = observed
    if "draft_id" in evidence:
        state["draft_id"] = evidence["draft_id"]
    state["evidence"] = evidence


def run(args):
    job = args.job.resolve(strict=True)
    if not job.is_dir():
        raise ValueError("JOB must be a directory")
    path = job / "job.json"
    if args.command == "status":
        return read_state(path)

    from package import current_blockers, load_package

    with (job / ".job.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        envelope = read_state(path)
        records = envelope["platforms"]
        platform = args.platform
        if args.command == "begin" and platform in records:
            raise ValueError("platform already started; inspect its recorded state")
        package = load_package(job)
        if platform not in package["platforms"]:
            raise ValueError(f"package does not contain {platform}")
        if any(record["fingerprint"] != package["fingerprint"] for record in records.values()):
            raise ValueError("package fingerprint differs from the started job")
        now = datetime.now(timezone.utc)
        if args.command == "begin":
            blockers = current_blockers(package, platform)
            if blockers:
                raise ValueError(f"{platform} has unresolved package blockers: {', '.join(blockers)}")
            identity = {field: getattr(args, field) for field in IDENTITY}
            if any(not value.strip() for value in identity.values()):
                raise ValueError("space, tab, and account must be nonempty identifiers")
            creator_url(platform, args.url)
            state = {
                "schema_version": 1,
                "status": "preparing",
                "platform": platform,
                "fingerprint": package["fingerprint"],
                "url": args.url,
                "started_at": now.isoformat(timespec="seconds"),
                **identity,
            }
        elif args.command == "reconcile":
            state = reconcile_manual(platform, package, records.get(platform), read_object(args.file), now)
        else:
            if platform not in records:
                raise ValueError("platform has not started")
            state = records[platform]
            evidence = read_object(args.file)
            update_state(args, package, state, evidence, datetime.now(timezone.utc))
        records[platform] = state
        save_state(path, envelope)
        return state


def main(argv=None):
    parser = argparse.ArgumentParser(description="Record publishing job stages and readbacks")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status").add_argument("job", type=Path)
    for name in ("begin", "observe", "authorize"):
        command = commands.add_parser(name)
        command.add_argument("job", type=Path)
        command.add_argument("platform", choices=PLATFORM_NAMES)
        if name == "begin":
            for field in (*IDENTITY, "url"):
                command.add_argument(f"--{field}", required=True)
        else:
            command.add_argument("--file", type=Path, required=True)
        if name == "authorize":
            command.add_argument("--instruction", required=True)
            command.add_argument("--review-id", help="ID of the ready snapshot shown before approval")
    reconcile = commands.add_parser("reconcile", help="Record a user-published TikTok or Instagram post")
    reconcile.add_argument("job", type=Path)
    reconcile.add_argument("platform", choices=("tiktok", "instagram"))
    reconcile.add_argument("--file", type=Path, required=True)
    try:
        print(json.dumps(run(parser.parse_args(argv)), ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, ImportError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
