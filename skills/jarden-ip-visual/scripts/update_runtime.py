from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from runtime_activity import (
    active_leases,
    atomic_json,
    instance_gate,
    instance_home,
    real_root,
    require_idle,
    sync_directory,
    update_home,
)


SCHEMA_VERSION = 1
REPOSITORY = "Jarden-Jin/jarden-skills"
SKILL_NAME = "jarden-ip-visual"
SKILL_PATH = "skills/jarden-ip-visual"
CHANNEL = "stable"
RELEASE_DOWNLOAD_PREFIX = f"https://github.com/{REPOSITORY}/releases/download/"
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}"
ASSET_REDIRECT_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
MAX_INDEX_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_FILES = 2000
MAX_FILE_BYTES = 32 * 1024 * 1024
CHECK_TTL = 24 * 60 * 60
FAILURE_COOLDOWN = 60 * 60
NETWORK_BUDGET = 2.0
TERMINAL = {"committed", "rolled_back", "recovered", "aborted"}


class UpdateError(RuntimeError):
    def __init__(self, message: str, code: str = "update_error", exit_code: int = 2):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


class SourceError(UpdateError):
    def __init__(self, message: str, code: str = "source_rejected"):
        super().__init__(message, code)


def maintenance_environment() -> dict[str, str]:
    keep = {
        "HOME", "PATH", "GH_CONFIG_DIR", "XDG_CONFIG_HOME", "TMPDIR", "TEMP", "TMP",
        "APPDATA", "LOCALAPPDATA", "USERPROFILE", "SystemRoot", "ComSpec", "PATHEXT",
        "LANG", "LC_ALL", "TERM", "USER", "LOGNAME", "JARDEN_UPDATE_HOME", "XDG_STATE_HOME",
    }
    return {key: os.environ[key] for key in keep if key in os.environ}


class _TrustedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        original = urllib.parse.urlsplit(request.full_url)
        binary_asset = request.get_header("Accept") == "application/octet-stream" and (
            re.fullmatch(re.escape(API_ROOT) + r"/releases/assets/[1-9]\d*", request.full_url)
            or getattr(request, "_jarden_asset_redirect", False) and original.hostname in ASSET_REDIRECT_HOSTS
        )
        if not binary_asset:
            raise SourceError("普通 API 请求不允许重定向", "untrusted_source")
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.hostname not in ASSET_REDIRECT_HOSTS:
            raise SourceError("下载重定向到非受信 GitHub 资产地址", "untrusted_source")
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected._jarden_asset_redirect = True
            if original.hostname != parsed.hostname:
                for name in ("Authorization", "authorization"):
                    redirected.remove_header(name)
        return redirected


def emit(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, sort_keys=True))


def safe_version(value: str) -> bool:
    return bool(re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", value))


def version_key(value: str) -> tuple[int, int, int]:
    return tuple(map(int, value.split(".")))  # type: ignore[return-value]


def skill_tag(version: str) -> str:
    return f"{SKILL_NAME}/v{version}"


def remember_release(release: dict) -> None:
    fingerprint = {
        "version": release["version"],
        "tag": release["tag"],
        "commit": release["commit"],
        "archive_sha256": release["archive"]["sha256"],
    }
    home = update_home()
    root = real_root()
    if home == root or root in home.parents or home in root.parents:
        raise UpdateError("发行记录目录必须与安装目录分离", "unsafe_path")
    path = home / "releases" / SKILL_NAME / f"{release['version']}.json"
    if path.is_symlink():
        raise SourceError("已知发行记录不能是符号链接", "version_mutated")
    if path.is_file():
        known = read_json(path)
        if known != fingerprint:
            raise SourceError("同一版本号的发行内容已经变化；拒绝可变同号发行", "version_mutated")
        return
    atomic_json(path, fingerprint)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, limit: int = MAX_INDEX_BYTES) -> dict:
    try:
        if path.is_symlink() or path.stat().st_size > limit:
            raise UpdateError("JSON 文件是链接或超过大小限制", "invalid_file")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, UnicodeError, ValueError) as error:
        raise UpdateError(f"无法读取有效 JSON：{path}", "invalid_json") from error


def installed_version(root: Path) -> str:
    path = root / "SKILL.md"
    try:
        if path.is_symlink():
            raise ValueError
        line = next(line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("  version:"))
        value = line.split(":", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not safe_version(value):
            raise ValueError
        return value
    except (OSError, UnicodeError, ValueError, StopIteration) as error:
        raise UpdateError("安装 SKILL.md 缺少有效 metadata.version", "invalid_installed_version") from error


def normalize_manifest_files(files: object, release: bool = True) -> list[dict]:
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise UpdateError("文件清单无效", "invalid_manifest")
    result = []
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256", "mode"}:
            raise UpdateError("文件清单字段无效", "invalid_manifest")
        relative, size, digest, mode = item["path"], item["size"], item["sha256"], item["mode"]
        if not isinstance(relative, str) or relative in seen or "\\" in relative:
            raise UpdateError("文件路径重复或无效", "invalid_manifest")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts) or pure.parts[0] != SKILL_NAME:
            raise SourceError("发行文件路径越界", "unsafe_path")
        if release:
            if "evals" in pure.parts or pure.parts[-1] in {".jarden-install.json", "release.json", ".env", "credentials.json", "settings.json"}:
                raise UpdateError("发行包包含禁止发布的评估、配置或安装文件", "invalid_manifest")
            if pure.parts[-1].endswith((".key", ".pem", ".pyc", ".pyo")):
                raise UpdateError("发行包包含禁止发布的密钥或缓存文件", "invalid_manifest")
        if type(size) is not int or not 0 <= size <= MAX_FILE_BYTES:
            raise UpdateError("文件大小无效", "invalid_manifest")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise UpdateError("文件摘要无效", "invalid_manifest")
        if release and mode != "0644":
            raise UpdateError("发行文件权限必须是 0644", "invalid_manifest")
        if not release and not re.fullmatch(r"0[0-7]{3}", mode):
            raise UpdateError("文件权限无效", "invalid_manifest")
        seen.add(relative)
        result.append(dict(item))
    if release and f"{SKILL_NAME}/SKILL.md" not in seen:
        raise UpdateError("文件清单必须包含 SKILL.md", "invalid_manifest")
    return result


