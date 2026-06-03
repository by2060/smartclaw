# Flocks Tool + Agent + Skill + Session Prompt 中文化方案设计

> 状态：当前分支按独立 commit 重建中文化的设计参考版
> 更新日期：2026-06-03
> 基线分支：`feature/tool-data-permission-control`
> 参考来源：`feature/cn-localization` 中的 `b4e7e9ca`、`167865a3`、`c3f6313d`、`5edcaaf5`，以及 Session Prompt 中文化提交 `080ece3`
> 适用范围：`flocks/tool`、`flocks/agent`、`flocks/skill`、`flocks/server/routes/skill.py`、项目级 `.flocks/plugins/skills/*/SKILL.md`、`.flocks/plugins/skills/tool-builder/validator.py`、项目级 `.flocks/plugins/tools/python/*.py`、`webui/src/pages/Tool`、`flocks/session/prompt`、`tests/skills/test_tool_validator.py`、`tests/session/test_prompt_tokens.py`
> 落地执行：见 `docs/CN_LOCALIZATION_COMMIT_AUDIT.md`、`docs/CN_LOCALIZATION_ROLLOUT_PLAN.md` 和 `docs/CN_LOCALIZATION_DIFF_SPLIT.md`

## 1. 背景

> 重要更新：考虑到中文化改动范围较大，实际落地应从最新 `feature/tool-data-permission-control` 基线在当前分支从 0 重做；PR-0 到 PR-8 参考 `b4e7e9ca`、`167865a3`、`c3f6313d`、`5edcaaf5` 四个源 commit，PR-9+ 单独参考 `080ece3` 的 Session Prompt 中文化，并按照 `docs/CN_LOCALIZATION_ROLLOUT_PLAN.md` 和 `docs/CN_LOCALIZATION_DIFF_SPLIT.md` 拆成多个可独立回滚的 commit。

原始 C 阶段中文本地化设计覆盖 Tool、Agent、Skill 等多个层面，但早期实施方式偏向“直接把内容替换为中文”。该方式在 Tool 层存在明显风险：

1. `description` 是模型实际看到的 tool schema 描述，直接改为中文会改变模型侧工具调用上下文。
2. 自动替换容易引入语法问题，例如孤立逗号、字符串截断、YAML frontmatter 解析失败。
3. 动态描述、动态 prompt builder 如果被静态文件覆盖，会破坏运行时注入和占位符替换。

因此，本轮整理后的方案不再采用单一的“全部直接替换中文”策略，而是按层区分：

- Tool：保留 `description`，补充 `description_cn`，用于 API/UI 中文展示，尽量不影响模型 tool schema 行为。
- WebUI：中文环境下消费 `description_cn`，缺失时回退 `description`，避免中文界面仍显示英文工具描述。
- Agent：补充 `description_cn` 元数据，同时将 Agent prompt 正文中文化；动态 Agent 必须保留 `prompt_builder.py` 机制。
- Skill：保留 `description`，补充并解析 `description_cn`，让 API/UI、Skill 工具动态描述、Agent 动态 prompt 注入都能使用中文描述。
- Session Prompt：不直接粗暴覆盖核心 system prompt，采用分层、双版本或 locale 选择的方式逐步中文化，降低模型行为漂移风险。

## 2. 总体目标

1. 建立 Tool + Agent + Skill 三层统一的中文展示字段策略。
2. 在不破坏模型 tool schema、动态 Agent prompt、Skill frontmatter 解析的前提下补齐中文描述。
3. 让 API/UI 在中文环境下可以优先展示 `description_cn`，缺失时回退到 `description`。
4. 让 Agent 动态 prompt 中的可委派 Agent 和可用 Skill 描述优先展示中文。
5. 修复当前项目中已发现的 Skill frontmatter 格式问题。
6. 保留既有英文 `description`，降低兼容性和行为漂移风险。
7. 为 `flocks/session/prompt` 这类核心 system prompt 建立可控的中文化策略，避免中文发布版出现底层提示词英文暴露、中文响应漂移和品牌残留。

## 3. 非目标

本轮不做以下内容：

