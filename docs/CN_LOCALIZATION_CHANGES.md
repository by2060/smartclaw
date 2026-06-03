# Flocks Tool + Agent + Skill 中文化改造细节

> 原始整理时间：2026-05-25
> 当前实施基线：当前分支上的最新 `feature/tool-data-permission-control`
> 参考来源：`feature/cn-localization` 中的 `b4e7e9ca`、`167865a3`、`c3f6313d`、`5edcaaf5`，以及 Session Prompt 中文化提交 `080ece3`
> 范围：`flocks/tool`、`flocks/agent`、`flocks/skill`、`flocks/server/routes/skill.py`、项目级 `.flocks/plugins/skills/*/SKILL.md`、`.flocks/plugins/skills/tool-builder/validator.py`、项目级 `.flocks/plugins/tools/python/*.py`、`webui/src/pages/Tool`、`flocks/session/prompt`、`tests/skills/test_tool_validator.py`、`tests/session/test_prompt_tokens.py`
> 对应方案文档：`docs/CN_LOCALIZATION_DESIGN.md`
> 重要更新：当前中文化落地改为在当前分支从 0 重做；PR-0 到 PR-8 参考四个源 commit，PR-9+ 单独参考 Session Prompt 中文化提交；按多个独立 commit 拆分以便独立验证和回滚。

## 1. 本轮最终方案

本轮将原 Tool 单项中文化整理扩展为 Tool + Agent + Skill 三层方案。

最终策略：

| 层级 | 主要策略 | 是否直接改中文正文 |
|---|---|---|
| Tool | 保留 `description`，补齐 `description_cn` | 否，避免影响模型 tool schema |
| Agent metadata | 保留 `description`，补齐 `description_cn` | metadata 不直接替换 |
| Agent prompt | 静态 prompt 直接中文化；动态 prompt 在 builder 内中文化 | 是 |
| Skill metadata | 保留 `description`，解析并暴露 `description_cn` | metadata 不直接替换 |
| Skill 正文 | 本轮不批量覆盖正文 | 否 |
| WebUI Tool 页面 | 中文环境优先展示 `description_cn`，缺失时回退 `description` | 否 |

与早期 C 阶段实现思路相比，本轮修正了三个关键问题：

1. Tool 不再直接替换 `description`，避免改变模型侧工具描述。
2. 动态 Agent 不新增 `prompt.md`，避免覆盖 `prompt_builder.py`。
3. Skill 的 `description_cn` 不再只是 frontmatter 字段，而是进入 parser、API、Agent 注入链路。

## 2. 改动规模

当前 tracked 代码改动统计：

```text
70 files changed, 1785 insertions(+), 1028 deletions(-)
```

其中包含：

- 既有 Tool 中文化改动。
- 本轮新增 Agent 中文化改动。
- 本轮新增 Skill `description_cn` 链路改动。
- 本轮修复项目级 Skill frontmatter。
- 本轮补齐项目级 Python Tool 中文描述，并修复 WebUI Tool 页面描述字段消费链路。
- 本轮补充 `tool-builder` 生成规范和 validator，避免后续新生成 Python Tool 漏写 `description_cn`。

文档文件：

```text
docs/CN_LOCALIZATION_DESIGN.md
docs/CN_LOCALIZATION_CHANGES.md
```

## 3. Tool 层改造细节

### 3.1 插件工具加载链路

#### `flocks/tool/tool_loader.py`

改动：YAML 插件工具加载时透传 `description_cn`。

效果：插件 YAML 中声明的中文描述可以进入 `ToolInfo.description_cn`。

```yaml
description: English description
description_cn: 中文描述
```

#### `flocks/tool/registry.py`

改动：

- Python declarative plugin `TOOLS` dict 支持 `description_cn`。
- registry 内置示例工具补充中文描述。

效果：Python 插件也可声明中文描述。

```python
TOOLS = [
    {
        "name": "example_tool",
        "description": "English description",
        "description_cn": "中文描述",
    }
]
```

### 3.2 动态描述工具

动态描述工具不能只添加静态常量，本轮为其补齐中文动态构造逻辑。

| 文件 | 工具 | 改动内容 |
|---|---|---|
| `flocks/tool/code/bash.py` | `bash` | 新增 `get_description_cn(directory)`，注册时传入动态中文描述 |
| `flocks/tool/web/websearch.py` | `websearch` | 新增 `get_description_cn()`，中文描述保留当前日期插值 |
| `flocks/tool/task/run_workflow.py` | `run_workflow` | 新增中文 base description、中文缓存、`_build_description_cn()`，运行时同步 `tool.info.description_cn` |
| `flocks/tool/system/skill.py` | `skill` | 新增中文 Skill 列表描述构造，运行时同步 `tool.info.description_cn`；`get_all_skills(...)` / `get_skill(...)` 兼容 helper 返回 `description_cn`，保证旧 `/skill` 兼容接口也可拿到中文描述 |

