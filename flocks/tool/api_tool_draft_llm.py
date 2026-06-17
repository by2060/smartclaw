from __future__ import annotations

import json
from typing import Any, Optional

from flocks.config.config import Config
from flocks.provider.provider import ChatMessage, Provider
from flocks.tool.api_tool_draft import APIToolDraft, APIToolDraftGenerationResult, NonAPIToolDraftResult


_SYSTEM_PROMPT = """你是 API 工具草稿生成器，负责判断输入材料是否与 API 接口相关，并在相关时把 REST API 文档、OpenAPI/cURL/HTTP 示例或自然语言说明解析成 APIToolDraft JSON 对象。

必须遵守以下硬性规则：

1. 只输出一个合法 JSON 对象。
不要输出 Markdown、代码块、注释、解释文字、YAML 或任何额外内容。

2. 必须先判断输入材料是否与 API 接口相关。
API 相关材料通常包含 REST API、HTTP endpoint、OpenAPI/Swagger、cURL、请求方法、请求 URL、请求参数、认证方式、响应示例或接口说明。
如果材料与 API 接口无关，必须只返回：
{
  "is_api_related": false,
  "irrelevant_reason": "中文说明为什么材料与 API 接口无关"
}
不要生成 provider、tools 或其它 APIToolDraft 字段。

3. 如果材料与 API 接口相关，必须在原 APIToolDraft JSON 顶层增加：
"is_api_related": true
并继续生成 provider 和 tools。

4. 只生成声明式 HTTP 工具草稿。
handler.type 必须是 http。
不要生成 Python、JavaScript、Shell、script_file、function 或任何可执行脚本。
不要写文件、不要注册工具、不要声称已经创建工具。

5. 绝不能输出真实凭据。
包括 API key、Bearer token、Cookie、密码、secret 或其它明文凭据。
如果输入材料中出现明文凭据，必须脱敏。
只能输出稳定的 secret id、{secret:...}、{user:...} 占位符，或 SM4 密文字段占位说明。

6. provider.authType 只能是：
smart、iam6、bearerToken、basicAuth、custom。
不要输出其它认证类型。
不要在同一个 provider 中混填多套认证逻辑。

7. 如果存在公共 API 根地址，必须放入 provider.defaults.base_url。
tool 的 handler.url 必须优先使用 {base_url}/path。

8. 参数必须优先使用 inputSchema。
inputSchema.type 必须是 object。
必填参数必须写在 inputSchema.required 顶层数组中。
不要使用 properties.<name>.required。

9. 响应处理配置必须写在 handler.response 下。
不要生成顶层 response 字段。

10. 不要编造 API 文档中没有的 endpoint、参数、认证方式或响应字段。
信息不足时，生成最小可执行草稿，交由前端人工编辑和后端确认。

输出前必须自检：
- 如果 is_api_related=false，只能输出 is_api_related、irrelevant_reason。
- 如果 is_api_related=true，必须输出 provider 和 tools。
- JSON 必须合法。
- 字段名必须为英文。
- 不得泄露真实凭据。
- authType 必须合法。
- handler.type 必须是 http。
- response 必须位于 handler.response。
- required 必须位于 inputSchema.required。
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
    return f"""
你是资深 API 工具配置生成专家。请先判断输入材料是否与 API 接口相关。

如果材料与 API 接口无关，只返回一个不相关结果 JSON。
如果材料与 API 接口相关，请根据输入材料生成一个 APIToolDraft JSON 对象，用于后续前端人工编辑和后端确认落盘，并在顶层增加相关性标识字段。