1. 不实现完整 runtime locale 切换框架。
2. 不把 Tool 的 `description` 大规模直接替换成中文。
3. 不修改 Tool 参数描述字段 `ToolParameter.description`。
4. 不系统性翻译工具运行时输出、错误信息、日志文本。
5. 不批量覆盖所有 Skill 正文内容；Skill 本轮重点是 metadata/frontmatter 和加载链路。
6. 不给动态 Agent 新增静态 `prompt.md`，避免绕过 `prompt_builder.py`。
7. 不直接全量翻译并覆盖 `flocks/session/prompt/*.txt`；Session Prompt 中文化必须作为独立阶段实施，先设计、后验证、再切换。

## 4. 核心设计原则

### 4.1 双字段描述策略

| 字段 | 用途 | 本轮策略 |
|---|---|---|
| `description` | 原始描述、兼容旧代码、部分模型上下文 | 尽量保留现状 |
| `description_cn` | 中文 API/UI 展示、中文 prompt 表格展示 | 新增或补齐 |

该策略适用于 Tool、Agent、Skill 三类对象。

### 4.2 模型侧行为最小扰动

Tool 层不修改模型实际 tool schema 的描述来源，继续保留 `description` 作为主要模型描述字段。这样可避免工具选择行为因为描述语言切换而发生不可控变化。

Agent prompt 是 Agent 的系统行为说明，本轮按 C 阶段目标进行中文化，但对动态 Agent 采用 builder 内模板中文化，而不是新增静态 `prompt.md`。

### 4.3 动态内容不得静态覆盖

以下 Agent 使用 `prompt_builder.py` 动态构造 prompt：

- `rex`
- `rex_junior`
- `hephaestus`
- `librarian`

这些 Agent 不能新增静态 `prompt.md`，因为 Agent 加载逻辑优先读取 `prompt.md`，会导致：

- 动态占位符不再替换。
- 当前年份、工作流、技能、委派表等动态内容丢失。
- 运行时注入逻辑失效。

因此，动态 Agent 的中文化必须发生在 `prompt_builder.py` 内部模板和共享 `prompt_utils.py` 中。

### 4.4 YAML frontmatter 必须安全可解析

Skill 的 `SKILL.md` 依赖 YAML frontmatter。包含冒号、中文、长文本的描述必须安全序列化或使用 block scalar，否则会出现：

```text
mapping values are not allowed here
```

设计要求：

- 手工维护的长描述使用 `>-` block scalar。
- API create/update 使用 `yaml.safe_dump(..., allow_unicode=True)` 生成 frontmatter。
- Parser 优先使用 `yaml.safe_load`，失败时才进入简单 fallback。

### 4.5 Session Prompt 分层中文化原则

`flocks/session/prompt/*.txt` 属于核心 system prompt，会直接影响模型身份、工具调用、安全边界、plan 模式和多模型行为一致性。该层不能按普通 UI 文案处理。

设计原则：

1. **不直接粗暴覆盖英文基线**：保留当前英文 prompt 作为行为基线，便于回滚和对比。
2. **按风险分层推进**：用户可见提醒优先，核心行为约束后置。
3. **优先新增中文变体或中英双语变体**：避免一次性替换导致行为漂移。
4. **通过 locale / model profile 选择**：中文环境加载中文或双语 prompt，缺失时继续回退英文基线。
5. **每次变更必须有行为验证**：不仅检查文本，还要验证工具调用、plan 模式、安全约束和中文输出一致性。

## 5. Tool 层设计

### 5.1 数据流

```text
内置 Tool 注册 / 插件 Tool 定义
        ↓
ToolRegistry / tool_loader
        ↓
ToolInfo.description + ToolInfo.description_cn
        ↓
/api/tools 等接口返回双字段
        ↓
WebUI 根据当前语言选择 description_cn 或 description
```

### 5.2 静态描述工具

对于已有 `DESCRIPTION` 常量的工具，采用以下模式：

```python
DESCRIPTION = """English model-facing description..."""
DESCRIPTION_CN = """中文 UI/API 描述..."""

@ToolRegistry.register_function(
    name="glob",
    description=DESCRIPTION,
    description_cn=DESCRIPTION_CN,
    ...
)
async def glob_tool(...):
    ...
```

特点：

- `description` 保持原内容。
- `description_cn` 新增中文。
- API 返回双字段，WebUI 在中文语言环境下优先显示中文。
- 模型 tool schema 行为不变。

### 5.3 动态描述工具

