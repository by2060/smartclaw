#!/usr/bin/env python3
"""
构建通用 Python 交付产物。

功能概述：
1. 扫描整个项目，排除开发缓存（如 .git, .venv）与旧构建产物。
2. 筛选出待编译的 Python 源文件（根据白名单保留部分源码）。
3. 采用 Cython 引擎将指定的 .py 代码编译为二进制共享对象（Linux: .so, Windows: .pyd）。
4. 将非编译资源文件复制到 delivery_build 目录中。
5. 自动检索并在文本配置文件中将旧的 '.py' 路径引用替换为对应的二进制文件名称（如 main.py -> main.pyd）。
6. 支持“预览模式”（不带 --apply）与“执行模式”（带 --apply）。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import sysconfig
from dataclasses import dataclass, field
from pathlib import Path

# 确保控制台输出在不同操作系统下都能正确处理 UTF-8 编码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ==============================================================================
# 核心配置区域（日常维护/复用到其他项目时，只需修改此区域）
# ==============================================================================

# 定位项目根目录（假设本脚本位于项目根目录的某个子文件夹中，如 automated_script/）
ROOT = Path(__file__).resolve().parents[1]

# 编译交付产物的目标输出目录
OUTPUT_DIR = ROOT / "delivery_build"

# 需要在打包期临时无感注入“Pydantic 兼容补丁”的项目入口文件相对路径。
# 如果复用到其他不使用 Pydantic 的项目，或者不需要补丁，请直接将其设置为 None。
ENTRYPOINT_INJECTION_FILE: str | None = "smartclaw/__init__.py"

# 绝对不扫描和打包的顶层文件夹（相对于项目根目录）---> 绝对不参与打包，不需要交给客户的文件夹或文件
EXCLUDED_TOP_LEVEL_PATHS: frozenset[str] = frozenset({
    "automated_script",  # 脚本自身所在的工具链目录
    "delivery_build",    # 构建输出目录本身
})

# 全局忽略的目录名称与文件后缀（防止把旧的编译缓存也打包进去）
# 无论在项目的哪一层目录，只要碰到了名字叫 .git（Git版本库）、.venv（虚拟环境缓存）、__pycache__（Python运行缓存）的文件夹，或者碰到了 .pyc 格式的文件，一律直接丢弃，不放入交付产物中。
SKIPPED_DIR_NAMES: frozenset[str] = frozenset({
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    ".vscode",
    "__pycache__",
    "node_modules",
})

# 全局忽略的文件后缀（过滤 Python 缓存以及各种 C 编译器产生的中间体文件）
SKIPPED_FILE_SUFFIXES: frozenset[str] = frozenset({
    ".pyc",
    ".pyo",
    ".c",    # 忽略 Cython 转换产生的中间 C 源码文件
    ".o",    # 忽略 GCC 产生的目标对象文件
    ".obj",  # 忽略 MSVC 产生的目标对象文件
    ".so",   # 忽略本地可能残留的旧二进制文件
    ".pyd",  # 忽略本地可能残留的旧二进制文件
})

# 需要【保留源码】而不进行 Cython 编译的特定 Python 文件与文件的通配符匹配规则（项目相对路径）
# 默认情况下，会把所有 .py 文件都编译成看不懂的二进制（.so 或 .pyd）以保护源码。
# 有些特殊的 Python 文件如果变成二进制，程序就可能无法运行，这两个配置就是**“白名单”**，写在这里的 Python 文件将不被编译，而是直接原样复制源码。
# 元组，注意后面带有逗号
KEEP_SOURCE_PYTHON_FILES: tuple[str, ...] = (
    # "scripts/example.py",  # 这里是元组，每一行末尾都保留一个英文逗号 
    "smartclaw/browser/admin.py",           # 含有 Cython 不支持的 del 参数操作 (del force)
    "smartclaw/command/command_loader.py",  # 含有未定义变量 Bug (smartclaw_global_dir)
    "scripts/serve_webui.py",               # 前端启动入口脚本，必须以 .py 形式执行，不能编译为 .so
)

# 需要【保留源码】而不进行 Cython 编译的 Python 文件的通配符匹配规则
# 注意：由于我们在底层注入了全局 Pydantic 兼容性补丁，除必要的静态配置外，其他业务模块（如 auth, session, memory, tool, provider）现在可以全部参与安全编译。
# 元组，注意后面带有逗号
KEEP_SOURCE_PYTHON_GLOBS: tuple[str, ...] = (
    # "scripts/runtime/*.py",  # # 这里是元组，每一行末尾都保留一个英文逗号 
    ".smartclaw/plugins/**/*.py",           # 插件/工具目录。必须保持原生的 .py 格式，否则动态加载引擎无法扫描到，** 是一种通配符写法，代表递归匹配 plugins 文件夹下的所有 .py 文件。
)

# 决定下面“哪些配置文件里面的 xxx.py 需要自动改成 xxx.so / xxx.pyd”
# 把 .py 编译成了二进制（例如 main.py 变成了 main.pyd），那以前在启动脚本、Docker 配置里写的 python main.py 命令就会失效（因为 main.py 已经不存在了）
# 这个配置代表：只要遇到这些后缀的文件，就进去把里面所有的 xxx.py 字样，自动替换成编译后的二进制文件名 xxx.pyd 或 xxx.so。防止启动或引用的.py文件报错
PATCHABLE_TEXT_SUFFIXES: frozenset[str] = frozenset({
    ".sh",
    ".bash",
    ".zsh",
    ".command",
    ".bat",
    ".cmd",
    ".ps1",
    ".psm1",
    ".service",
    ".conf",
    ".cfg",
    ".ini",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".env",
    ".properties",
})

# 无后缀但同样需要检索并执行路径引用替换的特殊配置文件名称（同上）
PATCHABLE_TEXT_NAMES: frozenset[str] = frozenset({
    "Dockerfile",
})

# 动态获取当前平台对应的编译文件后缀（例如 Linux/macOS 为 '.cpython-310-x86_64-linux-gnu.so'，Windows 为 '.pyd'）
EXT_SUFFIX: str = sysconfig.get_config_var("EXT_SUFFIX") or ".so"

# ==============================================================================
# Pydantic v2 + Cython 全局兼容性补丁定义（在编译期自动且临时地注入到入口文件头部）
# 通过 Monkey Patch（猴子补丁） 动态修改了 Pydantic v2 内部的类构造行为。
# 核心原理：在 Pydantic 扫描类属性之前，拦截类的命名空间，将所有未标注类型的可调用对象（包括编译后的 cyfunction）动态标注为 ClassVar。这样 Pydantic 就会将其视作类变量而不再当成模型字段，从而避免报错。
# ==============================================================================
PYDANTIC_PATCH_CODE = """# ==============================================================================
# Pydantic v2 + Cython 全局兼容性补丁 (打包脚本自动注入，确保 C 函数不会被 Pydantic 误识为普通字段)
# ==============================================================================
try:
    from typing import ClassVar
    # 导入 Pydantic v2 内部负责构建模型及检查类命名空间的底层模块
    import pydantic._internal._model_construction as mc

    # 1. 备份 Pydantic 原始的命名空间检查函数，以便后续调用
    _original_inspect_namespace = mc.inspect_namespace

    # 2. 定义补丁函数，用于在类构建时拦截并修正属性标注
    def _patched_inspect_namespace(namespace: dict, *args, **kwargs):
        # 确保类中存在类型标注字典（__annotations__），若不存在则初始化一个空字典
        if "__annotations__" not in namespace:
            namespace["__annotations__"] = {}
        annotations = namespace["__annotations__"]

        # 遍历该类定义中的所有属性和方法（命名空间中的键值对）
        for key, value in namespace.items():
            # 排除 Python 内置的魔术方法与系统属性（如 __init__, __module__ 等）
            if key.startswith("__") and key.endswith("__"):
                continue
            
            # 关键逻辑：
            # 如果属性是可调用的（callable，包括普通函数、类方法以及 Cython 编译后的 cyfunction），
            # 并且该属性在类中没有被手动标注类型（不在 annotations 中）
            if callable(value) and key not in annotations:
                # 动态将其标注为 ClassVar。
                # Pydantic 看到 ClassVar 标注后，会将其识别为普通的类属性或方法，而不会误判定为“未标注类型的 Model Field”
                annotations[key] = ClassVar

        # 3. 将修改后的命名空间传递给 Pydantic 原始的检查函数，继续后续的模型构建流程
        return _original_inspect_namespace(namespace, *args, **kwargs)

    # 4. 用补丁函数替换掉 Pydantic 内部原有的 inspect_namespace 函数
    mc.inspect_namespace = _patched_inspect_namespace
