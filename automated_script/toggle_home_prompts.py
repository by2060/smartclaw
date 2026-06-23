#!/usr/bin/env python3
"""通过补丁前端源码来切换 WebUI 首页提示抑制状态。"""

from __future__ import annotations

import argparse
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAYOUT_FILE = ROOT / "webui" / "src" / "components" / "layout" / "Layout.tsx"
HOME_FILE = ROOT / "webui" / "src" / "pages" / "Home" / "index.tsx"
STATE_FILES = (
    ROOT / "scripts" / ".toggle_home_prompts_state.json",
    ROOT / "scripts" / ".toggle_home_prompts_source_state.json",
)
PATCH_MARKER = "const homePromptsDisabled = true;"
STATUS_DISPLAY = {
    "disabled": "已禁用",
    "enabled": "已启用",
    "partially patched": "部分已处理",
}

LAYOUT_DISABLE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        "  const isHome = location.pathname === '/';\n  const [showOnboarding, setShowOnboarding] = useState(false);",
        "  const isHome = location.pathname === '/';\n  const homePromptsDisabled = true;\n  const [showOnboarding, setShowOnboarding] = useState(false);",
    ),
    (
        "      {showOnboarding && (",
        "      {!homePromptsDisabled && showOnboarding && (",
    ),
    (
        "      {showUpdate && (",
        "      {!homePromptsDisabled && showUpdate && (",
    ),
    (
        "      {visibleNotifications.length > 0 && (",
        "      {!homePromptsDisabled && visibleNotifications.length > 0 && (",
    ),
    (
        "        { name: t('tools'), href: '/tools', icon: Wrench },\n        { name: t('hub'), href: '/hub', icon: Archive },\n        { name: t('models'), href: '/models', icon: Brain },",
        "        { name: t('tools'), href: '/tools', icon: Wrench },\n        ...(!homePromptsDisabled ? [{ name: t('hub'), href: '/hub', icon: Archive }] : []),\n        { name: t('models'), href: '/models', icon: Brain },",
    ),
    (
        "            {!collapsed && (\n"
        "              <>\n"
        "                {hasUpdate ? (\n"
        "                  <button\n"
        "                    onClick={() => setShowUpdate(true)}\n"
        "                    className=\"mt-3 w-full rounded-xl border border-amber-200 bg-gradient-to-r from-amber-50 via-orange-50 to-rose-50 px-3 py-2 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md\"\n"
        "                  >\n"
        "                    <div className=\"flex items-center gap-2 text-sm\">\n"
        "                      <span className=\"min-w-0 flex-1 truncate font-semibold text-amber-900\">\n"
        "                        {t('newVersion')} {latestVersion ? `v${latestVersion}` : ''}\n"
        "                      </span>\n"
        "                      <span className=\"inline-flex flex-shrink-0 items-center rounded-full bg-amber-500 px-2 py-0.5 text-xs font-semibold text-white shadow-sm\">\n"
        "                        {t('updateNow')}\n"
        "                      </span>\n"
        "                    </div>\n"
        "                    <div className=\"mt-1 text-xs text-amber-700\">\n"
        "                      {currentVersion\n"
        "                        ? t('currentVersionLabel', { version: currentVersion })\n"
        "                        : 'Flocks'}\n"
        "                    </div>\n"
        "                    <div className=\"mt-0.5 text-xs font-medium text-amber-900\">\n"
        "                      AI Native SecOps Platform\n"
        "                    </div>\n"
        "                  </button>\n"
        "                ) : (\n"
        "                  <button\n"
        "                    onClick={() => setShowUpdate(true)}\n"
        "                    className=\"w-full text-left mt-3 group rounded-lg px-1 py-1 hover:bg-gray-50 transition-colors\"\n"
        "                  >\n"
        "                    <div className=\"flex items-center gap-1.5\">\n"
        "                      <span className=\"text-xs font-medium text-gray-500 group-hover:text-gray-700 transition-colors\">\n"
        "                        Flocks {currentVersion ? `v${currentVersion}` : '...'}\n"
        "                      </span>\n"
        "                    </div>\n"
        "                    <div className=\"mt-0.5 text-xs text-gray-400\">AI Native SecOps Platform</div>\n"
        "                  </button>\n"
        "                )}\n"
        "              </>\n"
        "            )}",
        "            {!collapsed && (\n"
        "              <>\n"
        "                {homePromptsDisabled ? (\n"
        "                  <div className=\"w-full mt-3 rounded-lg px-1 py-1\">\n"
        "                    <div className=\"flex items-center gap-1.5\">\n"
        "                      <span className=\"text-xs font-medium text-gray-500\">\n"
        "                        {currentVersion\n"
        "                          ? t('currentVersionLabel', { version: currentVersion })\n"
        "                          : 'Flocks'}\n"
        "                      </span>\n"
        "                    </div>\n"
        "                    <div className=\"mt-0.5 text-xs text-gray-400\">AI Native SecOps Platform</div>\n"
        "                  </div>\n"
        "                ) : hasUpdate ? (\n"
        "                  <button\n"
        "                    onClick={() => setShowUpdate(true)}\n"
        "                    className=\"mt-3 w-full rounded-xl border border-amber-200 bg-gradient-to-r from-amber-50 via-orange-50 to-rose-50 px-3 py-2 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md\"\n"
        "                  >\n"
        "                    <div className=\"flex items-center gap-2 text-sm\">\n"
        "                      <span className=\"min-w-0 flex-1 truncate font-semibold text-amber-900\">\n"
        "                        {t('newVersion')} {latestVersion ? `v${latestVersion}` : ''}\n"
        "                      </span>\n"
        "                      <span className=\"inline-flex flex-shrink-0 items-center rounded-full bg-amber-500 px-2 py-0.5 text-xs font-semibold text-white shadow-sm\">\n"
        "                        {t('updateNow')}\n"
        "                      </span>\n"
        "                    </div>\n"
        "                    <div className=\"mt-1 text-xs text-amber-700\">\n"
        "                      {currentVersion\n"
        "                        ? t('currentVersionLabel', { version: currentVersion })\n"
        "                        : 'Flocks'}\n"
        "                    </div>\n"
        "                    <div className=\"mt-0.5 text-xs font-medium text-amber-900\">\n"
        "                      AI Native SecOps Platform\n"
        "                    </div>\n"
        "                  </button>\n"
        "                ) : (\n"
        "                  <button\n"
        "                    onClick={() => setShowUpdate(true)}\n"
        "                    className=\"w-full text-left mt-3 group rounded-lg px-1 py-1 hover:bg-gray-50 transition-colors\"\n"
        "                  >\n"
        "                    <div className=\"flex items-center gap-1.5\">\n"
        "                      <span className=\"text-xs font-medium text-gray-500 group-hover:text-gray-700 transition-colors\">\n"
        "                        Flocks {currentVersion ? `v${currentVersion}` : '...'}\n"
        "                      </span>\n"
        "                    </div>\n"
        "                    <div className=\"mt-0.5 text-xs text-gray-400\">AI Native SecOps Platform</div>\n"
        "                  </button>\n"
        "                )}\n"
        "              </>\n"
        "            )}",
    ),
    (
        "            {collapsed && (\n"
        "              <button\n"
        "                onClick={() => setShowUpdate(true)}\n"
        "                title={hasUpdate ? t('hasNewVersion', { version: latestVersion ? `v${latestVersion}` : '' }) : t('versionInfo')}\n"
        "                className={`relative rounded-xl p-2 transition-colors ${\n"
        "                  hasUpdate\n"
        "                    ? 'bg-amber-50 text-amber-600 hover:bg-amber-100'\n"
        "                    : 'text-gray-400 hover:text-gray-600 hover:bg-gray-100'\n"
        "                }`}\n"
        "              >\n"
        "                {hasUpdate ? <ArrowUpCircle className=\"w-4 h-4\" /> : <Sparkles className=\"w-4 h-4\" />}\n"
        "                {hasUpdate && (\n"
        "                  <>\n"
        "                    <span className=\"absolute inset-0 rounded-xl border border-amber-200 animate-pulse\" />\n"
        "                    <span className=\"absolute top-1 right-1 w-2 h-2 bg-amber-400 rounded-full\" />\n"
        "                  </>\n"
        "                )}\n"
        "              </button>\n"
        "            )}",
        "            {collapsed && (\n"
        "              homePromptsDisabled ? (\n"
        "                <div\n"
        "                  title={t('versionInfo')}\n"
        "                  className=\"rounded-xl p-2 text-gray-400\"\n"
        "                >\n"
        "                  <Sparkles className=\"w-4 h-4\" />\n"
        "                </div>\n"
        "              ) : (\n"
        "                <button\n"
        "                  onClick={() => setShowUpdate(true)}\n"
        "                  title={hasUpdate ? t('hasNewVersion', { version: latestVersion ? `v${latestVersion}` : '' }) : t('versionInfo')}\n"
        "                  className={`relative rounded-xl p-2 transition-colors ${\n"
        "                    hasUpdate\n"
        "                      ? 'bg-amber-50 text-amber-600 hover:bg-amber-100'\n"
        "                      : 'text-gray-400 hover:text-gray-600 hover:bg-gray-100'\n"
        "                  }`}\n"
        "                >\n"
        "                  {hasUpdate ? <ArrowUpCircle className=\"w-4 h-4\" /> : <Sparkles className=\"w-4 h-4\" />}\n"
        "                  {hasUpdate && (\n"
        "                    <>\n"
        "                      <span className=\"absolute inset-0 rounded-xl border border-amber-200 animate-pulse\" />\n"
        "                      <span className=\"absolute top-1 right-1 w-2 h-2 bg-amber-400 rounded-full\" />\n"
        "                    </>\n"
        "                  )}\n"
        "                </button>\n"
        "              )\n"
        "            )}",
    ),
)

