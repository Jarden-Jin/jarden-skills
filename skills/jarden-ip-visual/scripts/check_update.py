from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runtime_activity import RuntimeBusyError
from update_runtime import (
    ACCEPTED_PROJECT_SCHEMAS,
    SCHEMA_VERSION as RELEASE_INDEX_SCHEMA,
    UpdateError,
    check_update,
    emit,
    install,
    installed_version,
    make_plan,
    real_root,
    recover,
    rollback,
    status,
)


def add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON（目前默认也是 JSON）")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="jarden-ip-visual 受控检查、计划、安装、状态与回滚")
    commands = root.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="检查最高 stable 版本；不会自动串联桥接安装")
    check.add_argument("--force", action="store_true", help="跳过 24 小时成功缓存和 1 小时失败冷却")
    add_json(check)
    plan = commands.add_parser("plan", help="下载并校验候选包，只写外部更新计划")
    plan.add_argument("--version", required=True, help="明确指定目标版本；旧客户端先指定兼容桥接版，安装核验后再建新版计划")
    plan.add_argument("--out", required=True, type=Path)
    add_json(plan)
    apply = commands.add_parser("apply", help="应用已审批安装计划；必须显式 --yes")
    apply.add_argument("--plan", required=True, type=Path)
    apply.add_argument("--yes", action="store_true")
    add_json(apply)
    show = commands.add_parser("status", help="读取安装、活动 lease 与事务状态")
    show.add_argument("--recover", action="store_true", help="处理所有未结束事务")
    show.add_argument("--yes", action="store_true")
    add_json(show)
    back = commands.add_parser("rollback", help="从外部备份恢复旧代码，不迁移项目")
    back.add_argument("--transaction", required=True)
    back.add_argument("--yes", action="store_true")
    add_json(back)
    selfcheck = commands.add_parser("selfcheck", help=argparse.SUPPRESS)
    add_json(selfcheck)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "check":
            result = check_update(force=args.force)
        elif args.command == "plan":
            result = make_plan(args.version, args.out)
        elif args.command == "apply":
            result = install(args.plan, yes=args.yes)
        elif args.command == "status":
            if args.recover:
                if not args.yes:
                    raise UpdateError("恢复必须显式传 --yes", "confirmation_required")
                for item in status()["transactions"]:
                    if item.get("phase") not in {"committed", "rolled_back", "recovered", "aborted"} and item.get("transaction"):
                        recover(item["transaction"], yes=True)
            result = status()
        elif args.command == "rollback":
            result = rollback(args.transaction, yes=args.yes)
        elif args.command == "selfcheck":
            result = {"ok": True, "skill": "jarden-ip-visual", "version": installed_version(real_root()), "real_root": str(real_root()),
                      "release_index_schema": RELEASE_INDEX_SCHEMA, "accepted_project_schemas": ACCEPTED_PROJECT_SCHEMAS}
        else:
            raise UpdateError("未知命令", "invalid_command")
        emit(result)
        return 0
    except (UpdateError, RuntimeBusyError) as error:
        print(json.dumps({
            "ok": False,
            "code": getattr(error, "code", "runtime_busy"),
            "error": str(error),
        }, ensure_ascii=False, sort_keys=True))
        return getattr(error, "exit_code", 2)
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "code": "interrupted", "error": "操作被中断；可用 status 检查事务"}, ensure_ascii=False))
        return 130
    except Exception as error:
        print(json.dumps({"ok": False, "code": "unexpected_error", "error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