except Exception:
    pass
# ==============================================================================
"""


# ==============================================================================
# 数据结构与辅助函数
# ==============================================================================

@dataclass(slots=True)
class BuildStats:
    """用于统计构建过程中各类操作的文件数量。"""
    compiled: int = 0          # 已编译的 Python 文件数
    copied: int = 0            # 已复制的非编译文件数
    patched: int = 0           # 已修补引用的配置文件数
    skipped: int = 0           # 被过滤跳过的项目数
    skipped_items: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class BuildPlan:
    """保存构建前扫描出的文件分类计划。"""
    compiled_python_files: tuple[Path, ...]     # 待 Cython 编译的文件
    kept_source_python_files: tuple[Path, ...]  # 显式保留源码的 Python 文件
    copied_files: tuple[Path, ...]              # 需要原样复制的所有文件
    compiled_path_map: dict[str, str]           # 编译前后路径的映射关系（用于修补引用）


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="构建通用 Python 交付产物。")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正生成编译产物目录；如果不传，则仅以 'Dry-run' 模式预览行为。",
    )
    return parser.parse_args()


def normalize_relative_path(path_text: str) -> str:
    """标准化用户配置的相对路径，去除斜杠差异和潜在的安全路径回溯。"""
    normalized = Path(path_text).as_posix().strip("/")
    if not normalized or normalized == "." or normalized.startswith("../") or "/../" in normalized:
        raise ValueError(f"不支持的仓库相对路径：{path_text!r}")
    return normalized


def build_keep_source_matchers() -> tuple[frozenset[str], tuple[str, ...]]:
    """初始化并缓存保留源码的白名单规则。"""
    exact_paths = frozenset(normalize_relative_path(path_text) for path_text in KEEP_SOURCE_PYTHON_FILES)
    glob_patterns = tuple(normalize_relative_path(path_text) for path_text in KEEP_SOURCE_PYTHON_GLOBS)
    return exact_paths, glob_patterns


def to_relative(path: Path) -> str:
    """将绝对路径转换为相对于项目根目录的 POSIX 相对路径字符串。"""
    return path.relative_to(ROOT).as_posix()


def print_action(status: str, source: str, destination: str | None = None) -> None:
    """统一控制台输出日志格式。"""
    if destination is None:
        print(f"[{status}] {source}")
        return
    print(f"[{status}] {source} -> {destination}")


# ==============================================================================
# 文件树扫描与过滤逻辑
# ==============================================================================

def should_skip_dir(relative_dir: Path) -> bool:
    """判断是否需要跳过某个目录。"""
    if not relative_dir.parts:
        return False
    # 过滤顶层排除路径
    if relative_dir.parts[0] in EXCLUDED_TOP_LEVEL_PATHS:
        return True
    # 过滤黑名单目录名
    return any(part in SKIPPED_DIR_NAMES for part in relative_dir.parts)


def should_skip_file(relative_file: Path) -> bool:
    """判断是否需要跳过某个文件。"""
    if not relative_file.parts:
        return False
    if relative_file.parts[0] in EXCLUDED_TOP_LEVEL_PATHS:
        return True
    if any(part in SKIPPED_DIR_NAMES for part in relative_file.parts[:-1]):
        return True
    return relative_file.suffix in SKIPPED_FILE_SUFFIXES


def should_keep_python_source(relative_file: str, exact_paths: frozenset[str], glob_patterns: tuple[str, ...]) -> bool:
    """
    判断一个 Python 文件是否命中了“保留源码”的规则。
    兼容性修复：
    由于 Python 3.12 及以下版本的 Path.match() 不支持 '**' 递归目录通配符，
    在该方法中加入了前缀提取校验，确保在所有 Python 版本下都能完美支持深层嵌套。
    """
    # if relative_file in exact_paths:
    #     return True
    # return any(Path(relative_file).match(pattern) for pattern in glob_patterns)

    if relative_file in exact_paths:
        return True
        
    for pattern in glob_patterns:
        # 如果通配符中包含递归标记 '/**'，我们提取前缀并执行路径归属检查
        if "/**" in pattern:
            prefix = pattern.split("/**")[0]
            # 匹配当前文件夹或任意子文件夹深度的文件
            if relative_file.startswith(prefix + "/") or relative_file == prefix:
                return True
        # 否则回退到标准的 Path.match
        elif Path(relative_file).match(pattern):
            return True
            
    return False


def iter_project_files() -> list[Path]:
    """深度遍历项目，获取所有需要处理的有效文件。"""
    files: list[Path] = []
    for current_root, dirnames, filenames in os.walk(ROOT, topdown=True):
        current_path = Path(current_root)
        try:
            current_relative = current_path.relative_to(ROOT)
        except ValueError:
            continue

        # 动态修改 dirnames 列表可以阻止 os.walk进入被忽略的子目录
        retained_dirs: list[str] = []
        for dirname in dirnames:
            candidate_relative = (current_relative / dirname) if current_relative.parts else Path(dirname)
            if should_skip_dir(candidate_relative):
                continue
            retained_dirs.append(dirname)
        dirnames[:] = retained_dirs

        # 收集非跳过的文件
        for filename in filenames:
            candidate = current_path / filename
            relative_file = candidate.relative_to(ROOT)
            if should_skip_file(relative_file):
                continue
            files.append(candidate)
    files.sort()
    return files


def build_plan() -> BuildPlan:
    """分析项目，生成编译与复制的完整行动计划。"""
    keep_source_exact, keep_source_globs = build_keep_source_matchers()
    compiled_python_files: list[Path] = []
    kept_source_python_files: list[Path] = []
    copied_files: list[Path] = []
    compiled_path_map: dict[str, str] = {}

    for file_path in iter_project_files():
        relative_text = to_relative(file_path)
        if file_path.suffix == ".py":
            # 如果命中了保留源码规则，则作为普通文件原样复制
            if should_keep_python_source(relative_text, keep_source_exact, keep_source_globs):
                kept_source_python_files.append(file_path)
                copied_files.append(file_path)
                continue
            # 否则加入编译队列
            compiled_python_files.append(file_path)
            # 建立从源 '.py' 路径到目标 '.so/.pyd' 路径的映射，用于后期修补配置文件引用
            compiled_path_map[relative_text] = file_path.with_suffix(EXT_SUFFIX).relative_to(ROOT).as_posix()
            continue
        # 静态资源、非编译脚本等，全部原样复制
        copied_files.append(file_path)

    return BuildPlan(
        compiled_python_files=tuple(compiled_python_files),
        kept_source_python_files=tuple(kept_source_python_files),
        copied_files=tuple(copied_files),
        compiled_path_map=compiled_path_map,
    )


# ==============================================================================
# 执行逻辑（构建、编译与文件操作）
# ==============================================================================

def prepare_output_dir(apply: bool) -> None:
    """初始化输出文件夹。"""
    if OUTPUT_DIR.exists():
        print_action("清理目录", to_relative(OUTPUT_DIR))
        if apply:
            shutil.rmtree(OUTPUT_DIR)
    print_action("创建目录", to_relative(OUTPUT_DIR))
    if apply:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def compile_python_file(source: Path, apply: bool) -> None:
    """打印编译进度。真实的编译在 apply 模式下由 compile_cython_batch 批量接管。"""
    destination = OUTPUT_DIR / source.relative_to(ROOT).with_suffix(EXT_SUFFIX)
    print_action("编译", to_relative(source), to_relative(destination))


def to_valid_module_parts(parts: tuple[str, ...]) -> tuple[str, ...]:
    """
    清洗路径中的非法字符（如前导点、连字符等），生成符合 Python 语法规范的模块段。
    例如: ('.smartclaw', 'tool-builder') -> ('_smartclaw', 'tool_builder')
    """
    valid_parts = []
    for part in parts:
        # 将非字母数字下划线的字符全部替换为下划线
        clean = re.sub(r'[^a-zA-Z0-9_]', '_', part)
        # Python 标识符不能以数字开头，如果是数字开头则补前缀下划线
        if clean and clean[0].isdigit():
            clean = "_" + clean
        if not clean:
            clean = "_"
        valid_parts.append(clean)
    return tuple(valid_parts)


def compile_cython_batch(compiled_files: tuple[Path, ...]) -> None:
    """
    使用 Cython 进行并行编译，并在编译后将生成的二进制文件移至输出目录。
    采用标准 build_ext 编译至强制指定的项目绝对路径 ROOT/build/lib/ 目录下。
    这消除了因运行脚本时工作路径（CWD）不同导致的文件检索失败问题。
    """
    if not compiled_files:
        return

    # 动态导入构建所需的依赖库
    from Cython.Build import cythonize
    from setuptools import Extension, setup

    # 构建 Cython Extension 列表，清洗模块名称
    extensions: list[Extension] = []
    for path in compiled_files:
        relative_path = path.relative_to(ROOT)
        parts = relative_path.with_suffix("").parts
        valid_parts = to_valid_module_parts(parts)
        module_name = ".".join(valid_parts)
        
        extensions.append(
            Extension(
                name=module_name,
                sources=[str(path)],
            )
        )

    # 计算编译并发线程数
    cpu_count = os.cpu_count() or 1
    jobs = max(1, min(cpu_count, len(compiled_files)))


    # 定义平台无关、工作目录无关的绝对 build 临时输出路径
    build_lib_dir = ROOT / "build" / "lib"

    # 临时备份和重构命令行参数，通过 --build-lib 强制约束输出目录
    old_argv = sys.argv
    sys.argv = [
        sys.argv[0], 
        "build_ext", 
        "--build-lib", str(build_lib_dir), 
        "--parallel", str(jobs)
    ]

    try:
        setup(
            name="delivery-build-cython",
            ext_modules=cythonize(
                extensions,
                nthreads=jobs,
                compiler_directives={
                    "language_level": 3,
                    "boundscheck": True,       # 开启边界检查，保证二进制安全
                    "wraparound": True,        # 必须启用负索引支持，防止 a[-1] 报错或行为未定义
                    # "binding": True,          # 关键：使编译后的函数表现得像普通 Python 函数
                    # "embedsignature": True,   # 建议：在 docstring 中嵌入函数签名，帮助反射
                    "annotation_typing": False,  # 【新增】禁用类型注解静态化。防止 Cython 将 FastAPI 的参数注入（如 : str = Query()）误编译为 C 静态类型而导致类型冲突报错
                },
                quiet=True,
            ),
            script_args=[
                "build_ext", 
                "--build-lib", str(build_lib_dir), 
                "--parallel", str(jobs)
            ],
        )
    finally:
        # 还原 sys.argv 避免对调用者产生副作用
        sys.argv = old_argv

    # 检查强制约束的绝对输出目录是否存在
    if not build_lib_dir.exists():
        raise RuntimeError(f"编译失败，未能找到强制指定的绝对构建输出目录：{build_lib_dir}")

    # 从确定的 build/lib 目录中，提取并转移二进制产物至交付文件夹
    for path in compiled_files:
        relative_path = path.relative_to(ROOT)
        parts = relative_path.with_suffix("").parts
        valid_parts = to_valid_module_parts(parts)
        
        # 二进制产物在临时 build 里的绝对路径
        so_source = build_lib_dir.joinpath(*valid_parts).with_suffix(EXT_SUFFIX)
        # 期望转移到交付包中的物理相对路径（完全复原原项目路径层级）
        so_destination = OUTPUT_DIR / relative_path.with_suffix(EXT_SUFFIX)

        if so_source.exists():
            so_destination.parent.mkdir(parents=True, exist_ok=True)
            # 安全地复制到交付产物中
            shutil.copy2(so_source, so_destination)

        # 删除在本地源码目录下生成的中间 .c 文件，保持源码目录百分百干净
        generated_c = path.with_suffix(".c")
        if generated_c.exists():
            generated_c.unlink()

    # 彻底删除编译过程中临时生成的 build/ 目录，保证不留任何垃圾
    build_dir = ROOT / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir)


# ==============================================================================
# 引用替换与修补逻辑 (Patching)
# ==============================================================================

def should_patch_text_file(path: Path) -> bool:
    """判断该文件是否需要检索并修补其中的文件路径引用。"""
    return path.name in PATCHABLE_TEXT_NAMES or path.suffix in PATCHABLE_TEXT_SUFFIXES


def replace_compiled_python_references(text: str, compiled_path_map: dict[str, str]) -> str:
    """
    执行文本替换。将配置文件中所有的 `.py` 路径引用升级为 `.so` / `.pyd`。
    """
    patched = text
    for source_relative, destination_relative in compiled_path_map.items():
        source_windows = source_relative.replace("/", "\\")
        destination_windows = destination_relative.replace("/", "\\")
        
        # 兼容 Windows 路径与 Linux 路径，同时匹配 `./path` 与 `path` 的书写格式
        replacements = (
            (f"./{source_relative}", f"./{destination_relative}"),
            (source_relative, destination_relative),
            (f".\\{source_windows}", f".\\{destination_windows}"),
            (source_windows, destination_windows),
        )
        for source_text, destination_text in replacements:
            patched = re.sub(re.escape(source_text) + r"(?!c)", lambda _: destination_text, patched)
    return patched


def copy_file(source: Path, compiled_path_map: dict[str, str], apply: bool) -> bool:
    """
    将文件复制到交付目录。如果是可修补文本文件，在复制时自动对内容进行正则修补。
    """
    destination = OUTPUT_DIR / source.relative_to(ROOT)
    print_action("复制", to_relative(source), to_relative(destination))
    if not apply:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)

    # 过滤无需文本修补的普通资源文件，原样快速复制
    if not should_patch_text_file(source):
        shutil.copy2(source, destination)
        return False

    try:
        original_text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # 如果文件无法用 UTF-8 解码，直接原样拷贝
        shutil.copy2(source, destination)
        return False

    # 对文本进行引用修改
    patched_text = replace_compiled_python_references(original_text, compiled_path_map)
    destination.write_text(patched_text, encoding="utf-8")
    shutil.copystat(source, destination)
    
    # 返回该文件内容是否确实发生过修改
    return patched_text != original_text


# ==============================================================================
# 控制流展现与主入口
# ==============================================================================

def print_summary(plan: BuildPlan, stats: BuildStats, apply: bool) -> None:
    """打印构建行动的最终报表。"""
    print(
        "汇总："
        f"编译 {stats.compiled} 个 .py 文件，"
        f"复制 {stats.copied} 个文件，"
        f"补丁 {stats.patched} 个文本文件，"
        f"跳过 {stats.skipped} 项。"
    )
    if plan.kept_source_python_files:
        print("保留源码的 .py 文件：")
        for path in plan.kept_source_python_files:
            print(f"- {to_relative(path)}")
    
    if KEEP_SOURCE_PYTHON_GLOBS:
        print("保留源码通配符规则：")
        for pattern in KEEP_SOURCE_PYTHON_GLOBS:
            print(f"- {pattern}")

    if stats.skipped_items:
        print("跳过明细：")
        for item in stats.skipped_items:
            print(f"- {item}")
    if not apply:
        print("\n当前为【预览模式】。未对物理文件进行实质性修改。")
        print("若确认以上构建计划无误，请执行：\n  python automated_script/build_delivery_artifact.py --apply")
        return
    print(f"\n交付产物已成功生成：{OUTPUT_DIR}")


def print_plan(plan: BuildPlan) -> None:
    """打印当前的编译架构与全局配置项。"""
    print(f"项目根目录：{ROOT}")
    print(f"输出目录：{OUTPUT_DIR}")
    print(f"待编译 .py 文件数：{len(plan.compiled_python_files)}")
    print(f"保留源码 .py 文件数：{len(plan.kept_source_python_files)}")
    print(f"待复制非 .py 文件数：{len(plan.copied_files)}")
    print("排除的顶层路径：" + ", ".join(sorted(EXCLUDED_TOP_LEVEL_PATHS)))
    print("跳过的目录名：" + ", ".join(sorted(SKIPPED_DIR_NAMES)))
    if KEEP_SOURCE_PYTHON_FILES or KEEP_SOURCE_PYTHON_GLOBS:
        print("已配置保留源码的 .py 规则。")


def main() -> int:
    """主逻辑控制流。"""
    args = parse_args()
    
    # --------------------------------------------------------------------------
    # 编译期无感注入逻辑（完全配置化，非侵入式）
    # --------------------------------------------------------------------------
    app_py_file = (ROOT / ENTRYPOINT_INJECTION_FILE) if ENTRYPOINT_INJECTION_FILE else None
    original_app_content = ""
    has_patched = False
    
    # 仅在真正打包时、配置不为空时，在内存中临时对入口文件头部追加 Pydantic 补丁
    if args.apply and app_py_file and app_py_file.exists():
        original_app_content = app_py_file.read_text(encoding="utf-8")
        if "Pydantic v2 + Cython 全局兼容性补丁" not in original_app_content:
            patched_content = PYDANTIC_PATCH_CODE + "\n" + original_app_content
            app_py_file.write_text(patched_content, encoding="utf-8")
            has_patched = True

    try:
        plan = build_plan()
        stats = BuildStats()

        # 打印基础参数信息
        print_plan(plan)
        prepare_output_dir(args.apply)

        # 1. 遍历并记录需要编译的文件（打印计划）
        for source in plan.compiled_python_files:
            compile_python_file(source, args.apply)
            stats.compiled += 1

        # 2. 真正执行构建时，调用 Cython 进行高度并行的批量编译
        if args.apply and plan.compiled_python_files:
            compile_cython_batch(plan.compiled_python_files)

        # 3. 原样复制非编译资源文件，并在必要时修补文本中旧的 Python 路径指向
        for source in plan.copied_files:
            patched = copy_file(source, plan.compiled_path_map, args.apply)
            stats.copied += 1
            if patched:
                stats.patched += 1

        # 输出汇总报告
        print_summary(plan, stats, args.apply)
        
    finally:
        # --------------------------------------------------------------------------
        # 无论成功还是失败，都在一瞬间复原项目开发目录，确保 Git 仓库没有任何“脏改动”
        # --------------------------------------------------------------------------
        if has_patched and app_py_file and app_py_file.exists():
            app_py_file.write_text(original_app_content, encoding="utf-8")
            
    return 0


if __name__ == "__main__":
    raise SystemExit(main())