动态列表处理原则：

- 条目存在 `description_cn` 时优先使用。
- 缺失时回退到 `description`。

### 3.3 静态描述工具

这些文件新增中文描述常量，并在注册时传入 `description_cn`：

| 文件 | 工具名 | 改动方式 |
|---|---|---|
| `flocks/tool/code/codesearch.py` | `codesearch` | 新增 `DESCRIPTION_CN`，注册透传 |
| `flocks/tool/code/grep.py` | `grep` | 新增 `DESCRIPTION_CN`，注册透传 |
| `flocks/tool/code/lsp_tool.py` | `lsp` | 新增 `DESCRIPTION_CN`，注册透传 |
| `flocks/tool/file/apply_patch.py` | `apply_patch` | 新增 `DESCRIPTION_CN`，注册透传 |
| `flocks/tool/file/edit.py` | `edit` | 新增 `DESCRIPTION_CN`，注册透传 |
| `flocks/tool/file/glob.py` | `glob` | 新增 `DESCRIPTION_CN`，注册透传 |
| `flocks/tool/file/list_tool.py` | `list` | 新增 `DESCRIPTION_CN`，注册透传 |
| `flocks/tool/file/multiedit.py` | `multiedit` | 新增 `DESCRIPTION_CN`，注册透传 |
| `flocks/tool/file/read.py` | `read` | 新增 `DESCRIPTION_CN`，注册透传 |
| `flocks/tool/file/write.py` | `write` | 新增 `DESCRIPTION_CN`，注册透传 |
| `flocks/tool/invalid.py` | `invalid` | 新增 `DESCRIPTION_CN`，注册透传 |
| `flocks/tool/skill/flocks_skills.py` | `flocks_skills` | 新增 `_DESCRIPTION_CN`，注册透传 |
| `flocks/tool/system/batch.py` | `batch` | 新增 `DESCRIPTION_CN`，注册透传 |
| `flocks/tool/system/question.py` | `question` | 新增 `DESCRIPTION_CN`，注册透传 |
| `flocks/tool/system/tool_search.py` | `tool_search` | 新增 `DESCRIPTION_CN`，注册透传 |
| `flocks/tool/task/run_workflow_node.py` | `run_workflow_node` | 新增 `DESCRIPTION_CN`，注册透传 |
| `flocks/tool/task/task.py` | `task` | 新增 `DESCRIPTION_CN`，注册透传 |
| `flocks/tool/web/webfetch.py` | `webfetch` | 新增 `DESCRIPTION_CN`，注册透传 |

### 3.4 内联描述工具

这些文件原本在注册装饰器中直接写 `description`。本轮保留原描述，并补充 `description_cn`：

| 文件 | 工具名 | 改动方式 |
|---|---|---|
| `flocks/tool/agent/call_omo_agent.py` | `call_omo_agent` | 注册中新增 `description_cn` |
| `flocks/tool/agent/delegate_task.py` | `delegate_task` | 注册中新增 `description_cn` |
| `flocks/tool/channel/channel_message.py` | `channel_message` | 注册中新增 `description_cn` |
| `flocks/tool/file/doc_parser.py` | `doc_parser` | 注册中新增 `description_cn` |
| `flocks/tool/file/file_search.py` | `file_search` | 注册中新增 `description_cn` |
| `flocks/tool/security/ssh_host_cmd.py` | `ssh_host_cmd` | 注册中新增 `description_cn` |
| `flocks/tool/security/ssh_run_script.py` | `ssh_run_script` | 注册中新增 `description_cn` |
| `flocks/tool/system/background_cancel.py` | `background_cancel` | 注册中新增 `description_cn` |
| `flocks/tool/system/background_output.py` | `background_output` | 注册中新增 `description_cn` |
| `flocks/tool/system/memory.py` | `memory_search` / `memory_get` / `memory_write` | 注册中新增 `description_cn` |
| `flocks/tool/system/model_config.py` | `list_providers` / `add_provider` / `add_model` | 新增中文描述常量并注册透传 |
| `flocks/tool/system/slash_command.py` | `run_slash_command` | 新增 `_COMMAND_DESCRIPTIONS_CN` / `_TOOL_DESCRIPTION_CN` 并注册透传 |
| `flocks/tool/task/plan.py` | `plan_enter` / `plan_exit` | 新增中文描述常量并注册透传 |
| `flocks/tool/task/task_center.py` | `task_create` / `task_list` / `task_status` / `task_update` / `task_delete` / `task_rerun` | 注册中新增 `description_cn` |
| `flocks/tool/task/todo.py` | `todowrite` / `todoread` | 新增中文描述常量并注册透传 |

