"""
Grep Tool - Code search using ripgrep

Fast content search tool that:
- Searches file contents using regular expressions
- Supports full regex syntax
- Filter files by pattern
- Returns results sorted by modification time
"""

import os
import asyncio
import shutil
import re
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from smartclaw.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from smartclaw.project.instance import Instance
from smartclaw.utils.log import Log
from smartclaw.tool.file.sandbox_paths import resolve_sandbox_path, sandbox_search_roots


log = Log.create(service="tool.grep")


# Constants
MAX_LINE_LENGTH = 2000
MAX_MATCHES = 100


# Description matching SmartClaw' grep.txt
DESCRIPTION = """- Fast content search tool that works with any codebase size
- Searches file contents using regular expressions
- Supports full regex syntax (eg. "log.*Error", "function\\s+\\w+", etc.)
- Filter files by pattern with the include parameter (eg. "*.js", "*.{ts,tsx}")
- Returns file paths and line numbers with at least one match sorted by modification time
- Use this tool when you need to find files containing specific patterns
- If you need to identify/count the number of matches within files, use the Bash tool with `rg` (ripgrep) directly. Do NOT use `grep`.
- When you are doing an open-ended search that may require multiple rounds of globbing and grepping, use the Task tool instead"""

DESCRIPTION_CN = """- 快速内容搜索工具，适用于任何规模的代码库
- 使用正则表达式搜索文件内容
- 支持完整正则语法，例如 "log.*Error"、"function\\s+\\w+"
- 可通过 include 参数按文件模式过滤，例如 "*.js"、"*.{ts,tsx}"
- 返回至少包含一个匹配项的文件路径和行号，并按修改时间排序
- 当需要查找包含特定模式的文件时使用此工具
- 如需识别或统计文件内的匹配数量，请直接使用 Bash 工具调用 `rg`（ripgrep），不要使用 `grep`
- 当进行开放式搜索且可能需要多轮 glob 和 grep 时，请改用 Task 工具"""


def find_ripgrep() -> Optional[str]:
    """
    Find ripgrep executable
    
    Returns:
        Path to ripgrep or None
    """
    # Try common names
    for name in ['rg', 'ripgrep']:
        path = shutil.which(name)
        if path:
            return path
    return None


