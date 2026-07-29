"""
YAML-based tool loader.

Converts a YAML config dict into a :class:`Tool` instance, supporting:
- ``inputSchema``: MCP-compatible JSON Schema parameter definition
- ``parameters``: simplified parameter list (auto-converted to JSON Schema)
- ``handler.type=http``: declarative HTTP request handler
- ``handler.type=script``: external Python script handler

Used as the ``yaml_item_factory`` for the ``TOOLS`` extension point
in :mod:`flocks.plugin.loader`.
"""

from __future__ import annotations

import ast
import datetime as _dt
import importlib.util
import inspect
import os
import re
import shutil
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from flocks.plugin.loader import DEFAULT_PLUGIN_ROOT
from flocks.tool.auth_crypto import decrypt_sm4_value as _decrypt_sm4_value
from flocks.tool.registry import (
    ParameterType,
    Tool,
    ToolCategory,
    ToolContext,
    ToolHandler,
    ToolInfo,
    ToolParameter,
    ToolResult,
)
from flocks.tool.smart_auth import SmartAuthBinding, SmartAuthError, build_smart_auth_binding
from flocks.utils.log import Log

log = Log.create(service="tool.loader")

_DEFAULT_TOOLS_SUBDIR = DEFAULT_PLUGIN_ROOT / "tools"
_TOOLS_SUBDIR = _DEFAULT_TOOLS_SUBDIR
_PROVIDER_FILENAME = "_provider.yaml"
_SECRET_PATTERN = re.compile(r"\{secret:([^}]+)\}")
_USER_PATTERN = re.compile(r"\{user:([^}]+)\}")
_SM4_PATTERN = re.compile(r"\{sm4:([^}]+)\}")
_PARAM_PATTERN = re.compile(r"\{([^}]+)\}")
_EXACT_PARAM_PATTERN = re.compile(r"^\{([^}]+)\}$")
_MISSING = object()
_FS_SAFE_RE = re.compile(r"[^A-Za-z0-9._\-]+")
_FILENAME_STAR_RE = re.compile(r"filename\*\s*=\s*(?:UTF-8''|\"UTF-8'')?([^;\"]+)", re.IGNORECASE)
_FILENAME_RE = re.compile(r"filename\s*=\s*\"?([^\";]+)\"?", re.IGNORECASE)