### 3.5 已是中文描述的工具

这些文件原本 `description` 就是中文。本轮不反向改英文，只补充相同或等价 `description_cn`：

| 文件 | 工具名 | 改动方式 |
|---|---|---|
| `flocks/tool/system/session_manage.py` | `session_list` / `session_get` / `session_create` / `session_update` / `session_delete` / `session_archive` | 保留中文 `description`，补充 `description_cn` |
| `flocks/tool/wecom/wecom_mcp.py` | `wecom_mcp` | 保留中文 `description`，补充 `description_cn` |

### 3.6 项目级 Python Tool 补充

截图中出现的两个项目级 Python Tool 原本只有英文 `description`，即使 WebUI 改为中文优先也会回退英文。本轮补齐：

| 文件 | 工具名 | 改动方式 |
|---|---|---|
| `.flocks/plugins/tools/python/dify_kb_search.py` | `dify_kb_search` | 注册中新增 `description_cn` |
| `.flocks/plugins/tools/python/flocks_mcp.py` | `flocks_mcp` | 注册中新增 `description_cn` |

### 3.7 WebUI Tool 页面展示链路

问题：`/tools` 页面部分表格和详情面板直接渲染 `tool.description`，没有使用已有的 `getLocalizedToolDescription(...)`，导致中文 UI 下仍显示英文描述。

改动：

- 全量工具表格、MCP 详情工具表格、API 服务详情工具表格改为中文环境优先展示 `description_cn`。
- Tool 详情面板改为中文环境优先展示 `description_cn`。
- MCP tab 搜索逻辑补充 `description_cn` 匹配。

涉及文件：

```text
webui/src/pages/Tool/index.tsx
webui/src/pages/Tool/components/ServiceDetailPanel.tsx
webui/src/pages/Tool/components/ToolTable.tsx
webui/src/pages/Tool/components/ToolDetailModal.tsx
webui/src/pages/Tool/components/MCPTabContent.tsx
```

## 4. Agent 层改造细节

### 4.1 Agent context model

#### `flocks/agent/agent.py`

改动：

- `AvailableAgent` 增加 `description_cn`。
- `AvailableSkill` 增加 `description_cn`。

作用：动态 prompt builder 接收的上下文对象可以携带中文描述。

关键位置：

- `flocks/agent/agent.py:73`
- `flocks/agent/agent.py:89`

### 4.2 Agent registry 注入链路

#### `flocks/agent/registry.py`

改动：

- 默认 prompt metadata 生成时优先使用 `agent.description_cn`。
- 构造 `AvailableAgent` 时透传 `description_cn`。
- 构造 `AvailableSkill` 时透传 `description_cn`。
- Agent reload/update 时同步 `description_cn`。

关键位置：

- `flocks/agent/registry.py:116`
- `flocks/agent/registry.py:134`
- `flocks/agent/registry.py:192`
- `flocks/agent/registry.py:326`

效果：Rex、Hephaestus 等动态 Agent 中展示的 Agent / Skill 表格可以使用中文描述。

### 4.3 共享动态 prompt 片段

#### `flocks/agent/prompt_utils.py`

改动：

- 新增/使用 `_display_description(...)`，优先读取 `description_cn`。
- 中文化“关键触发词”“工具与智能体选择”“分类 + 技能委派系统”等公共段落。
- Agent 表格、Skill 表格都使用中文描述优先。

关键位置：

- `flocks/agent/prompt_utils.py:94`
- `flocks/agent/prompt_utils.py:125`
- `flocks/agent/prompt_utils.py:149`
- `flocks/agent/prompt_utils.py:218`
- `flocks/agent/prompt_utils.py:223`
- `flocks/agent/prompt_utils.py:348`

### 4.4 Agent YAML `description_cn`

已补齐 11 个内置 Agent 的中文描述：