def validate_release(value: dict, expected_version: str | None = None) -> dict:
    required = {
        "schema_version", "skill", "repository", "skill_path", "channel", "version",
        "tag", "commit", "archive", "files", "project_schema", "dependencies",
        "change_type", "migration_notes",
    }
    if set(value) != required:
        raise UpdateError("发行索引字段不符合 schema", "unknown_index_fields")
    if value["schema_version"] != SCHEMA_VERSION:
        raise UpdateError("不支持的发行索引 schema", "unsupported_schema")
    if (value["skill"], value["repository"], value["skill_path"], value["channel"]) != (SKILL_NAME, REPOSITORY, SKILL_PATH, CHANNEL):
        raise SourceError("发行来源不是固定的受信仓库、Skill 与 channel", "untrusted_source")
    version = value["version"]
    if not isinstance(version, str) or not safe_version(version) or (expected_version and version != expected_version):
        raise UpdateError("发行版本无效或与请求不一致", "version_mismatch")
    if value["tag"] != skill_tag(version):
        raise UpdateError("发行标签与版本不一致", "version_mismatch")
    if not isinstance(value["commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", value["commit"]):
        raise UpdateError("发行清单缺少不可变 commit", "invalid_manifest")
    archive = value["archive"]
    if not isinstance(archive, dict) or set(archive) != {"name", "size", "sha256"}:
        raise UpdateError("归档字段无效", "invalid_manifest")
    expected_name = f"{SKILL_NAME}-{version}.zip"
    if archive["name"] != expected_name or type(archive["size"]) is not int or not 0 < archive["size"] <= MAX_ARCHIVE_BYTES:
        raise UpdateError("归档名称或大小无效", "invalid_manifest")
    if not isinstance(archive["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", archive["sha256"]):
        raise UpdateError("归档摘要无效", "invalid_manifest")
    normalize_manifest_files(value["files"])
    if value["project_schema"] != {"read": [1, 2], "write": [2]}:
        raise UpdateError("项目 schema 兼容声明不匹配", "incompatible_manifest")
    if value["dependencies"] != {"python": ">=3.10", "pillow": "required-for-image-operations"}:
        raise UpdateError("依赖声明不匹配", "incompatible_manifest")
    if not isinstance(value["change_type"], str) or value["change_type"] not in {"patch", "minor", "major"}:
        raise UpdateError("变更类型无效", "invalid_manifest")
    notes = value["migration_notes"]
    prefix = f"https://github.com/{REPOSITORY}/blob/{value['commit']}/skills/{SKILL_NAME}/references/project-state.md"
    if not isinstance(notes, str) or not (notes == prefix or notes.startswith(prefix + "#")):
        raise SourceError("迁移说明不是固定仓库中固定 commit 的文档", "untrusted_source")
    return value


class Source:
    def __init__(self, timeout: float = NETWORK_BUDGET):
        self.timeout = timeout
        self._token: str | None = None
        self._gh_checked = False
        self._opener = urllib.request.build_opener(_TrustedRedirect())

    def _headers(self, accept: str, deadline: float) -> dict[str, str]:
        headers = {"Accept": accept, "User-Agent": "jarden-ip-visual-updater/1"}
        if not self._gh_checked:
            self._gh_checked = True
            try:
                result = subprocess.run(
                    ["gh", "auth", "token", "--hostname", "github.com"],
                    capture_output=True, text=True,
                    timeout=max(0.1, min(self.timeout, deadline - time.monotonic())),
                    env=maintenance_environment(),
                )
                token = result.stdout.strip()
                if result.returncode == 0 and token and "\n" not in token:
                    self._token = token
            except (OSError, subprocess.TimeoutExpired):
                pass
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(self, url: str, accept: str = "application/vnd.github+json", limit: int | None = None) -> bytes:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or not (
            url.startswith(API_ROOT + "/") or url.startswith(RELEASE_DOWNLOAD_PREFIX)
        ):
            raise SourceError("下载地址不在固定受信来源", "untrusted_source")
        if "api.github.com" in parsed.netloc and not url.startswith(API_ROOT + "/"):
            raise SourceError("API 地址越界", "untrusted_source")
        deadline = time.monotonic() + self.timeout
        request = urllib.request.Request(url, headers=self._headers(accept, deadline))
        try:
            with self._opener.open(request, timeout=max(0.1, deadline - time.monotonic())) as response:
                final_url = urllib.parse.urlsplit(response.geturl())
                if final_url.scheme != "https" or final_url.username or final_url.password:
                    raise SourceError("下载最终地址不是受信 HTTPS 来源", "untrusted_source")
                asset_redirect = accept == "application/octet-stream" and re.fullmatch(
                    re.escape(API_ROOT) + r"/releases/assets/[1-9]\d*", url
                ) and final_url.hostname in ASSET_REDIRECT_HOSTS
                if parsed.hostname == "api.github.com" and final_url.geturl().split("?")[0] != url.split("?")[0] and not asset_redirect:
                    raise SourceError("API 被重定向", "untrusted_source")
                length = response.headers.get("Content-Length")
                maximum = limit or MAX_INDEX_BYTES
                if length and int(length) > maximum:
                    raise SourceError("响应超过大小限制", "size_mismatch")
                chunks = []
                size = 0
                while chunk := response.read(64 * 1024):
                    size += len(chunk)
                    if size > maximum or time.monotonic() > deadline:
                        raise SourceError("下载超时或超过大小限制", "network_timeout")
                    chunks.append(chunk)
                return b"".join(chunks)
        except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError) as error:
            raise UpdateError(f"网络检查失败：{getattr(error, 'reason', error)}", "network_error") from error

    def latest(self) -> dict:
        data = self._request(f"{API_ROOT}/releases?per_page=100")
        try:
            releases = json.loads(data)
            if not isinstance(releases, list):
                raise ValueError
        except (ValueError, UnicodeError) as error:
            raise SourceError("Release 列表不是有效数组", "invalid_release") from error
        versions = []
        for item in releases:
            if not isinstance(item, dict) or item.get("draft") is not False or item.get("prerelease") is not False:
                continue
            tag = item.get("tag_name")
            prefix = f"{SKILL_NAME}/v"
            if isinstance(tag, str) and tag.startswith(prefix):
                version = tag[len(prefix):]
                if safe_version(version):
                    versions.append(version)
        if not versions:
            raise SourceError("没有可用的 stable Skill 发行", "release_missing")
        version = max(versions, key=version_key)
        return {"version": version, "tag": skill_tag(version)}

    def release(self, version: str) -> dict:
        tag = skill_tag(version)
        data = self._request(f"{API_ROOT}/releases/tags/{urllib.parse.quote(tag, safe='')}")
        try:
            release = json.loads(data)
            if not isinstance(release, dict) or release.get("tag_name") != tag or release.get("draft") is not False or release.get("prerelease") is not False:
                raise ValueError
            assets = release.get("assets")
            if not isinstance(assets, list):
                raise ValueError
        except (ValueError, TypeError, UnicodeError) as error:
            raise SourceError("Release 标签、发布状态或资产无效", "invalid_release") from error
        names = {}
        for asset in assets:
            if isinstance(asset, dict) and isinstance(asset.get("name"), str):
                if asset["name"] in names:
                    raise SourceError("Release 资产名称重复", "invalid_release")
                names[asset["name"]] = asset
        if set(names) != {f"{SKILL_NAME}-{version}.zip", "release.json"}:
            raise SourceError("Release 必须且只能包含 zip 与外部 release.json", "invalid_release")
        index_asset = names["release.json"]
        filename = f"{SKILL_NAME}-{version}.zip"
        index_data, index_sha256 = self._asset_json(index_asset, "release.json")
        index = validate_release(index_data, version)
        archive_asset = names[filename]
        if archive_asset.get("size") != index["archive"]["size"]:
            raise SourceError("Release 归档大小与索引不一致", "size_mismatch")
        if type(archive_asset.get("id")) is not int or archive_asset["id"] <= 0:
            raise SourceError("Release 归档资产缺少 id", "invalid_release")
        index["_download_url"] = f"{API_ROOT}/releases/assets/{archive_asset['id']}"
        index["_asset_id"] = archive_asset["id"]
        index["_index_sha256"] = index_sha256
        remember_release(index)
        return index

    def _asset_json(self, asset: dict, name: str) -> tuple[dict, str]:
        if asset.get("name") != name or type(asset.get("id")) is not int or asset["id"] <= 0 or type(asset.get("size")) is not int or not 0 < asset["size"] <= MAX_INDEX_BYTES:
            raise SourceError("Release JSON 资产描述无效", "invalid_release")
        url = asset.get("browser_download_url")
        if not isinstance(url, str) or not url.startswith(RELEASE_DOWNLOAD_PREFIX):
            raise SourceError("Release 资产地址不在固定来源", "untrusted_source")
        data = self._request(f"{API_ROOT}/releases/assets/{asset['id']}", "application/octet-stream", MAX_INDEX_BYTES)
        if len(data) != asset["size"]:
            raise SourceError("release.json 大小与 Release 资产不一致", "size_mismatch")
        try:
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError
        except (ValueError, UnicodeError) as error:
            raise SourceError("release.json 不是有效对象", "invalid_json") from error
        return value, sha256_bytes(data)

    def asset(self, release: dict) -> bytes:
        data = self._request(release["_download_url"], "application/octet-stream", MAX_ARCHIVE_BYTES)
        if len(data) != release["archive"]["size"] or sha256_bytes(data) != release["archive"]["sha256"]:
            raise SourceError("发行归档大小或摘要不符", "digest_mismatch")
        return data


def unpack(archive: bytes, staging: Path, release: dict) -> Path:
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise SourceError("归档超过大小限制", "size_mismatch")
    try:
        with zipfile.ZipFile(io.BytesIO(archive), "r") as bundle:
            infos = bundle.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or len(names) > MAX_FILES:
                raise SourceError("归档条目重复或过多", "invalid_archive")
            expected = {item["path"]: item for item in release["files"]}
            if set(names) != set(expected):
                raise SourceError("归档条目与文件清单不一致", "invalid_archive")
            root = staging.resolve()
            staging.mkdir(parents=True, exist_ok=False)
            for info in infos:
                relative = info.filename
                pure = PurePosixPath(relative)
                if info.is_dir() or "\\" in relative or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
                    raise SourceError("归档路径越界或包含目录项", "unsafe_path")
                mode = (info.external_attr >> 16) & 0o777
                if mode & 0o170000 == 0o120000:
                    raise SourceError("归档包含符号链接", "unsafe_path")
                if mode and mode & 0o111:
                    raise SourceError("归档包含可执行权限，不符合发行清单", "invalid_archive")
                target = staging.joinpath(*pure.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                current = staging
                for part in pure.parts[:-1]:
                    current = current / part
                    if current.is_symlink():
                        raise SourceError("归档路径经过符号链接", "unsafe_path")
                expected_mode = int(expected[relative]["mode"], 8)
                with bundle.open(info, "r") as source, target.open("xb") as output:
                    size = 0
                    digest = hashlib.sha256()
                    while chunk := source.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_FILE_BYTES:
                            raise SourceError("归档文件超过大小限制", "size_mismatch")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if size != expected[relative]["size"] or digest.hexdigest() != expected[relative]["sha256"]:
                    raise SourceError(f"归档文件摘要不符：{relative}", "digest_mismatch")
                if expected_mode & 0o111:
                    raise SourceError("发行文件声明了可执行权限", "invalid_archive")
                target.chmod(expected_mode)
            return staging / SKILL_NAME
    except (zipfile.BadZipFile, RuntimeError, OSError) as error:
        if isinstance(error, SourceError):
            raise
        raise SourceError(f"归档无法安全展开：{error}", "invalid_archive") from error


def download_cache(version: str, release: dict, source: Source) -> Path:
    home = instance_home(real_root())
    directory = home / "downloads" / version
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / f"{SKILL_NAME}-{version}.zip"
    index = directory / "release.json"
    if archive.is_symlink() or index.is_symlink():
        raise UpdateError("下载缓存不能是符号链接", "invalid_cache")
    if index.is_file():
        try:
            cached = validate_release(read_json(index), version)
        except UpdateError:
            cached = None
        if cached and {key: cached[key] for key in ("archive", "files", "commit", "tag")} == {key: release[key] for key in ("archive", "files", "commit", "tag")} and archive.is_file():
            if archive.stat().st_size == cached["archive"]["size"] and sha256_file(archive) == cached["archive"]["sha256"]:
                return archive
    data = source.asset(release)
    temporary = archive.with_name(f".{archive.name}.{uuid.uuid4().hex}.tmp")
    staging = directory / f".staging-{uuid.uuid4().hex}"
    try:
        with temporary.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        unpack(data, staging, release)
        os.replace(temporary, archive)
        atomic_json(index, {key: value for key, value in release.items() if not key.startswith("_")})
        sync_directory(directory)
        return archive
    finally:
        temporary.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)


def local_record(path: Path, relative: str) -> dict:
    if path.is_symlink():
        return {"path": relative, "kind": "link", "size": 0, "sha256": None}
    if path.is_file():
        return {"path": relative, "kind": "file", "size": path.stat().st_size, "sha256": sha256_file(path)}
    return {"path": relative, "kind": "other", "size": 0, "sha256": None}


def scan_installed(root: Path, release: dict) -> tuple[list[str], list[str], list[str], list[dict]]:
    target = {str(PurePosixPath(item["path"]).relative_to(SKILL_NAME)): item for item in release["files"]}
    ignored = {".DS_Store", "__pycache__", ".git", ".venv", "node_modules"}
    add, modified, remove = [], [], []
    observed: dict[str, dict] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in ignored for part in relative.parts):
            continue
        name = relative.as_posix()
        if path.is_symlink():
            modified.append(name)
            observed[name] = local_record(path, name)
        elif path.is_file():
            expected = target.get(name)
            if expected is None:
                remove.append(name)
                observed[name] = local_record(path, name)
            elif path.stat().st_size != expected["size"] or sha256_file(path) != expected["sha256"]:
                modified.append(name)
                observed[name] = local_record(path, name)
    for name in target:
        path = root / name
        if not path.exists():
            add.append(name)
        elif not path.is_file() or path.is_symlink():
            if name not in observed:
                modified.append(name)
                observed[name] = local_record(path, name)
    local_manifest = [observed[name] for name in sorted(observed)]
    return sorted(add), sorted(modified), sorted(remove), local_manifest


