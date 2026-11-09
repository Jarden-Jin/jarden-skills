from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


REPOSITORY = "Jarden-Jin/jarden-skills"
SKILL_NAME = "jarden-ip-visual"
SKILL_PATH = PurePosixPath("skills") / SKILL_NAME
CHANNEL = "stable"
PROJECT_SCHEMAS = {
    2: {"read": [1, 2], "write": [2]},
    3: {"read": [1, 2, 3], "write": [3]},
}
DEPENDENCIES = {"python": ">=3.10", "pillow": "required-for-image-operations"}
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
MAX_FILES = 2000
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024


class BuildError(RuntimeError):
    pass


def git(repo: Path, *args: str, binary: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise BuildError(result.stderr.decode("utf-8", "replace").strip() or f"git {' '.join(args)} 失败")
    return result.stdout if binary else result.stdout.decode("utf-8")


def version_of(source: str) -> str:
    lines = [line for line in source.splitlines() if line.startswith("  version:")]
    if len(lines) != 1:
        raise BuildError("SKILL.md 必须只有一条标准格式 metadata.version")
    value = lines[0].split(":", 1)[1].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", value):
        raise BuildError("metadata.version 不是 MAJOR.MINOR.PATCH")
    return value


def project_schema_of(source: bytes | None) -> dict:
    version = 2
    if source is not None:
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError, UnicodeError) as error:
            raise BuildError("固定 commit 中 project.py 无法解析") from error
        bindings = [node for node in ast.walk(tree) if isinstance(node, ast.Name)
                    and node.id == "SCHEMA_VERSION" and isinstance(node.ctx, ast.Store)]
        declarations = [node.value for node in tree.body if
                        isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "SCHEMA_VERSION"
                        or isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                        and node.target.id == "SCHEMA_VERSION"]
        if len(bindings) != 1 or len(declarations) != 1 or not isinstance(declarations[0], ast.Constant):
            raise BuildError("固定 commit 中 project.py 必须有唯一的顶层整数 SCHEMA_VERSION 声明")
        version = declarations[0].value
    if type(version) is not int or version not in PROJECT_SCHEMAS:
        raise BuildError("固定 commit 中 project.py 的项目 schema 不受支持")
    return {field: list(values) for field, values in PROJECT_SCHEMAS[version].items()}


def excluded(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        "evals" in parts
        or "__pycache__" in parts
        or "node_modules" in parts
        or parts[-1] in {".DS_Store", ".jarden-install.json", "release.json", ".env", "credentials.json", "settings.json"}
        or parts[-1].endswith((".key", ".pem"))
        or parts[-1].endswith((".pyc", ".pyo", ".tmp", ".swp"))
    )


def commit_files(repo: Path, commit: str) -> list[tuple[str, str, int]]:
    data = git(repo, "ls-tree", "-r", "-z", "--full-tree", commit, "--", SKILL_PATH.as_posix(), binary=True)
    assert isinstance(data, bytes)
    result = []
    for record in data.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ")
        path = raw_path.decode("utf-8")
        relative = PurePosixPath(path).relative_to(SKILL_PATH).as_posix()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise BuildError(f"Skill 发行只接受普通文件，拒绝 {mode} {path}")
        if mode == "100755" and not relative.endswith(".sh"):
            raise BuildError(f"发行文件不能带可执行权限：{path}")
        if excluded(relative):
            continue
        result.append((relative, object_id, 0o755 if mode == "100755" else 0o644))
    result.sort(key=lambda item: item[0])
    if not result or len(result) > MAX_FILES:
        raise BuildError("Skill 文件数量不在发行限制内")
    if not any(path == "SKILL.md" for path, _, _ in result):
        raise BuildError("固定 commit 中没有 SKILL.md")
    return result