| 文件 | 中文描述用途 |
|---|---|
| `flocks/agent/agents/rex/agent.yaml` | 主编排 Agent 中文描述 |
| `flocks/agent/agents/rex_junior/agent.yaml` | 专注执行 Agent 中文描述 |
| `flocks/agent/agents/hephaestus/agent.yaml` | 自主深度工作 Agent 中文描述 |
| `flocks/agent/agents/librarian/agent.yaml` | 代码库理解 Agent 中文描述 |
| `flocks/agent/agents/explore/agent.yaml` | 快速代码探索 Agent 中文描述 |
| `flocks/agent/agents/oracle/agent.yaml` | 只读顾问 Agent 中文描述 |
| `flocks/agent/agents/metis/agent.yaml` | 预规划顾问 Agent 中文描述 |
| `flocks/agent/agents/momus/agent.yaml` | 计划审核 Agent 中文描述 |
| `flocks/agent/agents/multimodal_looker/agent.yaml` | 多模态分析 Agent 中文描述 |
| `flocks/agent/agents/self_enhance/agent.yaml` | 能力扩展 Agent 中文描述 |
| `flocks/agent/agents/plan/agent.yaml` | 内部计划模式 Agent 中文描述 |

注意：本轮修正了旧 C 阶段可能出现的 YAML 插入位置问题，`description_cn` 放在完整 `description: >-` block 后面。

### 4.5 静态 Agent prompt 中文化

以下 Agent 使用静态 `prompt.md`，本轮直接中文化正文：

| 文件 | 改动方式 |
|---|---|
| `flocks/agent/agents/explore/prompt.md` | 静态 prompt 中文化 |
| `flocks/agent/agents/oracle/prompt.md` | 静态 prompt 中文化 |
| `flocks/agent/agents/metis/prompt.md` | 静态 prompt 中文化 |
| `flocks/agent/agents/momus/prompt.md` | 静态 prompt 中文化 |
| `flocks/agent/agents/multimodal_looker/prompt.md` | 静态 prompt 中文化 |
| `flocks/agent/agents/self_enhance/prompt.md` | 静态 prompt 中文化 |

### 4.6 动态 Agent prompt builder 中文化

以下 Agent 使用 `prompt_builder.py`，本轮没有新增 `prompt.md`，而是在 builder 内中文化模板：

| 文件 | 改动方式 | 保留能力 |
|---|---|---|
| `flocks/agent/agents/rex/prompt_builder.py` | 主模板中文化 | 保留动态工具、Agent、Skill、workflow、任务管理等占位符替换 |
| `flocks/agent/agents/rex_junior/prompt_builder.py` | 默认 prompt 中文化 | 保留 `prompt_append` 拼接能力 |
| `flocks/agent/agents/hephaestus/prompt_builder.py` | 主模板中文化 | 保留动态委派表、技能、工具选择协议 |
| `flocks/agent/agents/librarian/prompt_builder.py` | 主模板中文化 | 保留当前年份 / 上一年动态计算 |

该处理避免了旧 C 阶段“用静态 prompt 覆盖动态 builder”的风险。

## 5. Skill 层改造细节

### 5.1 SkillInfo 增加中文描述字段

#### `flocks/skill/skill.py`

改动：

- `SkillInfo` 增加 `description_cn`。
- `_parse_skill_md(...)` 解析 `description_cn`。
- 兼容读取 `descriptionCn`。
- 构造 `SkillInfo` 时写入中文描述。

关键位置：

- `flocks/skill/skill.py:64`
- `flocks/skill/skill.py:129`
- `flocks/skill/skill.py:130`
- `flocks/skill/skill.py:132`
- `flocks/skill/skill.py:165`

效果：`SKILL.md` frontmatter 中的中文描述真正进入运行时 Skill 对象。

### 5.2 Skill API 暴露和写入 `description_cn`

#### `flocks/server/routes/skill.py`

改动：

- `SkillResponse` 增加 `description_cn`。
- `SkillCreateRequest` 增加 `description_cn`。
- Skill list/detail response 返回 `description_cn`。
- create/update 返回 `description_cn`。
- 新增 `_build_skill_frontmatter(...)`，使用 `yaml.safe_dump` 安全生成 YAML frontmatter。
- create/update 使用 `_build_skill_frontmatter(req)`。

关键位置：

- `flocks/server/routes/skill.py:141`
- `flocks/server/routes/skill.py:159`
- `flocks/server/routes/skill.py:163`
- `flocks/server/routes/skill.py:170`
- `flocks/server/routes/skill.py:172`
- `flocks/server/routes/skill.py:306`
- `flocks/server/routes/skill.py:502`
- `flocks/server/routes/skill.py:548`

效果：通过 API 创建/更新 Skill 时，包含冒号、中文的描述不再破坏 YAML frontmatter。

### 5.3 项目级 Skill frontmatter 修复

#### `.flocks/plugins/skills/detect-malicious-skill/SKILL.md`

问题：缺少标准 `---` frontmatter 边界，导致 parser 无法按 YAML frontmatter 解析。

改动：

- 补齐起止 `---`。
- 保留/补充 `description_cn`。

#### `.flocks/plugins/skills/tool-builder/SKILL.md`

