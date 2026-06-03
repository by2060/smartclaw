# CN Localization Rollout Plan

> 状态：当前分支按独立 commit 重建中文化的分阶段落地方案
> 更新日期：2026-06-03
> 基线：最新 `feature/tool-data-permission-control`
> 参考来源：`feature/cn-localization` 中的 `b4e7e9ca`、`167865a3`、`c3f6313d`、`5edcaaf5`，以及 Session Prompt 中文化提交 `080ece3`
> 核心目标：在当前分支中按 PR-0 到 PR-9+ 对应的独立 commit 逐步实现中文化，每个 commit 可独立验证和回滚。

## 1. 本版关键修正

本轮不再以当前 `feature/cn-localization` 的最终文件状态作为直接实施目标，也不继续修补该分支上的历史回滚关系。

新的实施口径是：

```text
1. 先同步最新 feature/tool-data-permission-control。
2. 当前分支作为中文化落地分支。
3. 从当前基线开始重新实施中文化。
4. 只参考 cn-localization 分支中的四个源 commit：
   - b4e7e9ca
   - 167865a3
   - c3f6313d
   - 5edcaaf5
5. 不考虑其他 revert 情形作为实施依据。
6. 不整体 cherry-pick 大提交，必须拆成多个独立 commit。
7. Session Prompt 中文化作为 PR-9+ 单独追加，不混入 PR-0 到 PR-8。
```

## 2. 分支和 PR 策略

### 2.1 分支策略

```text
base branch：feature/tool-data-permission-control
working branch：当前分支 smartclaw-cn-localization
reference branch：feature/cn-localization，仅用于查看源 commit 内容
```

推荐流程：

```bash
git fetch origin
git switch feature/tool-data-permission-control
git pull --ff-only
git switch smartclaw-cn-localization
```

### 2.2 PR 策略

```text
PR-0：文档与实施计划
PR-1：数据模型字段
PR-2：后端 parser / registry / API 透传
PR-3：Skill metadata 内容补齐
PR-4：Tool description_cn 内容和动态描述
PR-5：WebUI 中文展示
PR-6：生成规范与防漏规则
PR-7：Agent metadata 和动态描述
PR-8：Agent prompt / prompt_builder 中文变体
PR-9+：Session Prompt 中文化
```

每个 PR 必须满足：

```text
独立 diff
独立测试
独立验收
独立回滚方式
不为灰度新增运行时开关；commit 本身作为上线、测试和回滚边界
只有语言选择或 fallback 机制本身需要配置时，才保留 locale 选择
```

## 3. 参考源 commit 与拆分原则

| 源 commit | 原始主题 | 拆分目标 |
|---|---|---|
| `5edcaaf5` | Skills 层中文本地化，添加 `description_cn` 字段 | PR-3 Skill metadata |
| `b4e7e9ca` | Tool + Agent + Skill 大范围中文化 | PR-1/2/3/4/7/8 拆分 |
| `167865a3` | `agent-builder` 中文生成规则 | PR-6 生成规范 |
| `c3f6313d` | WebUI 读取 `description_cn`、tool-builder 防漏、项目级 Python Tool 补齐 | PR-5 WebUI + PR-6 生成防漏 |

明确规则：

```text
不直接 cherry-pick b4e7e9ca。
不直接 cherry-pick c3f6313d。
不把 WebUI 展示和 tool-builder validator 混在一个 PR。
不把 Agent prompt 中文化和 metadata / API 透传混在一个 PR。
Session Prompt 中文化不混入 PR-0 到 PR-8，单独作为 PR-9+ 追加。
```

## 4. Phase / PR 详细计划

## 4.1 PR-0：文档与实施计划

### 目标

建立新分支中文化从 0 重建的计划、源 commit 审计和拆分表。

### 文件

```text
docs/CN_LOCALIZATION_COMMIT_AUDIT.md
docs/CN_LOCALIZATION_ROLLOUT_PLAN.md
docs/CN_LOCALIZATION_DIFF_SPLIT.md
docs/CN_LOCALIZATION_DESIGN.md
docs/CN_LOCALIZATION_CHANGES.md
docs/CN_LOCALIZATION_INDEX.md
```

### 验收

