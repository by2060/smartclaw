from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


_SAFE_COMPONENT_PATTERN = re.compile(r"[^A-Za-z0-9_]+")
_PLACEHOLDER_PATTERN = re.compile(r"\{([^}]+)\}")


class DraftValidationIssue(BaseModel):
    severity: Literal["error", "warning", "info"] = "error"
    path: str
    message: str


class ProviderDraft(BaseModel):
    id: str = Field("", description="Provider directory/id")
    name: str = Field("", description="Provider display name")
    service_id: Optional[str] = None
    description: str = ""
    description_cn: Optional[str] = None
    auth: Optional[dict[str, Any]] = None
    defaults: dict[str, Any] = Field(default_factory=dict)
    credential_fields: Optional[list[dict[str, Any]]] = None
    compound_secret: Optional[dict[str, Any]] = None
    docs_url: Optional[str] = None
    category: Optional[str] = None


class ToolDraft(BaseModel):
    name: str
    description: str = ""
    description_cn: Optional[str] = None
    category: str = "custom"
    enabled: bool = True
    requires_confirmation: bool = False
    provider: Optional[str] = None
    inputSchema: Optional[dict[str, Any]] = None
    parameters: Optional[list[dict[str, Any]]] = None
    handler: dict[str, Any] = Field(default_factory=dict)
    response: Optional[dict[str, Any]] = None


class APIToolDraft(BaseModel):
    provider: ProviderDraft
    tools: list[ToolDraft] = Field(default_factory=list)


def safe_component(value: Any, fallback: str) -> str:
    text = str(value or "").strip().lower()
    text = _SAFE_COMPONENT_PATTERN.sub("_", text).strip("_")
    return text or fallback


