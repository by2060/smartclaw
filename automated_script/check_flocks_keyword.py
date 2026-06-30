#!/usr/bin/env python3
"""递归检查项目路径和文件内容中是否存在指定品牌词。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_KEYWORDS = ("Flocks", "flocks", "rex", "Rex")

DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "site-packages",
}


@dataclass(frozen=True, slots=True)
class NameMatch:
    path: str
    entry_type: str
    keyword: str


@dataclass(frozen=True, slots=True)
class ContentMatch:
    path: str
    line: int
    column: int
    keyword: str
    text: str


@dataclass(frozen=True, slots=True)
class SkippedFile:
    path: str
    reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "递归检查当前 Python 项目的文件名、目录名、路径和文件内容中是否存在 "
            "'Flocks'、'flocks'、'rex' 或 'Rex' 字样。"
        )
    )
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="要扫描的项目根目录，默认为 automated_script 的上一级目录。",
    )
    parser.add_argument(
        "--keyword",
        action="append",
        dest="keywords",
        help="要查找的关键词，可重复传入；不传时默认查找 'Flocks'、'flocks'、'rex' 和 'Rex'。",
    )
    parser.add_argument(
        "--ignore-case",
        action="store_true",
        help="忽略大小写进行匹配。",
    )
    parser.add_argument(
        "--no-default-excludes",
        action="store_true",
        help="不跳过 .git、node_modules 等常见生成目录。",
    )
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="要跳过的目录名，可重复传入。",
    )
    parser.add_argument(
        "--output",
        help="可选的 JSON 报告输出路径；控制台始终会打印结果。",
    )
    return parser.parse_args()


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def contains_keyword(value: str, keyword: str, ignore_case: bool) -> bool:
    if ignore_case:
        return keyword.casefold() in value.casefold()
    return keyword in value


def find_column(line: str, keyword: str, ignore_case: bool) -> int:
    haystack = line.casefold() if ignore_case else line
    needle = keyword.casefold() if ignore_case else keyword
    return haystack.find(needle) + 1


def iter_project_entries(
    root: Path,
    excluded_dirs: set[str],
) -> Iterable[Path]:
    for child in sorted(root.iterdir(), key=lambda item: item.as_posix()):
        if child.is_dir():
            if child.name in excluded_dirs:
                yield child
                continue
            yield child
            yield from iter_project_entries(child, excluded_dirs)
        else:
            yield child


def is_probably_binary(path: Path, sample_size: int = 4096) -> bool:
    try:
        sample = path.read_bytes()[:sample_size]
    except OSError:
        return False
    return b"\0" in sample


def normalize_keywords(keywords: list[str] | None, ignore_case: bool) -> list[str]:
    raw_keywords = keywords or list(DEFAULT_KEYWORDS)
    normalized: list[str] = []
    seen: set[str] = set()
    for keyword in raw_keywords:
        if not keyword:
            continue
        key = keyword.casefold() if ignore_case else keyword
        if key in seen:
            continue
        seen.add(key)
        normalized.append(keyword)
    return normalized


def scan_file_content(
    path: Path,
    root: Path,
    keywords: list[str],
    ignore_case: bool,
) -> tuple[list[ContentMatch], SkippedFile | None]:
    rel_path = relative_path(path, root)

    if path == SCRIPT_PATH:
        return [], None

    if is_probably_binary(path):
        return [], SkippedFile(rel_path, "二进制文件")

    matches: list[ContentMatch] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, start=1):
                for keyword in keywords:
                    if not contains_keyword(line, keyword, ignore_case):
                        continue
                    matches.append(
                        ContentMatch(
                            path=rel_path,
                            line=line_number,
                            column=find_column(line, keyword, ignore_case),
                            keyword=keyword,
                            text=line.rstrip("\r\n"),
                        )
                    )
    except OSError as exc:
        return [], SkippedFile(rel_path, f"读取失败：{exc}")

    return matches, None


def scan_project(
    root: Path,
    keywords: list[str],
    ignore_case: bool,
    excluded_dirs: set[str],
) -> dict[str, object]:
    name_matches: list[NameMatch] = []
    content_matches: list[ContentMatch] = []
    skipped_files: list[SkippedFile] = []
    scanned_files = 0
    scanned_dirs = 0

    for entry in iter_project_entries(root, excluded_dirs):
        if entry == SCRIPT_PATH:
            continue

        rel_path = relative_path(entry, root)

        for keyword in keywords:
            if contains_keyword(entry.name, keyword, ignore_case) or contains_keyword(
                rel_path, keyword, ignore_case
            ):
                name_matches.append(
                    NameMatch(
                        rel_path,
                        "directory" if entry.is_dir() else "file",
                        keyword,
                    )
                )

        if entry.is_dir():
            scanned_dirs += 1
            continue

        scanned_files += 1
        file_matches, skipped_file = scan_file_content(
            entry,
            root,
            keywords,
            ignore_case,
        )
        content_matches.extend(file_matches)
        if skipped_file is not None:
            skipped_files.append(skipped_file)

    return {
        "root": root.as_posix(),
        "keywords": keywords,
        "ignore_case": ignore_case,
        "excluded_dirs": sorted(excluded_dirs),
        "scanned_files": scanned_files,
        "scanned_dirs": scanned_dirs,
        "name_matches": [asdict(item) for item in name_matches],
        "content_matches": [asdict(item) for item in content_matches],
        "skipped_files": [asdict(item) for item in skipped_files],
    }


def print_report(report: dict[str, object]) -> None:
    print(f"扫描根目录：{report['root']}")
    print(f"查找关键词：{', '.join(repr(item) for item in report['keywords'])}")
    print(f"是否忽略大小写：{report['ignore_case']}")
    print(f"已扫描文件数：{report['scanned_files']}")
    print(f"已扫描目录数：{report['scanned_dirs']}")
    print()

    name_matches = report["name_matches"]
    content_matches = report["content_matches"]
    skipped_files = report["skipped_files"]

    print(f"文件名/路径命中数：{len(name_matches)}")
    for item in name_matches:
        entry_type = "目录" if item["entry_type"] == "directory" else "文件"
        print(f"  [{entry_type}] {item['path']}，关键词：{item['keyword']!r}")
    print()

    print(f"文件内容命中数：{len(content_matches)}")
    for item in content_matches:
        print(
            f"  {item['path']}:{item['line']}:{item['column']} "
            f"关键词：{item['keyword']!r}: {item['text']}"
        )
    print()

    print(f"跳过文件数：{len(skipped_files)}")
    for item in skipped_files:
        print(f"  {item['path']}: {item['reason']}")


def write_json_report(report: dict[str, object], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    if not root.exists() or not root.is_dir():
        print(f"无效的扫描根目录：{root}", file=sys.stderr)
        return 2

    keywords = normalize_keywords(args.keywords, args.ignore_case)
    if not keywords:
        print("至少需要提供一个非空关键词。", file=sys.stderr)
        return 2

    excluded_dirs = set(args.exclude_dir)
    if not args.no_default_excludes:
        excluded_dirs.update(DEFAULT_EXCLUDE_DIRS)

    report = scan_project(
        root=root,
        keywords=keywords,
        ignore_case=args.ignore_case,
        excluded_dirs=excluded_dirs,
    )
    print_report(report)

    if args.output:
        output_path = Path(args.output).resolve()
        write_json_report(report, output_path)
        print()
        print(f"JSON 报告已写入：{output_path}")

    has_matches = bool(report["name_matches"] or report["content_matches"])
    return 1 if has_matches else 0


if __name__ == "__main__":
    raise SystemExit(main())