部分工具描述由函数动态生成，必须同步提供中文动态描述。

典型工具：

- `bash`：描述中包含当前工作目录。
- `websearch`：描述中包含当前日期。
- `run_workflow`：描述中包含运行时 workflow 列表。
- `skill`：描述中包含运行时已安装 Skill 列表。

设计模式：

```python
tool.info.description = await _build_description()
tool.info.description_cn = await _build_description_cn()
```

如果动态条目本身有 `description_cn`，中文描述优先使用；否则回退到 `description`。

### 5.4 插件工具加载

YAML 插件工具：

```yaml
description: English description
description_cn: 中文描述
```

Python declarative plugin：

```python
TOOLS = [
    {
        "name": "example_tool",
        "description": "English description",
        "description_cn": "中文描述",
    }
]
```

加载器和 registry 都需要将 `description_cn` 透传到 `ToolInfo`。

### 5.5 WebUI 展示消费

Tool 页面不应直接渲染 `tool.description`。统一要求：

```ts
getLocalizedToolDescription(tool, i18n.language)
```

该 helper 在中文语言环境下优先返回 `description_cn`，缺失时回退 `description`；非中文环境保持英文优先。适用范围包括全量工具表格、工具详情、MCP / API 服务详情里的工具列表。

### 5.6 新生成工具的防漏机制

通过会话生成项目级 Python Tool 时，生成源头是 `tool-builder` Skill。为避免后续再次生成只有英文 `description` 的工具，设计要求：

1. `tool-builder` 的 Python Tool 模板必须同时包含 `description` 和 `description_cn`。
2. `tool-builder` validator 对 Mode B Python Tool 缺失或空的 `description_cn=` 直接判定为 `FAIL`。
3. validator 测试必须覆盖“有效工具包含 `description_cn`”和“缺失 `description_cn` 失败”两类场景。

这样后续生成链路变为：

```text
tool-builder 模板生成 description_cn
        ↓
validator 强制校验 description_cn
        ↓
registry / API / WebUI 透传并展示 description_cn
```

## 6. Agent 层设计

### 6.1 数据流

```text
agent.yaml description_cn
        ↓
AgentInfo.description_cn
        ↓
AvailableAgent.description_cn
        ↓
prompt_utils 动态表格 / 委派说明
        ↓
Rex / Hephaestus 等动态 Agent prompt
```

### 6.2 Agent YAML 元数据

每个内置 Agent 的 `agent.yaml` 补充 `description_cn`：

```yaml
name: rex
description: >-
  Powerful AI orchestrator for security operations...
description_cn: 强大的安全运维 AI 编排器，分析威胁、制定策略、调度专业智能体
```

注意：如果 `description` 使用 block scalar，`description_cn` 必须放在完整 block scalar 之后，不能插入到 `description: >-` 和其正文之间。

### 6.3 静态 Agent prompt

已有静态 `prompt.md` 的 Agent 可直接中文化正文：

- `explore`
- `oracle`
- `metis`
- `momus`
- `multimodal_looker`
- `self_enhance`

这些 Agent 不依赖 `prompt_builder.py`，替换 `prompt.md` 不会破坏动态注入。

### 6.4 动态 Agent prompt

动态 Agent 必须在 `prompt_builder.py` 内中文化模板，同时保留：

- 原有函数签名。
- placeholder 替换逻辑。
- 动态年份、workflow、skills、agents、tools 注入。
- `prompt_append` 等配置扩展能力。

适用 Agent：

- `rex`
- `rex_junior`
- `hephaestus`
- `librarian`

### 6.5 共享动态 prompt 片段

`prompt_utils.py` 负责生成动态表格和公共协议段落。设计要求：

1. 公共段落中文化。
2. Agent 表格描述优先使用 `description_cn`。
3. Skill 表格描述优先使用 `description_cn`。
4. 缺失中文描述时回退到 `description`。

## 7. Skill 层设计

### 7.1 数据流

```text
SKILL.md frontmatter description_cn
        ↓
Skill._parse_skill_md(...)
        ↓
SkillInfo.description_cn
        ↓
Skill API 响应 / skill 工具动态描述 / Agent AvailableSkill
        ↓
API/UI 与 Agent prompt 中文展示
```

### 7.2 SkillInfo 模型

