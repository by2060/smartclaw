from __future__ import annotations

import json
from typing import Any, Optional

from flocks.config.config import Config
from flocks.provider.provider import ChatMessage, Provider
from flocks.tool.api_tool_draft import APIToolDraft


_SYSTEM_PROMPT = """你是 API 工具草稿生成器，负责把 REST API 文档、OpenAPI/cURL/HTTP 示例或自然语言说明解析成 APIToolDraft JSON。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、注释、解释文字或 YAML。
只生成 handler.type=http 的声明式 HTTP 工具草稿；不要生成 Python script handler。
不要写文件、不要注册工具、不要声称已经创建工具。
绝不能输出真实 API key、Bearer token、Cookie、密码或其它明文凭据；只能输出 secret id、{secret:...} 或 {user:...} 占位符。
如果存在公共 API 根地址，工具 URL 必须优先写成 {base_url}/path，并把根地址放入 provider.defaults.base_url。
响应处理配置必须写在 handler.response 下，不要生成顶层 response 字段。
参数必填项必须写在 inputSchema.required 顶层数组中，不要使用 properties.<name>.required。
"""


def extract_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif text.startswith("```"):
        text = text.split("```", 1)[1].split("```", 1)[0].strip()

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("LLM output did not contain a JSON object")


def build_prompt(
    *,
    source_context: str,
    auth_hint: Optional[dict[str, Any]] = None,
    tool_name_prefix: Optional[str] = None,
) -> str:
    hints = {
        "auth_hint": auth_hint or {},
        "tool_name_prefix": tool_name_prefix or "",
    }
    return f"""请根据输入材料生成一个 APIToolDraft JSON 对象，用于后续前端人工编辑和后端确认落盘。

输出必须满足以下 JSON 结构，字段名必须保持英文：
{{
  "provider": {{
    "id": "snake_case_provider_id",
    "name": "服务展示名称",
    "service_id": "snake_case_provider_id",
    "description": "英文服务能力描述，说明服务能解决什么问题",
    "description_cn": "自然中文服务能力描述",
    "docs_url": "可选 API 文档地址",
    "auth": {{
      "secret": "provider_api_key",
      "inject_as": "header",
      "header_name": "Authorization",
      "header_prefix": "Bearer "
    }},
    "defaults": {{
      "base_url": "https://api.example.com",
      "timeout": 30,
      "category": "custom"
    }},
    "credential_fields": [
      {{
        "key": "api_key",
        "label": "API Key",
        "description": "API 访问密钥",
        "storage": "secret",
        "sensitive": true,
        "required": true,
        "input_type": "password",
        "config_key": "api_key",
        "secret_id": "provider_api_key"
      }}
    ]
  }},
  "tools": [
    {{
      "name": "snake_case_tool_name",
      "name_cn": "中文名",
      "description": "英文工具能力描述，说明何时使用以及能获得什么结果",
      "description_cn": "自然中文工具能力描述",
      "category": "custom",
      "enabled": true,
      "requires_confirmation": false,
      "provider": "snake_case_provider_id",
      "inputSchema": {{
        "type": "object",
        "properties": {{
          "param_name": {{
            "type": "string",
            "description": "参数用途说明"
          }}
        }},
        "required": ["param_name"]
      }},
      "handler": {{
        "type": "http",
        "method": "GET",
        "url": "{{base_url}}/path/{{param_name}}",
        "headers": {{}},
        "query_params": {{}},
        "body": {{}},
        "timeout": 30,
        "response": {{
          "extract": "",
          "error_mapping": {{
            "401": "认证失败或 API key 无效",
            "429": "请求过于频繁，请稍后重试"
          }}
        }}
      }}
    }}
  ]
}}

生成规则：
1. 只返回 JSON 对象本身，不要输出 ```json、Markdown、YAML、解释文字或注释。
2. provider.id、provider.service_id、tool.name 必须是小写 snake_case，只包含字母、数字、下划线，不能包含空格、中文、斜杠、反斜杠或 ..。
3. 如果传入 tool_name_prefix，并且不会造成语义重复，工具名可加此前缀以保证全局唯一。
4. API 服务应使用 provider 子目录模式：每个 tool.provider 应等于 provider.id。
5. provider.description 写英文能力描述；provider.description_cn 写自然中文描述。不要只重复厂商名。
6. tool.description 写英文工具能力描述，强调使用场景和返回价值，不要只描述“调用某接口”。tool.description_cn 写自然中文描述。
7. enabled 默认 true。GET/只读查询通常 requires_confirmation=false；DELETE、PUT、PATCH、批量 POST、提交/创建/删除/扫描等有副作用操作必须 requires_confirmation=true。
8. 优先使用 inputSchema，不要同时生成 parameters。只有无法表达时才考虑 parameters。
9. inputSchema.type 必须是 object；所有必填参数必须出现在 inputSchema.required 顶层数组；不要在 properties.<name> 内写 required: true。
10. handler.type 必须是 http；不要生成 script_file、function 或 Python 代码。
11. handler.method 必须大写。GET 参数优先放 query_params；POST/PUT/PATCH 的 JSON 请求体放 handler.body。
12. handler.url 优先使用 {{base_url}}/path；公共根地址放 provider.defaults.base_url，且不要把完整 URL 到处重复。
13. handler.url、headers、query_params、body 中出现的普通占位符如 {{id}}、{{query}}，必须在 inputSchema.properties 中声明。
14. {{base_url}}、{{secret:key}}、{{user:key}} 是特殊占位符，不需要声明为工具参数。
15. provider.auth 用于声明共享认证：
    - header 认证：{{"secret":"xxx_api_key","inject_as":"header","header_name":"Authorization","header_prefix":"Bearer "}}
    - query 认证：{{"secret":"xxx_api_key","inject_as":"query_param","param_name":"api_key"}}
16. credential_fields 应与 provider.auth.secret 对齐；敏感字段 storage=secret、sensitive=true、input_type=password，并填写 secret_id。
17. 绝不能把材料中的真实 key/token/password/cookie 输出到 auth、headers、query_params、body、credential_fields 或任何字段里；遇到明文凭据时只生成稳定的 secret id，例如 provider_api_key。
18. response 配置必须放在 handler.response，禁止输出顶层 response 字段。
19. handler.response.extract 为空字符串表示返回完整上游 JSON。除非文档明确只需要某个字段，否则优先保持上游响应完整，不要随意裁剪字段。
20. handler.response.error_mapping 可根据文档补充常见 HTTP 状态码；key 可以是字符串数字。
21. 如果文档里有多个清晰 endpoint，应生成多个 tools；不要只生成第一个 endpoint。每个 endpoint 一个工具。
22. 工具名应尽量保留真实 API path 的核心语义词，例如 report、reputation、sandbox、submit、query，不要随意替换成别的近义词。
23. 不要编造文档里没有的 endpoint、参数、认证方式或响应字段；不确定时保留最小可执行草稿，并让后端校验/人工编辑处理。
24. 如果信息不足以确定 base_url，可从文档、cURL、HTTP 示例或用户文本中提取公共根地址；仍无法确定时使用最可能的公共根地址，但不要留空。

前端/用户提供的提示信息如下，仅作为辅助约束；如果与 API 文档事实冲突，以文档事实为准，并选择更安全的 draft：
{json.dumps(hints, ensure_ascii=False, indent=2)}

待解析的 API 材料如下：
{source_context}
"""


async def generate_api_tool_draft(
    *,
    source_context: str,
    auth_hint: Optional[dict[str, Any]] = None,
    tool_name_prefix: Optional[str] = None,
    model_id: Optional[str] = None,
) -> APIToolDraft:
    target_model = model_id
    if not target_model:
        default_llm = await Config.resolve_default_llm()
        if default_llm:
            target_model = default_llm.get("model_id")
    if not target_model:
        raise ValueError("Default LLM model is not configured")

    prompt = build_prompt(
        source_context=source_context,
        auth_hint=auth_hint,
        tool_name_prefix=tool_name_prefix,
    )
    response = await Provider.chat(
        model_id=target_model,
        messages=[
            ChatMessage(role="system", content=_SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompt),
        ],
        temperature=0.2,
    )
    raw = extract_json_object(response.content)
    return APIToolDraft.model_validate(raw)