def installed_manifest(root: Path) -> list[dict]:
    result = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in {".DS_Store", "__pycache__", ".git", ".venv", "node_modules"} for part in relative.parts):
            continue
        if path.is_file() and not path.is_symlink():
            result.append({
                "path": f"{SKILL_NAME}/{relative.as_posix()}",
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
                "mode": f"0{path.stat().st_mode & 0o777:03o}",
            })
    return result


def has_git_marker(root: Path) -> bool:
    return any((parent / ".git").exists() for parent in [root, *root.parents])


def plan_bytes(plan: dict) -> str:
    return sha256_bytes(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def check_state_path() -> Path:
    return instance_home(real_root()) / "check.json"


def failure_state_path() -> Path:
    return instance_home(real_root()) / "failures.json"


def note_failure() -> None:
    path = failure_state_path()
    try:
        data = read_json(path)
    except UpdateError:
        data = {}
    atomic_json(path, {"last_failure_at": time.time()})


def check_update(force: bool = False, source: Source | None = None) -> dict:
    root = real_root()
    current = installed_version(root)
    cached = None
    try:
        cached = read_json(check_state_path())
    except UpdateError:
        pass
    if not force and cached and cached.get("current") == current and isinstance(cached.get("checked_at"), (int, float)) and time.time() - cached["checked_at"] < CHECK_TTL:
        return {"ok": True, "cached": True, "current": current, "latest": cached.get("latest"), "update_available": bool(cached.get("update"))}
    if not force:
        try:
            failures = read_json(failure_state_path())
            if isinstance(failures.get("last_failure_at"), (int, float)) and time.time() - failures["last_failure_at"] < FAILURE_COOLDOWN:
                return {"ok": True, "cached": False, "current": current, "latest": None, "update_available": False, "deferred": True, "say": "上次检查失败，1小时内不重试"}
        except UpdateError:
            pass
    try:
        latest = (source or Source()).latest()
    except UpdateError:
        note_failure()
        raise
    newer = version_key(latest["version"]) > version_key(current)
    atomic_json(check_state_path(), {"current": current, "latest": latest["version"], "checked_at": time.time(), "update": newer})
    try:
        failure_state_path().unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True, "cached": False, "current": current, "latest": latest["version"], "update_available": newer}