def fallback_grep(
    pattern: str,
    search_path: str,
    include: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Fallback grep implementation using Python's re module
    
    Used when ripgrep is not available.
    
    Args:
        pattern: Regex pattern to search
        search_path: Directory to search
        include: File pattern to include
        
    Returns:
        List of matches
    """
    import fnmatch
    
    matches = []
    
    try:
        regex = re.compile(pattern)
    except re.error as e:
        raise ValueError(f"Invalid regex pattern: {e}")
    
    for root, dirs, files in os.walk(search_path):
        # Skip hidden and common ignore directories
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in (
            'node_modules', '__pycache__', '.git', 'dist', 'build', 'target'
        )]
        
        for filename in files:
            # Apply include filter
            if include:
                # Handle glob patterns like "*.js" or "*.{ts,tsx}"
                patterns = []
                if '{' in include:
                    # Expand brace patterns
                    match = re.match(r'\*\.?\{([^}]+)\}', include)
                    if match:
                        exts = match.group(1).split(',')
                        patterns = [f"*.{ext.strip()}" for ext in exts]
                else:
                    patterns = [include]
                
                if not any(fnmatch.fnmatch(filename, p) for p in patterns):
                    continue
            
            filepath = os.path.join(root, filename)
            
            try:
                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                    for line_num, line in enumerate(f, 1):
                        if regex.search(line):
                            stat = os.stat(filepath)
                            matches.append({
                                'path': filepath,
                                'modTime': stat.st_mtime,
                                'lineNum': line_num,
                                'lineText': line.rstrip('\n\r')
                            })
                            
                            if len(matches) >= MAX_MATCHES * 10:
                                return matches
                                
            except (IOError, OSError):
                continue
    
    return matches


async def ripgrep_search(
    rg_path: str,
    pattern: str,
    search_path: str,
    include: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Search using ripgrep
    
    Args:
        rg_path: Path to ripgrep executable
        pattern: Regex pattern
        search_path: Directory to search
        include: File pattern to include
        
    Returns:
        List of matches
    """
    args = [
        rg_path,
        "-nH",              # Line numbers, filenames
        "--hidden",         # Search hidden files
        "--follow",         # Follow symlinks
        "--no-messages",    # Suppress error messages
        "--field-match-separator=|",
        "--regexp", pattern
    ]
    
    if include:
        args.extend(["--glob", include])
    
    args.append(search_path)
    
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    stdout, stderr = await proc.communicate()
    output = stdout.decode('utf-8', errors='replace')
    
    # Exit codes: 0 = matches found, 1 = no matches, 2 = errors
    if proc.returncode == 1 or (proc.returncode == 2 and not output.strip()):
        return []
    
    matches = []
    for line in output.strip().split('\n'):
        if not line:
            continue
        
        parts = line.split('|', 2)
        if len(parts) < 3:
            continue
        
        filepath, line_num_str, line_text = parts
        
        try:
            line_num = int(line_num_str)
        except ValueError:
            continue
        
        try:
            stat = os.stat(filepath)
            mod_time = stat.st_mtime
        except OSError:
            mod_time = 0
        
        matches.append({
            'path': filepath,
            'modTime': mod_time,
            'lineNum': line_num,
            'lineText': line_text
        })
    
    return matches


@ToolRegistry.register_function(
    name="grep",
    description=DESCRIPTION,
    description_cn=DESCRIPTION_CN,
    category=ToolCategory.SEARCH,
    parameters=[
        ToolParameter(
            name="pattern",
            type=ParameterType.STRING,
            description="The regex pattern to search for in file contents",
            required=True
        ),
        ToolParameter(
            name="path",
            type=ParameterType.STRING,
            description="The directory to search in. Defaults to the current working directory.",
            required=False
        ),
        ToolParameter(
            name="include",
            type=ParameterType.STRING,
            description='File pattern to include in the search (e.g. "*.js", "*.{ts,tsx}")',
            required=False
        ),
    ]
)
async def grep_tool(
    ctx: ToolContext,
    pattern: str,
    path: Optional[str] = None,
    include: Optional[str] = None,
) -> ToolResult:
    """
    Search file contents using regex
    
    Args:
        ctx: Tool context
        pattern: Regex pattern to search
        path: Directory to search
        include: File pattern filter
        
    Returns:
        ToolResult with search results
    """
    if not pattern:
        return ToolResult(
            success=False,
            error="pattern is required"
        )
    
    # Request permission
    await ctx.ask(
        permission="grep",
        patterns=[pattern],
        always=["*"],
        metadata={
            "pattern": pattern,
            "path": path,
            "include": include
        }
    )
    
    # Resolve search path
    base_dir = Instance.get_directory() or os.getcwd()
    search_path = path or base_dir
    # 上传文档目录挂载到沙箱新增
    sandbox = ctx.extra.get("sandbox") if ctx.extra else None
    
    if not os.path.isabs(search_path) and not (path and isinstance(sandbox, dict)):
        search_path = os.path.join(base_dir, search_path)
    if path:
        resolved, sandbox_error = await resolve_sandbox_path(ctx, search_path)
        if sandbox_error:
            return ToolResult(success=False, error=sandbox_error, title=pattern)
        search_roots = [resolved.path if resolved else search_path]
    else:
        search_roots = sandbox_search_roots(ctx, base_dir)
    # --------------------end--------------------------
    # Find ripgrep
    rg_path = find_ripgrep()
    
    try:
        matches = []
        for root in search_roots:
            if rg_path:
                matches.extend(await ripgrep_search(rg_path, pattern, root, include))
            else:
                log.warn("grep.ripgrep_not_found", {"fallback": "python_regex"})
                matches.extend(fallback_grep(pattern, root, include))
    except Exception as e:
        return ToolResult(
            success=False,
            error=f"Search failed: {str(e)}",
            title=pattern
        )
    
    # Sort by modification time (most recent first)
    matches.sort(key=lambda x: x['modTime'], reverse=True)
    
    # Truncate results
    truncated = len(matches) > MAX_MATCHES
    final_matches = matches[:MAX_MATCHES]
    
    if not final_matches:
        return ToolResult(
            success=True,
            output="No files found",
            title=pattern,
            metadata={"matches": 0, "truncated": False}
        )
    
    # Build output
    output_lines = [f"Found {len(final_matches)} matches"]
    
    current_file = ""
    for match in final_matches:
        display_file = match['path']
        if current_file != display_file:
            if current_file:
                output_lines.append("")
            current_file = display_file
            output_lines.append(f"{display_file}:")
        
        # Truncate long lines
        line_text = match['lineText']
        if len(line_text) > MAX_LINE_LENGTH:
            line_text = line_text[:MAX_LINE_LENGTH] + "..."
        
        output_lines.append(f"  Line {match['lineNum']}: {line_text}")
    
    if truncated:
        output_lines.append("")
        output_lines.append("(Results are truncated. Consider using a more specific path or pattern.)")
    
    return ToolResult(
        success=True,
        output="\n".join(output_lines),
        title=pattern,
        truncated=truncated,
        metadata={
            "matches": len(final_matches),
            "truncated": truncated
        }
    )