问题：原一行 `description` 中包含 `When to use:` 等冒号，YAML 解析失败。

改动：

- `description` 改为 `>-` block scalar。
- `description_cn` 改为 `>-` block scalar。

#### `.flocks/plugins/skills/onesec-use/SKILL.md`

问题：原一行长描述中包含冒号和复杂符号，YAML 解析失败。

改动：

- `description` 改为 `>-` block scalar。
- `description_cn` 改为 `>-` block scalar。

### 5.4 Skill 正文语言规则补充

#### `.flocks/plugins/skills/agent-builder/SKILL.md`

问题：`agent-builder` 正文用于生成新 Agent 的 `agent.yaml` 和 `prompt.md`。如果用户使用中文，但 Skill 正文中的模板和原则偏英文，生成结果可能偏英文或中英文混杂。

改动：在 Prompt writing principles 中补充中文用户场景下的输出语言规则：

```text
当用户使用中文时，生成的 agent.yaml description、prompt.md 正文和最终说明应优先使用中文；配置字段、文件名、工具名保持英文原样。
```

效果：加载 `agent-builder` 后，模型在生成 Agent 配置、Prompt 正文和最终说明时有明确的中文优先约束，同时保留配置字段、文件名和工具名的英文原样。

### 5.5 `tool-builder` 生成工具防漏规则

#### `.flocks/plugins/skills/tool-builder/SKILL.md`

问题：项目级 Python Tool 通常由会话加载 `tool-builder` Skill 后生成。原 Python Tool 模板只包含 `description`，没有要求生成 `description_cn`，因此后续新生成的 `.flocks/plugins/tools/python/*.py` 仍可能只展示英文。

改动：

- 将“双语描述”要求从 API service 扩展为所有生成模式。
- Python Tool 示例 decorator 新增 `description_cn`。
- Python Tool 校验清单新增 `description_cn` 必填要求。

#### `.flocks/plugins/skills/tool-builder/validator.py`

改动：Mode B Python Tool 校验读取 `description_cn=`，缺失或空值时直接 `FAIL`。

#### `tests/skills/test_tool_validator.py`

改动：

- 有效 Python Tool 样例补充 `description_cn`。
- 新增缺失 `description_cn` 会失败的测试。

效果：后续通过 `tool-builder` 生成 Python Tool 时，若漏写中文描述，会在验证阶段被拦截，不需要再等 UI 暴露英文回退后手动补。

### 5.6 已检查但无需改动的 Skill

`.flocks/plugins/skills` 下共检查到 15 个项目级 Skill 目录。除上述 4 个已改动 `SKILL.md` 文件外，其他 11 个 `SKILL.md` 均满足本轮 metadata / frontmatter 范围要求：

- YAML frontmatter 可解析。
- 存在 `description`。
- 存在 `description_cn`。

无需改动清单：

| Skill | 结论 |
|---|---|
| `browser-use` | frontmatter 可解析，已有 `description_cn` |
| `find-skills` | frontmatter 可解析，已有 `description_cn` |
| `ndr-alert-analysis` | frontmatter 可解析，已有 `description_cn` |
| `onboarding` | frontmatter 可解析，已有 `description_cn` |
| `onesig-use` | frontmatter 可解析，已有 `description_cn` |
| `qingteng-use` | frontmatter 可解析，已有 `description_cn` |
| `skyeye-sensor-data-fetch` | frontmatter 可解析，已有 `description_cn` |
| `skyeye-use` | frontmatter 可解析，已有 `description_cn` |
| `tdp-use` | frontmatter 可解析，已有 `description_cn` |
| `web2cli` | frontmatter 可解析，已有 `description_cn` |
| `workflow-builder` | frontmatter 可解析，已有 `description_cn` |

这些 Skill 目录下的 `references/`、`scripts/`、`validator.py` 等辅助文件不参与 Skill 元数据 frontmatter 解析，本轮没有纳入“metadata 中文展示链路”改造范围。

## 6. 完整文件清单

### 6.1 Skill 文件

```text
.flocks/plugins/skills/agent-builder/SKILL.md
.flocks/plugins/skills/detect-malicious-skill/SKILL.md
.flocks/plugins/skills/onesec-use/SKILL.md
.flocks/plugins/skills/tool-builder/SKILL.md
.flocks/plugins/skills/tool-builder/validator.py
flocks/skill/skill.py
flocks/server/routes/skill.py
tests/skills/test_tool_validator.py
```

### 6.2 Agent 文件

