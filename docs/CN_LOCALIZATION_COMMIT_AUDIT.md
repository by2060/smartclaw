# CN Localization Source Commit Audit

> 更新时间：2026-06-03
> 用途：为新的中文化分支提供源 commit 输入清单和拆分依据。
> 新口径：以当前分支的最新 `feature/tool-data-permission-control` 状态为基线，从 0 重做；PR-0 到 PR-8 只参考 `feature/cn-localization` 中的 `b4e7e9ca`、`167865a3`、`c3f6313d`、`5edcaaf5` 四个提交；PR-9+ 单独参考 Session Prompt 中文化提交 `080ece3`。

## 1. 新分支实施口径

### 1.1 基线分支

```text
base：最新 feature/tool-data-permission-control
working branch：smartclaw-cn-localization
reference branch：feature/cn-localization，仅作为读取源 commit 的参考
```

建分支前必须先同步 base：

```bash
git fetch origin
git switch feature/tool-data-permission-control
git pull --ff-only
git switch smartclaw-cn-localization
```

实际分支名可按团队规范调整。

### 1.2 只参考的源 commit

```text
5edcaaf5：Skills 层中文本地化 - 添加 description_cn 字段
b4e7e9ca：smartClaw中文化0525版
167865a3：agent-builder 生成规范中文约束
c3f6313d：WebUI + tool-builder + Python Tool description_cn 尝试
080ece3：Session Prompt 中文化
```

### 1.3 不作为本轮实施依据的内容

```text
其他 revert commit：不纳入本轮重建依据。
旧的“中文本地化完成”提交及其回滚：不纳入本轮重建依据。
branch2 发布转换脚本和 release-flow skill：不纳入运行时中文化提交。
```

说明：`c3f6313d` 在新分支从 0 重做时作为 WebUI / tool-builder / Python Tool 方向的源输入。实现时不能整体 cherry-pick，应按 PR 拆分重新应用。

## 2. 总览表

| 源 commit | 主题 | 当前用途 | 影响层级 | 风险 | 推荐处理 | 对应 PR / Phase |
|---|---|---|---|---|---|---|
| `5edcaaf5` | Skill metadata 增加 `description_cn` | Skill 中文 metadata 内容来源 | Skill frontmatter | 低到中 | 重新应用并验证 YAML 解析 | PR-3 / Phase 3 |
| `b4e7e9ca` | Tool + Agent + Skill 大范围中文化 | 核心拆分来源 | Tool、Agent、Skill、API、prompt builder | 高 | 拆成字段、透传、Tool、Agent metadata、Agent prompt 多个 PR | PR-1/2/4/7/8 |
| `167865a3` | agent-builder 生成中文约束 | 未来生成 Agent 的语言规则 | Skill 正文规则、Agent 生成规范 | 中 | 单独合入或并入生成规范 PR | PR-6 / Phase 6 |
| `c3f6313d` | WebUI + tool-builder + Python Tool `description_cn` | WebUI 展示和生成防漏来源 | WebUI、tool-builder、项目级 Python Tool、测试 | 中 | 拆成 WebUI 展示 PR 与生成防漏 PR | PR-5/6 / Phase 5/6 |
| `080ece3` | Session Prompt 中文化 | Session Prompt locale 和 `.zh.txt` 变体来源 | Session Prompt、prompt cache、测试 | 高 | 单独追加，不混入 PR-0 到 PR-8 | PR-9+ |

## 3. 源 commit 明细

## 3.1 `5edcaaf5` — Skill metadata `description_cn`

### 原始规模

```text
15 files changed, 15 insertions(+)
```

### 原始涉及文件

```text
.flocks/plugins/skills/agent-builder/SKILL.md
.flocks/plugins/skills/browser-use/SKILL.md
.flocks/plugins/skills/detect-malicious-skill/SKILL.md
.flocks/plugins/skills/find-skills/SKILL.md
.flocks/plugins/skills/ndr-alert-analysis/SKILL.md
.flocks/plugins/skills/onboarding/SKILL.md
.flocks/plugins/skills/onesec-use/SKILL.md
.flocks/plugins/skills/onesig-use/SKILL.md
.flocks/plugins/skills/qingteng-use/SKILL.md
.flocks/plugins/skills/skyeye-sensor-data-fetch/SKILL.md
.flocks/plugins/skills/skyeye-use/SKILL.md
.flocks/plugins/skills/tdp-use/SKILL.md
.flocks/plugins/skills/tool-builder/SKILL.md
.flocks/plugins/skills/web2cli/SKILL.md
.flocks/plugins/skills/workflow-builder/SKILL.md
```