LEGACY_DISABLE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        "            {!homePromptsDisabled && !collapsed && (",
        "            {!collapsed && (",
    ),
    (
        "            {collapsed && !homePromptsDisabled && (",
        "            {collapsed && (",
    ),
    (
        "                {!collapsed && !homePromptsDisabled && (",
        "                {!collapsed && (",
    ),
)

HOME_DISABLE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        "  const { t } = useTranslation('home');\n  const [isRepoMenuOpen, setIsRepoMenuOpen] = useState(false);",
        "  const { t } = useTranslation('home');\n  const homePromptsDisabled = true;\n  const [isRepoMenuOpen, setIsRepoMenuOpen] = useState(false);",
    ),
    (
        "          <div className=\"flex flex-wrap sm:flex-nowrap gap-3 sm:flex-shrink-0\">\n"
        "            <button\n"
        "              onClick={() => window.dispatchEvent(new Event('flocks:open-onboarding'))}\n"
        "              className=\"inline-flex items-center px-6 py-2.5 bg-red-600 text-white rounded-lg font-semibold hover:bg-red-500 transition-colors shadow-lg shadow-red-900/40\"\n"
        "            >\n"
        "              {t('getStarted')}\n"
        "              <ChevronRight className=\"ml-1.5 w-4 h-4\" />\n"
        "            </button>\n"
        "\n"
        "            <div className=\"relative\">\n"
        "              <button\n"
        "                type=\"button\"\n"
        "                onClick={() => setIsRepoMenuOpen((open) => !open)}\n"
        "                className=\"inline-flex items-center px-6 py-2.5 bg-white/10 text-slate-200 rounded-lg font-semibold hover:bg-white/15 transition-colors border border-white/10\"\n"
        "              >\n"
        "                <Github className=\"mr-2 w-4 h-4\" />\n"
        "                {t('openSource')}\n"
        "                <ChevronDown className=\"ml-2 w-4 h-4\" />\n"
        "              </button>\n"
        "\n"
        "              {isRepoMenuOpen ? (\n"
        "                <div className=\"absolute right-0 mt-2 min-w-52 overflow-hidden rounded-lg border border-white/10 bg-slate-900/95 shadow-xl backdrop-blur\">\n"
        "                  <a\n"
        "                    href={GITHUB_URL}\n"
        "                    target=\"_blank\"\n"
        "                    rel=\"noopener noreferrer\"\n"
        "                    className=\"flex items-center px-4 py-3 text-sm text-slate-200 hover:bg-white/10 transition-colors\"\n"
        "                  >\n"
        "                    <Github className=\"mr-2 w-4 h-4\" />\n"
        "                    GitHub\n"
        "                  </a>\n"
        "                  <a\n"
        "                    href={GITEE_URL}\n"
        "                    target=\"_blank\"\n"
        "                    rel=\"noopener noreferrer\"\n"
        "                    className=\"flex items-center px-4 py-3 text-sm text-slate-200 hover:bg-white/10 transition-colors border-t border-white/10\"\n"
        "                  >\n"
        "                    <img src={GITEE_LOGO_URL} alt=\"Gitee\" className=\"mr-2 w-4 h-4 rounded-sm\" />\n"
        "                    Gitee\n"
        "                  </a>\n"
        "                </div>\n"
        "              ) : null}\n"
        "            </div>\n"
        "          </div>",
        "          {!homePromptsDisabled && (\n"
        "            <div className=\"flex flex-wrap sm:flex-nowrap gap-3 sm:flex-shrink-0\">\n"
        "              <button\n"
        "                onClick={() => window.dispatchEvent(new Event('flocks:open-onboarding'))}\n"
        "                className=\"inline-flex items-center px-6 py-2.5 bg-red-600 text-white rounded-lg font-semibold hover:bg-red-500 transition-colors shadow-lg shadow-red-900/40\"\n"
        "              >\n"
        "                {t('getStarted')}\n"
        "                <ChevronRight className=\"ml-1.5 w-4 h-4\" />\n"
        "              </button>\n"
        "\n"
        "              <div className=\"relative\">\n"
        "                <button\n"
        "                  type=\"button\"\n"
        "                  onClick={() => setIsRepoMenuOpen((open) => !open)}\n"
        "                  className=\"inline-flex items-center px-6 py-2.5 bg-white/10 text-slate-200 rounded-lg font-semibold hover:bg-white/15 transition-colors border border-white/10\"\n"
        "                >\n"
        "                  <Github className=\"mr-2 w-4 h-4\" />\n"
        "                  {t('openSource')}\n"
        "                  <ChevronDown className=\"ml-2 w-4 h-4\" />\n"
        "                </button>\n"
        "\n"
        "                {isRepoMenuOpen ? (\n"
        "                  <div className=\"absolute right-0 mt-2 min-w-52 overflow-hidden rounded-lg border border-white/10 bg-slate-900/95 shadow-xl backdrop-blur\">\n"
        "                    <a\n"
        "                      href={GITHUB_URL}\n"
        "                      target=\"_blank\"\n"
        "                      rel=\"noopener noreferrer\"\n"
        "                      className=\"flex items-center px-4 py-3 text-sm text-slate-200 hover:bg-white/10 transition-colors\"\n"
        "                    >\n"
        "                      <Github className=\"mr-2 w-4 h-4\" />\n"
        "                      GitHub\n"
        "                    </a>\n"
        "                    <a\n"
        "                      href={GITEE_URL}\n"
        "                      target=\"_blank\"\n"
        "                      rel=\"noopener noreferrer\"\n"
        "                      className=\"flex items-center px-4 py-3 text-sm text-slate-200 hover:bg-white/10 transition-colors border-t border-white/10\"\n"
        "                    >\n"
        "                      <img src={GITEE_LOGO_URL} alt=\"Gitee\" className=\"mr-2 w-4 h-4 rounded-sm\" />\n"
        "                      Gitee\n"
        "                    </a>\n"
        "                  </div>\n"
        "                ) : null}\n"
        "              </div>\n"
        "            </div>\n"
        "          )}",
    ),
)