```text
flocks/agent/agent.py
flocks/agent/registry.py
flocks/agent/prompt_utils.py
flocks/agent/agents/explore/agent.yaml
flocks/agent/agents/explore/prompt.md
flocks/agent/agents/hephaestus/agent.yaml
flocks/agent/agents/hephaestus/prompt_builder.py
flocks/agent/agents/librarian/agent.yaml
flocks/agent/agents/librarian/prompt_builder.py
flocks/agent/agents/metis/agent.yaml
flocks/agent/agents/metis/prompt.md
flocks/agent/agents/momus/agent.yaml
flocks/agent/agents/momus/prompt.md
flocks/agent/agents/multimodal_looker/agent.yaml
flocks/agent/agents/multimodal_looker/prompt.md
flocks/agent/agents/oracle/agent.yaml
flocks/agent/agents/oracle/prompt.md
flocks/agent/agents/plan/agent.yaml
flocks/agent/agents/rex/agent.yaml
flocks/agent/agents/rex/prompt_builder.py
flocks/agent/agents/rex_junior/agent.yaml
flocks/agent/agents/rex_junior/prompt_builder.py
flocks/agent/agents/self_enhance/agent.yaml
flocks/agent/agents/self_enhance/prompt.md
```

### 6.3 Tool 文件

```text
.flocks/plugins/tools/python/dify_kb_search.py
.flocks/plugins/tools/python/flocks_mcp.py
flocks/tool/agent/call_omo_agent.py
flocks/tool/agent/delegate_task.py
flocks/tool/channel/channel_message.py
flocks/tool/code/bash.py
flocks/tool/code/codesearch.py
flocks/tool/code/grep.py
flocks/tool/code/lsp_tool.py
flocks/tool/file/apply_patch.py
flocks/tool/file/doc_parser.py
flocks/tool/file/edit.py
flocks/tool/file/file_search.py
flocks/tool/file/glob.py
flocks/tool/file/list_tool.py
flocks/tool/file/multiedit.py
flocks/tool/file/read.py
flocks/tool/file/write.py
flocks/tool/invalid.py
flocks/tool/registry.py
flocks/tool/security/ssh_host_cmd.py
flocks/tool/security/ssh_run_script.py
flocks/tool/skill/flocks_skills.py
flocks/tool/system/background_cancel.py
flocks/tool/system/background_output.py
flocks/tool/system/batch.py
flocks/tool/system/memory.py
flocks/tool/system/model_config.py
flocks/tool/system/question.py
flocks/tool/system/session_manage.py
flocks/tool/system/skill.py
flocks/tool/system/slash_command.py
flocks/tool/system/tool_search.py
flocks/tool/task/plan.py
flocks/tool/task/run_workflow.py
flocks/tool/task/run_workflow_node.py
flocks/tool/task/task.py
flocks/tool/task/task_center.py
flocks/tool/task/todo.py
flocks/tool/tool_loader.py
flocks/tool/web/webfetch.py
flocks/tool/web/websearch.py
flocks/tool/wecom/wecom_mcp.py
```

### 6.4 WebUI 文件

```text
webui/src/pages/Tool/index.tsx
webui/src/pages/Tool/components/MCPTabContent.tsx
webui/src/pages/Tool/components/ServiceDetailPanel.tsx
webui/src/pages/Tool/components/ToolDetailModal.tsx
webui/src/pages/Tool/components/ToolTable.tsx
```

### 6.5 文档文件

```text
docs/CN_LOCALIZATION_DESIGN.md
docs/CN_LOCALIZATION_CHANGES.md
```

## 7. 验证记录

### 7.1 Python 编译验证

命令：

```bash
python3 -m compileall -q \
  "$PWD/flocks/agent" \
  "$PWD/flocks/skill" \
  "$PWD/flocks/server/routes/skill.py"
```

结果：通过，无输出。

### 7.2 Agent 加载验证

命令：

```bash
PYTHONPATH="$PWD" uv run --frozen python - <<'PY'
from pathlib import Path
from flocks.agent.agent_factory import load_agent
root = Path.cwd() / 'flocks/agent/agents'
failed = []
missing_description_cn = []
for agent_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith('_')):
    agent = load_agent(agent_dir)
    if agent is None:
        failed.append(str(agent_dir))
        continue
    if agent.name != 'plan' and not getattr(agent, 'description_cn', None):
        missing_description_cn.append(agent.name)
print('failed', failed)
print('missing_description_cn', missing_description_cn)
PY
```

结果：

```text
failed []
missing_description_cn []
```

### 7.3 Skill parser 验证

命令：