def _is_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _copy_non_empty(data: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in keys:
        value = data.get(key)
        if _is_non_empty(value):
            result[key] = value
    return result


def _declared_parameter_names(tool: ToolDraft) -> set[str]:
    declared: set[str] = set()
    if isinstance(tool.inputSchema, dict):
        properties = tool.inputSchema.get("properties")
        if isinstance(properties, dict):
            declared.update(str(name) for name in properties.keys())
    if isinstance(tool.parameters, list):
        for param in tool.parameters:
            if isinstance(param, dict) and param.get("name"):
                declared.add(str(param["name"]))
    return declared


def _iter_template_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_template_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_template_strings(item)


def _normalize_required_from_property_flags(schema: dict[str, Any]) -> None:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return

    required = schema.get("required")
    if not isinstance(required, list):
        required = []
    required_set = {str(item) for item in required}

    for name, prop in properties.items():
        if isinstance(prop, dict) and prop.pop("required", False) is True:
            required_set.add(str(name))

    if required_set:
        schema["required"] = [name for name in properties.keys() if str(name) in required_set]


def normalize_api_tool_draft(draft: APIToolDraft) -> APIToolDraft:
    provider = draft.provider
    provider_id = safe_component(provider.id or provider.name, "api_service")
    provider.id = provider_id
    provider.name = provider.name or provider_id
    provider.service_id = provider_id
    provider.defaults = dict(provider.defaults or {})
    provider.defaults.setdefault("category", provider.category or "custom")
    provider.defaults.setdefault("timeout", 30)

    base_url = provider.defaults.get("base_url")
    if isinstance(base_url, str):
        provider.defaults["base_url"] = base_url.rstrip("/")

    for index, tool in enumerate(draft.tools):
        tool.name = safe_component(tool.name, f"api_tool_{index + 1}")
        tool.category = tool.category or "custom"
        tool.provider = provider_id
        tool.handler = dict(tool.handler or {})
        tool.handler.setdefault("type", "http")
        if "method" in tool.handler and isinstance(tool.handler["method"], str):
            tool.handler["method"] = tool.handler["method"].upper()
        else:
            tool.handler["method"] = "GET"

        if tool.response and "response" not in tool.handler:
            tool.handler["response"] = tool.response
        tool.response = None

        if isinstance(tool.inputSchema, dict):
            tool.inputSchema.setdefault("type", "object")
            tool.inputSchema.setdefault("properties", {})
            _normalize_required_from_property_flags(tool.inputSchema)

        url = tool.handler.get("url")
        if isinstance(url, str) and isinstance(provider.defaults.get("base_url"), str):
            base = provider.defaults["base_url"].rstrip("/")
            if base and url.startswith(base + "/"):
                tool.handler["url"] = "{base_url}" + url[len(base):]

    return draft


def validate_api_tool_draft(draft: APIToolDraft, *, check_collisions: bool = True) -> list[DraftValidationIssue]:
    from flocks.tool.tool_loader import find_yaml_tool

    issues: list[DraftValidationIssue] = []
    provider = draft.provider

    if not provider.id or safe_component(provider.id, "") != provider.id:
        issues.append(DraftValidationIssue(path="provider.id", message="provider.id 必须是安全的路径组件"))

    base_url = provider.defaults.get("base_url") if isinstance(provider.defaults, dict) else None
    if not isinstance(base_url, str) or not base_url.strip():
        issues.append(DraftValidationIssue(path="provider.defaults.base_url", message="provider.defaults.base_url 不能为空"))
    elif not (base_url.startswith("http://") or base_url.startswith("https://")):
        issues.append(DraftValidationIssue(path="provider.defaults.base_url", message="base_url 必须以 http:// 或 https:// 开头"))

    if isinstance(provider.auth, dict):
        inject_as = provider.auth.get("inject_as")
        if inject_as and inject_as not in {"header", "query_param"}:
            issues.append(DraftValidationIssue(path="provider.auth.inject_as", message="auth.inject_as 必须是 header 或 query_param"))

    if not draft.tools:
        issues.append(DraftValidationIssue(path="tools", message="至少需要一个工具草稿"))

    seen_tool_names: set[str] = set()
    for index, tool in enumerate(draft.tools):
        prefix = f"tools[{index}]"
        if not tool.name or safe_component(tool.name, "") != tool.name:
            issues.append(DraftValidationIssue(path=f"{prefix}.name", message="工具名必须是安全的 snake_case 组件"))
        if tool.name in seen_tool_names:
            issues.append(DraftValidationIssue(path=f"{prefix}.name", message=f"工具名重复：{tool.name}"))
        seen_tool_names.add(tool.name)
        if check_collisions and tool.name and find_yaml_tool(tool.name):
            issues.append(DraftValidationIssue(path=f"{prefix}.name", message=f"工具 '{tool.name}' 已存在"))

        handler = tool.handler if isinstance(tool.handler, dict) else {}
        handler_type = handler.get("type", "http")
        if handler_type != "http":
            issues.append(DraftValidationIssue(path=f"{prefix}.handler.type", message="草稿创建阶段仅支持 http handler"))

        method = str(handler.get("method") or "GET").upper()
        url = handler.get("url")
        if not isinstance(url, str) or not url.strip():
            issues.append(DraftValidationIssue(path=f"{prefix}.handler.url", message="handler.url 不能为空"))
        elif not (url.startswith("http://") or url.startswith("https://") or url.startswith("{base_url}")):
            issues.append(DraftValidationIssue(path=f"{prefix}.handler.url", message="handler.url 必须以 http://、https:// 或 {base_url} 开头"))

        if method in {"DELETE", "PATCH", "PUT"}:
            issues.append(DraftValidationIssue(
                severity="warning",
                path=f"{prefix}.requires_confirmation",
                message=f"{method} 工具通常应开启执行前确认",
            ))

        if isinstance(tool.inputSchema, dict):
            if tool.inputSchema.get("type") != "object":
                issues.append(DraftValidationIssue(path=f"{prefix}.inputSchema.type", message="inputSchema.type 必须是 object"))
            properties = tool.inputSchema.get("properties")
            if not isinstance(properties, dict):
                issues.append(DraftValidationIssue(path=f"{prefix}.inputSchema.properties", message="inputSchema.properties 必须是对象"))
            required = tool.inputSchema.get("required", [])
            if required is not None and not isinstance(required, list):
                issues.append(DraftValidationIssue(path=f"{prefix}.inputSchema.required", message="inputSchema.required 必须是数组"))
            elif isinstance(properties, dict):
                for item in required or []:
                    if item not in properties:
                        issues.append(DraftValidationIssue(path=f"{prefix}.inputSchema.required", message=f"必填字段 '{item}' 未在 properties 中声明"))
        elif not tool.parameters:
            issues.append(DraftValidationIssue(
                severity="warning",
                path=f"{prefix}.inputSchema",
                message="工具未声明参数",
            ))

        declared = _declared_parameter_names(tool)
        for template in _iter_template_strings(handler):
            for match in _PLACEHOLDER_PATTERN.finditer(template):
                placeholder = match.group(1)
                if placeholder == "base_url" or placeholder.startswith("secret:") or placeholder.startswith("user:"):
                    continue
                if placeholder not in declared:
                    issues.append(DraftValidationIssue(
                        path=f"{prefix}.handler",
                        message=f"占位符 '{{{placeholder}}}' 未声明为工具参数",
                    ))

    return issues


def has_validation_errors(issues: list[DraftValidationIssue]) -> bool:
    return any(issue.severity == "error" for issue in issues)


def compile_provider_yaml(provider: ProviderDraft) -> dict[str, Any]:
    data = _copy_non_empty(
        provider.model_dump(),
        [
            "name",
            "service_id",
            "description",
            "description_cn",
            "auth",
            "defaults",
            "credential_fields",
            "compound_secret",
            "docs_url",
        ],
    )
    data.setdefault("name", provider.name or provider.id)
    data.setdefault("description", provider.description or f"{provider.name or provider.id} API service")
    data.setdefault("defaults", provider.defaults or {})
    return data


def compile_tool_yaml(tool: ToolDraft) -> dict[str, Any]:
    data = _copy_non_empty(
        tool.model_dump(),
        [
            "name",
            "description",
            "description_cn",
            "category",
            "enabled",
            "requires_confirmation",
            "provider",
            "inputSchema",
            "parameters",
            "handler",
        ],
    )
    data.setdefault("category", "custom")
    data.setdefault("enabled", True)
    data.setdefault("requires_confirmation", False)
    return data