@dataclass(frozen=True)
class PatchTarget:
    path: Path
    patch_marker: str
    disable_replacements: tuple[tuple[str, str], ...]
    legacy_disable_replacements: tuple[tuple[str, str], ...] = ()


PATCH_TARGETS = (
    PatchTarget(
        path=LAYOUT_FILE,
        patch_marker=PATCH_MARKER,
        disable_replacements=LAYOUT_DISABLE_REPLACEMENTS,
        legacy_disable_replacements=LEGACY_DISABLE_REPLACEMENTS,
    ),
    PatchTarget(
        path=HOME_FILE,
        patch_marker=PATCH_MARKER,
        disable_replacements=HOME_DISABLE_REPLACEMENTS,
    ),
)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".toggle_home_prompts_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def existing_state_files() -> list[Path]:
    return [path for path in STATE_FILES if path.exists()]


def cleanup_state_files() -> list[Path]:
    removed: list[Path] = []
    for path in STATE_FILES:
        try:
            path.unlink()
            removed.append(path)
        except FileNotFoundError:
            pass
    return removed


def normalize_legacy_patch(content: str, replacements: tuple[tuple[str, str], ...]) -> str:
    updated = content
    for old, new in replacements:
        if old in updated:
            updated = updated.replace(old, new, 1)
    return updated


