# CN Localization Diff Split

> 用途：把 `feature/cn-localization` 中的中文化改动拆成基于最新 `feature/tool-data-permission-control` 的多个独立 commit，保证每个逻辑 PR 可独立评审、验证和回滚。
> 更新日期：2026-06-03
> 新口径：PR-0 到 PR-8 只参考 `b4e7e9ca`、`167865a3`、`c3f6313d`、`5edcaaf5`；Session Prompt 中文化参考 `080ece3` 并作为 PR-9+；不整体 cherry-pick 大提交。

## 1. 本版结论

本轮中文化不再按当前 `feature/cn-localization` 最终状态整体实施，而是在当前分支上从最新 `feature/tool-data-permission-control` 基线拆成独立 commit 重做。

```text
base：feature/tool-data-permission-control 最新状态
working branch：smartclaw-cn-localization
reference source：feature/cn-localization 中的四个源 commit 和 Session Prompt 提交
```

四个源 commit：

```text
5edcaaf5：Skill metadata description_cn。
b4e7e9ca：Tool + Agent + Skill 大范围中文化。
167865a3：agent-builder 中文生成规则。
c3f6313d：WebUI + tool-builder + Python Tool description_cn 尝试。
080ece3：Session Prompt 中文化。
```

核心判断：

```text
b4e7e9ca 是最大来源，但不能整体应用。
c3f6313d 要拆成 WebUI 展示和生成防漏两个 PR。
167865a3 虽然只有一行，但影响未来 Agent 生成行为。
5edcaaf5 是低风险 Skill metadata 内容补齐。
080ece3 不混入 PR-0 到 PR-8，单独作为 PR-9+ 处理。
```

## 2. 新分支准备

推荐命令：

```bash
git fetch origin
git switch feature/tool-data-permission-control
git pull --ff-only
git switch smartclaw-cn-localization
```

查看源 commit：

```bash
git show --stat --name-status 5edcaaf5
git show --stat --name-status b4e7e9ca
git show --stat --name-status 167865a3
git show --stat --name-status c3f6313d
git show --stat --name-status 080ece3
```

要求：

```text
不要直接 cherry-pick b4e7e9ca。
不要直接 cherry-pick c3f6313d。
如果局部借用 patch，必须按本文件的 PR 边界拆分并手工复核。
```

## 3. 源 commit 到 PR 的映射

| 源 commit | 原始文件类型 | 拆到哪些 PR |
|---|---|---|
| `5edcaaf5` | `.flocks/plugins/skills/*/SKILL.md` | PR-3 |
| `b4e7e9ca` | Tool、Agent、Skill、API、prompt builder | PR-1、PR-2、PR-3、PR-4、PR-7、PR-8 |
| `167865a3` | `.flocks/plugins/skills/agent-builder/SKILL.md` | PR-6 |
| `c3f6313d` | WebUI、tool-builder、Python Tool、测试 | PR-5、PR-6 |
| `080ece3` | Session Prompt locale、`.zh.txt`、测试 | PR-9+ |

## 4. 推荐 PR 顺序

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

每个 PR 都必须可以单独 revert。commit 本身作为上线、测试和回滚边界，不为灰度额外新增运行时开关；PR-9+ 的 locale/fallback 机制属于语言选择，不是灰度开关。

## 5. PR-0：文档与实施计划

### 文件

```text
docs/CN_LOCALIZATION_COMMIT_AUDIT.md
docs/CN_LOCALIZATION_ROLLOUT_PLAN.md
docs/CN_LOCALIZATION_DIFF_SPLIT.md
docs/CN_LOCALIZATION_DESIGN.md
docs/CN_LOCALIZATION_CHANGES.md
docs/CN_LOCALIZATION_INDEX.md
```

### 内容

```text
明确 base 是 feature/tool-data-permission-control。
明确只参考四个源 commit。
明确不考虑其他 revert 情形。
明确 PR-1 到 PR-9+ 拆分边界。
明确不为灰度新增运行时开关。
```

### 建议提交信息

```text
docs: plan staged localization from tool-data baseline
```

## 6. PR-1：数据模型字段

### 主要来源

```text
b4e7e9ca
```

### 文件类型

```text
flocks/agent/agent.py
flocks/skill/skill.py
flocks/tool 相关 ToolInfo / schema 定义
相关 response model / dataclass
```

### 内容

```text
增加 description_cn 可选字段。
保持 description 原字段不变。
保证旧数据缺失 description_cn 时可正常加载。
```

### 不纳入

```text
registry / parser / API 透传。
WebUI 展示。
Tool / Agent / Skill 内容补齐。
prompt.md / prompt_builder.py 中文化。
```

### 建议提交信息

```text
feat(localization): add optional chinese description fields
```

### 验收

```text
python3 -m compileall -q flocks
相关 schema / model tests 通过。
```

## 7. PR-2：后端 parser / registry / API 透传

