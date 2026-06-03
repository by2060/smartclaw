import os
import re
from pathlib import Path
from typing import Optional, List, Dict, Any
from flocks.tool.registry import ToolContext, ToolRegistry, ToolResult
from flocks.tool.file.sandbox_paths import display_path, resolve_sandbox_path, sandbox_search_roots

# Sensitive directories to exclude from search
SENSITIVE_DIRS = {
    '.ssh', '.gnupg', '.aws', '.azure', '.config',
    'node_modules', '.git', '.venv', 'venv', 'env',
    '__pycache__', '.pytest_cache', '.mypy_cache'
}

@ToolRegistry.register_function(
    name="file_search",
    description="Search for files by name pattern or content within a directory. Supports glob patterns for filenames and regex for content search.",
    description_cn="在目录中按文件名模式或内容搜索文件。文件名支持 glob 模式，内容搜索支持正则表达式。",
    parameters=[
        {
            "name": "pattern",
            "type": "string",
            "description": "File name pattern (glob syntax) or content search term. Examples: '*.py', 'test_*.json', 'config.*'",
            "required": True
        },
        {
            "name": "directory",
            "type": "string",
            "description": "Directory to search in. Defaults to current working directory.",
            "required": False
        },
        {
            "name": "search_content",
            "type": "boolean",
            "description": "If true, search file contents instead of filenames. Pattern becomes a regex for content matching.",
            "required": False
        },
        {
            "name": "recursive",
            "type": "boolean",
            "description": "Search recursively in subdirectories. Defaults to true.",
            "required": False
        },
        {
            "name": "max_results",
            "type": "integer",
            "description": "Maximum number of results to return. Defaults to 100.",
            "required": False
        },
        {
            "name": "file_extensions",
            "type": "string",
            "description": "Comma-separated list of file extensions to filter (e.g., 'py,js,ts'). Only applies to content search.",
            "required": False
        }
    ]
)
async def file_search(
    ctx: Optional[ToolContext] = None,
    pattern: str = "",
    directory: Optional[str] = None,
    search_content: bool = False,
    recursive: bool = True,
    max_results: int = 100,
    file_extensions: Optional[str] = None
) -> Dict[str, Any] | ToolResult:
    """
    Search for files by name pattern or content.
    
    Args:
        pattern: File name pattern (glob) or content search regex
        directory: Directory to search in (default: current working directory)
        search_content: If True, search file contents instead of filenames
        recursive: Search recursively in subdirectories
        max_results: Maximum number of results to return
        file_extensions: Comma-separated extensions to filter (content search only)
    
    Returns:
        Dictionary with search results and metadata
    """
    def finish(payload: Dict[str, Any]) -> Dict[str, Any] | ToolResult:
        if ctx is None:
            return payload
        ok = payload.get("status") == "success"
        return ToolResult(
            success=ok,
            output=payload if ok else None,
            error=None if ok else str(payload.get("error") or "Search failed"),
            metadata=payload,
        )

    try:
        if not pattern:
            return finish({
                "status": "error",
                "error": "pattern is required",
                "results": []
            })
        # Set default directory
        if directory is None:
            directory = os.getcwd()

        search_dirs: list[Path] = []
        sandbox = ctx.extra.get("sandbox") if ctx and ctx.extra else None
        if sandbox:
            if directory:
                resolved, sandbox_error = await resolve_sandbox_path(ctx, directory)
                if sandbox_error:
                    return finish({"status": "error", "error": sandbox_error, "results": []})
                search_dirs = [Path(resolved.path if resolved else directory).resolve()]
            else:
                search_dirs = [Path(root).resolve() for root in sandbox_search_roots(ctx, os.getcwd())]
        else:
            search_dirs = [Path(directory).resolve()]

        for search_dir in search_dirs:
            if not search_dir.exists():
                return finish({
                    "status": "error",
                    "error": f"Directory does not exist: {directory}",
                    "results": []
                })
            if not search_dir.is_dir():
                return finish({
                    "status": "error",
                    "error": f"Path is not a directory: {directory}",
                    "results": []
                })
        
        # Parse file extensions filter
        extensions = None
        if file_extensions:
            extensions = [ext.strip().lstrip('.') for ext in file_extensions.split(',')]
        
        results = []
        total_scanned = 0
        
        for search_dir in search_dirs:
            remaining = max_results - len(results)
            if remaining <= 0:
                break
            if search_content:
                try:
                    regex_pattern = re.compile(pattern, re.IGNORECASE)
                except re.error as e:
                    return finish({
                        "status": "error",
                        "error": f"Invalid regex pattern: {e}",
                        "results": []
                    })
                results.extend(_search_content(
                    search_dir, regex_pattern, recursive, remaining, extensions
                ))
            else:
                results.extend(_search_filenames(
                    search_dir, pattern, recursive, remaining
                ))

        if ctx:
            for item in results:
                if isinstance(item, dict) and item.get("path"):
                    item["path"] = display_path(str(item["path"]), ctx)
        
        return finish({
            "status": "success",
            "search_type": "content" if search_content else "filename",
            "pattern": pattern,
            "directory": str(search_dirs[0]) if len(search_dirs) == 1 else [str(p) for p in search_dirs],
            "results": results,
            "count": len(results),
            "truncated": len(results) >= max_results
        })
        
    except PermissionError as e:
        return finish({
            "status": "error",
            "error": f"Permission denied: {e}",
            "results": []
        })
    except Exception as e:
        return finish({
            "status": "error",
            "error": f"Search failed: {str(e)}",
            "results": []
        })