def normalize_content(target: PatchTarget, content: str) -> str:
    return normalize_legacy_patch(content, target.legacy_disable_replacements)


def expected_disabled_snippets(target: PatchTarget) -> tuple[str, ...]:
    return tuple(new for _, new in target.disable_replacements)


def enable_replacements(target: PatchTarget) -> tuple[tuple[str, str], ...]:
    return tuple((new, old) for old, new in target.disable_replacements)


def is_disabled(target: PatchTarget, content: str) -> bool:
    normalized = normalize_content(target, content)
    return all(snippet in normalized for snippet in expected_disabled_snippets(target))


def is_partially_disabled(target: PatchTarget, content: str) -> bool:
    normalized = normalize_content(target, content)
    if all(snippet in normalized for snippet in expected_disabled_snippets(target)):
        return False
    matched_new_snippets = sum(1 for _, new in target.disable_replacements if new in normalized)
    return (
        target.patch_marker in normalized
        or 0 < matched_new_snippets < len(target.disable_replacements)
        or any(old in content for old, _ in target.legacy_disable_replacements)
    )


def transform(content: str, replacements: tuple[tuple[str, str], ...], target_path: Path) -> str:
    updated = content
    for old, new in replacements:
        if old in updated:
            updated = updated.replace(old, new, 1)
            continue
        if new in updated:
            continue
        raise ValueError(f"更新 {target_path.relative_to(ROOT).as_posix()} 时未找到预期代码片段: {old!r}")
    return updated


