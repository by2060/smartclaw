"""
LSP Tool - Language Server Protocol operations

Provides LSP operations for code intelligence:
- Go to definition
- Find references
- Hover information
- Document/workspace symbols
- Call hierarchy
"""

import os
import json
from typing import Optional, List, Dict, Any

from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.project.instance import Instance
from flocks.utils.log import Log


log = Log.create(service="tool.lsp")


# Supported LSP operations
LSP_OPERATIONS = [
    "goToDefinition",
    "findReferences",
    "hover",
    "documentSymbol",
    "workspaceSymbol",
    "goToImplementation",
    "prepareCallHierarchy",
    "incomingCalls",
    "outgoingCalls",
]


DESCRIPTION = """Perform LSP (Language Server Protocol) operations for code intelligence.

Supported operations:
- goToDefinition: Jump to where a symbol is defined
- findReferences: Find all usages of a symbol
- hover: Get type/documentation info for a symbol
- documentSymbol: List all symbols in a file
- workspaceSymbol: Search symbols across workspace
- goToImplementation: Find implementations of an interface
- prepareCallHierarchy: Get call hierarchy item at position
- incomingCalls: Find callers of a function
- outgoingCalls: Find functions called by a function

Parameters:
- operation: The LSP operation to perform
- filePath: Path to the file
- line: Line number (1-based)
- character: Character offset (1-based)"""

DESCRIPTION_CN = """执行 LSP（语言服务器协议）操作以实现代码智能。

支持的操作：
- goToDefinition：跳转到符号定义位置
- findReferences：查找符号的所有用法
- hover：获取符号的类型/文档信息

用法：
- 需要 LSP 服务器在项目中运行
- file 必须是工作区中的文件绝对路径
- line 和 character 是从 0 开始的位置""",

    "webfetch.py": 从指定 URL 获取内容并以可读格式返回。

用法：
- URL 必须是以 http:// 或 https:// 开头的完整有效 URL
- 默认以 markdown 格式返回内容（HTML 会被转换）
- 支持 text、markdown 和 html 输出格式


@ToolRegistry.register_function(
    name="lsp",
    description=DESCRIPTION,
    category=ToolCategory.CODE,
    parameters=[
        ToolParameter(
            name="operation",
            type=ParameterType.STRING,
            description="The LSP operation to perform",
            required=True,
            enum=LSP_OPERATIONS
        ),
        ToolParameter(
            name="filePath",
            type=ParameterType.STRING,
            description="The absolute or relative path to the file",
            required=True
        ),
        ToolParameter(
            name="line",
            type=ParameterType.INTEGER,
            description="The line number (1-based, as shown in editors)",
            required=True
        ),
        ToolParameter(
            name="character",
            type=ParameterType.INTEGER,
            description="The character offset (1-based, as shown in editors)",
            required=True
        ),
    ]
)
async def lsp_tool(
    ctx: ToolContext,
    operation: str,
    filePath: str,
    line: int,
    character: int,
) -> ToolResult:
    """
    Perform an LSP operation
    
    Args:
        ctx: Tool context
        operation: LSP operation to perform
        filePath: Target file path
        line: Line number (1-based)
        character: Character offset (1-based)
        
    Returns:
        ToolResult with LSP results
    """
    # Validate operation
    if operation not in LSP_OPERATIONS:
        return ToolResult(
            success=False,
            error=f"Invalid operation: {operation}. Supported: {', '.join(LSP_OPERATIONS)}"
        )
    
    # Resolve path
    base_dir = Instance.get_directory() or os.getcwd()
    filepath = filePath if os.path.isabs(filePath) else os.path.join(base_dir, filePath)
    
    # Check file exists
    if not os.path.exists(filepath):
        return ToolResult(
            success=False,
            error=f"File not found: {filepath}"
        )
    
    # Request permission
    await ctx.ask(
        permission="lsp",
        patterns=["*"],
        always=["*"],
        metadata={}
    )
    
    # Get relative path for title
    worktree = Instance.get_worktree() or os.getcwd()
    try:
        rel_path = os.path.relpath(filepath, worktree)
    except ValueError:
        rel_path = filepath
    
    title = f"{operation} {rel_path}:{line}:{character}"
    
    # Convert to 0-based indices for LSP
    position = {
        "line": line - 1,
        "character": character - 1
    }
    
    try:
        # Import LSP module
        from flocks.lsp import LSP
        
        # Check if LSP is available for this file
        has_client = await LSP.has_clients(filepath)
        if not has_client:
            return ToolResult(
                success=False,
                error="No LSP server available for this file type."
            )
        
        # Touch file to ensure LSP has it open
        await LSP.touch_file(filepath, sync=True)
        
        # Execute operation
        result: List[Any] = []
        
        if operation == "goToDefinition":
            result = await LSP.definition({
                "file": filepath,
                "line": position["line"],
                "character": position["character"]
            })
        elif operation == "findReferences":
            result = await LSP.references({
                "file": filepath,
                "line": position["line"],
                "character": position["character"]
            })
        elif operation == "hover":
            result = await LSP.hover({
                "file": filepath,
                "line": position["line"],
                "character": position["character"]
            })
        elif operation == "documentSymbol":
            uri = f"file://{filepath}"
            result = await LSP.document_symbol(uri)
        elif operation == "workspaceSymbol":
            result = await LSP.workspace_symbol("")
        elif operation == "goToImplementation":
            result = await LSP.implementation({
                "file": filepath,
                "line": position["line"],
                "character": position["character"]
            })
        elif operation == "prepareCallHierarchy":
            result = await LSP.prepare_call_hierarchy({
                "file": filepath,
                "line": position["line"],
                "character": position["character"]
            })
        elif operation == "incomingCalls":
            result = await LSP.incoming_calls({
                "file": filepath,
                "line": position["line"],
                "character": position["character"]
            })
        elif operation == "outgoingCalls":
            result = await LSP.outgoing_calls({
                "file": filepath,
                "line": position["line"],
                "character": position["character"]
            })
        
        # Format output
        if not result:
            output = f"No results found for {operation}"
        else:
            output = json.dumps(result, indent=2)
        
        return ToolResult(
            success=True,
            output=output,
            title=title,
            metadata={"result": result}
        )
        
    except ImportError:
        # LSP module not available, return placeholder
        return ToolResult(
            success=False,
            error="LSP module not initialized. Start the LSP subsystem first.",
            title=title
        )
    except Exception as e:
        log.error("lsp.error", {"operation": operation, "error": str(e)})
        return ToolResult(
            success=False,
            error=f"LSP operation failed: {str(e)}",
            title=title
        )