```text
文档明确 base branch 是 feature/tool-data-permission-control。
文档明确只参考四个源 commit。
文档明确 PR-0 到 PR-9+ 的拆分和回滚粒度。
文档明确 commit 版本级回滚取代运行时灰度开关。
```

### 回滚

只回滚文档 PR。

## 4.2 PR-1：数据模型字段

### 目标

先加入中文描述所需的可选字段，不改变任何消费逻辑。

### 主要来源

```text
b4e7e9ca
```

### 范围

```text
ToolInfo.description_cn
AgentInfo.description_cn
AvailableAgent.description_cn
AvailableSkill.description_cn
SkillInfo.description_cn
相关 Pydantic / dataclass / schema 字段
```

### 明确不做

```text
不让模型 tool schema 使用 description_cn。
不让 Agent prompt 使用 description_cn。
不让 WebUI 默认展示 description_cn。
不修改 prompt.md / prompt_builder.py。
```

### 验收

```text
旧数据缺失 description_cn 可正常加载。
description 原字段不变化。
序列化 / 反序列化兼容。
相关单元测试通过。
```

### 回滚

revert PR-1。正常情况下也可以保留可选字段但关闭后续消费。

## 4.3 PR-2：后端 parser / registry / API 透传

### 目标

让 `description_cn` 能被读取、注册、保存和返回，但不改变默认展示或模型上下文。

### 主要来源

```text
b4e7e9ca
```

### 范围

```text
flocks/tool/tool_loader.py
flocks/tool/registry.py
flocks/skill/skill.py
flocks/server/routes/skill.py
flocks/agent/registry.py
```

### 明确不做

```text
不改变 tool schema 的 description 来源。
不把 description_cn 注入模型 prompt。
不改 WebUI 展示默认值。
```

### 验收

```text
YAML Skill frontmatter 带中文、冒号、长文本可解析。
Python Tool / YAML Tool 的 description_cn 能进入 ToolInfo。
Skill list/detail API 能返回 description_cn。
缺失 description_cn 时不影响旧逻辑。
```

### 回滚

revert PR-2 对应 commit。

## 4.4 PR-3：Skill metadata 内容补齐

### 目标

补齐项目级 Skill 的 `description_cn`，必要时修复 YAML frontmatter 格式。

### 主要来源

```text
5edcaaf5
b4e7e9ca 中的 Skill frontmatter 修复
```

### 范围

```text
.flocks/plugins/skills/*/SKILL.md
```

### 明确不做

```text
不批量翻译 Skill 正文。
不修改 references、scripts、validator 业务逻辑。
不修改 Skill 执行流程。
```

### 格式要求

```yaml
---
name: example-skill
description: >-
  Existing English description that may contain colon: safely.
description_cn: >-
  中文描述，可以包含冒号：以及较长文本。
---
```

### 验收

```text
所有 SKILL.md frontmatter 可解析。
description 原文保留。
description_cn 非空且语义准确。
Skill list/detail API 正常。
```

### 回滚

单个 Skill 出错时只回滚对应 `SKILL.md`；parser 出错回滚 PR-2。

## 4.5 PR-4：Tool `description_cn` 内容和动态描述

### 目标

给内置 Tool 和动态描述 Tool 补齐中文描述字段，但不启用中文 tool schema。

### 主要来源

```text
b4e7e9ca
```

### 范围

```text
flocks/tool/agent/*
flocks/tool/channel/*
flocks/tool/code/*
flocks/tool/file/*
flocks/tool/security/*
flocks/tool/skill/*
flocks/tool/system/*
flocks/tool/task/*
flocks/tool/web/*
flocks/tool/wecom/*
```

### 明确不做

```text
不把 description 替换成中文。
不让模型 tool schema 使用 description_cn。
不改 Tool 参数描述。
不系统性翻译运行时输出、错误信息、日志。
```

### 验收

```text
Tool 注册中 description 保持英文或原内容。
description_cn 作为独立字段存在。
动态 Tool 运行时能生成 description_cn。
模型看到的 tool schema 仍使用 description。
```

### 回滚

revert PR-4 对应 commit。

## 4.6 PR-5：WebUI 中文展示

### 目标

让中文 UI 按现有 i18n 语言优先展示 `description_cn`，仅影响展示和搜索，不影响模型行为。

### 主要来源

```text
c3f6313d
```

### 范围