def build(repo: Path, commit: str, version: str, change_type: str) -> tuple[bytes, dict, list[dict]]:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise BuildError("--commit 必须是完整的 40 位小写 Git SHA")
    resolved = git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}").strip()  # type: ignore[union-attr]
    if resolved != commit:
        raise BuildError(f"commit 未解析为同一完整 SHA：{resolved}")
    tag = f"{SKILL_NAME}/v{version}"
    try:
        tagged = git(repo, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}").strip()  # type: ignore[union-attr]
    except BuildError as error:
        raise BuildError(f"标签 {tag} 不存在；先提交并创建指向该 commit 的标签") from error
    if tagged != commit:
        raise BuildError(f"标签 {tag} 指向 {tagged}，不是指定 commit {commit}")
    skill_source = git(repo, "show", f"{commit}:{SKILL_PATH.as_posix()}/SKILL.md")
    assert isinstance(skill_source, str)
    if version_of(skill_source) != version:
        raise BuildError("--version 与固定 commit 中 SKILL.md metadata.version 不一致")
    files = commit_files(repo, commit)
    manifest = []
    project_schema = project_schema_of(None)
    archive_buffer = io.BytesIO()
    total = 0
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, object_id, mode in files:
            blob = git(repo, "cat-file", "blob", object_id, binary=True)
            assert isinstance(blob, bytes)
            if len(blob) > MAX_FILE_BYTES:
                raise BuildError(f"文件超过大小限制：{relative}")
            total += len(blob)
            if total > MAX_ARCHIVE_BYTES:
                raise BuildError("发行解压总大小超过限制")
            if relative == "scripts/project.py":
                project_schema = project_schema_of(blob)
            package_path = f"{SKILL_NAME}/{relative}"
            info = zipfile.ZipInfo(package_path, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100000 | mode) << 16
            archive.writestr(info, blob, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            manifest.append({
                "path": package_path,
                "size": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
                "mode": f"0{mode:o}",
            })
    archive_bytes = archive_buffer.getvalue()
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise BuildError("ZIP 超过发行大小限制")
    archive_name = f"{SKILL_NAME}-{version}.zip"
    migration_notes = (
        f"https://github.com/{REPOSITORY}/blob/{commit}/"
        f"{SKILL_PATH.as_posix()}/references/project-state.md"
    )
    release = {
        "schema_version": 1,
        "skill": SKILL_NAME,
        "repository": REPOSITORY,
        "skill_path": SKILL_PATH.as_posix(),
        "channel": CHANNEL,
        "version": version,
        "tag": tag,
        "commit": commit,
        "archive": {
            "name": archive_name,
            "size": len(archive_bytes),
            "sha256": hashlib.sha256(archive_bytes).hexdigest(),
        },
        "files": manifest,
        "project_schema": project_schema,
        "dependencies": DEPENDENCIES,
        "change_type": change_type,
        "migration_notes": migration_notes,
    }
    return archive_bytes, release, manifest


def write_fresh(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise BuildError(f"拒绝覆盖链接：{path}")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise BuildError(f"目标已存在，拒绝覆盖：{path}") from error
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从固定 Git commit 生成确定性 Jarden Skill 发行包与外部索引")
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True, help="完整 40 位 commit；必须与本地标签一致")
    parser.add_argument("--change-type", required=True, choices=("patch", "minor", "major"))
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1], help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        version_of(f"  version: \"{args.version}\"")
        archive, release, manifest = build(args.repo.resolve(), args.commit, args.version, args.change_type)
        output = args.output_dir.expanduser().resolve()
        zip_path = output / release["archive"]["name"]
        index_path = output / "release.json"
        for path in (zip_path, index_path):
            if path.exists() or path.is_symlink():
                raise BuildError(f"输出目标已存在，拒绝覆盖：{path}")
        write_fresh(zip_path, archive)
        write_fresh(index_path, (json.dumps(release, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
        if any(item["path"].split("/")[1:2] == ["evals"] for item in manifest):
            raise BuildError("发行清单意外包含 evals")
        print(json.dumps({
            "ok": True,
            "tag": release["tag"],
            "commit": release["commit"],
            "archive": str(zip_path),
            "index": str(index_path),
            "files": len(manifest),
            "archive_sha256": release["archive"]["sha256"],
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except (BuildError, OSError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