def format_paths(paths: list[Path]) -> str:
    return ", ".join(path.relative_to(ROOT).as_posix() for path in paths)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--disable", action="store_true", help="通过补丁前端源码来屏蔽首页相关提示。")
    group.add_argument("--enable", action="store_true", help="通过回滚补丁恢复前端源码。")
    group.add_argument("--status", action="store_true", help="输出当前补丁状态。")
    return parser.parse_args()


def print_status() -> int:
    statuses: list[str] = []
    state_files = existing_state_files()
    print("正在检查首页提示补丁状态...")
    print("目标文件状态：")
    for target in PATCH_TARGETS:
        content = read_text(target.path)
        if is_disabled(target, content):
            patch_status = "disabled"
        elif is_partially_disabled(target, content):
            patch_status = "partially patched"
        else:
            patch_status = "enabled"
        statuses.append(patch_status)
        print(f"- {target.path.relative_to(ROOT).as_posix()}: {STATUS_DISPLAY.get(patch_status, patch_status)}")

    if all(status == "disabled" for status in statuses):
        overall_status = "disabled"
    elif all(status == "enabled" for status in statuses):
        overall_status = "enabled"
    else:
        overall_status = "partially patched"

    print(f"首页提示补丁状态：{STATUS_DISPLAY.get(overall_status, overall_status)}")
    print(f"额外状态文件：{format_paths(state_files) if state_files else '无'}")
    print("影响范围：首页引导弹窗、更新弹窗、通知弹窗、侧边栏更新入口、Flocks Hub 侧边栏菜单，以及首页引导/开源入口。")
    return 0