### 新分支处理

```text
建议作为 PR-3：Skill metadata 内容补齐。
```

要求：

```text
保留原 description。
新增 description_cn。
长文本或包含冒号的描述使用 YAML block scalar。
不批量翻译 Skill 正文。
不修改 references、scripts、validator 行为。
```

### 验收

```text
所有 SKILL.md frontmatter 可解析。
description 原字段保留。
description_cn 非空且语义准确。
Skill list/detail API 不因新增字段异常。
```

## 3.2 `b4e7e9ca` — Tool + Agent + Skill 大范围中文化

### 原始规模

```text
70 files changed, 1785 insertions(+), 1028 deletions(-)
```

### 原始涉及范围

```text
.flocks/plugins/skills/*/SKILL.md
flocks/agent/agent.py
flocks/agent/agents/*/agent.yaml
flocks/agent/agents/*/prompt.md
flocks/agent/agents/*/prompt_builder.py
flocks/agent/prompt_utils.py
flocks/agent/registry.py
flocks/server/routes/skill.py
flocks/skill/skill.py
flocks/tool/*
```

### 为什么不能整体应用

`b4e7e9ca` 把多个风险等级完全不同的能力放在一个提交中：

```text
低风险：可选字段、metadata 内容。
中风险：parser / registry / API 透传、动态描述。
高风险：Agent prompt.md、prompt_builder.py、prompt_utils 中文化。
```

如果整体 cherry-pick 到新分支，会导致：

```text
出问题时无法判断是字段、API、Tool、Agent metadata 还是 prompt 造成。
单个回滚会同时撤销多个层级。
PR 评审困难。
模型行为变化难以定位和回滚。
```

### 新分支拆分

`b4e7e9ca` 应拆成以下 PR：

```text
PR-1：数据模型字段，只加 description_cn 可选字段。
PR-2：后端 parser / registry / API 透传，不改变默认消费。
PR-4：Tool description_cn 内容和动态描述构造，不启用中文 tool schema。
PR-7：Agent metadata 和动态描述。
PR-8：Agent prompt / prompt_builder 中文变体，高风险，单独 commit。
```

### 验收重点

```text
description 不被 description_cn 覆盖。
API 只透传，不默认驱动模型上下文。
Agent 动态描述只改变文案，不改变委派结构。
Agent prompt 中文化必须单独行为回归。
```

## 3.3 `167865a3` — agent-builder 生成规范中文约束

### 原始规模

```text
1 file changed, 1 insertion
```

### 原始涉及文件

```text
.flocks/plugins/skills/agent-builder/SKILL.md
```

### 原始意图

在 `agent-builder` 的 prompt writing principles 中补充中文用户场景下的输出语言规则：

```text
当用户使用中文时，生成的 agent.yaml description、prompt.md 正文和最终说明应优先使用中文；配置字段、文件名、工具名保持英文原样。
```

### 新分支处理

```text
建议作为 PR-6：生成规范与防漏规则。
```

可以和 `c3f6313d` 中的 `tool-builder` 防漏规则放在同一个“生成规范”PR，但必须在 PR 描述中分清：

```text
agent-builder：影响未来 Agent 生成语言。
tool-builder：影响未来 Python Tool description_cn 是否必填。
```

### 验收

```text
中文用户创建 Agent 时，生成的 agent.yaml description、prompt.md 正文和最终说明优先中文。
配置字段、文件名、工具名保持英文原样。
```

## 3.4 `c3f6313d` — WebUI + tool-builder + Python Tool `description_cn`

### 原始规模

```text
10 files changed, 63 insertions(+), 18 deletions(-)
```

### 原始涉及文件

```text
.flocks/plugins/skills/tool-builder/SKILL.md
.flocks/plugins/skills/tool-builder/validator.py
.flocks/plugins/tools/python/dify_kb_search.py
.flocks/plugins/tools/python/flocks_mcp.py
tests/skills/test_tool_validator.py
webui/src/pages/Tool/components/MCPTabContent.tsx
webui/src/pages/Tool/components/ServiceDetailPanel.tsx
webui/src/pages/Tool/components/ToolDetailModal.tsx
webui/src/pages/Tool/components/ToolTable.tsx
webui/src/pages/Tool/index.tsx
```