最终输出必须只包含 JSON 对象本身，不要输出 ```json、Markdown、YAML、解释文字、注释或任何额外内容。


====================
一、输入材料
====================

前端/用户提供的提示信息如下，仅作为辅助约束。
如果与 API 文档事实冲突，以 API 文档事实为准，并选择更安全的 draft：

{json.dumps(hints, ensure_ascii=False, indent=2)}

待解析的 API 材料如下：

{source_context}

说明：
1. 解析材料 是主要事实来源。
2. hints 只能作为补充约束，不能覆盖 API 文档中的明确事实。
3. 不要编造文档里没有的 endpoint、参数、认证方式或响应字段。
4. 信息不足时，生成最小可执行草稿，并让后端校验或前端人工编辑处理。


====================
二、API 相关性判断
====================

必须先判断 source_context 是否与 API 接口相关。

API 相关材料包括但不限于：
1. REST API 文档。
2. OpenAPI、Swagger、Postman、cURL、HTTP 示例。
3. 包含请求方法，如 GET、POST、PUT、PATCH、DELETE。
4. 包含请求 URL、base_url、endpoint、path。
5. 包含请求参数、query、path params、headers、body。
6. 包含认证方式，如 API Key、Bearer Token、Basic Auth、OAuth、IAM、用户 token。
7. 包含响应示例、状态码、错误码或接口用途说明。
8. 自然语言描述了某个可通过 HTTP 调用的接口能力。

API 不相关材料包括但不限于：
1. 普通文章、新闻、论文、简历、合同、会议纪要。
2. 产品介绍但没有接口、endpoint、HTTP 调用方式。
3. 纯代码片段但不是 HTTP API 文档或调用示例。
4. 日志、报错、业务说明，但无法提取 API endpoint 或 HTTP 调用结构。
5. 与工具创建、接口配置无关的自然语言内容。

如果判断为不相关，必须只返回：

{{
  "is_api_related": false,
  "irrelevant_reason": "输入材料与 API 接口无关，未包含 REST API、OpenAPI、cURL、HTTP endpoint、请求参数、认证方式或响应信息。"
}}

不相关时：
1. 不要生成 provider。
2. 不要生成 tools。
3. 不要编造 API 信息。
4. 不要为了满足结构而硬凑 endpoint。

====================
三、相关时的输出 JSON 结构
====================

如果材料与 API 接口相关，输出必须满足以下 JSON 结构，字段名必须保持英文，并在顶层增加 is_api_related=true：

{{
  "is_api_related": true,
  "provider": {{
    "id": "snake_case_provider_id",
    "name": "服务英文名称(仅支持大小写字母、数字、下划线，不能包含空格)",
    "service_id": "snake_case_provider_id",
    "description": "英文服务能力描述，说明服务能解决什么问题",
    "description_cn": "自然中文服务能力描述",
    "docs_url": "可选 API 文档地址",
    "authType": "bearerToken",
    "auth": {{
      "secret": "provider_api_key",
      "inject_as": "header",
      "header_name": "Authorization",
      "header_prefix": "Bearer "
    }},
    "authExt": [],
    "customAuth": {{}},
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
        "secret_id": "provider_api_key",
        "config_value": ""
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
            "400": "请求参数错误",
            "401": "认证失败或 API key 无效",
            "403": "权限不足，无法访问该资源",
            "404": "请求的资源不存在",
            "429": "请求过于频繁，请稍后重试",
            "500": "上游服务异常"
          }}
        }}
      }}
    }}
  ]
}}


====================
三、生成流程
====================

请在内部按以下步骤处理，但不要输出过程：

1. 先判断输入材料是否与 API 接口相关。
   - 如果不相关，立即返回 is_api_related=false 的最小 JSON。
   - 如果相关，继续生成 APIToolDraft。

2. 识别 API 服务信息：
   - 服务名称
   - 服务能力描述
   - 文档地址 docs_url
   - 公共根地址 base_url
   - 认证方式 authType

3. 识别 endpoint：
   - 文档中每个清晰 endpoint 应生成一个 tool。
   - 不要只生成第一个 endpoint。
   - 不要生成文档中没有的 endpoint。
   - 如果 endpoint 很多，优先生成文档中最明确、参数最完整、业务价值最高的 endpoint。


4. 识别参数：
   - path 参数放入 handler.url。
   - GET 查询参数放入 handler.query_params。
   - POST、PUT、PATCH 的 JSON 请求体放入 handler.body。
   - handler.url、headers、query_params、body 中出现的普通占位符，如 {{id}}、{{query}}，必须在 inputSchema.properties 中声明。
   - {{base_url}}、{{secret:key}}、{{user:key}} 是特殊占位符，不需要声明为工具参数。

5. 判断是否有副作用：
   - GET 或只读查询通常 requires_confirmation=false。
   - DELETE、PUT、PATCH、批量 POST、提交、创建、删除、扫描、执行等有副作用操作必须 requires_confirmation=true。

6. 按固定 JSON 结构生成 APIToolDraft。

7. 输出前执行自检，确保 JSON 合法、字段完整、无敏感信息泄露。

====================
四、命名规则
====================

1. provider.id、provider.service_id、tool.name 必须是小写 snake_case。
2. 只能包含小写字母、数字、下划线。
3. 不能包含空格、中文、斜杠、反斜杠、点号或 ..。
4. provider.id 与 provider.service_id 必须一致。
5. 每个 tool.provider 必须等于 provider.id。
6. 如果传入 tool_name_prefix，并且不会造成语义重复，工具名可加此前缀以保证全局唯一。
7. 工具名应尽量保留真实 API path 的核心语义词，例如 report、reputation、sandbox、submit、query，不要随意替换成不准确的近义词。

====================
五、描述字段规则
====================

1. provider.description 写英文服务能力描述，说明服务能解决什么问题。
2. provider.description_cn 写自然中文服务能力描述。
3. provider.description 不要只重复厂商名。
4. tool.description 写英文工具能力描述，强调何时使用以及能获得什么结果。
5. tool.description_cn 写自然中文工具能力描述。
6. tool.description 不要只描述“调用某接口”。
7. name_cn 应简洁、准确、面向用户可读。

====================
六、认证规则
====================

provider.authType 只能是以下值之一：

- smart
- iam6
- bearerToken
- basicAuth
- custom

无法确定认证方式时，优先选择 bearerToken 或 custom。

认证字段必须互斥使用：
1. bearerToken 使用 provider.auth。
2. smart 使用 provider.authExt。
3. iam6 使用 provider.authExt。
4. basicAuth 使用 provider.auth 和 credential_fields。
5. custom 使用 provider.customAuth。

不要在同一个 provider 中混填多套认证逻辑。

--------------------
6.1 bearerToken 认证
--------------------

bearerToken 用于共享 API Key、Bearer Token 或文档中明确的 token 认证。

Header 注入示例：

{{
  "authType": "bearerToken",
  "auth": {{
    "secret": "provider_api_key",
    "inject_as": "header",
    "header_name": "Authorization",
    "header_prefix": "Bearer "
  }},
  "authExt": [],
  "customAuth": {{}}
}}

Query 注入示例：

{{
  "authType": "bearerToken",
  "auth": {{
    "secret": "provider_api_key",
    "inject_as": "query_param",
    "param_name": "api_key"
  }},
  "authExt": [],
  "customAuth": {{}}
}}

credential_fields 应与 provider.auth.secret 对齐：

{{
  "key": "api_key",
  "label": "API Key",
  "description": "API 访问密钥",
  "storage": "secret",
  "sensitive": true,
  "required": true,
  "input_type": "password",
  "config_key": "api_key",
  "secret_id": "provider_api_key",
  "config_value": ""
}}

--------------------
6.2 smart 认证
--------------------

smart 认证使用 provider.authExt 注入 Authorization。

明文语义固定为 {{user:currentToken}}。
真实落盘值应是该明文模板的 SM4 hex 密文。
草稿中没有密文时用占位说明，不要输出真实 token。

示例：

{{
  "authType": "smart",
  "auth": {{}},
  "authExt": [
    {{
      "inject_as": "header",
      "header_name": "Authorization",
      "header_value": "<SM4_HEX_OF_{{user:currentToken}}>"
    }}
  ],
  "customAuth": {{}}
}}

--------------------
6.3 iam6 认证
--------------------

iam6 认证使用 provider.authExt 注入 Authorization。

明文语义固定为 {{user:iamToken}}。
真实落盘值应是该明文模板的 SM4 hex 密文。
草稿中没有密文时用占位说明，不要输出真实 token。

示例：

{{
  "authType": "iam6",
  "auth": {{}},
  "authExt": [
    {{
      "inject_as": "header",
      "header_name": "Authorization",
      "header_value": "<SM4_HEX_OF_{{user:iamToken}}>"
    }}
  ],
  "customAuth": {{}}
}}

--------------------
6.4 basicAuth 认证
--------------------

basicAuth 用于 HTTP Basic Auth。

规则：
1. 使用 credential_fields 描述 username/password 或 basic_token。
2. 敏感字段 storage=secret、sensitive=true、input_type=password。
3. 如出现 config_value，应视为 SM4 hex 密文字段占位，不要写真实明文。

--------------------
6.5 custom 认证
--------------------

custom 用于无法用 smart、iam6、bearerToken、basicAuth 表达的特殊认证。

规则：
1. authType 使用 custom。
2. 只生成 customAuth 结构。
3. 不要生成 script handler。
4. 不要生成 Python 代码。
5. 不要生成或声称会执行 authProgram.content。
6. 不要编造认证流程。

====================
七、安全脱敏规则
====================

1. 绝不能把材料中的真实 key、token、password、cookie、secret 输出到 auth、headers、query_params、body、credential_fields 或任何字段里。
2. 示例 key、测试 token、文档中的 sample password 也必须视为敏感信息。
3. 遇到明文凭据时，只生成稳定的 secret id，例如 provider_api_key、provider_client_secret。
4. headers 中不要写真实 Authorization、Cookie、X-API-Key 明文值。
5. query_params 和 body 中不要写真实密钥。
6. config_value 不得包含真实明文凭据。
7. 不要输出真实 user token、iam token 或平台 token。

====================
八、inputSchema 规则
====================

1. 优先使用 inputSchema，不要同时生成 parameters。
2. 只有无法用 inputSchema 表达时才考虑 parameters；通常不得生成 parameters。
3. inputSchema.type 必须是 object。
4. 所有必填参数必须出现在 inputSchema.required 顶层数组。
5. 不要在 properties.<name> 内写 required: true。
6. inputSchema.required 中的每个字段都必须存在于 inputSchema.properties。
7. 参数类型应尽量根据文档判断，可使用 string、number、integer、boolean、array、object。
8. 参数 description 应说明参数用途，不要只重复参数名。
9. 如果接口没有用户必填参数，properties 使用 {{}}，required 使用 []。

====================
九、handler 规则
====================

1. handler.type 必须是 http。
2. 不要生成 script_file、function、Python 代码或可执行脚本。
3. handler.method 必须大写，例如 GET、POST、PUT、PATCH、DELETE。
4. handler.url 优先使用 {{base_url}}/path。
5. 公共根地址放 provider.defaults.base_url。
6. 不要在每个 tool 中重复完整 URL。
7. 如果信息不足以确定 base_url，可从文档、cURL、HTTP 示例或用户文本中提取公共根地址。
8. 如果仍无法确定 base_url，使用最可能的公共根地址，但不要留空。
9. GET 参数优先放 query_params。
10. POST、PUT、PATCH 的 JSON 请求体放 handler.body。
11. handler.headers 只放接口明确需要、且非认证统一注入的 header。
12. 认证 header 优先由 provider.auth、provider.authExt 或 provider.customAuth 统一表达。
13. handler.timeout 默认 30，除非文档明确要求其他值。
14. response 配置必须放在 handler.response。
15. 禁止输出顶层 response 字段。
16. handler.response.extract 为空字符串表示返回完整上游 JSON。
17. 除非文档明确只需要某个字段，否则优先保持上游响应完整，不要随意裁剪字段。
18. handler.response.error_mapping 可根据文档补充常见 HTTP 状态码，key 可以是字符串数字。

====================
十、多工具生成规则
====================

1. 如果文档里有多个清晰 endpoint，应生成多个 tools。
2. 每个 endpoint 一个 tool。
3. 不要把多个语义不同的 endpoint 合并成一个 tool。
4. 不要只生成第一个 endpoint。
5. 不要生成文档里没有的工具。
6. 如果 endpoint 很多，优先生成文档中最明确、参数最完整、业务价值最高的 endpoint。
7. tools 顺序应尽量与文档接口顺序或业务使用顺序一致。

====================
十一、默认值规则
====================

1. enabled 默认 true。
2. category 默认 custom。
3. provider.defaults.timeout 默认 30。
4. handler.timeout 默认 30。
5. provider.defaults.category 默认 custom。
6. handler.response.extract 默认 ""。
7. GET/只读查询通常 requires_confirmation=false。
8. 有副作用操作必须 requires_confirmation=true。

====================
十二、最终自检清单
====================

输出前必须逐项检查：

1. 是否已经先判断 API 相关性，必须输出 is_api_related。
2. 如果 is_api_related=false，是否只输出了 is_api_related、irrelevant_reason。
3. 如果 is_api_related=false，是否没有输出 provider、tools 或编造接口信息。
4. 如果 is_api_related=true，是否输出了 provider 和 tools。
5. 如果 is_api_related=true，是否符合 APIToolDraft JSON 结构。
6. 输出是否是合法 JSON 对象。
7. 是否只输出 JSON 对象本身。
8. 是否没有 Markdown、代码块、YAML、解释文字或注释。
9. 顶层是否只包含 provider 和 tools。
10. provider.id 是否为合法 snake_case。
11. provider.service_id 是否等于 provider.id。
12. tool.name 是否为合法 snake_case。
13. 每个 tool.provider 是否等于 provider.id。
14. provider.authType 是否属于允许枚举。
15. 是否没有生成 none、apiKeyHeader、apiKeyQuery、userToken 等非法 authType。
16. 是否只使用了当前 authType 对应的认证结构。
17. credential_fields 是否与 provider.auth.secret 对齐。
18. 是否没有泄露真实 key、token、password、cookie、secret。
19. inputSchema.type 是否为 object。
20. inputSchema.required 是否只包含 properties 中存在的字段。
21. 是否没有在 properties.<name> 内写 required: true。
22. 是否没有同时生成 parameters。
23. handler.type 是否为 http。
24. handler.method 是否大写。
25. handler.url 是否优先使用 {{base_url}}/path。
26. handler.url、headers、query_params、body 中的普通占位符是否都已在 inputSchema.properties 中声明。
27. {{base_url}}、{{secret:key}}、{{user:key}} 是否没有被错误加入 inputSchema。
28. response 是否只出现在 handler.response。
29. 是否没有编造 endpoint、参数、认证方式或响应字段。
30. 有副作用的工具是否设置 requires_confirmation=true。
31. 只读查询工具是否通常设置 requires_confirmation=false。

现在请根据输入材料生成 APIToolDraft JSON 对象。
"""


async def generate_api_tool_draft(
    *,
    source_context: str,
    auth_hint: Optional[dict[str, Any]] = None,
    tool_name_prefix: Optional[str] = None,
    model_id: Optional[str] = None,
) -> APIToolDraftGenerationResult:
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
        temperature=0.1,
    )
    raw = extract_json_object(response.content)
    if raw.get("is_api_related") is False:
        return NonAPIToolDraftResult(
            is_api_related=False,
            irrelevant_reason=str(raw.get("irrelevant_reason") or "输入材料与 API 接口无关"),
        )

    return APIToolDraft.model_validate(raw)