`SkillInfo` 增加字段：

```python
description_cn: Optional[str] = Field(default=None, description="Chinese UI description")
```

Parser 支持：

- `description_cn`
- `descriptionCn`

其中 `description_cn` 是推荐格式，`descriptionCn` 只作为兼容输入。

### 7.3 Skill API

Skill API 需要支持：

- 返回 `description_cn`。
- 创建 Skill 时接收 `description_cn`。
- 更新 Skill 时接收 `description_cn`。
- create/update frontmatter 使用 YAML 安全序列化。

### 7.4 Skill frontmatter 格式

推荐格式：

```yaml
---
name: example-skill
description: >-
  English or existing description that may contain colon: safely.
description_cn: >-
  中文描述，可以包含冒号：以及较长文本。
---
```

短描述可由 API 自动序列化为单行或必要时带引号的 YAML 值。

## 8. Session Prompt 层设计

### 8.1 当前范围

当前 `flocks/session/prompt/` 下存在多套面向不同模型或运行状态的 system prompt：

```text
anthropic.txt
anthropic-20250930.txt
anthropic_spoof.txt
beast.txt
build-switch.txt
codex_header.txt
copilot-gpt-5.txt
gemini.txt
max-steps.txt
plan-reminder-anthropic.txt
plan.txt
qwen.txt
```

相邻代码中还有：

```text
flocks/session/prompt.py
flocks/session/prompt_strings.py
```

这些内容会影响：

- 模型身份和产品定位表达。
- 用户语言选择和输出语言稳定性。
- 工具调用原则、权限边界和高风险操作约束。
- Plan 模式、max steps、build switch 等系统提醒。
- 多模型 prompt profile 的行为一致性。

### 8.2 不中文化的影响

如果该层长期不中文化，中文发布版会有以下问题：

| 问题 | 影响 |
|---|---|
| 底层 system prompt 仍全英文 | 中文产品体验不完整，部分系统提醒可能以英文暴露 |
| 英文 prompt 权重较高 | 中文用户场景下更容易出现英文总结、英文任务标题或英文边界提示 |
| 多模型 profile 差异扩大 | `anthropic`、`gemini`、`qwen` 等模型对中文用户的输出风格更不一致 |
| 品牌词残留 | 发布态品牌替换时，`Flocks`、`Rex` 等词会继续残留在核心 prompt 中 |
| 国产/中文优化模型约束不够直接 | 中文安全边界、工具约束、操作确认的表达不如中文 prompt 稳定 |

但直接全量翻译也有风险：system prompt 是控制面，翻译偏差可能改变工具调用、安全边界、任务完成标准和模型角色定位。

### 8.3 分层策略

按风险从低到高分层推进：

| 层级 | 范围 | 策略 | 是否可优先做 |
|---|---|---|---|
| L1 用户可见提醒 | `max-steps.txt`、`plan.txt`、`plan-reminder-anthropic.txt`、`build-switch.txt` | 可先做中文或中英双语，重点保证用户看到的提醒自然 | 是 |
| L2 任务标题/短文本模板 | `prompt_strings.py` 中标题生成、简短提示 | 中文化示例和输出约束，保持函数行为不变 | 是 |
| L3 模型身份和产品定位 | `anthropic.txt`、`gemini.txt`、`qwen.txt`、`copilot-gpt-5.txt` 等开头身份段落 | 优先中英双语或新增 zh 变体，不直接删除英文基线 | 谨慎 |
| L4 工具调用、安全边界、权限规则 | 各主 prompt 中工具使用、安全、执行约束段落 | 必须逐条语义对齐，需测试工具调用和风险动作确认 | 后置 |
| L5 特殊兼容 prompt | `anthropic_spoof.txt`、`beast.txt`、`codex_header.txt` | 先确认是否仍被使用、对应模型 profile 和兼容目的，再决定是否中文化 | 最后 |

### 8.4 推荐实现形态

推荐采用“保留英文基线 + 新增中文变体 + 运行时选择”的方式，而不是直接覆盖原文件。

候选目录结构：

```text
flocks/session/prompt/
├── anthropic.txt          # 英文基线，保留
├── anthropic.zh.txt       # 中文或中英双语变体
├── gemini.txt
├── gemini.zh.txt
├── qwen.txt
├── qwen.zh.txt
├── plan.txt
├── plan.zh.txt
└── max-steps.zh.txt
```

