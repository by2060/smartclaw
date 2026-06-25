from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


_ALLOWED_AUTH_TYPES = {"smart", "iam6", "bearerToken", "basicAuth", "custom"}
_ALLOWED_AUTH_EXT_INJECT_AS = {"header", "query_param", "body"}
# _MIN_HTTP_STATUS = 100
# _MAX_HTTP_STATUS = 599


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
    authType: Optional[str] = None
    auth: Optional[dict[str, Any]] = None
    authExt: Optional[list[dict[str, Any]]] = None
    customAuth: Optional[dict[str, Any]] = None
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
    is_api_related: bool = True
    provider: ProviderDraft
    tools: list[ToolDraft] = Field(default_factory=list)


class NonAPIToolDraftResult(BaseModel):
    is_api_related: Literal[False] = False
    irrelevant_reason: str

    @model_validator(mode="after")
    def validate_result(self) -> "NonAPIToolDraftResult":
        if not self.irrelevant_reason.strip():
            raise ValueError("Non-API draft generation results must include irrelevant_reason")
        return self


APIToolDraftGenerationResult = APIToolDraft | NonAPIToolDraftResult

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
    provider_id = str(provider.id or provider.name or "api_service").strip()
    provider.id = provider_id
    provider.name = provider.name or provider_id
    provider.service_id = provider_id
    provider.defaults = dict(provider.defaults or {})
    provider.defaults.setdefault("category", provider.category or "custom")
    provider.defaults.setdefault("timeout", 30)

    base_url = provider.defaults.get("base_url")
    if isinstance(base_url, str):
        provider.defaults["base_url"] = base_url.rstrip("/")

    for tool in draft.tools:
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


def _validate_http_response_config(
    response_cfg: Any,
    *,
    path: str,
    issues: list[DraftValidationIssue],
) -> None:
    if response_cfg is None:
        return
    if not isinstance(response_cfg, dict):
        issues.append(DraftValidationIssue(path=path, message="handler.response 必须是对象"))
        return

    error_mapping = response_cfg.get("error_mapping")
    if error_mapping is None:
        return
    if not isinstance(error_mapping, dict):
        issues.append(DraftValidationIssue(path=f"{path}.error_mapping", message="error_mapping 必须是对象"))
        return

    for raw_status in error_mapping.keys():
        status_path = f"{path}.error_mapping.{raw_status}"
        try:
            if isinstance(raw_status, bool):
                raise ValueError
            status_code = int(raw_status.strip() if isinstance(raw_status, str) else raw_status)
        except (TypeError, ValueError):
            issues.append(DraftValidationIssue(
                path=status_path,
                message="HTTP 状态码必须是数字，例如 400、401、500",
            ))
            continue
        # if status_code < _MIN_HTTP_STATUS or status_code > _MAX_HTTP_STATUS:
        #     issues.append(DraftValidationIssue(
        #         path=status_path,
        #         message="HTTP 状态码必须在 100-599 范围内",
        #     ))