```text
webui/src/pages/Tool/index.tsx
webui/src/pages/Tool/components/MCPTabContent.tsx
webui/src/pages/Tool/components/ServiceDetailPanel.tsx
webui/src/pages/Tool/components/ToolDetailModal.tsx
webui/src/pages/Tool/components/ToolTable.tsx
webui/src/utils 或 i18n helper
```

### 验收

```text
中文语言：优先展示 description_cn。
英文语言：仍展示 description。
description_cn 缺失：回退 description。
搜索同时匹配 description 和 description_cn。
WebUI build 通过，或失败原因明确为环境缺依赖。
```

### 回滚

revert PR-5 对应 commit。

## 4.7 PR-6：生成规范与防漏规则

### 目标

保证后续新生成的 Agent / Python Tool 不倒退为只含英文描述或语言混杂。

### 主要来源

```text
167865a3
c3f6313d
```

### 范围

```text
.flocks/plugins/skills/agent-builder/SKILL.md
.flocks/plugins/skills/tool-builder/SKILL.md
.flocks/plugins/skills/tool-builder/validator.py
.flocks/plugins/tools/python/dify_kb_search.py
.flocks/plugins/tools/python/flocks_mcp.py
tests/skills/test_tool_validator.py
```

### 明确不做

```text
不和 WebUI 展示混在一个 PR。
不和 Agent prompt 大规模中文化混在一个 PR。
不在 PR-1/2 未完成前强依赖 description_cn 运行时消费。
```

### 验收

```text
中文用户创建 Agent 时，agent.yaml description、prompt.md 正文和最终说明优先中文。
配置字段、文件名、工具名保持英文原样。
Python Tool 模板包含 description 和 description_cn。
validator 能阻止缺失 description_cn 的 Python Tool。
tests/skills/test_tool_validator.py 覆盖通过和失败场景。
项目级 Python Tool 语法验证通过。
```

### 回滚

validator 误杀时回滚 validator 或放宽规则；Agent 生成异常时回滚 `agent-builder/SKILL.md` 对应规则。

## 4.8 PR-7：Agent metadata 和动态描述

### 目标

让 Agent / Skill 的动态表格和委派信息使用中文描述，缺失时回退英文描述。

### 主要来源

```text
b4e7e9ca
```

### 范围

```text
flocks/agent/agent.py
flocks/agent/registry.py
flocks/agent/prompt_utils.py
flocks/agent/agents/*/agent.yaml
```

### 明确不做

```text
不翻译 prompt.md。
不修改 prompt_builder.py 主行为模板。
不改变 Agent 名称、工具名、Skill 名称。
不改变委派规则。
```

### 验收

```text
动态 prompt 结构不变。
仅描述文案变中文。
Agent 委派、工具选择、Skill 触发行为无明显变化。
缺失 description_cn 时回退英文。
```

### 回滚

revert PR-7 对应 commit。

## 4.9 PR-8：Agent prompt / prompt_builder 中文变体

### 目标

为 Agent prompt 正文和动态 prompt builder 提供中文化能力，作为独立高风险 commit 单独验证和回滚。

### 主要来源

```text
b4e7e9ca
```

### 范围

```text
flocks/agent/agents/explore/prompt.md
flocks/agent/agents/hephaestus/prompt_builder.py
flocks/agent/agents/librarian/prompt_builder.py
flocks/agent/agents/metis/prompt.md
flocks/agent/agents/momus/prompt.md
flocks/agent/agents/multimodal_looker/prompt.md
flocks/agent/agents/oracle/prompt.md
flocks/agent/agents/rex/prompt_builder.py
flocks/agent/agents/rex_junior/prompt_builder.py
flocks/agent/agents/self_enhance/prompt.md
flocks/agent/prompt_utils.py
```

### 明确不做

```text
不删除动态 prompt_builder 能力。
不新增静态 prompt.md 覆盖动态 Agent。
不和 Session Prompt 中文化混在一起。
```

### 验收

```text
动态 Agent 仍保留年份、workflow、skills、agents、tools 注入。
中文模式下工具名、文件名、配置字段保持英文原样。
Agent 委派、工具选择、安全边界无明显退化。
至少覆盖中文问答、英文输入、工具调用、计划、拒绝高风险请求。
```

### 回滚

revert PR-8 对应 commit。

## 4.10 PR-9+：Session Prompt 中文化

### 目标