### 原始意图

```text
WebUI Tool 页面中文环境优先展示 description_cn。
Tool 页面搜索补充 description_cn 匹配。
项目级 Python Tool 补 description_cn。
tool-builder 模板和 validator 要求生成 description_cn。
tests/skills/test_tool_validator.py 覆盖缺失 description_cn 的失败场景。
```

### 新分支处理

`c3f6313d` 不整体应用，应拆成两个 PR：

```text
PR-5：WebUI description_cn 展示。
PR-6：tool-builder / validator / 项目级 Python Tool 防漏规则。
```

### PR-5 范围

```text
webui/src/pages/Tool/index.tsx
webui/src/pages/Tool/components/MCPTabContent.tsx
webui/src/pages/Tool/components/ServiceDetailPanel.tsx
webui/src/pages/Tool/components/ToolDetailModal.tsx
webui/src/pages/Tool/components/ToolTable.tsx
```

要求：

```text
中文语言优先展示 description_cn。
英文语言保持 description。
description_cn 缺失时回退 description。
搜索同时匹配 description 和 description_cn。
```

### PR-6 范围

```text
.flocks/plugins/skills/tool-builder/SKILL.md
.flocks/plugins/skills/tool-builder/validator.py
.flocks/plugins/tools/python/dify_kb_search.py
.flocks/plugins/tools/python/flocks_mcp.py
tests/skills/test_tool_validator.py
.flocks/plugins/skills/agent-builder/SKILL.md
```

要求：

```text
Python Tool 模板包含 description 和 description_cn。
validator 阻止新生成 Python Tool 漏写 description_cn。
现有项目级 Python Tool 补齐 description_cn。
agent-builder 中文用户生成 Agent 规则同步加入。
```

## 4. 新分支 PR 阶段建议

| PR | 目标 | 主要来源 | 是否改变默认行为 | 风险 | 回滚粒度 |
|---|---|---|---|---|---|
| PR-0 | 文档与实施计划 | 当前文档 | 否 | 低 | 只回滚文档 |
| PR-1 | 数据模型字段 | `b4e7e9ca` | 否 | 低 | 字段 commit |
| PR-2 | parser / registry / API 透传 | `b4e7e9ca` | 否 | 低到中 | 后端透传 commit |
| PR-3 | Skill metadata 内容 | `5edcaaf5` + `b4e7e9ca` Skill frontmatter 修复 | 否 | 低到中 | 单个 Skill 或 metadata commit |
| PR-4 | Tool `description_cn` 内容和动态描述 | `b4e7e9ca` | 不应改变模型 schema | 中 | Tool 内容 commit |
| PR-5 | WebUI 中文描述展示 | `c3f6313d` | 仅影响 UI 展示 | 中低 | 回滚 WebUI commit |
| PR-6 | 生成规范与防漏规则 | `c3f6313d` + `167865a3` | 影响后续生成，不影响现有运行 | 中 | 回滚 validator / Skill 规则 |
| PR-7 | Agent metadata 和动态描述 | `b4e7e9ca` | 进入 Agent 动态描述 | 中 | 回滚 Agent metadata commit |
| PR-8 | Agent prompt / prompt_builder 中文变体 | `b4e7e9ca` | 进入 Agent prompt | 高 | 回滚 Agent prompt commit |
| PR-9+ | Session Prompt 中文化 | `080ece3` | 进入 Session Prompt | 高 | 回滚 Session Prompt commit |

## 5. 明确不纳入首轮新分支的内容

```text
四个源 commit 之外的其他提交：不作为实现输入。
branch2 发布转换脚本：如果需要，应作为独立 release-flow 任务，不和中文化运行时 PR 混合。
```

Session Prompt 中文化已调整为 PR-9+，独立于 PR-0 到 PR-8，并单独做模型行为回归。

## 6. 最终结论

新分支中文化实施不是继续修补当前 `feature/cn-localization` 的最终状态，而是：

```text
1. 从最新 feature/tool-data-permission-control 新建分支。
2. 只参考 b4e7e9ca、167865a3、c3f6313d、5edcaaf5 四个源 commit。
3. 不考虑其他 revert 情形。
4. 不整体 cherry-pick 大提交。
5. 按 PR-0 到 PR-9+ 分层逐步提交，确保每个 PR 可独立验证和回滚。
6. 不为灰度新增运行时开关；commit 本身作为上线测试和回滚边界。
```