加载策略：

```text
用户语言 / runtime locale / model profile
        ↓
优先查找 <prompt>.zh.txt
        ↓ 不存在
回退 <prompt>.txt
        ↓
追加项目级 prompt_append / 用户配置
```

设计要求：

1. 英文基线文件保留，避免行为回滚困难。
2. 中文变体命名稳定，例如 `*.zh.txt`，不要覆盖原 prompt。
3. 中文 locale 加载中文变体；缺失或异常时回退英文基线。
4. 对模型强相关 prompt，不同模型分别维护中文变体，不做“一份中文 prompt 适配所有模型”。
5. `prompt_append`、项目级附加提示、动态注入逻辑必须保持原有顺序和能力。

### 8.5 翻译规范

Session Prompt 翻译必须遵守：

1. **语义优先，不逐字直译**：保持原约束含义、强度和边界。
2. **保留关键英文术语**：工具名、文件名、命令名、API 字段、模型 profile 名保持英文。
3. **安全约束不弱化**：涉及权限、危险操作、拒绝范围、工具调用限制的语气不能变弱。
4. **中文输出约束显式化**：中文 locale 下要求默认使用中文回复，除非用户要求其他语言或代码/协议必须英文。
5. **品牌词按发布策略处理**：`Flocks`、`Rex` 等词在 branch1 可保留；branch2 发布态由转换脚本或 zh prompt 变体处理。
6. **避免新增未验证能力**：翻译时不能顺手承诺不存在的工具、权限或自动化能力。

### 8.6 推荐推进阶段

| 阶段 | 目标 | 产出 |
|---|---|---|
| Phase A：盘点 | 标记每个 prompt 的调用入口、模型 profile、风险等级 | prompt 清单 + 风险分级 |
| Phase B：低风险中文化 | 处理 plan/max-steps/build-switch 等用户可见提醒 | `*.zh.txt` 或双语提醒 |
| Phase C：核心 prompt 中文变体 | 为主模型 prompt 新增 zh 版本，不替换英文基线 | `anthropic.zh.txt`、`gemini.zh.txt`、`qwen.zh.txt` 等 |
| Phase D：加载器支持 | 根据 locale/profile 选择 zh prompt 并回退英文 | prompt loader 策略 |
| Phase E：行为回归 | 验证中文回复、工具调用、plan、安全边界、品牌残留 | 回归报告 |
| Phase F：发布转换接入 | branch2 发布态检查 prompt 残留和品牌替换 | 转换脚本规则或允许清单 |

### 8.7 验收标准

Session Prompt 中文化完成必须满足：

```text
1. 英文基线 prompt 仍可加载。
2. 中文 locale 下优先加载中文变体，缺失时回退英文。
3. 中文用户输入时默认中文回复，不无故切回英文。
4. 工具调用能力、参数约束和权限边界与英文基线一致。
5. Plan 模式、max steps、build switch 提醒中文可读且不改变原流程语义。
6. `Flocks`、`Rex` 等品牌词在 branch2 发布态按策略处理，无未解释残留。
7. 不同模型 profile 至少完成一组中文任务回归：问答、工具调用、代码修改、plan、安全拒绝。
```

## 9. 验证方案

### 9.1 Python 编译验证

验证 Agent、Skill、Skill API 路由语法：

```bash
python3 -m compileall -q flocks/agent flocks/skill flocks/server/routes/skill.py
```

### 9.2 Tool 注册覆盖验证

验证可执行 Tool 注册中 `description_cn` 的覆盖率，排除示例代码。

验收标准：

- 所有内置可执行 Tool 注册包含 `description_cn`。
- YAML/Python 插件工具可透传 `description_cn`。

### 9.3 Agent 加载验证

验证所有内置 Agent 可加载，且除内部特殊 Agent 外均包含 `description_cn`。

验收标准：

```text
failed []
missing_description_cn []
```

### 9.4 Skill 解析验证

验证项目级 Skill 均可通过 `_parse_skill_md` 解析，并包含 `description_cn`。

验收标准：

```text
failed []
missing_description_cn []
```

### 9.5 YAML 安全序列化验证