def disable_prompts() -> int:
    print("开始执行禁用操作：首页提示相关弹窗与入口将被关闭。")
    removed_state_files = cleanup_state_files()
    patched_files: list[Path] = []
    refreshed_files: list[Path] = []

    for target in PATCH_TARGETS:
        relative_path = target.path.relative_to(ROOT).as_posix()
        print(f"正在处理 {relative_path} ...")
        content = read_text(target.path)
        normalized = normalize_content(target, content)

        if is_disabled(target, content):
            if normalized != content:
                write_text(target.path, normalized)
                refreshed_files.append(target.path)
                print(f"  已刷新 {relative_path} 中的旧版补丁痕迹。")
            else:
                print(f"  {relative_path} 已处于禁用状态，无需修改。")
            continue

        patched = transform(normalized, target.disable_replacements, target.path)
        write_text(target.path, patched)
        patched_files.append(target.path)
        print(f"  已写入禁用补丁到 {relative_path}。")

    if patched_files:
        print(f"已完成补丁写入：{format_paths(patched_files)}，首页提示相关弹窗与入口现已禁用。")
    elif not refreshed_files:
        print(f"当前源码已处于禁用状态，无需再次处理：{format_paths([target.path for target in PATCH_TARGETS])}。")

    if refreshed_files:
        print(f"已刷新文件：{format_paths(refreshed_files)}，移除了旧版异常补丁并保留当前禁用状态。")
    if removed_state_files:
        print(f"已清理额外状态文件：{format_paths(removed_state_files)}。")
    if patched_files or refreshed_files:
        print("如需将该行为同步到其他机器，请重新构建 WebUI，并基于当前源码树重新部署。")
    return 0


def enable_prompts() -> int:
    print("开始执行恢复操作：首页提示相关弹窗与入口将重新启用。")
    removed_state_files = cleanup_state_files()
    restored_files: list[Path] = []

    for target in PATCH_TARGETS:
        relative_path = target.path.relative_to(ROOT).as_posix()
        print(f"正在处理 {relative_path} ...")
        content = read_text(target.path)
        normalized = normalize_content(target, content)

        if not is_disabled(target, content) and not is_partially_disabled(target, content):
            print(f"  {relative_path} 当前已是启用状态，无需修改。")
            continue

        restored = transform(normalized, enable_replacements(target), target.path)
        write_text(target.path, restored)
        restored_files.append(target.path)
        print(f"  已恢复 {relative_path} 中的原始显示逻辑。")

    if restored_files:
        print(f"已恢复文件：{format_paths(restored_files)}，首页提示相关弹窗与入口已重新启用。")
    else:
        print(f"当前源码已是启用状态，无需恢复：{format_paths([target.path for target in PATCH_TARGETS])}。")
    if removed_state_files:
        print(f"已清理额外状态文件：{format_paths(removed_state_files)}。")
    return 0


def main() -> int:
    args = parse_args()
    if args.status:
        print("收到状态检查请求。")
        return print_status()
    if args.enable:
        print("收到恢复请求。")
        return enable_prompts()
    print("未指定 --status 或 --enable，默认执行禁用操作。")
    return disable_prompts()


if __name__ == "__main__":
    raise SystemExit(main())