### 主要来源

```text
b4e7e9ca
```

### 文件

```text
flocks/tool/tool_loader.py
flocks/tool/registry.py
flocks/skill/skill.py
flocks/server/routes/skill.py
flocks/agent/registry.py
```

### 内容

```text
YAML Tool / Python Tool 可读取 description_cn。
Skill parser 可读取 description_cn / descriptionCn。
Skill create/update/list/detail API 可透传 description_cn。
Agent registry 可携带 description_cn，但默认不改变 prompt 选择。
```

### 不纳入

```text
WebUI 展示。
Tool description_cn 批量内容。
Agent prompt 中文化。
```

### 建议提交信息

```text
feat(localization): pass through chinese descriptions
```

### 验收

```text
Skill frontmatter 带中文、冒号、长文本可解析。
API 返回 description_cn 不影响 description。
缺失 description_cn 不报错。
```

## 8. PR-3：Skill metadata 内容补齐

### 主要来源

```text
5edcaaf5
b4e7e9ca 中的 Skill frontmatter 修复
```

### 文件

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

### 内容

```text
补齐 description_cn。
修复包含冒号或长文本的 YAML frontmatter。
保留 description 原文。
```

### 不纳入

```text
Skill 正文批量中文化。
tool-builder validator。
agent-builder 生成规则。
```

### 建议提交信息

```text
feat(skills): add chinese skill metadata
```

### 验收

```text
所有 SKILL.md frontmatter 可解析。
Skill list/detail API 正常。
```

## 9. PR-4：Tool `description_cn` 内容和动态描述

### 主要来源

```text
b4e7e9ca
```

### 文件类型

```text
flocks/tool/agent/*.py
flocks/tool/channel/*.py
flocks/tool/code/*.py
flocks/tool/file/*.py
flocks/tool/security/*.py
flocks/tool/skill/*.py
flocks/tool/system/*.py
flocks/tool/task/*.py
flocks/tool/web/*.py
flocks/tool/wecom/*.py
```

### 内容

```text
静态 Tool 注册补 description_cn。
动态描述 Tool 增加中文动态描述构造。
Tool registry 可承载 description_cn。
```

### 不纳入

```text
不把 description 替换成中文。
不让模型 tool schema 默认使用 description_cn。
不修改 Tool 参数 description。
不翻译运行时输出、错误、日志。
```

### 建议提交信息

```text
feat(tools): add chinese tool descriptions
```

### 验收

```text
Tool 注册成功。
description 原字段保持原语义。
description_cn 缺失时不影响工具加载。
模型 schema 仍使用 description，不切到 description_cn。
```

## 10. PR-5：WebUI 中文展示

### 主要来源

```text
c3f6313d
```

### 文件

```text
webui/src/pages/Tool/index.tsx
webui/src/pages/Tool/components/MCPTabContent.tsx
webui/src/pages/Tool/components/ServiceDetailPanel.tsx
webui/src/pages/Tool/components/ToolDetailModal.tsx
webui/src/pages/Tool/components/ToolTable.tsx
webui/src/utils 或 i18n helper
```

### 内容

```text
统一 helper 选择展示 description_cn / description。
中文语言优先展示 description_cn。
搜索逻辑同时支持 description 和 description_cn。
```

### 不纳入

```text
tool-builder validator。
项目级 Python Tool 补 description_cn。
Agent / Skill / Tool 后端字段。
```

### 建议提交信息

```text
feat(webui): localize tool descriptions
```

### 验收

```text
中文语言展示 description_cn。
英文语言仍展示 description。
description_cn 缺失回退 description。
npm --prefix webui run build。
```

## 11. PR-6：生成规范与防漏规则

### 主要来源

```text
167865a3
c3f6313d
```

### 文件

```text
.flocks/plugins/skills/agent-builder/SKILL.md
.flocks/plugins/skills/tool-builder/SKILL.md
.flocks/plugins/skills/tool-builder/validator.py
.flocks/plugins/tools/python/dify_kb_search.py
.flocks/plugins/tools/python/flocks_mcp.py
tests/skills/test_tool_validator.py
```

### 内容

```text
agent-builder：中文用户创建 Agent 时，生成内容优先中文，配置字段/文件名/工具名保持英文。
tool-builder：Python Tool 模板包含 description 和 description_cn。
validator：缺失或空 description_cn 的 Python Tool 失败。
项目级 Python Tool：补齐 description_cn。
测试：覆盖有效样例和缺失 description_cn 失败样例。
```

### 不纳入

```text
WebUI 展示。
Agent prompt 中文化。
Tool description_cn 批量内容。
```

### 建议提交信息

```text
chore(skills): enforce generated localization metadata
```

### 验收

```text
PYTHONPATH="$PWD" uv run --frozen pytest tests/skills/test_tool_validator.py -q
项目级 Python Tool py_compile 通过。
```