def validate_api_tool_draft(draft: APIToolDraft, *, check_collisions: bool = True) -> list[DraftValidationIssue]:
    from flocks.tool.tool_loader import find_yaml_tool

    issues: list[DraftValidationIssue] = []
    provider = draft.provider

    if not provider.id:
        issues.append(DraftValidationIssue(path="provider.id", message="provider.id 不能为空"))

    base_url = provider.defaults.get("base_url") if isinstance(provider.defaults, dict) else None
    if not isinstance(base_url, str) or not base_url.strip():
        issues.append(DraftValidationIssue(path="provider.defaults.base_url", message="provider.defaults.base_url 不能为空"))
    elif not (base_url.startswith("http://") or base_url.startswith("https://")):
        issues.append(DraftValidationIssue(path="provider.defaults.base_url", message="base_url 必须以 http:// 或 https:// 开头"))

    auth_type = provider.authType
    if auth_type is not None:
        if auth_type not in _ALLOWED_AUTH_TYPES:
            issues.append(DraftValidationIssue(
                path="provider.authType",
                message="authType 必须是 smart、iam6、bearerToken、basicAuth 或 custom",
            ))
        elif auth_type in {"smart", "iam6"}:
            auth_ext = provider.authExt
            if not isinstance(auth_ext, list) or not auth_ext:
                issues.append(DraftValidationIssue(path="provider.authExt", message="smart/iam6 认证必须配置 authExt"))
            else:
                has_authorization = False
                for ext_index, item in enumerate(auth_ext):
                    ext_path = f"provider.authExt[{ext_index}]"
                    if not isinstance(item, dict):
                        issues.append(DraftValidationIssue(path=ext_path, message="authExt 条目必须是对象"))
                        continue
                    inject_as = item.get("inject_as", "header")
                    if inject_as not in _ALLOWED_AUTH_EXT_INJECT_AS:
                        issues.append(DraftValidationIssue(path=f"{ext_path}.inject_as", message="authExt.inject_as 必须是 header、query_param 或 body"))
                    # if inject_as == "header":
                    #     header_name = item.get("key")
                    #     header_value = item.get("value")
                        # if isinstance(header_name, str) and header_name.lower() == "authorization" and isinstance(header_value, str) and header_value.strip():
                        #     has_authorization = True
                # if not has_authorization:
                #     issues.append(DraftValidationIssue(path="provider.authExt", message="smart/iam6 认证必须通过 authExt 注入 Authorization header"))
        elif auth_type == "bearerToken" and not isinstance(provider.auth, dict):
            issues.append(DraftValidationIssue(path="provider.auth", message="bearerToken 认证必须配置 auth"))
        elif auth_type == "basicAuth" and not isinstance(provider.credential_fields, list):
            issues.append(DraftValidationIssue(path="provider.credential_fields", message="basicAuth 认证必须配置 credential_fields"))
        elif auth_type == "custom":
            issues.append(DraftValidationIssue(
                severity="warning",
                path="provider.customAuth",
                message="customAuth 当前仅保留配置，不执行自定义认证程序",
            ))

    if isinstance(provider.auth, dict):
        inject_as = provider.auth.get("inject_as")
        if inject_as and inject_as not in {"header", "query_param"}:
            issues.append(DraftValidationIssue(path="provider.auth.inject_as", message="auth.inject_as 必须是 header 或 query_param"))

    if isinstance(provider.authExt, list):
        for ext_index, item in enumerate(provider.authExt):
            ext_path = f"provider.authExt[{ext_index}]"
            if not isinstance(item, dict):
                issues.append(DraftValidationIssue(path=ext_path, message="authExt 条目必须是对象"))
                continue
            inject_as = item.get("inject_as", "header")
            if inject_as not in _ALLOWED_AUTH_EXT_INJECT_AS:
                issues.append(DraftValidationIssue(path=f"{ext_path}.inject_as", message="authExt.inject_as 必须是 header、query_param 或 body"))
            auth_ext_key = item.get("key")
            auth_ext_value = item.get("value")
            if not isinstance(auth_ext_key, str) or not auth_ext_key.strip():
                issues.append(DraftValidationIssue(path=f"{ext_path}.key", message="authExt 必须配置 key"))
            # 不校验 value 是否为空
            # if not isinstance(auth_ext_value, str) or not auth_ext_value.strip():
            #     issues.append(DraftValidationIssue(path=f"{ext_path}.value", message="authExt 必须配置 SM4 密文 value"))

    if not draft.tools:
        issues.append(DraftValidationIssue(path="tools", message="至少需要一个工具草稿"))

    seen_tool_names: set[str] = set()
    for index, tool in enumerate(draft.tools):
        prefix = f"tools[{index}]"
        if not tool.name:
            issues.append(DraftValidationIssue(path=f"{prefix}.name", message="工具名不能为空"))
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

        _validate_http_response_config(
            handler.get("response"),
            path=f"{prefix}.handler.response",
            issues=issues,
        )

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
            "authType",
            "auth",
            "authExt",
            "customAuth",
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
