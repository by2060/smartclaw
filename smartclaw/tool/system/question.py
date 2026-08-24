"""
Question Tool - User interaction and confirmation

Provides a way for agents to ask questions to users and receive answers.
Supports multiple choice questions with custom options.
"""

import asyncio
from contextvars import ContextVar
from typing import List, Dict, Any, Optional, Callable, Awaitable

from smartclaw.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from smartclaw.session.prompt_locale import is_zh_prompt_locale
from smartclaw.utils.log import Log


log = Log.create(service="tool.question")


# Context variable to pass message_id to handler
_current_message_id: ContextVar[Optional[str]] = ContextVar('current_message_id', default=None)
_current_call_id: ContextVar[Optional[str]] = ContextVar('current_call_id', default=None)


def get_current_message_id() -> Optional[str]:
    """
    Get the current message ID from context
    
    This is used by question handlers to get the message ID associated
    with the current question tool call.
    
    Returns:
        Message ID if available, None otherwise
    """
    return _current_message_id.get()


def get_current_call_id() -> Optional[str]:
    """
    Get the current call ID from context
    
    This is used by question handlers to get the call ID associated
    with the current question tool call.
    
    Returns:
        Call ID if available, None otherwise
    """
    return _current_call_id.get()


# Question callback type - should be set by the application
QuestionCallback = Callable[[str, List[Dict[str, Any]]], Awaitable[List[List[str]]]]

# Global question handler (to be set by the application)
_question_handler: Optional[QuestionCallback] = None


def set_question_handler(handler: QuestionCallback) -> None:
    """
    Set the global question handler
    
    The handler should be an async function that:
    - Takes session_id and list of questions
    - Returns list of answers (each answer is a list of selected option labels)
    
    Args:
        handler: Question handler function
    """
    global _question_handler
    _question_handler = handler


class QuestionRejectedError(Exception):
    """Raised when user rejects/declines a question"""
    pass


DESCRIPTION = """Ask the user a question and wait for their response.

Use this tool when you need to:
- Confirm before making significant changes
- Get user preference between multiple options
- Clarify ambiguous instructions

Question format:
- Each question has a text prompt
- Optional header for context
- List of options for the user to choose from
- Options have label and optional description

The user's answers will be returned for you to continue with."""

DESCRIPTION_CN = """向用户提问并等待响应。

适用场景：
- 在进行重大更改前需要确认
- 需要在多个选项之间获取用户偏好
- 需要澄清模糊指令

问题格式：
- 每个问题包含文本提示
- 可选 header 用于提供上下文
- 提供可供用户选择的选项列表
- 选项包含 label 和可选 description

用户的回答会返回给你，以便继续执行。"""


async def default_question_handler(
    session_id: str,
    questions: List[Dict[str, Any]]
) -> List[List[str]]:
    """
    Default question handler that auto-accepts
    
    In production, this would be replaced with actual user interaction.
    
    Args:
        session_id: Session ID
        questions: List of questions
        
    Returns:
        List of answers (first option selected for each)
    """
    answers = []
    default_yes = "是" if is_zh_prompt_locale() else "Yes"
    for q in questions:
        options = q.get("options", [])
        if options:
            # Auto-select first option
            answers.append([options[0].get("label", default_yes)])
        else:
            answers.append([default_yes])
    return answers


def _tr(en: str, zh: str) -> str:
    return zh if is_zh_prompt_locale() else en


def _question_title(count: int) -> str:
    if is_zh_prompt_locale():
        return f"已询问 {count} 个问题"
    return f"Asked {count} question{'s' if count > 1 else ''}"