## 12. PR-7：Agent metadata 和动态描述

### 主要来源

```text
b4e7e9ca
```

### 文件

```text
flocks/agent/agent.py
flocks/agent/registry.py
flocks/agent/prompt_utils.py
flocks/agent/agents/*/agent.yaml
```

### 内容

```text
Agent metadata 补 description_cn。
AvailableAgent / AvailableSkill 携带 description_cn。
动态表格描述优先使用中文。
缺失 description_cn 回退 description。
```

### 不纳入

```text
prompt.md 中文化。
prompt_builder.py 主模板中文化。
Agent 名称、工具名、Skill 名称变更。
委派规则变更。
```

### 建议提交信息

```text
feat(agents): add chinese descriptions
```

### 验收

```text
动态 prompt 结构不变。
只改变描述文案。
Agent 委派和工具选择无明显变化。
```

## 13. PR-8：Agent prompt / prompt_builder 中文变体

### 主要来源

```text
b4e7e9ca
```

### 文件

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

### 内容

```text
为静态 Agent prompt 提供中文变体或受控中文化。
为动态 prompt_builder 提供中文变体能力。
保留动态注入、占位符替换、prompt_append 等能力。
```

### 不纳入

```text
不新增静态 prompt.md 覆盖动态 Agent。
不和 Session Prompt 中文化混合。
```

### 建议提交信息

```text
feat(agents): localize agent prompts
```

### 验收

```text
中文模式下工具调用、委派、安全边界不退化。
动态 Agent 仍保留动态内容注入。
覆盖中文问答、英文输入、工具调用、计划、高风险拒绝。
```

## 14. PR-9+：Session Prompt 中文化

### 主要来源

```text
080ece3
```

### 文件

```text
flocks/session/prompt.py
flocks/session/prompt_locale.py
flocks/session/prompt_strings.py
flocks/session/runner.py
flocks/session/prompt/*.zh.txt
tests/session/test_prompt_tokens.py
```

### 内容

```text
新增 Session Prompt locale 选择。
中文 locale 优先加载 .zh.txt，缺失回退英文。
System Prompt cache key 增加 prompt locale。
内部 summary / compaction / max steps prompt 增加中英文版本。
```

### 建议提交信息

```text
feat(session): add chinese prompt locale
```

### 验收

```text
PYTHONPATH="$PWD" uv run --frozen pytest tests/session/test_prompt_tokens.py -q
英文 prompt 仍可加载。
中文 locale 优先加载中文变体。
```

## 15. 不纳入本轮新分支的内容

```text
四个源 commit 之外的其他提交。
branch2 发布转换脚本和 release-flow skill。
Tool 参数 description_cn。
运行时输出、错误信息、日志系统中文化。
完整 runtime locale 框架。
```

## 16. 推荐暂存命令示例

只暂存文档：

```bash
git add docs/CN_LOCALIZATION_COMMIT_AUDIT.md docs/CN_LOCALIZATION_ROLLOUT_PLAN.md docs/CN_LOCALIZATION_DIFF_SPLIT.md docs/CN_LOCALIZATION_DESIGN.md docs/CN_LOCALIZATION_CHANGES.md docs/CN_LOCALIZATION_INDEX.md
```

只暂存 Skill metadata：

```bash
git add .flocks/plugins/skills/*/SKILL.md
```

只暂存 WebUI Tool 展示：

```bash
git add webui/src/pages/Tool/index.tsx webui/src/pages/Tool/components/MCPTabContent.tsx webui/src/pages/Tool/components/ServiceDetailPanel.tsx webui/src/pages/Tool/components/ToolDetailModal.tsx webui/src/pages/Tool/components/ToolTable.tsx
```

只暂存生成规范：

```bash
git add .flocks/plugins/skills/agent-builder/SKILL.md .flocks/plugins/skills/tool-builder/SKILL.md .flocks/plugins/skills/tool-builder/validator.py .flocks/plugins/tools/python/dify_kb_search.py .flocks/plugins/tools/python/flocks_mcp.py tests/skills/test_tool_validator.py
```

只暂存 Session Prompt 中文化：

```bash
git add flocks/session/prompt.py flocks/session/prompt_locale.py flocks/session/prompt_strings.py flocks/session/runner.py flocks/session/prompt/*.zh.txt tests/session/test_prompt_tokens.py
```

## 17. 当前最安全的下一步

```text
1. 同步 feature/tool-data-permission-control。
2. 在当前分支先提交 PR-0 文档。
3. 再按 PR-1 到 PR-8 逐步拆分应用四个源 commit 中的内容。
4. 最后追加 PR-9+ Session Prompt 中文化。
```

短结论：

```text
新分支从 0 做。
只参考四个源 commit。
PR-9+ 参考 080ece3。
不整体 cherry-pick。
先字段和透传，再 Skill / Tool / UI / 生成规范，最后 Agent prompt 和 Session Prompt。
```
