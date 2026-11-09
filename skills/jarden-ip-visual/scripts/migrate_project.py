#!/usr/bin/env python3
"""按明确计划迁移单个项目，不迁移安装或扫描其他项目。"""

from __future__ import annotations

import argparse
from pathlib import Path

import project as state


def main() -> int:
    parser = argparse.ArgumentParser(description="预览或明确执行旧项目到当前 schema 的迁移")
    parser.add_argument("project")
    parser.add_argument("--out")
    parser.add_argument("--plan")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path(args.project).resolve()
    try:
        from runtime_activity import execution_lease
        with execution_lease():
            if args.apply:
                if not args.plan or args.dry_run:
                    raise state.ProjectError("明确执行需要 --apply --plan <已检查计划>")
                return state.emit(state.apply_migration(root, state.read_json(Path(args.plan))))
            plan = state.migration_plan(root)
            if args.out:
                target = Path(args.out).resolve()
                if target.is_relative_to(root):
                    raise state.ProjectError("迁移预览计划必须保存在项目外")
                if target.exists():
                    raise state.ProjectError("计划文件已存在，拒绝覆盖")
                state.write_json(target, plan)
            return state.emit({"ok": True, "dry_run": True, "plan": plan})
    except (state.ProjectError, OSError, ValueError) as error:
        return state.emit({"ok": False, "error": str(error)})


if __name__ == "__main__":
    raise SystemExit(main())
