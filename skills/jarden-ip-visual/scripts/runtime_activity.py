from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Iterator


class RuntimeBusyError(OSError):
    pass


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
try:
    DEFAULT_IDENTITY = (DEFAULT_ROOT.stat().st_dev, DEFAULT_ROOT.stat().st_ino)
except OSError:
    DEFAULT_IDENTITY = None


def real_root(skill_root: Path | None = None) -> Path:
    return (skill_root or DEFAULT_ROOT).expanduser().resolve()


def update_home() -> Path:
    override = os.environ.get("JARDEN_UPDATE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "jarden-ip-visual" / "updates"


def instance_home(skill_root: Path | None = None) -> Path:
    root = real_root(skill_root)
    home = update_home()
    if home == root or root in home.parents or home in root.parents:
        raise RuntimeBusyError("更新工作区必须与安装目录分离，不能互相包含")
    identity = hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()
    return home / "instances" / identity


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def sync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def acquire_gate(stream, timeout: float) -> None:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as error:
            if time.monotonic() >= deadline:
                raise RuntimeBusyError("安装实例正被更新或注册占用，请稍后重试") from error
            time.sleep(min(0.02, max(0.001, deadline - time.monotonic())))


def release_gate(stream) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def instance_gate(skill_root: Path | None = None, timeout: float = 2.0) -> Iterator[Path]:
    home = instance_home(skill_root)
    home.mkdir(parents=True, exist_ok=True)
    lock = home / "activity.lock"
    if lock.is_symlink():
        raise RuntimeBusyError("实例锁不能是符号链接")
    with lock.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            acquire_gate(stream, timeout)
        except RuntimeBusyError:
            raise
        try:
            yield home
        finally:
            release_gate(stream)


def active_leases(home: Path, root: Path) -> list[dict]:
    result = []
    for marker in sorted((home / "readers").glob("*.json")):
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            process = data["pid"]
            if type(process) is not int or process <= 0 or data["real_root"] != str(root):
                raise ValueError("invalid reader")
            try:
                os.kill(process, 0)
            except ProcessLookupError:
                marker.unlink(missing_ok=True)
                continue
            except PermissionError:
                pass
            result.append(data)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise RuntimeBusyError("活动记录无法验证；保留记录并拒绝切换") from error
    return result


def require_idle(home: Path, root: Path) -> None:
    if active_leases(home, root):
        raise RuntimeBusyError("真实安装目录有 active execution lease；等待业务或页面服务退出")


@contextlib.contextmanager
def execution_lease(skill_root: Path | None = None) -> Iterator[None]:
    root = real_root(skill_root)
    marker = instance_home(root) / "readers" / f"{os.getpid()}-{uuid.uuid4().hex}.json"
    with instance_gate(root) as home:
        if not root.is_dir():
            raise RuntimeBusyError("安装目录不存在；先用外部恢复入口恢复事务")
        if skill_root is None and DEFAULT_IDENTITY is not None:
            current = (root.stat().st_dev, root.stat().st_ino)
            if current != DEFAULT_IDENTITY:
                raise RuntimeBusyError("安装已切换；请重启当前命令，避免混用旧代码")
        for transaction in (home / "transactions").glob("*.json"):
            try:
                phase = json.loads(transaction.read_text(encoding="utf-8"))["phase"]
            except (OSError, ValueError, KeyError) as error:
                raise RuntimeBusyError("更新事务无法读取；先检查更新状态") from error
            if phase not in {"committed", "rolled_back", "recovered", "aborted"}:
                raise RuntimeBusyError("存在未结束的更新事务；先执行 status --recover --yes")
        atomic_json(marker, {"pid": os.getpid(), "real_root": str(root), "started_at": time.time()})
    try:
        yield
    finally:
        marker.unlink(missing_ok=True)
