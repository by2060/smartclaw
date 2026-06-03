# CN Localization 文档索引

> 更新时间：2026-06-03
> 用途：说明中文化相关设计文档的分工、定位和相互关系。

## 文档清单与用途

| 文档 | 用途 | 读者 | 与其他文档关系 |
|---|---|---|---|
| `CN_LOCALIZATION_DESIGN.md` | 中文化总体设计原则、分层策略、数据流、验证方案和风险控制 | 架构师、评审者、实施者 | 设计总纲，被其他文档引用 |
| `CN_LOCALIZATION_ROLLOUT_PLAN.md` | 分阶段 PR/commit 实施计划、验收标准、回滚矩阵 | 实施者、测试、运维 | 依赖 `CN_LOCALIZATION_COMMIT_AUDIT.md` 的拆分依据 |
| `CN_LOCALIZATION_DIFF_SPLIT.md` | 每个PR 的具体文件范围、来源 commit、暂存命令和提交信息建议 | 实施者 | 执行层拆分表，配合 rollout 使用 |
| `CN_LOCALIZATION_COMMIT_AUDIT.md` | 四个源 commit 的审计、拆分依据、风险判断和 PR 映射 | 实施者、评审者 | 其他文档的 commit 级输入来源 |
| `CN_LOCALIZATION_CHANGES.md` | 已实施内容的细节记录、验证命令输出、文件清单 | 实施者、测试 | 变更历史记录，配合 design 使用 |

## 简要说明

### `CN_LOCALIZATION_DESIGN.md`

定位：**设计总纲**

主要内容：

```text
中文化背景和目标
非目标边界
核心设计原则（双字段描述、模型侧行为最小扰动、动态内容不得静态覆盖、YAML 安全解析）
Tool / Agent / Skill / Session Prompt 各层设计
验证方案
风险与控制
分阶段落地与回滚原则
```

适用场景：

```text
需要理解“为什么这样设计”时阅读。
评审中文化方案时作为主参考。
实施前确认设计边界和非目标。
```

### `CN_LOCALIZATION_ROLLOUT_PLAN.md`

定位：**实施计划**

主要内容：

```text
新分支策略：从 feature/tool-data-permission-control 新建，从 0 重做
参考源 commit：b4e7e9ca、167865a3、c3f6313d、5edcaaf5
PR-0 到 PR-9+ 的目标、范围、验收和回滚方式
Locale / fallback 机制汇总
推荐验证命令
回滚矩阵
```

适用场景：

```text
需要知道“按什么顺序提交 PR”时阅读。
需要确认某个 PR 的验收标准和回滚方式时查阅。
确认版本级回滚方式和必要的 locale/fallback 机制时参考。
```

### `CN_LOCALIZATION_DIFF_SPLIT.md`

定位：**执行拆分表**

主要内容：

```text
新分支准备命令
源 commit 到 PR 的映射表
每个 PR 的文件清单、来源 commit、不纳入范围、建议提交信息
推荐暂存命令示例
当前最安全的下一步
```

适用场景：

```text
准备提交某个 PR 时，查阅该 PR 的文件范围和暂存命令。
需要知道某个源 commit 拆到哪些 PR 时查阅。
执行 git add / git commit 时的命令参考。
```

### `CN_LOCALIZATION_COMMIT_AUDIT.md`

定位：**源 commit 审计**

主要内容：

```text
新分支实施口径
四个源 commit 的总览表
每个源 commit 的明细：原始规模、涉及文件、新分支处理、验收要求
新分支 PR 阶段建议表
不纳入本轮的内容
最终结论
```

适用场景：

```text
需要理解“为什么只参考这四个 commit”时阅读。
需要判断某个源 commit 的风险和拆分目标时查阅。
评审 PR 拆分合理性时作为依据。
```

### `CN_LOCALIZATION_CHANGES.md`

定位：**变更记录**

主要内容：

```text
本轮最终方案和策略
改动规模统计
Tool / Agent / Skill / Session Prompt 各层改造细节
完整文件清单
验证记录（命令和结果）
当前未纳入范围的内容
当前工作区额外状态
```

适用场景：

```text
需要知道“已经改了哪些文件”时查阅。
需要复现验证命令时参考。
需要确认某个具体文件的改动方式时检索。
```

## 推荐阅读顺序

```text
第一次了解：
1. CN_LOCALIZATION_DESIGN.md — 理解设计原则和边界
2. CN_LOCALIZATION_COMMIT_AUDIT.md — 理解源 commit 拆分依据
3. CN_LOCALIZATION_ROLLOUT_PLAN.md — 理解 PR 阶段计划

准备实施某个 PR：
1. CN_LOCALIZATION_ROLLOUT_PLAN.md — 查阅该 PR 的目标和验收
2. CN_LOCALIZATION_DIFF_SPLIT.md — 查阅该 PR 的文件范围和暂存命令
3. CN_LOCALIZATION_CHANGES.md — 查阅具体文件的改动方式参考

评审方案：
1. CN_LOCALIZATION_DESIGN.md — 设计合理性
2. CN_LOCALIZATION_COMMIT_AUDIT.md — 拆分合理性
3. CN_LOCALIZATION_ROLLOUT_PLAN.md — commit 拆分和回滚可行性
```

## 文档路径

```text
docs/CN_LOCALIZATION_DESIGN.md
docs/CN_LOCALIZATION_ROLLOUT_PLAN.md
docs/CN_LOCALIZATION_DIFF_SPLIT.md
docs/CN_LOCALIZATION_COMMIT_AUDIT.md
docs/CN_LOCALIZATION_CHANGES.md
docs/CN_LOCALIZATION_INDEX.md
```