验证 `description` / `description_cn` 包含冒号和中文时，生成的 frontmatter 仍能被 parser 正确解析。

### 9.6 `tool-builder` 生成规则验证

验证后续新生成项目级 Python Tool 不会漏写 `description_cn`：

```bash
PYTHONPATH="$PWD" uv run --frozen pytest tests/skills/test_tool_validator.py
```

验收标准：

- 有效 Python Tool 样例必须包含 `description_cn` 并通过校验。
- 缺失 `description_cn` 的 Python Tool 必须产生 `FAIL`。
- 现有项目级 Python Tool 经 validator 检查为 `0 FAIL`。

### 9.7 Session Prompt 行为验证

Session Prompt 中文化阶段必须增加行为验证，不只做文本检查。

建议验证用例：

```text
1. 中文问答：用户中文提问，默认中文回答。
2. 工具调用：中文任务仍能正确选择 read/edit/bash 等工具。
3. Plan 模式：中文提示不改变 plan 进入、退出和确认规则。
4. 高风险操作：删除、reset、push、tag 等仍会要求确认。
5. 安全拒绝：恶意或越权请求仍按原策略拒绝。
6. 多模型一致性：anthropic/gemini/qwen 等 profile 至少各跑一组核心路径。
7. 品牌残留：branch2 发布态检查 `Flocks`、`Rex` 等词是否符合允许清单。
```

验收标准：中文变体与英文基线在工具调用、安全边界和流程控制上无语义弱化。

### 9.8 Git diff 格式验证

```bash
git diff --check
```

验收标准：无 whitespace/error 输出。

## 10. 风险与控制

| 风险 | 控制方式 |
|---|---|
| Tool `description` 被直接中文化导致模型行为变化 | Tool 层只新增 `description_cn`，保留 `description` |
| 动态 Agent 被静态 `prompt.md` 覆盖 | 动态 Agent 只改 `prompt_builder.py`，不新增 `prompt.md` |
| YAML 描述中包含冒号导致 Skill 解析失败 | 长描述使用 block scalar；API 使用 `yaml.safe_dump` |
| 中文描述缺失导致 UI 回退英文 | 动态展示统一采用 `description_cn` 优先、`description` fallback |
| Skill 正文旧版本覆盖当前内容 | 本轮不批量覆盖 Skill 正文，只处理 metadata/frontmatter 和链路 |
| Session Prompt 直接全量翻译导致模型行为漂移 | 保留英文基线，新增 `*.zh.txt` 变体，通过 locale/profile 选择并做行为回归 |
| 中文 prompt 弱化安全或权限约束 | 安全、权限、工具调用段落逐条语义对齐，验证高风险操作确认和拒绝策略 |
| 多模型 prompt 中文化后风格不一致 | 按模型 profile 分别维护中文变体，不用一份中文 prompt 覆盖所有模型 |

## 11. 分阶段落地与回滚

当前中文化设计不应一次性全量发布。落地执行请以以下两个文档为准：

```text
docs/CN_LOCALIZATION_COMMIT_AUDIT.md
docs/CN_LOCALIZATION_ROLLOUT_PLAN.md
docs/CN_LOCALIZATION_DIFF_SPLIT.md
```

执行原则：

1. 文档、字段、API 透传优先。
2. WebUI 展示按现有 i18n 语言选择中文或英文描述，不额外新增灰度开关。
3. Agent / Session Prompt 等进入模型上下文的改动用独立 commit 做上线测试和回滚边界。
4. 发布态品牌替换和目录裁剪放在 branch2 自动化转换中完成。
5. 出问题时优先 revert 对应 commit；只有语言选择或 fallback 机制本身需要配置时才保留 locale 选择。

## 12. 后续可选增强

1. 为 Tool 参数引入 `description_cn`，并在 API/UI 参数说明中使用。
2. 为 tool search / skill search 增加中文关键词匹配能力。
3. 为运行时输出、错误信息、日志建立统一本地化策略。
4. 对 Skill 正文逐个进行版本感知的中文化合并。
5. 引入 locale 配置，在 UI/API 层明确选择展示语言。
6. 为 `flocks/session/prompt` 增加 `*.zh.txt` 变体和加载器选择逻辑。
7. 建立 prompt 行为回归集，覆盖中文对话、工具调用、plan、安全拒绝和品牌残留检查。