def _search_filenames(
    search_dir: Path,
    pattern: str,
    recursive: bool,
    max_results: int
) -> List[Dict[str, Any]]:
    """Search for files by name pattern."""
    results = []
    
    try:
        if recursive:
            # Use rglob for recursive search
            for file_path in search_dir.rglob('*'):
                if len(results) >= max_results:
                    break
                
                if _should_skip_path(file_path):
                    continue
                
                if file_path.is_file() and file_path.match(pattern):
                    results.append({
                        "path": str(file_path),
                        "name": file_path.name,
                        "size": file_path.stat().st_size,
                        "type": "file"
                    })
        else:
            # Non-recursive search
            for file_path in search_dir.glob('*'):
                if len(results) >= max_results:
                    break
                
                if file_path.is_file() and file_path.match(pattern):
                    results.append({
                        "path": str(file_path),
                        "name": file_path.name,
                        "size": file_path.stat().st_size,
                        "type": "file"
                    })
    except PermissionError:
        pass
    
    return results


def _search_content(
    search_dir: Path,
    pattern: re.Pattern,
    recursive: bool,
    max_results: int,
    extensions: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Search for content within files."""
    results = []
    
    # Get list of files to search
    files_to_search = []
    
    try:
        if recursive:
            for file_path in search_dir.rglob('*'):
                if _should_skip_path(file_path):
                    continue
                
                if file_path.is_file():
                    # Filter by extension if specified
                    if extensions:
                        if file_path.suffix.lstrip('.') not in extensions:
                            continue
                    
                    # Skip binary files and large files
                    if _is_binary_file(file_path):
                        continue
                    
                    files_to_search.append(file_path)
        else:
            for file_path in search_dir.glob('*'):
                if file_path.is_file():
                    if extensions:
                        if file_path.suffix.lstrip('.') not in extensions:
                            continue
                    
                    if _is_binary_file(file_path):
                        continue
                    
                    files_to_search.append(file_path)
    except PermissionError:
        pass
    
    # Search content in files
    for file_path in files_to_search:
        if len(results) >= max_results:
            break
        
        try:
            matches = _search_file_content(file_path, pattern)
            if matches:
                results.append({
                    "path": str(file_path),
                    "name": file_path.name,
                    "matches": matches,
                    "match_count": len(matches)
                })
        except (PermissionError, UnicodeDecodeError):
            continue
    
    return results


def _search_file_content(file_path: Path, pattern: re.Pattern) -> List[Dict[str, Any]]:
    """Search for pattern in a single file."""
    matches = []
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line_num, line in enumerate(f, 1):
                if pattern.search(line):
                    matches.append({
                        "line": line_num,
                        "content": line.rstrip('\n\r'),
                        "match": pattern.search(line).group(0) if pattern.search(line) else ""
                    })
    except Exception:
        pass
    
    return matches


def _should_skip_path(path: Path) -> bool:
    """Check if path should be skipped (sensitive directories)."""
    # Check if any part of the path is in sensitive directories
    for part in path.parts:
        if part in SENSITIVE_DIRS:
            return True
    return False


def _is_binary_file(file_path: Path) -> bool:
    """Check if file is likely binary."""
    # Check file extension
    binary_extensions = {
        '.pyc', '.so', '.dll', '.exe', '.bin', '.dat',
        '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico',
        '.pdf', '.zip', '.tar', '.gz', '.rar', '.7z',
        '.mp3', '.mp4', '.avi', '.mov', '.wav',
        '.class', '.jar', '.war', '.ear'
    }
    
    if file_path.suffix.lower() in binary_extensions:
        return True
    
    # Check file size (skip very large files)
    try:
        if file_path.stat().st_size > 10 * 1024 * 1024:  # 10MB
            return True
    except Exception:
        return True
    
    return False