把 `feature/cn-localization` 中的 Session Prompt 中文化作为独立追加提交处理，不混入 Agent prompt 或 Tool/Skill 改动。

### 主要来源

```text
080ece3
```

### 范围

```text
flocks/session/prompt.py
flocks/session/prompt_locale.py
flocks/session/prompt_strings.py
flocks/session/runner.py
flocks/session/prompt/*.zh.txt
tests/session/test_prompt_tokens.py
```

### 验收

```text
英文基线 prompt 仍可加载。
中文 locale 下优先加载 .zh.txt，缺失时回退英文。
System Prompt cache key 区分 prompt locale。
Session prompt token 相关测试通过。
```

### 回滚

revert PR-9+ 对应 commit。

## 5. 不纳入本轮的内容

```text
旧的“中文本地化完成”提交及其回滚。
其他 revert commit。
branch2 发布转换脚本和 release-flow skill。
Tool 参数 description_cn。
运行时输出、错误信息、日志的系统中文化。
完整 runtime locale 框架。
```

branch2 发布转换脚本和 release-flow skill 如需保留，应单独作为发布工程任务，不和运行时中文化 commit 混合。

## 6. Locale / Fallback 机制汇总

| 机制 | 默认值 | 控制范围 | 首次引入建议 |
|---|---:|---|---|
| WebUI i18n language | 现有 UI 语言 | 决定是否优先展示 description_cn | PR-5 |
| `FLOCKS_SESSION_PROMPT_LOCALE` / `FLOCKS_PROMPT_LOCALE` | `en-US` | Session Prompt `.zh.txt` 加载选择 | PR-9+ |
| `FLOCKS_SESSION_PROMPT_AUTO_LOCALE` | 关闭 | 是否从环境语言自动推断 Session Prompt locale | PR-9+ |

## 7. 推荐验证命令

基础格式：

```bash
git diff --check
```

Python 编译：

```bash
python3 -m compileall -q flocks
```

Skill parser / validator：

```bash
PYTHONPATH="$PWD" uv run --frozen pytest tests/skill/test_skill.py tests/skills/test_tool_validator.py -q
```

Agent 相关测试：

```bash
PYTHONPATH="$PWD" uv run --frozen pytest tests/agent -q
```

Tool 相关测试：

```bash
PYTHONPATH="$PWD" uv run --frozen pytest tests/tool -q
```

WebUI：

```bash
npm --prefix webui run build
```

如果当前环境缺少 `node_modules`，WebUI build 失败应记录为环境问题，不应视为本阶段逻辑失败。

## 8. 回滚矩阵

| 故障现象 | 优先判断 | 立即止血 | 代码回滚 |
|---|---|---|---|
| API 客户端字段异常 | PR-2 API 透传 | 回滚对应 commit | revert PR-2 |
| Skill 列表加载失败 | PR-2 parser 或 PR-3 frontmatter | 回滚单个 `SKILL.md` | revert PR-2 / PR-3 |
| Tool 注册异常 | PR-4 Tool 内容 | 回滚对应 commit | revert PR-4 |
| UI 描述显示异常 | PR-5 WebUI 展示 | 回滚对应 commit | revert PR-5 |
| 新 Tool 生成失败 | PR-6 validator | 临时放宽 validator | revert PR-6 |
| 新 Agent 生成语言异常 | PR-6 agent-builder 规则 | 回滚对应 Skill 规则 | revert PR-6 |
| Agent 委派异常 | PR-7 中文动态描述 | 回滚对应 commit | revert PR-7 |
| Agent 行为异常 | PR-8 Agent prompt | 回滚对应 commit | revert PR-8 |
| Session Prompt 行为异常 | PR-9+ Session Prompt locale | 回滚对应 commit | revert PR-9+ |

## 9. 最终建议

按 PR-0 到 PR-9+ 顺序逐步推进。每个 PR 对应独立 commit，commit 本身作为测试、上线和回滚边界。

硬性规则：

```text
从 feature/tool-data-permission-control 新分支开始。
只参考 b4e7e9ca、167865a3、c3f6313d、5edcaaf5。
不考虑其他 revert 情形。
不整体 cherry-pick 大提交。
一个 PR 只解决一个层级，保证可回滚。
不为灰度新增运行时开关；只有语言选择或 fallback 机制本身需要配置时才保留 locale 选择。
```
