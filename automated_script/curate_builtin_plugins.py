"""按策略配置整理仓库内预置的 smartClaw 内建插件。"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, NoReturn

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path(__file__).resolve().with_name("builtin-curation.yaml")
_VALID_STRATEGIES = {"allowlist", "blocklist"}
_SECTION_LABELS = {
    "agents": "智能体",
    "workflows": "工作流",
    "skills": "技能",
    "tools.api_providers": "工具 / API 提供方",
    "tools.python_tools": "工具 / Python 工具",
    "tools.mcp_tools": "工具 / MCP 工具",
    "tools.generated_tools": "工具 / 生成工具",
}


@dataclass(frozen=True)
class CuratedItem:
    section: str
    key: str
    path: Path
    fields: dict[str, str]

    def values_for(self, field_names: Iterable[str] | None = None) -> list[str]:
        names = list(field_names) if field_names else list(self.fields.keys())
        values: list[str] = []
        for name in names:
            value = str(self.fields.get(name, "")).strip()
            if value:
                values.append(value)
        return values


@dataclass(frozen=True)
class SectionSpec:
    name: str
    rel_root: str
    config_path: tuple[str, ...]
    discover: Callable[[str, Path], list[CuratedItem]]


@dataclass(frozen=True)
class Decision:
    item: CuratedItem
    keep: bool
    reason: str


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def section_label(name: str) -> str:
    label = _SECTION_LABELS.get(name)
    if not label:
        return name
    return f"{label}（{name}）"


def load_mapping_yaml(path: Path, *, context: str) -> dict[str, Any]:
    if not path.is_file():
        fail(f"未找到{context}文件：{path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        fail(f"{context}文件必须是 YAML 映射结构：{path}")
    return data


def nested_mapping(data: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    current: Any = data
    for segment in path:
        if current is None:
            return {}
        if not isinstance(current, dict):
            fail(f"策略路径 {'/'.join(path)} 必须解析为映射结构")
        current = current.get(segment, {})
    if current is None:
        return {}
    if not isinstance(current, dict):
        fail(f"策略路径 {'/'.join(path)} 必须解析为映射结构")
    return current


def string_list(config: dict[str, Any], key: str) -> list[str]:
    raw = config.get(key, [])
    if raw is None:
        return []
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        fail(f"策略字段 '{key}' 必须是字符串列表")
    return [item.strip() for item in raw if item.strip()]


def match_fields_for(item: CuratedItem, config: dict[str, Any]) -> list[str]:
    configured = config.get("match_fields")
    if configured is None:
        return list(item.fields.keys())
    if not isinstance(configured, list) or any(not isinstance(field, str) for field in configured):
        fail(f"分组 {section_label(item.section)} 中的策略字段 'match_fields' 必须是字符串列表")
    return [field for field in configured if field in item.fields]


def matches_exact(values: list[str], targets: set[str]) -> bool:
    return any(value in targets for value in values)


def matches_contains_ci(values: list[str], tokens: list[str]) -> bool:
    lowered = [value.lower() for value in values]
    return any(token.lower() in value for token in tokens for value in lowered)


_SCOPE_EXACT_KEYS = ("managed_exact", "keep_exact", "drop_exact")
_SCOPE_CONTAINS_KEYS = ("managed_contains_ci", "keep_contains_ci", "drop_contains_ci")


def has_explicit_scope_rules(config: dict[str, Any]) -> bool:
    return any(key in config for key in (*_SCOPE_EXACT_KEYS, *_SCOPE_CONTAINS_KEYS))


def merged_string_list(config: dict[str, Any], keys: Iterable[str]) -> list[str]:
    values: list[str] = []
    for key in keys:
        values.extend(string_list(config, key))
    return values


def is_managed_item(config: dict[str, Any], values: list[str]) -> bool:
    if not has_explicit_scope_rules(config):
        return True

    scope_exact = set(merged_string_list(config, _SCOPE_EXACT_KEYS))
    scope_contains_ci = merged_string_list(config, _SCOPE_CONTAINS_KEYS)
    return matches_exact(values, scope_exact) or matches_contains_ci(values, scope_contains_ci)


def decide(item: CuratedItem, config: dict[str, Any]) -> Decision:
    strategy = str(config.get("strategy", "allowlist")).strip().lower()
    if strategy not in _VALID_STRATEGIES:
        fail(
            f"分组 {section_label(item.section)} 使用了无效策略 '{strategy}'。"
            f"可选值为 {sorted(_VALID_STRATEGIES)}"
        )

    values = item.values_for(match_fields_for(item, config))
    if not is_managed_item(config, values):
        return Decision(item=item, keep=True, reason="未命中受管范围，按自定义内容默认保留")

    keep_exact = set(string_list(config, "keep_exact"))
    drop_exact = set(string_list(config, "drop_exact"))
    keep_contains_ci = string_list(config, "keep_contains_ci")
    drop_contains_ci = string_list(config, "drop_contains_ci")

    if matches_exact(values, keep_exact) or matches_contains_ci(values, keep_contains_ci):
        return Decision(item=item, keep=True, reason="命中保留规则")
    if matches_exact(values, drop_exact) or matches_contains_ci(values, drop_contains_ci):
        return Decision(item=item, keep=False, reason="命中删除规则")

    if strategy == "blocklist":
        return Decision(item=item, keep=True, reason="未命中删除规则，按阻止名单策略默认保留")
    return Decision(item=item, keep=False, reason="未命中保留规则，按允许名单策略默认删除")


def warn_for_unmatched_exact_rules(_spec: SectionSpec, items: list[CuratedItem], config: dict[str, Any]) -> list[str]:
    wanted = (
        set(string_list(config, "managed_exact"))
        | set(string_list(config, "keep_exact"))
        | set(string_list(config, "drop_exact"))
    )
    if not wanted:
        return []

    available: set[str] = set()
    for item in items:
        available.update(item.values_for(match_fields_for(item, config)))

    return sorted(wanted - available)


def discover_named_dirs(section: str, root: Path) -> list[CuratedItem]:
    if not root.is_dir():
        return []
    items: list[CuratedItem] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name.lower()):
        if not child.is_dir() or child.name.startswith(".") or child.name == "__pycache__":
            continue
        items.append(
            CuratedItem(
                section=section,
                key=child.name,
                path=child,
                fields={
                    "name": child.name,
                    "directory_name": child.name,
                },
            )
        )
    return items


def discover_api_providers(section: str, root: Path) -> list[CuratedItem]:
    if not root.is_dir():
        return []
    items: list[CuratedItem] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        provider_name = ""
        service_id = ""
        provider_file = child / "_provider.yaml"
        if provider_file.is_file():
            data = load_mapping_yaml(provider_file, context="提供方配置")
            provider_name = str(data.get("name") or "").strip()
            service_id = str(data.get("service_id") or "").strip()
        items.append(
            CuratedItem(
                section=section,
                key=child.name,
                path=child,
                fields={
                    "directory_name": child.name,
                    "provider_name": provider_name,
                    "service_id": service_id,
                },
            )
        )
    return items


def discover_plugin_files(
    section: str,
    root: Path,
    *,
    suffixes: tuple[str, ...],
    declared_name_field: str | None = None,
) -> list[CuratedItem]:
    if not root.is_dir():
        return []

    items: list[CuratedItem] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name.lower()):
        if child.name.startswith(".") or child.name == "__pycache__":
            continue
        if child.is_dir():
            items.append(
                CuratedItem(
                    section=section,
                    key=child.name,
                    path=child,
                    fields={
                        "directory_name": child.name,
                    },
                )
            )
            continue
        if child.suffix.lower() not in suffixes:
            continue

        declared_name = ""
        if declared_name_field and child.suffix.lower() in {".yaml", ".yml"}:
            data = load_mapping_yaml(child, context="工具配置")
            declared_name = str(data.get(declared_name_field) or "").strip()

        items.append(
            CuratedItem(
                section=section,
                key=child.stem,
                path=child,
                fields={
                    "stem": child.stem,
                    "filename": child.name,
                    "declared_name": declared_name,
                },
            )
        )
    return items


SECTION_SPECS = [
    SectionSpec(
        name="agents",
        rel_root=".smartclaw/plugins/agents",
        config_path=("agents",),
        discover=discover_named_dirs,
    ),
    SectionSpec(
        name="workflows",
        rel_root=".smartclaw/plugins/workflows",
        config_path=("workflows",),
        discover=discover_named_dirs,
    ),
    SectionSpec(
        name="skills",
        rel_root=".smartclaw/plugins/skills",
        config_path=("skills",),
        discover=discover_named_dirs,
    ),
    SectionSpec(
        name="tools.api_providers",
        rel_root=".smartclaw/plugins/tools/api",
        config_path=("tools", "api_providers"),
        discover=discover_api_providers,
    ),
    SectionSpec(
        name="tools.python_tools",
        rel_root=".smartclaw/plugins/tools/python",
        config_path=("tools", "python_tools"),
        discover=lambda section, root: discover_plugin_files(section, root, suffixes=(".py",)),
    ),
    SectionSpec(
        name="tools.mcp_tools",
        rel_root=".smartclaw/plugins/tools/mcp",
        config_path=("tools", "mcp_tools"),
        discover=lambda section, root: discover_plugin_files(
            section,
            root,
            suffixes=(".yaml", ".yml"),
            declared_name_field="name",
        ),
    ),
    SectionSpec(
        name="tools.generated_tools",
        rel_root=".smartclaw/plugins/tools/generated",
        config_path=("tools", "generated_tools"),
        discover=lambda section, root: discover_plugin_files(
            section,
            root,
            suffixes=(".py", ".yaml", ".yml", ".json"),
            declared_name_field="name",
        ),
    ),
]


def delete_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def print_section_report(
    spec: SectionSpec,
    decisions: list[Decision],
    root: Path,
    repo_root: Path,
    *,
    apply: bool,
) -> None:
    kept = [decision for decision in decisions if decision.keep]
    removed = [decision for decision in decisions if not decision.keep]
    mode = "实际执行" if apply else "预演"
    print(
        f"[{mode}] {section_label(spec.name)}：保留 {len(kept)} 项，"
        f"删除 {len(removed)} 项，目录 {root.relative_to(repo_root).as_posix()}"
    )
    if not decisions:
        print("  未发现可处理项。")
        print()
        return
    for decision in removed:
        rel_path = decision.item.path.relative_to(repo_root).as_posix()
        print(f"  删除 {decision.item.key:<32} {rel_path}（{decision.reason}）")
    for decision in kept:
        rel_path = decision.item.path.relative_to(repo_root).as_posix()
        print(f"  保留 {decision.item.key:<32} {rel_path}（{decision.reason}）")
    print()


def curate(root: Path, policy: dict[str, Any], *, apply: bool) -> int:
    warnings: list[str] = []
    removals: list[Path] = []

    for spec in SECTION_SPECS:
        target_root = root / spec.rel_root
        section_config = nested_mapping(policy, spec.config_path)
        items = spec.discover(spec.name, target_root)
        warnings.extend(
            f"{section_label(spec.name)}：精确匹配规则未命中任何条目：{name}"
            for name in warn_for_unmatched_exact_rules(spec, items, section_config)
        )
        decisions = [decide(item, section_config) for item in items]
        print_section_report(spec, decisions, target_root, root, apply=apply)
        removals.extend(decision.item.path for decision in decisions if not decision.keep)

    if warnings:
        print("\n警告信息：")
        for warning in warnings:
            print(f"- {warning}")

    if apply:
        if removals:
            print(f"\n开始删除 {len(removals)} 项内容。")
        else:
            print("\n没有需要删除的内容。")
        for path in removals:
            delete_path(path)
        print(f"\n已完成删除，共处理 {len(removals)} 项。")
    else:
        print(f"\n本次仅为预演，计划删除 {len(removals)} 项。若要实际删除，请重新运行并加上 --apply。")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="仓库根目录（默认：当前仓库根目录）")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="策略 YAML 文件路径（默认：脚本目录下的 builtin-curation.yaml）",
    )
    parser.add_argument("--apply", action="store_true", help="实际删除未匹配的内建插件")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    config_path = args.config.resolve()
    print("开始整理 smartClaw 内建插件。")
    print(f"仓库根目录：{root}")
    print(f"策略文件：{config_path}")
    print(f"执行模式：{'实际删除' if args.apply else '预演（不会删除文件）'}")
    policy = load_mapping_yaml(config_path, context="策略")
    print("策略文件加载完成，开始扫描各分组内容。\n")
    return curate(root, policy, apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