_BINARY_CONTENT_TYPES = {
    "application/octet-stream",
    "application/pdf",
    "application/zip",
    "application/x-zip-compressed",
    "application/gzip",
    "application/x-gzip",
    "application/x-tar",
    "application/x-7z-compressed",
    "application/vnd.tcpdump.pcap",
    "application/pcap",
    "application/x-pcap",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_BINARY_CONTENT_TYPE_PREFIXES = (
    "image/",
    "audio/",
    "video/",
    "font/",
)
_CONTENT_TYPE_EXTENSIONS = {
    "application/octet-stream": ".bin",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/gzip": ".gz",
    "application/x-gzip": ".gz",
    "application/x-tar": ".tar",
    "application/x-7z-compressed": ".7z",
    "application/vnd.tcpdump.pcap": ".pcap",
    "application/pcap": ".pcap",
    "application/x-pcap": ".pcap",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
}

# ---------------------------------------------------------------------------
# Tool type constants — each type maps to a subdirectory under _TOOLS_SUBDIR
# ---------------------------------------------------------------------------

TOOL_TYPE_MCP = "mcp"
"""MCP server configurations (type: local/remote). Managed by MCP subsystem."""

TOOL_TYPE_API = "api"
"""YAML-based HTTP/script tools (handler.type: http|script)."""

TOOL_TYPE_PYTHON = "python"
"""Python code tools using @ToolRegistry.register_function."""

TOOL_TYPE_GENERATED = "generated"
"""Auto-generated tools from API specs. Supports hot-reload."""

ALL_TOOL_TYPES = (TOOL_TYPE_MCP, TOOL_TYPE_API, TOOL_TYPE_PYTHON, TOOL_TYPE_GENERATED)

# 插件输出和执行结果输出新增
def _project_tools_root() -> Path:
    """Return the project-level tools plugin root for newly created tools."""
    if _TOOLS_SUBDIR != _DEFAULT_TOOLS_SUBDIR:
        return _TOOLS_SUBDIR
    from flocks.project.instance import Instance

    project_dir = Instance.get_directory() or os.getcwd()
    return Path(project_dir) / ".flocks" / "plugins" / "tools"


# ---------------------------------------------------------------------------
# Provider config
# ---------------------------------------------------------------------------

def _load_provider_config(yaml_path: Path) -> Optional[Dict[str, Any]]:
    """Load ``_provider.yaml`` from the same directory if it exists."""
    provider_file = yaml_path.parent / _PROVIDER_FILENAME
    if not provider_file.is_file():
        return None
    try:
        data = yaml.safe_load(provider_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:
        log.warn("tool.provider.load_failed", {
            "path": str(provider_file), "error": str(e),
        })
        return None


def extract_provider_version(provider_cfg: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract a provider/service version string from a ``_provider.yaml`` dict.

    Lookup order (first non-null wins):
    1. top-level ``version``
    2. ``defaults.product_version``
    3. ``defaults.version``

    Always coerces the result to ``str`` so YAML-decoded floats like ``9.2``
    (which would otherwise round-trip lossy) are stable.
    Returns ``None`` when no version is declared anywhere.
    """
    if not isinstance(provider_cfg, dict):
        return None
    raw = provider_cfg.get("version")
    if raw is None:
        defaults = provider_cfg.get("defaults") or {}
        if isinstance(defaults, dict):
            raw = defaults.get("product_version") or defaults.get("version")
    if raw is None:
        return None
    return str(raw)


def _merge_provider_defaults(raw: dict, provider: Optional[Dict[str, Any]]) -> dict:
    """Apply provider defaults (base_url, timeout, category, auth) to a tool config."""
    if provider is None:
        return raw

    defaults = provider.get("defaults", {})

    if "category" not in raw and "category" in defaults:
        raw["category"] = defaults["category"]

    handler = raw.get("handler")
    if not isinstance(handler, dict):
        return raw

    if handler.get("type") == "http":
        if "timeout" not in handler and "timeout" in defaults:
            handler["timeout"] = defaults["timeout"]
        if "verify_ssl" not in handler and "verify_ssl" in defaults:
            handler["verify_ssl"] = defaults["verify_ssl"]

        base_url = defaults.get("base_url", "")
        url = handler.get("url", "")
        if base_url and "{base_url}" in url:
            handler["url"] = url.replace("{base_url}", base_url.rstrip("/"))

        auth_type = provider.get("authType")
        auth = provider.get("auth")
        if auth_type == "smartAuth":
            handler["_smart_auth_binding"] = build_smart_auth_binding(provider)
        elif auth_type in {"smart", "iam6"}:
            _inject_provider_auth_ext(handler, provider.get("authExt"))
        elif auth_type == "bearerToken":
            if auth:
                _inject_provider_auth(handler, auth)
        elif auth_type == "basicAuth":
            _inject_provider_basic_auth(handler, provider.get("credential_fields"))
        elif not auth_type and auth:
            _inject_provider_auth(handler, auth)

    raw["handler"] = handler
    return raw


def _sm4_template(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    return f"{{sm4:{value.strip()}}}"


def _inject_provider_auth(handler: dict, auth: Dict[str, Any]) -> None:
    """Inject provider-level auth into handler headers or query params."""
    secret_ref = auth.get("secret")
    user_ref = auth.get("user")
    encrypted_value = auth.get("header_value") or auth.get("value") or auth.get("config_value")
    auth_value = _sm4_template(encrypted_value)
    if not auth_value:
        if user_ref:
            auth_value = f"{{user:{user_ref}}}"
        elif secret_ref:
            auth_value = f"{{secret:{secret_ref}}}"
        else:
            return

    inject_as = auth.get("inject_as", "header")

    if inject_as == "header":
        header_name = auth.get("header_name", "Authorization")
        prefix = auth.get("header_prefix", "Bearer ")
        headers = handler.setdefault("headers", {})
        if header_name not in headers:
            headers[header_name] = f"{prefix}{auth_value}"
    elif inject_as == "query_param":
        param_name = auth.get("param_name", "api_key")
        query_params = handler.setdefault("query_params", {})
        if param_name not in query_params:
            query_params[param_name] = auth_value


def _inject_provider_auth_ext(handler: dict, auth_ext: Any) -> None:
    if not isinstance(auth_ext, list):
        return
    for item in auth_ext:
        if not isinstance(item, dict):
            continue
        inject_as = item.get("inject_as", "header")
        encrypted_value = item.get("value")
        auth_value = _sm4_template(encrypted_value)
        if not auth_value:
            continue
        if inject_as == "header":
            header_name = item.get("key")
            if not isinstance(header_name, str) or not header_name.strip():
                continue
            headers = handler.setdefault("headers", {})
            headers.setdefault(header_name.strip(), auth_value)
        elif inject_as == "query_param":
            param_name = item.get("key")
            if not isinstance(param_name, str) or not param_name.strip():
                continue
            query_params = handler.setdefault("query_params", {})
            query_params.setdefault(param_name.strip(), auth_value)
        elif inject_as == "body":
            param_name = item.get("key")
            if not isinstance(param_name, str) or not param_name.strip():
                continue
            body = handler.setdefault("body", {})
            if isinstance(body, dict):
                body.setdefault(param_name.strip(), auth_value)


def _basic_auth_field_value(field: dict[str, Any]) -> Optional[str]:
    secret_ref = field.get("secret_id") or field.get("secret")
    encrypted_value = field.get("config_value") or field.get("value")
    if encrypted_value:
        return _sm4_template(encrypted_value)
    if secret_ref:
        return f"{{secret:{secret_ref}}}"
    return None


def _inject_provider_basic_auth(handler: dict, credential_fields: Any) -> None:
    if not isinstance(credential_fields, list) or "basic_auth" in handler:
        return

    values: dict[str, str] = {}
    for field in credential_fields:
        if not isinstance(field, dict):
            continue
        key = field.get("config_key") or field.get("key")
        value = _basic_auth_field_value(field)
        if isinstance(key, str) and key.strip() and value:
            values[key.strip()] = value

    username = values.get("username") or values.get("user")
    password = values.get("password") or values.get("pass")
    if username and password:
        handler["basic_auth"] = {"username": username, "password": password}


# ---------------------------------------------------------------------------
# Secret resolution
# ---------------------------------------------------------------------------

def _resolve_secrets(value: str) -> str:
    """Replace ``{secret:key}`` placeholders with actual secret values."""
    def _replacer(match: re.Match) -> str:
        secret_id = match.group(1)
        try:
            from flocks.security import get_secret_manager, resolve_secret_value
            secret_value = resolve_secret_value(secret_id, get_secret_manager())
            if secret_value:
                return secret_value
        except Exception:
            pass
        log.warn("tool.secret.not_found", {"secret_id": secret_id})
        return match.group(0)

    return _SECRET_PATTERN.sub(_replacer, value)


def _resolve_sm4_values(value: str) -> str:
    def _replacer(match: re.Match) -> str:
        return _decrypt_sm4_value(match.group(1))

    return _SM4_PATTERN.sub(_replacer, value)


def _as_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"false", "0", "no", "off"}:
            return False
        if text in {"true", "1", "yes", "on"}:
            return True
    return default


def _substitute_params(
    template: str,
    params: Dict[str, Any],
    url_encode: bool = False,
    user_context: Optional[Dict[str, Any]] = None,
    missing_user_keys: Optional[List[str]] = None,
) -> str:
    """Replace ``{param_name}`` placeholders with actual parameter values.

    SM4 values are decrypted first, then secrets, user context, and parameter placeholders.
    """
    result = _resolve_secrets(_resolve_sm4_values(template))

    def _user_replacer(match: re.Match) -> str:
        key = match.group(1)
        effective_user_context = user_context or {}
        value = effective_user_context.get(key)
        if value is None:
            if missing_user_keys is not None:
                missing_user_keys.append(key)
            return match.group(0)
        text = str(value)
        return urllib.parse.quote(text, safe="") if url_encode else text

    result = _USER_PATTERN.sub(_user_replacer, result)

    def _replacer(match: re.Match) -> str:
        key = match.group(1)
        if key.startswith("secret:") or key.startswith("user:"):
            return match.group(0)
        value = params.get(key)
        if value is None:
            return ""
        text = str(value)
        return urllib.parse.quote(text, safe="") if url_encode else text

    return _PARAM_PATTERN.sub(_replacer, result)


def _render_template_value(
    value: Any,
    params: Dict[str, Any],
    *,
    user_context: Optional[Dict[str, Any]] = None,
    missing_user_keys: Optional[List[str]] = None,
) -> Any:
    """Render a YAML template value while preserving exact JSON values.

    A body field like ``query: "{query}"`` should forward the original dict or
    list instead of stringifying it. Mixed templates such as ``"id-{id}"`` keep
    the existing string substitution behavior.
    """
    if isinstance(value, str):
        exact = _EXACT_PARAM_PATTERN.match(value)
        if exact:
            key = exact.group(1)
            if key.startswith("secret:") or key.startswith("user:") or key.startswith("sm4:"):
                return _substitute_params(
                    value,
                    params,
                    user_context=user_context,
                    missing_user_keys=missing_user_keys,
                )
            if key not in params or params[key] is None:
                return _MISSING
            return params[key]

        return _substitute_params(
            value,
            params,
            user_context=user_context,
            missing_user_keys=missing_user_keys,
        )

    if isinstance(value, dict):
        rendered: Dict[str, Any] = {}
        for k, v in value.items():
            item = _render_template_value(
                v,
                params,
                user_context=user_context,
                missing_user_keys=missing_user_keys,
            )
            if item is not _MISSING:
                rendered[k] = item
        return rendered

    if isinstance(value, list):
        rendered_list: List[Any] = []
        for item in value:
            rendered = _render_template_value(
                item,
                params,
                user_context=user_context,
                missing_user_keys=missing_user_keys,
            )
            if rendered is not _MISSING:
                rendered_list.append(rendered)
        return rendered_list

    return value


# ---------------------------------------------------------------------------
# inputSchema normalization
# ---------------------------------------------------------------------------

def _normalize_input_schema(raw: dict) -> List[ToolParameter]:
    """Convert ``inputSchema`` (JSON Schema) or ``parameters`` list into ToolParameters.

    Supports two formats:
    1. MCP-compatible ``inputSchema`` (preferred)::

        inputSchema:
          type: object
          properties:
            ip: {type: string, description: "IP address"}
          required: [ip]

    2. Simplified ``parameters`` list (sugar)::

        parameters:
          - name: ip
            type: string
            description: IP address
            required: true
    """
    input_schema = raw.get("inputSchema")
    if isinstance(input_schema, dict):
        return _json_schema_to_params(input_schema)

    params_list = raw.get("parameters")
    if isinstance(params_list, list):
        return _params_list_to_params(params_list)

    return []


_TYPE_MAP = {
    "string": ParameterType.STRING,
    "integer": ParameterType.INTEGER,
    "number": ParameterType.NUMBER,
    "boolean": ParameterType.BOOLEAN,
    "array": ParameterType.ARRAY,
    "object": ParameterType.OBJECT,
}


def _json_schema_to_params(schema: dict) -> List[ToolParameter]:
    """Convert a JSON Schema ``properties`` dict to ToolParameter list."""
    properties = schema.get("properties", {})
    required_set = set(schema.get("required", []))
    result = []
    for name, prop in properties.items():
        json_type = prop.get("type", "string")
        json_schema = dict(prop) if json_type in {"object", "array"} else None
        result.append(ToolParameter(
            name=name,
            type=_TYPE_MAP.get(json_type, ParameterType.STRING),
            description=prop.get("description", ""),
            required=name in required_set,
            default=prop.get("default"),
            enum=prop.get("enum"),
            json_schema=json_schema,
        ))
    return result


def _params_list_to_params(params_list: list) -> List[ToolParameter]:
    """Convert a simplified parameter list to ToolParameter list."""
    result = []
    for p in params_list:
        if not isinstance(p, dict) or "name" not in p:
            continue
        json_type = p.get("type", "string")
        result.append(ToolParameter(
            name=p["name"],
            type=_TYPE_MAP.get(json_type, ParameterType.STRING),
            description=p.get("description", ""),
            required=p.get("required", True),
            default=p.get("default"),
            enum=p.get("enum"),
        ))
    return result


# ---------------------------------------------------------------------------
# Handler builders
# ---------------------------------------------------------------------------

def _build_handler(raw_handler: dict, yaml_path: Path) -> ToolHandler:
    """Build a ToolHandler from the ``handler`` section of a YAML config."""
    handler_type = raw_handler.get("type", "http")

    if handler_type == "http":
        return _build_http_handler(raw_handler)
    elif handler_type == "script":
        return _build_script_handler(raw_handler, yaml_path)
    else:
        raise ValueError(f"Unknown handler type: {handler_type}")


def _response_header(resp: Any, name: str) -> str:
    headers = getattr(resp, "headers", None) or {}
    for key in (name, name.lower(), name.upper()):
        try:
            value = headers.get(key)
        except AttributeError:
            return ""
        if inspect.isawaitable(value):
            close = getattr(value, "close", None)
            if callable(close):
                close()
            return ""
        if value:
            return str(value)
    return ""


def _normalized_content_type(resp: Any) -> str:
    content_type = _response_header(resp, "Content-Type").strip().lower()
    return content_type.split(";", 1)[0].strip()


def _is_json_content_type(content_type: str) -> bool:
    return content_type == "application/json" or content_type.endswith("+json")


def _is_text_content_type(content_type: str) -> bool:
    return (
        content_type.startswith("text/")
        or content_type in {"application/xml", "application/xhtml+xml", "application/javascript"}
        or content_type.endswith("+xml")
    )


def _is_file_response_by_headers(resp: Any) -> bool:
    disposition = _response_header(resp, "Content-Disposition").lower()
    if "attachment" in disposition or "filename=" in disposition or "filename*" in disposition:
        return True

    content_type = _normalized_content_type(resp)
    if not content_type or _is_json_content_type(content_type) or _is_text_content_type(content_type):
        return False
    if content_type in _BINARY_CONTENT_TYPES:
        return True
    return any(content_type.startswith(prefix) for prefix in _BINARY_CONTENT_TYPE_PREFIXES)


def _sanitize_download_filename(filename: str) -> str:
    from pathlib import PurePosixPath

    base = PurePosixPath(str(filename).replace("\\", "/")).name
    safe = _FS_SAFE_RE.sub("_", base).strip("._-")
    return safe or "download"


def _filename_from_content_disposition(disposition: str) -> Optional[str]:
    if not disposition:
        return None
    match = _FILENAME_STAR_RE.search(disposition)
    if match:
        return urllib.parse.unquote(match.group(1).strip().strip('"'))
    match = _FILENAME_RE.search(disposition)
    if match:
        return urllib.parse.unquote(match.group(1).strip().strip('"'))
    return None


def _extension_for_content_type(content_type: str) -> str:
    if content_type in _CONTENT_TYPE_EXTENSIONS:
        return _CONTENT_TYPE_EXTENSIONS[content_type]
    if content_type.startswith("image/"):
        subtype = content_type.split("/", 1)[1].split("+", 1)[0]
        return f".{subtype}" if subtype else ""
    return ""


def _filename_from_response(resp: Any, url: str) -> str:
    disposition = _response_header(resp, "Content-Disposition")
    from_disposition = _filename_from_content_disposition(disposition)
    if from_disposition:
        return _sanitize_download_filename(from_disposition)

    parsed = urllib.parse.urlparse(url)
    path_name = Path(urllib.parse.unquote(parsed.path or "")).name
    if path_name:
        safe = _sanitize_download_filename(path_name)
        if not Path(safe).suffix:
            safe = f"{safe}{_extension_for_content_type(_normalized_content_type(resp))}"
        return safe

    ext = _extension_for_content_type(_normalized_content_type(resp))
    return f"download{ext}"


def _effective_output_session_id(ctx: ToolContext) -> str:
    for key in ("output_session_id", "main_session_key"):
        value = ctx.extra.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if isinstance(ctx.session_id, str) and ctx.session_id.strip():
        return ctx.session_id.strip()
    return "default-session"


def _api_tool_output_dir(ctx: ToolContext) -> Path:
    from flocks.workspace.manager import WorkspaceManager

    return WorkspaceManager.get_instance().get_outputs_dir(
        _effective_output_session_id(ctx),
        day=_dt.date.today(),
    )


def _unique_output_path(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_download_filename(filename)
    target = directory / safe_name
    if not target.exists():
        return target
    stem = target.stem or "download"
    suffix = target.suffix
    timestamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    candidate = directory / f"{stem}_{timestamp}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{timestamp}_{counter}{suffix}"
        counter += 1
    return candidate


async def _write_response_file(resp: Any, target: Path) -> int:
    size = 0
    content = getattr(resp, "content", None)
    iter_chunked = getattr(content, "iter_chunked", None)
    with target.open("wb") as fh:
        if callable(iter_chunked):
            chunks = iter_chunked(1024 * 1024)
            if hasattr(chunks, "__aiter__"):
                async for chunk in chunks:
                    if not chunk:
                        continue
                    fh.write(chunk)
                    size += len(chunk)
                return size
            if inspect.isawaitable(chunks):
                close = getattr(chunks, "close", None)
                if callable(close):
                    close()
        data = await resp.read()
        fh.write(data)
        size = len(data)
    return size


async def _save_file_response(resp: Any, url: str, ctx: ToolContext) -> dict[str, Any]:
    filename = _filename_from_response(resp, url)
    output_dir = _api_tool_output_dir(ctx)
    target = _unique_output_path(output_dir, filename)
    size = await _write_response_file(resp, target)
    return {
        "type": "file",
        "saved_path": str(target),
        "filename": target.name,
        "content_type": _response_header(resp, "Content-Type"),
        "size": size,
    }


def _build_http_handler(cfg: dict) -> ToolHandler:
    """Build an async HTTP request handler from declarative config."""
    method = cfg.get("method", "GET").upper()
    url_template = cfg.get("url", "")
    headers_template = cfg.get("headers", {})
    query_params_template = cfg.get("query_params", {})
    body_template = cfg.get("body")
    basic_auth_template = cfg.get("basic_auth")
    smart_auth_binding = cfg.get("_smart_auth_binding")
    timeout = cfg.get("timeout", 30)
    verify_ssl = _as_bool(cfg.get("verify_ssl", False), default=False)
    response_cfg = cfg.get("response", {})
    if not response_cfg:
        extract_path = cfg.get("response_path")
        error_mapping: Dict[int, str] = {}
    else:
        extract_path = response_cfg.get("extract") or cfg.get("response_path")
        error_mapping = {int(k): v for k, v in response_cfg.get("error_mapping", {}).items()}

    async def handler(ctx: ToolContext, **kwargs: Any) -> ToolResult:
        import aiohttp

        user_context = ctx.extra.get("user_context")
        if not isinstance(user_context, dict):
            user_context = {}
        missing_user_keys: List[str] = []

        try:
            url = _substitute_params(
                url_template,
                kwargs,
                url_encode=False,
                user_context=user_context,
                missing_user_keys=missing_user_keys,
            )
            headers = {
                k: _substitute_params(
                    v,
                    kwargs,
                    user_context=user_context,
                    missing_user_keys=missing_user_keys,
                )
                for k, v in headers_template.items()
            }
            query_params = {
                k: _substitute_params(
                    v,
                    kwargs,
                    user_context=user_context,
                    missing_user_keys=missing_user_keys,
                )
                for k, v in query_params_template.items()
            }
            query_params = {k: v for k, v in query_params.items() if v}

            body = None
            if body_template is not None:
                import json as _json

                body_obj = _render_template_value(
                    body_template,
                    kwargs,
                    user_context=user_context,
                    missing_user_keys=missing_user_keys,
                )
                if body_obj is not _MISSING:
                    body = _json.dumps(body_obj, ensure_ascii=False)
                    headers.setdefault("Content-Type", "application/json")
        except ValueError as e:
            return ToolResult(success=False, error=str(e))

        if missing_user_keys:
            keys = ", ".join(sorted(set(missing_user_keys)))
            return ToolResult(success=False, error=f"Missing user_context values: {keys}")

        try:
            client_timeout = aiohttp.ClientTimeout(total=timeout)
            session_kwargs: Dict[str, Any] = {"timeout": client_timeout}
            basic_auth = None
            if isinstance(basic_auth_template, dict):
                username_template = basic_auth_template.get("username")
                password_template = basic_auth_template.get("password")
                if isinstance(username_template, str) and isinstance(password_template, str):
                    username = _substitute_params(
                        username_template,
                        {},
                        user_context=user_context,
                        missing_user_keys=missing_user_keys,
                    )
                    password = _substitute_params(
                        password_template,
                        {},
                        user_context=user_context,
                        missing_user_keys=missing_user_keys,
                    )
                    if username and password:
                        basic_auth = aiohttp.BasicAuth(username, password)
            async with aiohttp.ClientSession(**session_kwargs) as session:
                req_kwargs: Dict[str, Any] = {"headers": headers}
                if query_params:
                    req_kwargs["params"] = query_params
                if body and method in ("POST", "PUT", "PATCH"):
                    req_kwargs["data"] = body
                if basic_auth is not None:
                    req_kwargs["auth"] = basic_auth
                if not verify_ssl:
                    req_kwargs["ssl"] = False

                smart_auth_token: Optional[str] = None
                if isinstance(smart_auth_binding, SmartAuthBinding):
                    smart_auth_token = await smart_auth_binding.get_token(session)
                    smart_auth_binding.inject(headers, smart_auth_token)

                for request_attempt in range(2):
                    async with session.request(method, url, **req_kwargs) as resp:
                        if (
                            resp.status == 401
                            and isinstance(smart_auth_binding, SmartAuthBinding)
                            and smart_auth_token is not None
                            and request_attempt == 0
                        ):
                            await resp.read()
                            smart_auth_token = await smart_auth_binding.refresh_token(
                                session,
                                smart_auth_token,
                            )
                            smart_auth_binding.inject(headers, smart_auth_token)
                            continue

                        if resp.status >= 400:
                            friendly = error_mapping.get(resp.status)
                            if friendly:
                                return ToolResult(success=False, error=friendly)
                            text = await resp.text()
                            return ToolResult(
                                success=False,
                                error=f"HTTP {resp.status}: {text[:500]}",
                            )

                        if _is_file_response_by_headers(resp):
                            file_output = await _save_file_response(resp, url, ctx)
                            return ToolResult(
                                success=True,
                                output=file_output,
                                metadata={"file": file_output},
                            )

                        data = await resp.json(content_type=None)
                        output = _extract_response(data, extract_path)
                        return ToolResult(success=True, output=output)

        except SmartAuthError as e:
            return ToolResult(success=False, error=str(e))
        except aiohttp.ClientError as e:
            return ToolResult(success=False, error=f"HTTP request failed: {e}")
        except Exception as e:
            return ToolResult(success=False, error=f"Tool execution error: {e}")

    return handler


def _extract_response(data: Any, path: Optional[str]) -> Any:
    """Extract a nested value from response data using a dot-separated path.

    Supports simple dot-notation like ``"data.results"`` (not full jmespath).
    """
    if not path or data is None:
        return data
    for key in path.split("."):
        if isinstance(data, dict):
            data = data.get(key)
        else:
            return data
    return data


def _build_script_handler(cfg: dict, yaml_path: Path) -> ToolHandler:
    """Build a handler that delegates to an external Python script."""
    script_file = cfg.get("script_file", "")
    function_name = cfg.get("function", "handle")

    script_path = (yaml_path.parent / script_file).resolve()

    user_plugins_root = DEFAULT_PLUGIN_ROOT.resolve()
    project_plugins_root = (Path.cwd() / ".flocks" / "plugins").resolve()
    script_str = str(script_path)
    allowed_prefixes = (
        str(user_plugins_root),
        str(project_plugins_root),
    )
    if not any(script_str.startswith(prefix) for prefix in allowed_prefixes):
        raise ValueError(
            f"Script path {script_path} is outside the allowed plugins directories. "
            f"For security, scripts must be under one of: {', '.join(allowed_prefixes)}."
        )

    if not script_path.is_file():
        raise FileNotFoundError(f"Handler script not found: {script_path}")

    spec = importlib.util.spec_from_file_location(
        f"_flocks_tool_handler_{script_path.stem}",
        str(script_path),
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create import spec for {script_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    fn = getattr(module, function_name, None)
    if fn is None:
        raise AttributeError(
            f"Function '{function_name}' not found in {script_path}"
        )

    if not callable(fn):
        raise TypeError(f"'{function_name}' in {script_path} is not callable")

    # Inspect the target function signature once so the wrapper can adapt the
    # invocation to legacy handlers that either:
    #   * read parameters from ``ctx.params`` (signature: ``(ctx) -> ...``); or
    #   * take parameters as explicit keyword arguments / ``**kwargs``.
    #
    # Without this adaptation, callers like the test-credentials flow that
    # invoke ``ToolRegistry.execute(tool_name, **params)`` would either raise
    # ``TypeError: got an unexpected keyword argument`` or
    # ``AttributeError: 'ToolContext' object has no attribute 'params'``.
    fn_sig = inspect.signature(fn)
    fn_params = fn_sig.parameters
    fn_has_var_kw = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in fn_params.values()
    )
    # Skip the first positional argument (always the ``ctx`` we supply
    # ourselves) so a user-provided kwarg named ``ctx`` cannot trigger
    # ``TypeError: got multiple values for argument 'ctx'``.
    _param_items = list(fn_params.items())
    if _param_items and _param_items[0][1].kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.POSITIONAL_ONLY,
    ):
        _param_items = _param_items[1:]
    fn_param_names = {
        name
        for name, p in _param_items
        if p.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }

    async def handler(ctx: ToolContext, **kwargs: Any) -> ToolResult:
        # Always expose the raw kwargs on the context so handlers that read
        # from ``ctx.params`` (e.g. ``params = dict(ctx.params)``) keep working
        # regardless of whether the caller created a fresh ``ToolContext``.
        try:
            ctx.params = kwargs  # type: ignore[attr-defined]
        except AttributeError:
            pass

        if fn_has_var_kw:
            call_kwargs = kwargs
        else:
            call_kwargs = {k: v for k, v in kwargs.items() if k in fn_param_names}

        result = await fn(ctx, **call_kwargs)
        if isinstance(result, ToolResult):
            return result
        return ToolResult(success=True, output=result)

    return handler


def _build_execution_handler(cfg: dict, yaml_path: Path) -> ToolHandler:
    """Build a handler from an ``execution`` section with inline Python code.

    Supports YAML configs that use the alternative format::

        execution:
          type: python
          code: |
            import os
            os.remove(file_path)
            return {"success": True}

    Parameter values are injected as local variables into the code scope.
    The code can ``return`` a value which becomes the tool output.
    """
    exec_type = cfg.get("type", "python")
    if exec_type != "python":
        raise ValueError(f"Unsupported execution type: {exec_type}")

    code = cfg.get("code", "")
    if not code or not code.strip():
        raise ValueError(f"Empty execution code in {yaml_path}")

    async def handler(ctx: ToolContext, **kwargs: Any) -> ToolResult:
        return ToolResult(
            success=False,
            error=(
                "Inline YAML execution is disabled for safety. "
                "Use handler.type=script for trusted Python tool handlers."
            ),
        )

    return handler


# ---------------------------------------------------------------------------
# yaml_item_factory for the TOOLS extension point
# ---------------------------------------------------------------------------

def yaml_to_tool(raw: dict, yaml_path: Path) -> Tool:
    """Convert a parsed YAML dict into a :class:`Tool`.

    This is the ``yaml_item_factory`` wired into the TOOLS extension point.

    Parameters
    ----------
    raw:
        The parsed YAML document (a dict).
    yaml_path:
        Absolute path to the source ``.yaml`` file.

    Raises
    ------
    ValueError
        If ``name`` or ``handler`` is missing.
    """
    name = raw.get("name")
    if not name:
        raise ValueError(f"Tool YAML config missing required 'name' field: {yaml_path}")

    handler_raw = raw.get("handler")
    execution_raw = raw.get("execution")
    if (not handler_raw or not isinstance(handler_raw, dict)) and (
        not execution_raw or not isinstance(execution_raw, dict)
    ):
        raise ValueError(
            f"Tool YAML config missing required 'handler' or 'execution' section: {yaml_path}"
        )

    provider_cfg = _load_provider_config(yaml_path)
    raw = _merge_provider_defaults(raw, provider_cfg)

    service_id = raw.get("provider")
    if not service_id and provider_cfg:
        service_id = provider_cfg.get("name")

    provider_version = extract_provider_version(provider_cfg)

    # ``info.provider`` is the lookup key in ``flocks.json`` ``api_services``.
    # When the plugin declares a version, promote it to a storage key so
    # multiple versions of the same product can keep credentials side-by-side
    # (see :mod:`flocks.config.api_versioning`). Without a version
    # the storage key collapses back to ``service_id`` for full back-compat.
    if service_id and provider_version:
        from flocks.config.api_versioning import derive_storage_key
        storage_key: Optional[str] = derive_storage_key(service_id, provider_version)
    else:
        storage_key = service_id

    cat_str = raw.get("category", "custom")
    try:
        category = ToolCategory(cat_str)
    except ValueError:
        category = ToolCategory.CUSTOM

    parameters = _normalize_input_schema(raw)

    if handler_raw and isinstance(handler_raw, dict):
        handler = _build_handler(raw["handler"], yaml_path)
        handler_type = raw["handler"].get("type", "http")
    else:
        handler = _build_execution_handler(execution_raw, yaml_path)
        handler_type = f"execution/{execution_raw.get('type', 'python')}"

    requires_confirm = raw.get("requires_confirmation", False)
    if not requires_confirm:
        safety_checks = raw.get("safety_checks")
        if isinstance(safety_checks, list) and any(
            isinstance(c, dict) and c.get("enabled", True) for c in safety_checks
        ):
            requires_confirm = True

    tool_type = _infer_tool_type(yaml_path)
    source = "api" if tool_type == TOOL_TYPE_API else None

    info = ToolInfo(
        name=name,
        description=raw.get("description", ""),
        description_cn=raw.get("description_cn") or None,
        category=category,
        parameters=parameters,
        enabled=raw.get("enabled", True),
        requires_confirmation=requires_confirm,
        provider=storage_key,
        provider_version=provider_version,
        source=source,
    )

    tool = Tool(info=info, handler=handler)
    tool._yaml_path = yaml_path  # type: ignore[attr-defined]
    tool._provider = storage_key  # type: ignore[attr-defined]
    tool._service_id = service_id  # type: ignore[attr-defined]
    tool._provider_version = provider_version  # type: ignore[attr-defined]
    tool._source = source or "yaml_plugin"  # type: ignore[attr-defined]

    log.info("tool.yaml.loaded", {
        "name": name,
        "service_id": service_id,
        "storage_key": storage_key,
        "provider_version": provider_version,
        "handler_type": handler_type,
        "path": str(yaml_path),
    })

    return tool


# ---------------------------------------------------------------------------
# YAML Tool file CRUD helpers
# ---------------------------------------------------------------------------

def _yaml_tool_search_roots() -> List[Path]:
    """Return YAML tool roots: user-level then project-level.

    Bundled flockshub directories are intentionally NOT included — bundled
    tool plugins must be installed via the Hub flow (which copies them
    into ``<plugins>/tools/<type>/<id>/``) before this CRUD-oriented
    helper is expected to find them. This keeps "installed" the single
    source of truth for editing/listing.
    """
    # 插件输出和执行结果输出修改
    # 删除
    '''roots = [
        _TOOLS_SUBDIR,
        Path.cwd() / ".flocks" / "plugins" / "tools",
    ]'''
    # 新增
    roots = [_project_tools_root(), _TOOLS_SUBDIR, Path.cwd() / ".flocks" / "plugins" / "tools"]
    result: List[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            continue
        seen.add(key)
        result.append(root)
    return result


def _find_yaml_file(name: str) -> Optional[Path]:
    """Find the YAML source file for a plugin tool by name.

    Search order (first match wins):

    1. New type-based paths: ``tools/{type}/{name}.yaml``
       and ``tools/{type}/{provider}/{name}.yaml``
    2. Legacy flat path: ``tools/{name}.yaml``
    3. Legacy provider path: ``tools/{provider}/{name}.yaml``

    The ``mcp/`` subdirectory is skipped — MCP configs have a different
    format and are managed via :func:`find_mcp_config`.
    """
    for tools_root in _yaml_tool_search_roots():
        if not tools_root.is_dir():
            continue

        # 1. New type-based directories (api/, python/)
        for type_dir in (TOOL_TYPE_API, TOOL_TYPE_PYTHON):
            type_path = tools_root / type_dir
            if not type_path.is_dir():
                continue
            for suffix in (".yaml", ".yml"):
                candidate = type_path / f"{name}{suffix}"
                if candidate.is_file():
                    return candidate
            # Provider sub-subdirectories within the type dir
            for subdir in type_path.iterdir():
                if not subdir.is_dir() or subdir.name.startswith("_"):
                    continue
                for suffix in (".yaml", ".yml"):
                    candidate = subdir / f"{name}{suffix}"
                    if candidate.is_file():
                        return candidate

        # 2. Legacy flat path (backward compat)
        for suffix in (".yaml", ".yml"):
            candidate = tools_root / f"{name}{suffix}"
            if candidate.is_file():
                return candidate

        # 3. Legacy provider subdirectories (backward compat)
        _type_dirs = set(ALL_TOOL_TYPES)
        for subdir in tools_root.iterdir():
            if not subdir.is_dir() or subdir.name.startswith("_"):
                continue
            if subdir.name in _type_dirs:
                continue  # already searched above
            for suffix in (".yaml", ".yml"):
                candidate = subdir / f"{name}{suffix}"
                if candidate.is_file():
                    return candidate

    return None


def _read_yaml_raw(yaml_path: Path) -> Dict[str, Any]:
    """Read and parse a YAML file, returning the raw dict."""
    return yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}


def _write_yaml(yaml_path: Path, data: Dict[str, Any]) -> None:
    """Atomic-ish write of a dict back to a YAML file."""
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.dump(
        data, default_flow_style=False, allow_unicode=True, sort_keys=False,
    )
    yaml_path.write_text(content, encoding="utf-8")


def _ensure_safe_path_component(value: str, label: str) -> str:
    if not value or any(part in value for part in ("..", "/", "\\")):
        raise ValueError(f"{label} must be a safe path component")
    return value


def create_api_provider_yaml(
    provider_id: str,
    data: Dict[str, Any],
    *,
    overwrite: bool = False,
) -> Path:
    """Create or update an API provider ``_provider.yaml`` file."""
    _ensure_safe_path_component(provider_id, "Provider id")

    target_path = _project_tools_root() / TOOL_TYPE_API / provider_id / _PROVIDER_FILENAME
    if target_path.exists() and not overwrite:
        raise ValueError(f"Provider '{provider_id}' already exists")

    _write_yaml(target_path, data)
    log.info("tool.provider_yaml.created", {"provider": provider_id, "path": str(target_path)})
    return target_path


def find_api_provider_dir(provider_id_or_service_id: str) -> Optional[Path]:
    identifier = _ensure_safe_path_component(provider_id_or_service_id, "Provider id")
    api_root = _project_tools_root() / TOOL_TYPE_API
    direct_dir = api_root / identifier
    if direct_dir.is_dir():
        return direct_dir
    if not api_root.is_dir():
        return None
    for provider_dir in api_root.iterdir():
        if not provider_dir.is_dir() or provider_dir.name.startswith("_"):
            continue
        provider_file = provider_dir / _PROVIDER_FILENAME
        if not provider_file.is_file():
            continue
        try:
            provider_data = _read_yaml_raw(provider_file)
        except Exception:
            continue
        service_id = provider_data.get("service_id")
        if isinstance(service_id, str) and service_id == identifier:
            return provider_dir
    return None


def find_api_provider_tool(provider_id: str, name: str) -> Optional[Path]:
    provider_dir = find_api_provider_dir(provider_id)
    _ensure_safe_path_component(name, "Tool name")
    if provider_dir is None:
        return None
    for suffix in (".yaml", ".yml"):
        candidate = provider_dir / f"{name}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def list_api_provider_tools(provider_id: str) -> List[Path]:
    provider_dir = find_api_provider_dir(provider_id)
    if provider_dir is None:
        return []
    return sorted(
        path
        for path in provider_dir.iterdir()
        if path.is_file() and path.suffix in {".yaml", ".yml"} and path.name != _PROVIDER_FILENAME
    )


def find_yaml_tool(name: str) -> Optional[Path]:
    """Public API: return the YAML path for a plugin tool, or None."""
    return _find_yaml_file(name)


def read_yaml_tool(name: str) -> Optional[Dict[str, Any]]:
    """Read the raw YAML dict for a plugin tool. Returns None if not found."""
    path = _find_yaml_file(name)
    if path is None:
        return None
    try:
        return _read_yaml_raw(path)
    except Exception as e:
        log.error("tool.yaml.read_failed", {"name": name, "error": str(e)})
        return None


def upsert_yaml_tool(
    data: Dict[str, Any],
    provider: Optional[str] = None,
    tool_type: str = TOOL_TYPE_API,
    *,
    overwrite: bool = False,
) -> Path:
    name = data.get("name")
    if not name:
        raise ValueError("Tool data missing required 'name' field")
    _ensure_safe_path_component(str(name), "Tool name")
    if provider:
        _ensure_safe_path_component(provider, "Provider id")

    base_dir = _project_tools_root() / tool_type
    target_dir = base_dir / provider if provider else base_dir
    provider_path = find_api_provider_tool(provider, str(name)) if provider and tool_type == TOOL_TYPE_API else None
    target_path = provider_path or target_dir / f"{name}.yaml"
    existing_path = _find_yaml_file(str(name))

    if existing_path is not None:
        try:
            same_target = existing_path.resolve() == target_path.resolve()
        except OSError:
            same_target = str(existing_path) == str(target_path)
        if not same_target:
            raise ValueError(f"Tool '{name}' already exists outside provider '{provider or ''}'")
        if not overwrite:
            raise ValueError(f"Tool '{name}' already exists")

    if target_path.exists() and not overwrite:
        raise ValueError(f"Tool '{name}' already exists")

    existed = target_path.exists()
    _write_yaml(target_path, data)
    log.info(
        "tool.yaml.updated" if existed else "tool.yaml.created",
        {"name": name, "tool_type": tool_type, "path": str(target_path)},
    )
    return target_path


def create_yaml_tool(
    data: Dict[str, Any],
    provider: Optional[str] = None,
    tool_type: str = TOOL_TYPE_API,
) -> Path:
    """Create a new YAML tool plugin file.

    Parameters
    ----------
    data:
        Tool definition dict (must include ``name``).
    provider:
        Optional provider name.  When given, the file is placed under
        a provider subdirectory.
    tool_type:
        Tool type determines the base subdirectory
        (``api``, ``python``, etc.).  Defaults to ``api``.

    The resulting path is::

        <project>/.flocks/plugins/tools/{tool_type}/{provider?}/{name}.yaml

    Returns
    -------
    Path to the created YAML file.

    Raises
    ------
    ValueError
        If ``name`` is missing or the tool already exists.
    """
    return upsert_yaml_tool(data, provider=provider, tool_type=tool_type, overwrite=False)


def update_yaml_tool(name: str, updates: Dict[str, Any]) -> bool:
    """Apply partial updates to a YAML plugin tool file.

    Returns True on success, False if the YAML file was not found.
    """
    path = _find_yaml_file(name)
    if path is None:
        return False

    try:
        data = _read_yaml_raw(path)

        for key, value in updates.items():
            if value is not None:
                data[key] = value
            else:
                data.pop(key, None)

        _write_yaml(path, data)
        log.info("tool.yaml.updated", {"name": name, "path": str(path)})
        return True
    except Exception as e:
        log.error("tool.yaml.update_failed", {"name": name, "error": str(e)})
        return False


def _delete_yaml_path(path: Path, name: str) -> bool:
    try:
        data = _read_yaml_raw(path)
        handler = data.get("handler", {})
        if isinstance(handler, dict) and handler.get("type") == "script":
            script_file = handler.get("script_file")
            if script_file:
                script_path = path.parent / script_file
                if script_path.is_file():
                    script_path.unlink()

        path.unlink()
        log.info("tool.yaml.deleted", {"name": name, "path": str(path)})
        return True
    except Exception as e:
        log.error("tool.yaml.delete_failed", {"name": name, "error": str(e)})
        return False


def delete_api_provider_tool(provider_id: str, name: str) -> bool:
    path = find_api_provider_tool(provider_id, name)
    if path is None:
        return False
    return _delete_yaml_path(path, name)


def delete_api_provider(provider_id_or_service_id: str) -> tuple[Optional[Path], List[str]]:
    provider_dir = find_api_provider_dir(provider_id_or_service_id)
    if provider_dir is None:
        return None, []

    api_root = (_project_tools_root() / TOOL_TYPE_API).resolve()
    provider_dir_resolved = provider_dir.resolve()
    if not provider_dir_resolved.is_relative_to(api_root):
        raise ValueError("Provider directory is outside API tools root")

    tool_names = [path.stem for path in list_api_provider_tools(provider_dir.name)]
    shutil.rmtree(provider_dir)
    log.info("tool.provider_yaml.deleted", {
        "provider": provider_dir.name,
        "path": str(provider_dir),
        "tools": tool_names,
    })
    return provider_dir, tool_names


def delete_yaml_tool(name: str) -> bool:
    """Delete a YAML plugin tool file and its handler script (if any).

    Returns True on success, False if the YAML file was not found.
    """
    path = _find_yaml_file(name)
    if path is None:
        return False
    return _delete_yaml_path(path, name)


def _python_tool_dirs() -> List[Path]:
    """Return user- and project-level python tool directories.

    Bundled flockshub directories are intentionally excluded; python
    tools must be installed via the Hub flow before being picked up
    by the discovery layer.
    """
    # 插件输出和执行结果输出修改
    # 删除
    '''dirs = [
        _TOOLS_SUBDIR / TOOL_TYPE_PYTHON,
        Path.cwd() / ".flocks" / "plugins" / "tools" / TOOL_TYPE_PYTHON,
    ]'''
    # 新增
    dirs = [_project_tools_root() / TOOL_TYPE_PYTHON, _TOOLS_SUBDIR / TOOL_TYPE_PYTHON]
    result: List[Path] = []
    seen: set[str] = set()
    for directory in dirs:
        key = str(directory.resolve()) if directory.exists() else str(directory)
        if key in seen:
            continue
        seen.add(key)
        result.append(directory)
    return result


def _iter_python_tool_files() -> List[Path]:
    """List all candidate plugin python files."""
    files: List[Path] = []
    for directory in _python_tool_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            files.append(path)
    return files


def _register_function_name(call: ast.Call) -> Optional[str]:
    """Extract tool name from a ToolRegistry.register_function decorator call."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "register_function":
        return None
    for kw in call.keywords:
        if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _find_python_tool_block(tool_name: str) -> tuple[Optional[Path], Optional[int], Optional[int]]:
    """Locate the decorated function block that registers *tool_name*."""
    for path in _iter_python_tool_files():
        try:
            source = path.read_text(encoding="utf-8")
            module = ast.parse(source)
        except Exception as e:
            log.warn("tool.python.parse_failed", {"path": str(path), "error": str(e)})
            continue

        for node in ast.walk(module):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                registered_name = _register_function_name(decorator)
                if registered_name != tool_name:
                    continue
                start_line = min(
                    [getattr(dec, "lineno", node.lineno) for dec in node.decorator_list] + [node.lineno]
                )
                end_line = getattr(node, "end_lineno", node.lineno)
                return path, start_line, end_line
    return None, None, None


def delete_python_tool(name: str) -> bool:
    """Delete a Python plugin tool definition by tool name.

    Removes only the decorated function that registers the requested tool.
    If the file becomes empty after removal, the file itself is deleted.
    """
    path, start_line, end_line = _find_python_tool_block(name)
    if path is None or start_line is None or end_line is None:
        return False

    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        start_idx = max(start_line - 1, 0)
        end_idx = min(end_line, len(lines))

        while start_idx > 0 and lines[start_idx - 1].strip() == "":
            start_idx -= 1
        while end_idx < len(lines) and lines[end_idx].strip() == "":
            end_idx += 1

        remaining = lines[:start_idx] + lines[end_idx:]
        if "".join(remaining).strip():
            path.write_text("".join(remaining), encoding="utf-8")
        else:
            path.unlink()
        log.info("tool.python.deleted", {"name": name, "path": str(path)})
        return True
    except Exception as e:
        log.error("tool.python.delete_failed", {"name": name, "path": str(path), "error": str(e)})
        return False


def _infer_tool_type(yaml_path: Path) -> str:
    """Infer the tool type from a YAML file's location in the directory tree.

    Checks both the user-level tools dir (~/.flocks/plugins/tools/) and
    the project-level tools dir (<cwd>/.flocks/plugins/tools/).
    """
    # 插件输出和执行结果输出修改
    # 删除
    # candidates = [_TOOLS_SUBDIR, Path.cwd() / ".flocks" / "plugins" / "tools"]
    candidates = [_project_tools_root(), _TOOLS_SUBDIR]

    for base in candidates:
        try:
            rel = yaml_path.relative_to(base)
            first_part = rel.parts[0] if rel.parts else ""
            if first_part in ALL_TOOL_TYPES:
                return first_part
        except ValueError:
            continue
    return "legacy"


def list_yaml_tools() -> List[Dict[str, Any]]:
    """List all YAML plugin tools with basic metadata.

    Returns a list of dicts with ``name``, ``description``, ``provider``,
    ``handler_type``, ``tool_type``, and ``path``.

    Searches both user-level (~/.flocks/plugins/tools/) and project-level
    (<cwd>/.flocks/plugins/tools/) directories, as well as legacy flat/provider paths.
    The ``mcp/`` subdirectory is excluded (MCP configs are a different format).
    """
    results: List[Dict[str, Any]] = []

    _skip_dirs = {TOOL_TYPE_MCP, TOOL_TYPE_GENERATED}
    yaml_files: List[Path] = []
    seen_names: set = set()

    def _collect(directory: Path, depth: int = 0, max_depth: int = 2) -> None:
        if not directory.is_dir():
            return
        for item in directory.iterdir():
            if item.is_file() and item.suffix in (".yaml", ".yml") and not item.name.startswith("_"):
                yaml_files.append(item)
            elif (
                item.is_dir()
                and not item.name.startswith("_")
                and depth < max_depth
                and not (depth == 0 and item.name in _skip_dirs)
            ):
                _collect(item, depth + 1, max_depth)

    # 插件输出和执行结果输出修改
    # search_roots = _yaml_tool_search_roots()
    search_roots = [_project_tools_root(), _TOOLS_SUBDIR]
    for root in search_roots:
        _collect(root)

    for yf in sorted(yaml_files):
        try:
            data = _read_yaml_raw(yf)
            name = data.get("name")
            if not name or name in seen_names:
                continue
            seen_names.add(name)

            provider_cfg = _load_provider_config(yf)
            provider_name = data.get("provider")
            if not provider_name and provider_cfg:
                provider_name = provider_cfg.get("name")

            handler = data.get("handler", {})
            results.append({
                "name": name,
                "description": data.get("description", ""),
                "provider": provider_name,
                "handler_type": handler.get("type", "unknown") if isinstance(handler, dict) else "unknown",
                "tool_type": _infer_tool_type(yf),
                "path": str(yf),
                "enabled": data.get("enabled", True),
            })
        except Exception as e:
            log.warn("tool.yaml.list.error", {"path": str(yf), "error": str(e)})

    return results


# ---------------------------------------------------------------------------
# MCP config CRUD helpers
# ---------------------------------------------------------------------------

_MCP_SUBDIR = _TOOLS_SUBDIR / TOOL_TYPE_MCP


def _mcp_filename(name: str) -> str:
    """Normalise an MCP server name to a safe filename stem."""
    return name.replace("-", "_")

# 插件输出和执行结果输出新增
def _mcp_dirs() -> List[Path]:
    return [_project_tools_root() / TOOL_TYPE_MCP, _TOOLS_SUBDIR / TOOL_TYPE_MCP]

def save_mcp_config(name: str, config: Dict[str, Any]) -> Path:
    """Save an MCP server config to the project plugin tools directory.

    Parameters
    ----------
    name:
        MCP server name (e.g. ``"brave-search"``).
    config:
        Server configuration dict (type, command/url, environment, etc.).

    Returns
    -------
    Path to the created/updated YAML file.
    """
    filename = _mcp_filename(name)
    # 插件输出和执行结果输出修改
    # target = _MCP_SUBDIR / f"{filename}.yaml"
    target = _project_tools_root() / TOOL_TYPE_MCP / f"{filename}.yaml"
    data: Dict[str, Any] = {"name": name}
    data.update(config)
    _write_yaml(target, data)
    log.info("tool.mcp_config.saved", {"name": name, "path": str(target)})
    return target


def find_mcp_config(name: str) -> Optional[Path]:
    """Find an MCP config YAML under project/user plugin tool roots."""
    filename = _mcp_filename(name)
    for mcp_dir in _mcp_dirs():
        if not mcp_dir.is_dir():
            continue
        for variant in (filename, name):
            for suffix in (".yaml", ".yml"):
                candidate = mcp_dir / f"{variant}{suffix}"
                if candidate.is_file():
                    return candidate
    return None


def delete_mcp_config(name: str) -> bool:
    """Delete an MCP config YAML.  Returns True if a file was removed."""
    path = find_mcp_config(name)
    if path is None:
        return False
    try:
        path.unlink()
        log.info("tool.mcp_config.deleted", {"name": name, "path": str(path)})
        return True
    except Exception as e:
        log.error("tool.mcp_config.delete_failed", {"name": name, "error": str(e)})
        return False

# 插件输出和执行结果输出修改
def list_mcp_configs() -> List[Dict[str, Any]]:
    """List all MCP server configs under project/user plugin tool roots."""
    results: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for mcp_dir in _mcp_dirs():
        if not mcp_dir.is_dir():
            continue
        for item in sorted(mcp_dir.iterdir()):
            if not item.is_file() or item.suffix not in (".yaml", ".yml"):
                continue
            if item.name.startswith("_"):
                continue
            try:
                data = _read_yaml_raw(item)
                name = data.get("name", item.stem)
                if name in seen:
                    continue
                seen.add(name)
                results.append({
                    "name": name,
                    "type": data.get("type", "unknown"),
                    "path": str(item),
                    **{k: v for k, v in data.items() if k not in ("name", "type")},
                })
            except Exception as e:
                log.warn("tool.mcp_config.list_error", {"path": str(item), "error": str(e)})
    return results
