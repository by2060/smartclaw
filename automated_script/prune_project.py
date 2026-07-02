#!/usr/bin/env python3
"""删除仓库中预定义的一组文件和目录。"""

from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS: tuple[str, ...] = (
    ".smartclaw/smartclawhub",
    ".smartclaw/plugins/tasks/daily-intel.yaml",
    "smartclaw/tool/wecom",
    "smartclaw/tool/agent/call_omo_agent.py",
    "packaging",
    ".github",
    "tests",
    "assets",
    ".venv",
    "docker",
    "docs",
    "scripts/install.ps1",
    "scripts/install.sh",
    "scripts/install_zh.ps1",
    "scripts/install_zh.sh",
    "webui/.gitignore",
    ".dockerignore",
    ".gitattributes",
    ".gitignore",
    "pyproject.toml",
    "install.ps1",
    "install.sh",
    "install_zh.ps1",
    "install_zh.sh",
    "LICENSE.txt",
    "Makefile",
    "README.md",
    "README_zh.md",
    "uv.lock",
    ".git",
    ".idea",
    "npm-wrapper",
    "tui",
    "scripts/container-start.sh",
    "scripts/dev.sh",
    "scripts/migrate_legacy_task_tables.py",
    "scripts/recover_raw_smartclaw_db.py",
    "scripts/run_legacy_task_migration.sh",
    "scripts/validate_smartclawhub.py",
    "webui/eslint.config.js",
    "webui/index.html",
    "webui/node_modules",
    "webui/package.json",
    "webui/package-lock.json",
    "webui/postcss.config.js",
    "webui/public",
    "webui/README.md",
    "webui/src",
    "webui/tailwind.config.js",
    "webui/tsconfig.json",
    "webui/tsconfig.node.json",
    "webui/vite.config.ts",
    "webui/vitest.config.ts",
    "webui/.env.example"


)


@dataclass(frozen=True, slots=True)
class DeletionResult:
    path: str
    status: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按目标清单删除仓库中的文件和目录。")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正执行删除；不传时仅预览将删除的内容。",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="打印当前目标清单后退出。",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="追加一个仓库相对路径到删除清单，可多次传入。",
    )
    parser.add_argument(
        "--no-defaults",
        action="store_true",
        help="仅处理 --path 传入的路径，不使用默认清单。",
    )
    return parser.parse_args()


def normalize_target(target: str) -> str:
    normalized = Path(target).as_posix().strip("/")
    if not normalized or normalized == ".":
        raise ValueError(f"不支持的目标路径：{target!r}")
    return normalized


def build_target_list(custom_targets: list[str], include_defaults: bool) -> list[str]:
    raw_targets = [*DEFAULT_TARGETS] if include_defaults else []
    raw_targets.extend(custom_targets)

    unique_targets: list[str] = []
    seen: set[str] = set()
    for raw_target in raw_targets:
        target = normalize_target(raw_target)
        if target not in seen:
            seen.add(target)
            unique_targets.append(target)
    return unique_targets


def resolve_target(target: str) -> Path:
    relative = Path(target)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"只允许使用仓库相对路径：{target}")

    candidate = ROOT / relative
    if candidate.is_symlink():
        return candidate

    resolved = candidate.resolve(strict=False)
    if resolved != ROOT and ROOT not in resolved.parents:
        raise ValueError(f"路径超出仓库根目录：{target}")
    return candidate


def describe_path(path: Path) -> str:
    if path.is_symlink():
        return "符号链接"
    if path.is_dir():
        return "目录"
    if path.is_file():
        return "文件"
    return "路径"


def delete_target(target: str, apply: bool) -> DeletionResult:
    candidate = resolve_target(target)
    if not candidate.exists() and not candidate.is_symlink():
        return DeletionResult(target, "不存在")

    kind = describe_path(candidate)
    if not apply:
        return DeletionResult(target, f"将删除{kind}")

    if candidate.is_symlink() or candidate.is_file():
        candidate.unlink(missing_ok=True)
        return DeletionResult(target, f"已删除{kind}")

    if candidate.is_dir():
        shutil.rmtree(candidate)
        return DeletionResult(target, "已删除目录")

    candidate.unlink(missing_ok=True)
    return DeletionResult(target, "已删除路径")


def print_summary(results: list[DeletionResult]) -> None:
    deleted_count = sum(result.status.startswith("已删除") for result in results)
    missing_count = sum(result.status == "不存在" for result in results)
    preview_count = sum(result.status.startswith("将删除") for result in results)
    print(f"汇总：已删除 {deleted_count} 项，不存在 {missing_count} 项，待删除 {preview_count} 项，共 {len(results)} 项。")


def main() -> int:
    args = parse_args()

    try:
        targets = build_target_list(args.path, include_defaults=not args.no_defaults)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    if args.list_targets:
        for target in targets:
            print(target)
        return 0

    if not targets:
        print("没有可处理的目标。")
        return 0

    results: list[DeletionResult] = []
    for target in targets:
        try:
            result = delete_target(target, apply=args.apply)
        except ValueError as error:
            raise SystemExit(str(error)) from error
        print(f"[{result.status}] {result.path}")
        results.append(result)

    print_summary(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