@ToolRegistry.register_function(
    name="question",
    description=DESCRIPTION,
    description_cn=DESCRIPTION_CN,
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="questions",
            type=ParameterType.ARRAY,
            description="Array of questions to ask the user",
            required=True,
            json_schema={
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Question text prompt",
                        },
                        "header": {
                            "type": "string",
                            "description": "Optional header/context for the question",
                        },
                        "type": {
                            "type": "string",
                            "description": (
                                "Input type for the question. "
                                "'choice' (default): select from options (single or multiple); "
                                "'text': free-form text input (single or multi-line); "
                                "'number': numeric input with optional range; "
                                "'file': file upload (content returned to agent); "
                                "'confirm': yes/no confirmation buttons; "
                                "'password': masked text input for sensitive data."
                            ),
                            "enum": ["choice", "text", "number", "file", "confirm", "password"],
                        },
                        "options": {
                            "type": "array",
                            "description": "Options for 'choice' type questions",
                            "items": {
                                "anyOf": [
                                    {"type": "string"},
                                    {
                                        "type": "object",
                                        "properties": {
                                            "label": {"type": "string"},
                                            "description": {"type": "string"},
                                        },
                                        "required": ["label"],
                                        "additionalProperties": False,
                                    },
                                ],
                            },
                        },
                        "multiple": {
                            "type": "boolean",
                            "description": "For 'choice' type: allow selecting multiple options",
                        },
                        "placeholder": {
                            "type": "string",
                            "description": "Placeholder/hint text for text, number, password, file inputs",
                        },
                        "multiline": {
                            "type": "boolean",
                            "description": "For 'text' type: use textarea (multi-line input)",
                        },
                        "min_value": {
                            "type": "number",
                            "description": "For 'number' type: minimum allowed value",
                        },
                        "max_value": {
                            "type": "number",
                            "description": "For 'number' type: maximum allowed value",
                        },
                        "step": {
                            "type": "number",
                            "description": "For 'number' type: step increment",
                        },
                        "accept": {
                            "type": "string",
                            "description": "For 'file' type: accepted file extensions, e.g. '.txt,.log,.csv'",
                        },
                    },
                    "required": ["question"],
                    "additionalProperties": True,
                },
            },
        ),
    ]
)
async def question_tool(
    ctx: ToolContext,
    questions: List[Dict[str, Any]],
) -> ToolResult:
    """
    Ask questions to the user
    
    Args:
        ctx: Tool context
        questions: List of question objects with question, header, options fields
        
    Returns:
        ToolResult with user's answers
    """
    if not questions:
        return ToolResult(
            success=False,
            error=_tr("At least one question is required", "至少需要提供一个问题")
        )
    
    # Normalize questions
    normalized_questions = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        
        normalized = {
            "question": str(q.get("question", "")),
            "header": q.get("header", ""),
            "type": q.get("type", "choice"),
            "options": [],
            "multiple": q.get("multiple", False),
            "placeholder": q.get("placeholder", ""),
            "multiline": q.get("multiline", False),
        }
        # Optional numeric range fields
        if "min_value" in q:
            normalized["min_value"] = q["min_value"]
        if "max_value" in q:
            normalized["max_value"] = q["max_value"]
        if "step" in q:
            normalized["step"] = q["step"]
        if "accept" in q:
            normalized["accept"] = q["accept"]

        options = q.get("options", [])
        for opt in options:
            if isinstance(opt, dict):
                normalized["options"].append({
                    "label": str(opt.get("label", "")),
                    "description": opt.get("description", "")
                })
            elif isinstance(opt, str):
                normalized["options"].append({
                    "label": opt,
                    "description": ""
                })
        
        normalized_questions.append(normalized)
    
    if not normalized_questions:
        return ToolResult(
            success=False,
            error=_tr("No valid questions provided", "未提供有效问题")
        )
    
    # Get handler
    handler = _question_handler or default_question_handler
    
    try:
        # Set message_id and call_id in context for handler to use
        _current_message_id.set(ctx.message_id)
        _current_call_id.set(ctx.call_id)
        
        # Ask questions
        answers = await handler(ctx.session_id, normalized_questions)
        
        # Format output
        def format_answer(answer: Optional[List[str]]) -> str:
            if not answer:
                return _tr("Unanswered", "未回答")
            return ", ".join(answer)
        
        formatted = ", ".join([
            f'"{q["question"]}"="{format_answer(answers[i] if i < len(answers) else None)}"'
            for i, q in enumerate(normalized_questions)
        ])
        
        if is_zh_prompt_locale():
            output = f"用户已回答你的问题：{formatted}。你现在可以根据用户的回答继续。"
        else:
            output = f"User has answered your questions: {formatted}. You can now continue with the user's answers in mind."
        
        return ToolResult(
            success=True,
            output=output,
            title=_question_title(len(normalized_questions)),
            metadata={
                "answers": answers
            }
        )
        
    except QuestionRejectedError:
        return ToolResult(
            success=False,
            error=_tr("User rejected the question", "用户拒绝回答问题")
        )
    except Exception as e:
        return ToolResult(
            success=False,
            error=_tr(f"Failed to get answers: {str(e)}", f"获取答案失败：{str(e)}")
        )