```bash
PYTHONPATH="$PWD" uv run --frozen python - <<'PY'
from pathlib import Path
from flocks.skill.skill import Skill
root = Path.cwd() / '.flocks/plugins/skills'
failed = []
missing_description_cn = []
for path in sorted(root.glob('*/SKILL.md')):
    skill = Skill._parse_skill_md(str(path), source='project')
    if skill is None:
        failed.append(str(path))
        continue
    if not getattr(skill, 'description_cn', None):
        missing_description_cn.append(skill.name)
print('failed', failed)
print('missing_description_cn', missing_description_cn)
PY
```

结果：

```text
failed []
missing_description_cn []
```

### 7.4 Skill frontmatter 安全序列化验证

命令：

```bash
PYTHONPATH="$PWD" uv run --frozen python - <<'PY'
from flocks.server.routes.skill import SkillCreateRequest, _build_skill_frontmatter
from flocks.skill.skill import Skill
req = SkillCreateRequest(
    name='yaml-safe-test',
    description='Handles cases: colon and unicode 中文',
    description_cn='处理带冒号: 和中文的描述',
    content='body',
)
content = _build_skill_frontmatter(req) + req.content
parsed = Skill._parse_frontmatter(content)
print(parsed)
PY
```

结果：

```text
{'name': 'yaml-safe-test', 'description': 'Handles cases: colon and unicode 中文', 'description_cn': '处理带冒号: 和中文的描述'}
```

### 7.5 Git diff 格式验证

命令：

```bash
git -C "$PWD" diff --check
```

结果：通过，无输出。

### 7.6 WebUI Tool 描述字段验证

命令：

```bash
# 确认 Tool 页面不再直接渲染 {tool.description}
# 结果：No matches found
```

结果：`webui/src/pages/Tool/**/*.tsx` 中不再存在直接 JSX 渲染 `{tool.description}` 的位置；搜索过滤逻辑仍保留 `description` 并补充 `description_cn` 匹配。

构建验证：

```bash
npm --prefix "$PWD/webui" run build
```

结果：未完成，当前环境缺少 WebUI 本地依赖，`node_modules/.bin/tsc` 不存在，命令在执行 `tsc` 时返回 `tsc: command not found`。该失败发生在 TypeScript 编译开始前。

### 7.7 项目级 Python Tool 语法验证

命令：

```bash
PYTHONPATH="$PWD" uv run --frozen python -m py_compile \
  "$PWD/.flocks/plugins/tools/python/dify_kb_search.py" \
  "$PWD/.flocks/plugins/tools/python/flocks_mcp.py"
```

结果：通过，无输出。

### 7.8 `tool-builder` validator 验证

命令：

```bash
PYTHONPATH="$PWD" uv run --frozen pytest \
  "$PWD/tests/skills/test_tool_validator.py"
```

结果：通过，`12 passed`。

同时使用更新后的 validator 检查现有两个项目级 Python Tool：

```bash
PYTHONPATH="$PWD" uv run --frozen python \
  "$PWD/.flocks/plugins/skills/tool-builder/validator.py" \
  "$PWD/.flocks/plugins/tools/python/dify_kb_search.py"

PYTHONPATH="$PWD" uv run --frozen python \
  "$PWD/.flocks/plugins/skills/tool-builder/validator.py" \
  "$PWD/.flocks/plugins/tools/python/flocks_mcp.py"
```

结果：两个工具均为 `0 FAIL, 0 WARN`。

## 8. Session Prompt 层补充改造

> 补充时间：2026-05-27
> 范围：`flocks/session/prompt.py`、`flocks/session/prompt_locale.py`、`flocks/session/prompt_strings.py`、`flocks/session/runner.py`、`flocks/session/prompt/*.zh.txt`、`tests/session/test_prompt_tokens.py`

### 8.1 Locale 选择策略

本轮按方案实现 Session Prompt 的显式 locale 选择，默认仍保持英文基线，避免改变既有部署行为。

支持的环境变量：

```text
FLOCKS_SESSION_PROMPT_LOCALE=zh-CN
FLOCKS_PROMPT_LOCALE=zh-CN
FLOCKS_SESSION_PROMPT_AUTO_LOCALE=1
```

规则：

- 默认返回 `en-US`。
- `FLOCKS_SESSION_PROMPT_LOCALE` / `FLOCKS_PROMPT_LOCALE` 显式配置为 `zh`、`zh-CN`、`zh_CN` 等时返回 `zh-CN`。
- 仅当 `FLOCKS_SESSION_PROMPT_AUTO_LOCALE` 为 `1` / `true` / `yes` 时，才尝试读取 `FLOCKS_LOCALE`、`FLOCKS_LANGUAGE`、`FLOCKS_INSTALL_LANGUAGE` 或系统 locale。
- locale 选择逻辑集中在 `flocks/session/prompt_locale.py`，供文件 prompt 和内部 prompt string 复用。