def make_plan(version: str, out: Path, source: Source | None = None) -> dict:
    root = real_root()
    requested = out.expanduser()
    if requested.is_symlink():
        raise UpdateError("计划输出不能是符号链接", "invalid_plan")
    temporary = requested.resolve()
    if temporary == root or temporary.is_relative_to(root):
        raise UpdateError("计划输出必须在安装目录外", "unsafe_path")
    if temporary.exists():
        raise UpdateError("计划输出已存在，拒绝覆盖", "plan_exists")
    current = installed_version(root)
    if not safe_version(version) or version_key(version) <= version_key(current):
        raise UpdateError("目标版本必须高于当前安装版本", "version_mismatch")
    if has_git_marker(root):
        raise UpdateError("这是源码 checkout，更新请由作者在仓库维护；不在源码树安装", "source_checkout")
    release = (source or Source()).release(version)
    remember_release(release)
    archive = download_cache(version, release, source or Source())
    staging = instance_home(root) / "staging" / f"plan-{uuid.uuid4().hex}"
    try:
        unpack(archive.read_bytes(), staging, release)
        staged = staging / SKILL_NAME
        staged_version = installed_version(staged)
        if staged_version != version:
            raise SourceError("包内 metadata.version 与发行索引不一致", "version_mismatch")
        add, modified, remove, local_manifest = scan_installed(root, release)
        plan = {
            "schema_version": 1,
            "action": "install",
            "plan_id": uuid.uuid4().hex,
            "created_at": time.time(),
            "skill": SKILL_NAME,
            "repository": REPOSITORY,
            "skill_path": SKILL_PATH,
            "channel": CHANNEL,
            "from_version": current,
            "target_version": version,
            "real_root": str(root),
            "source": {"tag": release["tag"], "commit": release["commit"], "archive": release["archive"], "index_sha256": release["_index_sha256"]},
            "files": release["files"],
            "add": add,
            "remove": remove,
            "modified": modified,
            "local_manifest": local_manifest,
            "compatibility": release["project_schema"],
            "dependencies": release["dependencies"],
            "migration_notes": release["migration_notes"],
            "backup": str(instance_home(root) / "backups"),
            "archive_path": str(archive),
            "requires_yes": True,
        }
        plan["summary"] = plan_bytes(plan)
        atomic_json(temporary, plan)
        return plan
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def validate_plan(value: dict) -> dict:
    required = {
        "schema_version", "action", "plan_id", "created_at", "skill", "repository", "skill_path",
        "channel", "from_version", "target_version", "real_root", "source", "files", "add",
        "remove", "modified", "local_manifest", "compatibility", "dependencies", "migration_notes", "backup",
        "archive_path", "requires_yes", "summary",
    }
    if set(value) != required:
        raise UpdateError("安装计划字段无效", "invalid_plan")
    if value["schema_version"] != SCHEMA_VERSION or value["action"] != "install" or value["requires_yes"] is not True:
        raise UpdateError("安装计划 schema 或动作无效", "invalid_plan")
    if (value["skill"], value["repository"], value["skill_path"], value["channel"]) != (SKILL_NAME, REPOSITORY, SKILL_PATH, CHANNEL):
        raise UpdateError("计划来源不是固定发行来源", "untrusted_source")
    if not isinstance(value["plan_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["plan_id"]):
        raise UpdateError("计划 ID 无效", "invalid_plan")
    if not safe_version(value["from_version"]) or not safe_version(value["target_version"]):
        raise UpdateError("计划版本无效", "version_mismatch")
    if not isinstance(value["real_root"], str) or value["real_root"] != str(real_root()):
        raise UpdateError("计划安装路径与当前真实路径不一致", "path_mismatch")
    source = value["source"]
    if not isinstance(source, dict) or set(source) != {"tag", "commit", "archive", "index_sha256"}:
        raise UpdateError("计划来源字段无效", "invalid_plan")
    if source["tag"] != skill_tag(value["target_version"]) or not isinstance(source["commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", source["commit"]):
        raise UpdateError("计划 tag 或 commit 无效", "version_mismatch")
    if not isinstance(source["index_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", source["index_sha256"]):
        raise UpdateError("计划摘要无效", "invalid_plan")
    archive = source["archive"]
    if not isinstance(archive, dict) or set(archive) != {"name", "size", "sha256"}:
        raise UpdateError("计划归档字段无效", "invalid_plan")
    if archive["name"] != f"{SKILL_NAME}-{value['target_version']}.zip" or type(archive["size"]) is not int or not 0 < archive["size"] <= MAX_ARCHIVE_BYTES:
        raise UpdateError("计划归档名称或大小无效", "invalid_plan")
    if not isinstance(archive["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", archive["sha256"]):
        raise UpdateError("计划归档摘要无效", "invalid_plan")
    if value["compatibility"] != {"read": [1, 2], "write": [2]} or value["dependencies"] != {"python": ">=3.10", "pillow": "required-for-image-operations"}:
        raise UpdateError("计划兼容性字段无效", "invalid_plan")
    if value["archive_path"] != str(instance_home(real_root()) / "downloads" / value["target_version"] / f"{SKILL_NAME}-{value['target_version']}.zip"):
        raise UpdateError("计划归档路径越界", "unsafe_path")
    if value["backup"] != str(instance_home(real_root()) / "backups"):
        raise UpdateError("计划备份路径越界", "unsafe_path")
    for key in ("files", "add", "remove", "modified", "local_manifest"):
        if not isinstance(value[key], list):
            raise UpdateError("计划文件字段无效", "invalid_plan")
    normalize_manifest_files(value["files"])
    groups = {}
    for key in ("add", "remove", "modified"):
        if any(not isinstance(item, str) or not item for item in value[key]) or len(value[key]) != len(set(value[key])):
            raise UpdateError("计划本地路径列表无效", "invalid_plan")
        for item in value[key]:
            pure = PurePosixPath(item)
            if pure.is_absolute() or "\\" in item or any(part in {"", ".", ".."} for part in pure.parts):
                raise UpdateError("计划本地路径越界", "unsafe_path")
        groups[key] = set(value[key])
    if groups["add"] & groups["modified"] or groups["modified"] & groups["remove"] or groups["add"] & groups["remove"]:
        raise UpdateError("计划本地路径分类冲突", "invalid_plan")
    observed = {}
    for item in value["local_manifest"]:
        if not isinstance(item, dict) or set(item) != {"path", "kind", "size", "sha256"}:
            raise UpdateError("计划本地摘要字段无效", "invalid_plan")
        relative = item["path"]
        if not isinstance(relative, str) or relative in observed or item["kind"] not in {"file", "link", "other"}:
            raise UpdateError("计划本地摘要无效", "invalid_plan")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or "\\" in relative or any(part in {"", ".", ".."} for part in pure.parts):
            raise UpdateError("计划本地路径越界", "unsafe_path")
        if item["kind"] == "file":
            if type(item["size"]) is not int or not 0 <= item["size"] <= MAX_FILE_BYTES or not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                raise UpdateError("计划本地文件摘要无效", "invalid_plan")
        elif item["size"] != 0 or item["sha256"] is not None:
            raise UpdateError("计划本地链接/目录摘要无效", "invalid_plan")
        observed[relative] = item
    if set(observed) != groups["modified"] | groups["remove"]:
        raise UpdateError("计划本地变更与摘要不一致", "invalid_plan")
    checked = value.copy()
    summary = checked.pop("summary")
    if sha256_bytes(json.dumps(checked, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")) != summary:
        raise UpdateError("计划内容与摘要不一致", "plan_tampered")
    return value


def transaction_path(home: Path, transaction: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", transaction):
        raise UpdateError("事务 ID 无效", "invalid_transaction")
    return home / "transactions" / f"{transaction}.json"


def load_transaction(transaction: str) -> tuple[Path, Path, dict]:
    home = instance_home(real_root())
    path = transaction_path(home, transaction)
    return home, path, read_json(path)


def write_transaction(path: Path, value: dict) -> None:
    atomic_json(path, value)


def copy_tree(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        if item.is_symlink():
            raise UpdateError("备份遇到符号链接；拒绝继续", "unsafe_path")
        if item.is_dir():
            (destination / relative).mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            shutil.copy2(item, destination / relative)


def safe_target(root: Path, relative: str | PurePosixPath) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise UpdateError("安装相对路径越界", "unsafe_path")
    target = root.joinpath(*pure.parts)
    current = target.parent
    while current != root and current != current.parent:
        if current.is_symlink():
            raise UpdateError(f"安装路径经过符号链接：{relative}", "unsafe_path")
        current = current.parent
    return target


def check_local_unchanged(root: Path, plan: dict) -> None:
    candidate = {"files": plan["files"]}
    add, modified, remove, observed = scan_installed(root, candidate)
    if (add, modified, remove, observed) != (plan["add"], plan["modified"], plan["remove"], plan["local_manifest"]):
        raise UpdateError("安装目录在计划后发生本地变化；重新生成计划", "concurrent_change")


def verify_plan_files(root: Path, plan: dict) -> None:
    if installed_version(root) != plan["target_version"]:
        raise UpdateError("安装后版本读回失败", "verification_failed")
    for item in plan["files"]:
        relative = PurePosixPath(item["path"]).relative_to(SKILL_NAME)
        target = safe_target(root, relative)
        if not target.is_file() or target.is_symlink() or sha256_file(target) != item["sha256"]:
            raise UpdateError(f"安装后文件校验失败：{relative}", "verification_failed")
    for relative in plan["remove"]:
        if relative != ".jarden-install.json" and safe_target(root, relative).exists():
            raise UpdateError(f"安装后旧文件仍存在：{relative}", "verification_failed")


def candidate_smoke(root: Path, version: str) -> None:
    script = root / "scripts" / "check_update.py"
    if not script.is_file() or script.is_symlink():
        raise UpdateError("新版本缺少离线检查入口", "verification_failed")
    try:
        result = subprocess.run(
            [sys.executable, "-E", "-s", "-B", str(script), "selfcheck", "--json"],
            capture_output=True, text=True, timeout=10, cwd=root, env=maintenance_environment(),
        )
        output = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else {}
        if result.returncode != 0 or output.get("ok") is not True or output.get("version") != version:
            raise ValueError(result.stderr.strip() or "selfcheck 未返回目标版本")
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, json.JSONDecodeError) as error:
        raise UpdateError(f"新版本离线 smoke 失败：{error}", "verification_failed") from error


def finish_install(home: Path, path: Path, value: dict, phase: str = "committed") -> dict:
    plan = value["plan"]
    root = Path(value["real_root"])
    verify_plan_files(root, plan)
    candidate_smoke(root, plan["target_version"])
    receipt = root / ".jarden-install.json"
    atomic_json(receipt, {
        "schema_version": 1, "skill": SKILL_NAME, "version": plan["target_version"],
        "tag": plan["source"]["tag"], "commit": plan["source"]["commit"],
        "archive_sha256": plan["source"]["archive"]["sha256"],
        "index_sha256": plan["source"]["index_sha256"], "files": plan["files"],
    })
    atomic_json(home / "active.json", {
        "schema_version": 1, "version": plan["target_version"],
        "transaction": value["transaction"], "real_root": str(root),
    })
    value["phase"] = phase
    value["updated_at"] = time.time()
    value["error"] = None
    write_transaction(path, value)
    shutil.rmtree(Path(value["stage"]), ignore_errors=True)
    return {
        "ok": True, "transaction": value["transaction"], "version": plan["target_version"],
        "phase": phase, "receipt": str(receipt), "backup": value["backup"],
    }


def install(plan_path: Path, yes: bool = False) -> dict:
    if not yes:
        raise UpdateError("安装必须显式传 --yes", "confirmation_required")
    plan = validate_plan(read_json(plan_path))
    root = real_root()
    if str(root) != plan["real_root"] or has_git_marker(root):
        raise UpdateError("真实安装路径或源码身份在计划后变化", "path_mismatch")
    if installed_version(root) != plan["from_version"]:
        raise UpdateError("已安装版本在计划后变化", "version_mismatch")
    home = instance_home(root)
    with instance_gate(root, timeout=0):
        require_idle(home, root)
        for existing in (home / "transactions").glob("*.json"):
            if read_json(existing).get("phase") not in TERMINAL:
                raise UpdateError("存在未结束事务；先运行 status --recover --yes", "recovery_required")
        check_local_unchanged(root, plan)
        if any(item["kind"] != "file" for item in plan["local_manifest"]):
            raise UpdateError("安装目录含符号链接或非普通文件；先人工处理本地变更", "unsafe_local_change")
        archive = Path(plan["archive_path"])
        if archive.is_symlink() or not archive.is_file():
            raise UpdateError("计划归档不存在", "invalid_cache")
        archive_data = archive.read_bytes()
        if len(archive_data) != plan["source"]["archive"]["size"] or sha256_bytes(archive_data) != plan["source"]["archive"]["sha256"]:
            raise SourceError("缓存归档在计划后发生变化", "digest_mismatch")
        transaction = uuid.uuid4().hex
        stage = home / "staging" / f"txn-{transaction}"
        unpack(archive_data, stage, {"files": plan["files"]})
        staged = stage / SKILL_NAME
        if installed_version(staged) != plan["target_version"]:
            raise SourceError("缓存包版本与计划不一致", "version_mismatch")
        for item in plan["files"]:
            relative = PurePosixPath(item["path"]).relative_to(SKILL_NAME).as_posix()
            target = safe_target(root, relative)
            if target.exists() and (target.is_symlink() or not target.is_file()):
                raise UpdateError(f"安装目标不是普通文件：{relative}", "unsafe_path")
        backup = home / "backups" / f"txn-{transaction}"
        backup_source = backup / "source"
        copy_tree(root, backup_source)
        old_manifest = installed_manifest(root)
        active_backup = backup / "active"
        active_backup.mkdir(parents=True)
        old_receipt = root / ".jarden-install.json"
        if old_receipt.is_file() and not old_receipt.is_symlink():
            shutil.copy2(old_receipt, active_backup / ".jarden-install.json")
        path = home / "transactions" / f"{transaction}.json"
        value = {
            "schema_version": 1, "transaction": transaction, "phase": "prepared",
            "created_at": time.time(), "updated_at": time.time(), "real_root": str(root),
            "from_version": plan["from_version"], "to_version": plan["target_version"],
            "plan": plan, "plan_path": str(plan_path.resolve()),
            "backup": str(backup), "stage": str(stage), "active_backup": str(active_backup),
            "old_files": old_manifest,
            "preexisting": sorted(item["path"] for item in plan["files"] if item["path"] in {old["path"] for old in old_manifest}),
            "index_sha256": plan["source"]["index_sha256"],
            "error": None,
        }
        write_transaction(path, value)
        try:
            value["phase"] = "activating"
            value["updated_at"] = time.time()
            write_transaction(path, value)
            old_receipt.unlink(missing_ok=True)
            for item in plan["files"]:
                relative = PurePosixPath(item["path"]).relative_to(SKILL_NAME)
                source = staged.joinpath(*relative.parts)
                target = safe_target(root, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                saved = active_backup.joinpath(*relative.parts)
                temporary = target.with_name(f".{target.name}.jarden-tmp")
                if temporary.exists() or temporary.is_symlink():
                    raise UpdateError(f"安装临时路径被占用：{relative}", "unsafe_path")
                if target.is_file():
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, saved)
                shutil.copy2(source, temporary)
                os.replace(temporary, target)
            for relative in plan["remove"]:
                if relative == ".jarden-install.json":
                    continue
                old = safe_target(root, relative)
                if old.is_file():
                    saved = active_backup.joinpath(*PurePosixPath(relative).parts)
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(old, saved)
            value["phase"] = "verifying"
            value["updated_at"] = time.time()
            write_transaction(path, value)
            verify_plan_files(root, plan)
            return finish_install(home, path, value)
        except Exception as error:
            value["error"] = str(error)
            write_transaction(path, value)
            try:
                restore_transaction(home, path, read_json(path))
            except Exception as restore_error:
                value["phase"] = "rollback_failed"
                value["error"] = f"{error}；恢复失败：{restore_error}"
                write_transaction(path, value)
                raise UpdateError(f"安装失败且自动恢复未完成：{error}；{restore_error}", "rollback_failed") from error
            raise UpdateError(f"安装失败，已恢复旧版：{error}", getattr(error, "code", "install_failed")) from error


def restore_transaction(home: Path, path: Path, value: dict) -> None:
    root = Path(value["real_root"])
    backup_source = Path(value["backup"]) / "source"
    plan = value["plan"]
    if not backup_source.is_dir() or backup_source.is_symlink():
        raise UpdateError("外部完整备份缺失", "backup_missing")
    old_files = {item["path"]: item for item in value["old_files"]}
    affected = {PurePosixPath(item["path"]).relative_to(SKILL_NAME).as_posix() for item in plan["files"]}
    affected.update(plan["remove"])
    affected.add(".jarden-install.json")
    for relative in sorted(affected):
        expected = old_files.get(f"{SKILL_NAME}/{relative}")
        if expected:
            saved = safe_target(backup_source, relative)
            if not saved.is_file() or saved.is_symlink():
                raise UpdateError(f"旧文件完整备份缺失：{relative}", "backup_missing")
            if saved.stat().st_size != expected["size"] or sha256_file(saved) != expected["sha256"]:
                raise UpdateError(f"旧文件完整备份摘要不符：{relative}", "backup_corrupt")
        target = safe_target(root, relative)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise UpdateError(f"恢复目标不是普通文件：{relative}", "rollback_conflict")
    value["phase"] = "rolling_back"
    value["updated_at"] = time.time()
    write_transaction(path, value)
    for relative in sorted(affected):
        target = safe_target(root, relative)
        interrupted = target.with_name(f".{target.name}.jarden-tmp")
        if interrupted.is_file() or interrupted.is_symlink():
            interrupted.unlink()
        expected = old_files.get(f"{SKILL_NAME}/{relative}")
        if not expected:
            target.unlink(missing_ok=True)
            continue
        saved = safe_target(backup_source, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.restore-{uuid.uuid4().hex}")
        try:
            shutil.copy2(saved, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    if installed_version(root) != value["from_version"]:
        raise UpdateError("恢复后版本读回失败", "verification_failed")
    value["phase"] = "rolled_back"
    value["updated_at"] = time.time()
    write_transaction(path, value)
    shutil.rmtree(Path(value["stage"]), ignore_errors=True)
    try:
        atomic_json(home / "active.json", {
            "schema_version": 1, "version": value["from_version"],
            "transaction": value["transaction"], "real_root": str(root),
        })
    except OSError:
        pass


def status() -> dict:
    root = real_root()
    home = instance_home(root)
    try:
        version = installed_version(root)
    except UpdateError:
        version = None
    result = {"ok": True, "real_root": str(root), "version": version, "transactions": [], "active_readers": [], "needs_recovery": False}
    transaction_dir = home / "transactions"
    if transaction_dir.is_dir():
        for path in sorted(transaction_dir.glob("*.json")):
            try:
                value = read_json(path)
                result["transactions"].append({
                    "transaction": value.get("transaction"), "phase": value.get("phase"),
                    "from_version": value.get("from_version"), "to_version": value.get("to_version"),
                    "error": value.get("error"), "updated_at": value.get("updated_at"),
                })
                if value.get("phase") not in TERMINAL:
                    result["needs_recovery"] = True
            except UpdateError as error:
                result["transactions"].append({"error": str(error), "path": str(path)})
                result["needs_recovery"] = True
    with instance_gate(root):
        result["active_readers"] = active_leases(home, root)
    return result


def validate_transaction(home: Path, value: dict) -> None:
    transaction = value.get("transaction")
    if not isinstance(transaction, str) or not re.fullmatch(r"[0-9a-f]{32}", transaction):
        raise UpdateError("事务 ID 无效", "invalid_transaction")
    if value.get("real_root") != str(real_root()):
        raise UpdateError("事务真实路径与当前安装不一致", "path_mismatch")
    if value.get("backup") != str(home / "backups" / f"txn-{transaction}"):
        raise UpdateError("事务备份路径越界", "unsafe_path")
    if value.get("stage") != str(home / "staging" / f"txn-{transaction}"):
        raise UpdateError("事务暂存路径越界", "unsafe_path")
    if value.get("active_backup") != str(home / "backups" / f"txn-{transaction}" / "active"):
        raise UpdateError("事务活动备份路径越界", "unsafe_path")
    plan = value.get("plan")
    if not isinstance(plan, dict):
        raise UpdateError("事务缺少安装计划", "invalid_transaction")
    validate_plan(plan)
    preexisting = value.get("preexisting")
    if not isinstance(preexisting, list) or any(not isinstance(item, str) for item in preexisting):
        raise UpdateError("事务前置文件清单无效", "invalid_transaction")
    if not set(preexisting) <= {item["path"] for item in plan["files"]} or len(preexisting) != len(set(preexisting)):
        raise UpdateError("事务前置文件清单越界", "invalid_transaction")
    old_files = value.get("old_files")
    if not isinstance(old_files, list):
        raise UpdateError("事务旧文件清单无效", "invalid_transaction")
    normalize_manifest_files(old_files, release=False)


def recover(transaction: str, yes: bool = False) -> dict:
    if not yes:
        raise UpdateError("恢复必须显式传 --yes", "confirmation_required")
    home, path, value = load_transaction(transaction)
    if value.get("phase") in TERMINAL:
        return {"ok": True, "transaction": transaction, "phase": value["phase"], "changed": False}
    validate_transaction(home, value)
    root = real_root()
    with instance_gate(root, timeout=0):
        require_idle(home, root)
        phase = value.get("phase")
        if phase == "prepared":
            value["phase"] = "aborted"
            value["updated_at"] = time.time()
            write_transaction(path, value)
            shutil.rmtree(Path(value["stage"]), ignore_errors=True)
            return {"ok": True, "transaction": transaction, "phase": "aborted", "changed": True}
        if phase in {"activating", "activated", "verifying"}:
            try:
                verify_plan_files(root, value["plan"])
                return finish_install(home, path, value, phase="recovered") | {"changed": True}
            except UpdateError as error:
                value["error"] = str(error)
            restore_transaction(home, path, value)
            return {"ok": True, "transaction": transaction, "phase": "rolled_back", "version": value["from_version"], "changed": True}
        if phase in {"rolling_back", "rollback_failed"}:
            restore_transaction(home, path, value)
            return {"ok": True, "transaction": transaction, "phase": "rolled_back", "version": value["from_version"], "changed": True}
        raise UpdateError("事务 phase 未知；不猜测恢复动作", "unknown_transaction_phase")


def rollback(transaction: str, yes: bool = False) -> dict:
    if not yes:
        raise UpdateError("回滚必须显式传 --yes", "confirmation_required")
    home, path, value = load_transaction(transaction)
    if value.get("phase") not in {"committed", "recovered"}:
        raise UpdateError("只有已提交或已恢复验证的事务可以回滚；未结束事务先用 status --recover", "invalid_transaction_state")
    validate_transaction(home, value)
    root = real_root()
    with instance_gate(root, timeout=0):
        require_idle(home, root)
        active_backup = Path(value["active_backup"])
        if not active_backup.is_dir():
            raise UpdateError("外部备份缺失，拒绝回滚", "backup_missing")
        if installed_version(root) != value["to_version"]:
            raise UpdateError("安装版本已变化，拒绝应用旧事务", "version_mismatch")
        for item in value["plan"]["files"]:
            relative = PurePosixPath(item["path"]).relative_to(SKILL_NAME)
            target = safe_target(root, relative)
            if not target.is_file() or target.is_symlink() or sha256_file(target) != item["sha256"]:
                raise UpdateError(f"回滚前检测到本地修改：{relative}", "local_modified")
        for relative in value["plan"]["remove"]:
            if relative == ".jarden-install.json":
                continue
            target = safe_target(root, relative)
            if target.exists() or target.is_symlink():
                raise UpdateError(f"回滚前检测到新增本地内容：{relative}", "local_modified")
        try:
            value["phase"] = "rolling_back"
            value["updated_at"] = time.time()
            write_transaction(path, value)
            restore_transaction(home, path, value)
            return {"ok": True, "transaction": transaction, "phase": "rolled_back", "version": value["from_version"]}
        except Exception as error:
            value["phase"] = "rollback_failed"
            value["error"] = str(error)
            value["updated_at"] = time.time()
            write_transaction(path, value)
            raise UpdateError(f"回滚失败，项目文件未迁移；可用外部备份恢复：{error}", "rollback_failed") from error