### 8.2 Prompt 文件加载

#### `flocks/session/prompt.py`

改动：`_load_prompt_file(...)` 支持中文变体优先加载。

加载顺序：

```text
zh-CN: <name>.zh.txt -> <name>.txt
en-US: <name>.txt
```

效果：

- 设置 `FLOCKS_SESSION_PROMPT_LOCALE=zh-CN` 后，`SystemPrompt.provider(...)` 会优先读取 `.zh.txt`。
- 未配置中文 locale 时，仍读取原英文 prompt 文件。
- 如果某个 `.zh.txt` 缺失，会自动回退英文文件，降低发布风险。

### 8.3 System Prompt cache key

#### `flocks/session/runner.py`

改动：system prompt cache key 增加 `prompt_locale`。

原因：同一个 session / agent / model 在不同 prompt locale 下应生成不同 system prompt，不能复用旧缓存。

### 8.4 内部 prompt string 中文化

#### `flocks/session/prompt_strings.py`

改动：以下内部字符串增加 EN/ZH 双版本，并按 `is_zh_prompt_locale()` 选择：

| 常量 | 用途 | 中文化方式 |
|---|---|---|
| `PROMPT_COMPACTION` | 对话压缩摘要 | 增加 `PROMPT_COMPACTION_EN` / `PROMPT_COMPACTION_ZH` |
| `PROMPT_SUMMARY` | 会话摘要生成 | 增加 `PROMPT_SUMMARY_EN` / `PROMPT_SUMMARY_ZH` |
| `PROMPT_MAX_STEPS` | 达到最大步骤数后的纯文本回复 | 增加 `PROMPT_MAX_STEPS_EN` / `PROMPT_MAX_STEPS_ZH` |

本轮未直接改写 `PROMPT_TITLE` 和 `PROMPT_GENERATE` 主体，因为它们包含较多格式约束和 JSON 输出约束；后续如继续中文化，应优先采用 EN/ZH 变体而非覆盖英文基线。

### 8.5 新增 `.zh.txt` 变体

`flocks/session/prompt/` 下 12 个英文模板均已补齐对应中文变体：

```text
anthropic.zh.txt
anthropic-20250930.zh.txt
anthropic_spoof.zh.txt
beast.zh.txt
build-switch.zh.txt
codex_header.zh.txt
copilot-gpt-5.zh.txt
gemini.zh.txt
max-steps.zh.txt
plan-reminder-anthropic.zh.txt
plan.zh.txt
qwen.zh.txt
```

处理原则：

- `plan.zh.txt`、`plan-reminder-anthropic.zh.txt`、`max-steps.zh.txt`、`build-switch.zh.txt` 直接提供中文提醒文本。
- 模型基础 prompt 采用“中文化要求 + 英文基线 prompt”结构：中文用户默认中文输出，但保留英文原始安全边界、工具调用格式、模型 profile 和 SecOps 约束。
- 技术名词、工具名、命令、文件路径、配置字段、检测规则字段和安全标准保持英文原样。

### 8.6 测试覆盖

#### `tests/session/test_prompt_tokens.py`

新增测试覆盖：

- 默认 locale 为 `en-US`。
- `FLOCKS_SESSION_PROMPT_LOCALE=zh_CN` 解析为 `zh-CN`。
- `_load_prompt_file("anthropic.txt", prompt_locale="zh-CN")` 优先加载中文变体。
- `prompt_locale="en-US"` 时仍加载英文基线。

## 9. 当前未纳入范围的内容

1. Tool 参数描述未增加 `description_cn`。
2. Tool 运行时输出、错误文本、日志文本未系统中文化。
3. `tool_search` / Skill 搜索逻辑未增强中文关键词匹配。
4. Skill 正文未批量套用旧 C 阶段导出内容，避免覆盖当前新版 Skill。
5. 未实现完整前后端 locale runtime 联动，仅实现 Session Prompt 侧环境变量控制。
6. TUI 静态 prompt import 仍需单独接入 locale 选择；本轮先完成 Python session 层。

## 10. 当前工作区额外状态

当前工作区还包含此前分支流程设计、发版脚本和本轮 Session Prompt 中文化产生的未跟踪项。以 `git status` 为准，不在文档内固定维护完整清单。

本轮 Session Prompt 中文化新增/修改的主要文件为：

```text
flocks/session/prompt.py
flocks/session/prompt_locale.py
flocks/session/prompt_strings.py
flocks/session/runner.py
flocks/session/prompt/*.zh.txt
tests/session/test_prompt_tokens.py
docs/CN_LOCALIZATION_CHANGES.md
```
