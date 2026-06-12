---
name: workflow-builder
category: system
description: 根据自然语言描述生成 flocks 内置工作流（workflow.md, workflow.json）。当用户提出创建/设计/生成/搭建工作流或任何多步骤流程（如告警调查、事件响应、SOP/Runbook 自动化）时使用本 skill。
description_cn: 根据自然语言描述生成 flocks 内置工作流（workflow.md, workflow.json）。当用户提出创建/设计/生成/搭建工作流或任何多步骤流程（如告警调查、事件响应、SOP/Runbook 自动化）时使用。
---

# Workflow Builder

分四个阶段构建工作流：**场景确认与流程设计** → **简化 JSON 预览循环** → **完整 workflow.md** → **完整 workflow.json**。

> **产物**：最终 `workflow.json` 默认优先使用多个中等粒度 `type="python"` 业务阶段节点。每个 Python 节点可以在阶段内调用工具、LLM、Agent/Skill 等外部能力，并统一做输入归一化、错误包络和阶段输出。`branch`/`loop` 用于控制流；`tool`、`llm`、`http_request`、`subworkflow` 仅在需要独立观测、并行、复用、或用户明确要求时使用。严禁把整个 workflow 塞进一个巨大 Python 节点；按业务阶段拆成清晰节点。`logic` 只允许用于第二阶段的简化预览，最终完整 `workflow.json` **禁止**包含 `type="logic"`。最终交付物固定为：`workflow.md`、`workflow.json`。

## 参考资料（按需读取）

| 文件                                                     | 内容                                                                                                    | 何时读取                         |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- | ---------------------------- |
| [references/reference.md](references/reference.md)     | 生成总原则、节点类型与最小示例、数据传递与 Edge Mapping、控制流与 Join、节点决策、工具返回值处理、外部能力调用、并发/多子任务、文件输出与报告生成、`workflow.json` 骨架模板 | **生成 `workflow.json` 前建议读取** |
| [references/templates/](references/templates/)         | Python 优先的阶段节点模板，以及少量分支、HTTP、子流程等边界模板，按场景选取后再裁剪，格式为 md 文件                                                           | 生成完整 `workflow.json` 前参考     |
| [references/templates/python-orchestrator.md](references/templates/python-orchestrator.md) | 默认 Python 阶段节点模板，示范在一个业务阶段内调用工具、LLM、Agent 并统一输出结果 | 生成完整 `workflow.json` 前优先参考 |
| [references/composition.md](references/composition.md) | 嵌套工作流（subworkflow）组合格式与展开规则                                                                           | 仅在用户需要嵌套工作流时读取               |

---

## 0. 开始前

### Todo List（每次创建必须生成）

**在开始任何工作前，必须先用 Todo 工具列出完整任务清单**，并在整个过程中实时更新状态（pending → in_progress → completed）。标准 Todo 清单如下，根据实际工作流复杂度增减：

```
[ ] 0.   核验可用工具列表（读取 registry.py）
[ ] 1.   场景深度确认：与用户对话，明确业务场景与核心目标
[ ] 1.   输出思考维度分析 + Mermaid 流程简图，与用户沟通对齐
[ ] 1.   获取样例数据（用户上传或自动构造后确认）
[ ] 2.   生成简化版预览 JSON（仅节点名称+描述，无代码）
[ ] 2.   写入简化 workflow.json 文件，供页面展示流程图
[ ] 2.   向用户展示并收集修改建议（循环直至满意）
[ ] 3.   生成完整 workflow.md（人读描述）
[ ] 3.   写入 workflow.md 文件
[ ] 3.   向用户展示流程摘要并请求确认
[ ] 4.   读取 reference.md，并按场景参考 references/templates/
[ ] 4.   生成完整 workflow.json（优先使用多个中等粒度 Python 业务阶段节点）
[ ] 4.   写入 workflow.json 文件
[ ] 4.   验证 JSON 格式 + Python 语法 + 最终 JSON 无 logic 节点
[ ] 4.   保存样例数据到 /api/workflow/{id}/sample-inputs
[ ] 5.   通知用户工作流已就绪
```

> 每完成一项立即标记为 completed；每进入一项立即标记为 in_progress。**严禁在 Todo 全部完成前宣布任务结束。**

---

### 实时工具核验（重要）

生成 workflow 前必须核验可用工具列表与参数签名：

- **强制读取** `flocks/tool/registry.py`（`ToolInfo.parameters` 是参数 schema 的权威来源）。
- 工具名必须一致，参数名必须严格对齐，禁止调用 `run_workflow`（在 `WORKFLOW_TOOL_BLOCKLIST` 中）。

---

## 1. 第一阶段：场景确认与流程设计

> 目标：通过对话真正理解用户需求，输出完整的思考维度与流程简图，让用户在花时间构建工作流之前就能确认方向。

### 1.1 深度场景对话

使用 `Question` 工具与用户确认以下维度（根据场景选取相关项，不必逐条询问，尽量合并为 1-2 轮对话）：

**业务背景**
- 这个工作流解决什么安全/业务问题？触发条件是什么？
- 谁会使用这个工作流？是定时自动触发还是手动触发？
- 有没有现有的 SOP 或人工处理流程可以参考？

**数据与工具**
- 输入数据是什么？（告警字段、IP/域名/哈希、日志条目等）
- 需要调用哪些外部工具或服务？（威胁情报、SIEM、资产库等）
- 输出结果是什么？（报告、工单、通知、打标签等）

**流程要求**
- 有没有需要特殊处理的条件分支？（如高危 vs 低危、内部 IP vs 外部 IP）
- 对运行时间有要求吗？（实时响应 < 30s？批量处理可接受数分钟？）
- 有哪些已知的"陷阱"或边界情况需要注意？

### 1.2 输出思考维度与流程简图

对话完成后，在消息中输出以下内容供用户确认：

**思考维度总结**（结构化列举，涵盖：数据流、工具调用链、分支逻辑、异常处理、性能关键点、可扩展性）

**流程简图**（使用 Mermaid flowchart 语法，清晰展示节点与边关系）

示例格式：
```
## 思考维度

**数据流**：输入告警 → 资产丰富 → 情报查询 → LLM 分析 → 报告输出
**分支逻辑**：IP 类型判断（内网 / 外网）→ 不同查询策略
**性能关键点**：情报查询可并发；LLM 调用是主要耗时节点
**异常处理**：工具调用失败时降级到日志记录，不中断流程
**可扩展性**：后续可插入 SOAR 工单节点

## 流程简图

\`\`\`mermaid
flowchart TD
    A[接收告警] --> B[提取 IP/域名]
    B --> C{IP 类型?}
    C -->|内网| D[查询资产库]
    C -->|外网| E[查询威胁情报]
    D --> F[汇总上下文]
    E --> F
    F --> G[LLM 分析]
    G --> H[生成报告]
\`\`\`
```

### 1.3 获取样例数据

在流程简图得到用户认可后，请用户上传一条完整的样例输入数据（JSON 格式）：

- 若用户能提供：直接使用
- 若用户无法提供：根据场景自动构造一条最小可用样例 JSON，并请用户确认字段和数值是否合理

> 样例数据将用于后续每个节点的逐步测试，是测试阶段的核心依据。获得样例后，待工作流 ID 确定时调用 `POST /api/workflow/{id}/sample-inputs` 保存（body: `{ "sampleInputs": <样例 JSON 对象> }`）。

---

## 2. 第二阶段：简化版 JSON 预览与确认循环

> 目标：在投入完整代码编写之前，让用户在页面上直观地看到流程图，并就节点/边的设计提出修改建议。

### 2.1 生成简化版 workflow.json

生成一份**只有结构、没有代码**的简化 JSON 文件：

- 处理/分析占位节点可使用 `type="logic"`（只需 `description`，无需 `code`）；明确的决策点使用 `branch`，循环点使用 `loop`
- **注意**：`logic` 仅允许出现在本阶段的简化预览 JSON 中。进入第四阶段生成完整可执行 `workflow.json` 时，必须把所有 `logic` 替换为明确节点：`python` / `branch` / `loop` / `llm` / `tool` / `http_request` / `subworkflow`。
- 明确可由内置工具、LLM、HTTP 或子工作流承担的步骤，可提前标注为 `tool`、`llm`、`http_request`、`subworkflow`，但仍不写 Python 代码
- 包含节点的 `id`、`name`（可读名称）、`description`（功能说明）
- 包含完整的 `edges`；`from` / `to` 必填，`label` 仅用于 branch/loop 决策边或需要在图上命名的边，预览中的并行结构边可省略
- 包含 `metadata.preview=true` 和 `metadata.stage="preview"`，让保存时的 workflow lint gate 使用预览草稿模式
- **不包含任何 Python 代码**

简化 JSON 最小结构示例：

```json
{
  "id": "alert_triage",
  "name": "告警分级调查",
  "description": "自动化 NDR 告警调查工作流",
  "start": "receive_alert",
  "metadata": {
    "preview": true,
    "stage": "preview"
  },
  "nodes": [
    {
      "id": "receive_alert",
      "type": "logic",
      "name": "接收告警",
      "description": "接收输入告警，提取 IP、端口、协议等关键字段"
    },
    {
      "id": "check_ip_type",
      "type": "branch",
      "name": "判断 IP 类型",
      "select_key": "ip_type",
      "description": "判断源 IP 是内网地址还是外网地址，分支处理"
    },
    {
      "id": "query_threat_intel",
      "type": "logic",
      "name": "查询威胁情报",
      "description": "调用威胁情报工具查询外部 IP 的恶意评分、标签"
    },
    {
      "id": "generate_report",
      "type": "logic",
      "name": "生成分析报告",
      "description": "汇总所有上下文，由 LLM 生成结构化调查报告"
    }
  ],
  "edges": [
    { "from": "receive_alert", "to": "check_ip_type", "order": 0 },
    { "from": "check_ip_type", "to": "query_threat_intel", "label": "外网", "order": 0 },
    { "from": "query_threat_intel", "to": "generate_report", "order": 0 }
  ]
}
```

### 2.2 写入文件并展示

1. 将简化 JSON 写入当前项目规范目录下的 `workflow.json`（**必须使用绝对路径**，见第 6 节）：`<workspace>/.flocks/plugins/workflows/<id>/`。用户级 `~/.flocks/plugins/workflows/<id>/` 仅允许作为历史兼容扫描路径，不允许作为新建或修改目标。
2. 在消息中告知用户：「已更新流程图，请在工作流页面查看。对节点名称、描述或流程结构有什么修改建议？」

### 2.3 用户反馈循环（循环直至满意）

收集用户的修改建议，按照以下循环执行，**直到用户确认满意**：

```
接收用户反馈
  ↓
分析修改需求（增/删节点、改描述、调整边关系）
  ↓
更新简化 JSON
  ↓
重新写入文件
  ↓
向用户展示更新摘要，询问是否满意
  ↓
[满意] → 进入第三阶段
[还有修改] → 重新循环
```

> **提示**：前端检测到 `workflow.json` 更新后会自动刷新流程图，用户无需手动刷新。

---

## 3. 第三阶段：生成完整 workflow.md（人读描述）

> 以第一阶段确认的流程结构为基础，生成**操作手册级别**的详细流程文档。

### 核心要求

每个步骤必须包含：

- **输入/输出**：数据来源、格式、用途。
- **处理逻辑**：具体操作步骤、判定条件、循环方式、异常处理。
- **工具/LLM/Agent 标注**：默认把强相关的工具、LLM、Agent/Skill 调用标注为某个 Python 阶段节点内的调用；只有需要独立观测、并行、复用、或用户明确要求时才标注为独立结构化节点（详细决策指南见 [reference.md § Tool / LLM / Python / HTTP 决策指南](references/reference.md#5-tool--llm--python--http-决策指南)）。
  - **推荐组合**：多个中等粒度 Python 阶段节点 + 必要的 `branch`/`loop` 控制节点；每个 Python 节点统一用 `tool.run_safe()` / `llm.ask()` / `delegate_task` 处理阶段内调用并输出阶段结果。
  - **默认使用 `tool.run_safe()`**，返回 `{"success", "text", "obj", "error", "metadata", "title"}` 统一包络。
- **文件落盘**：节点有任何文件输出时，必须通过 `write` 工具写入 session outputs；workflow 返回路径必须取 `result["metadata"]["filepath"]`，不得返回传入的候选 `filePath`。详见 [reference.md § 文件输出与报告生成](references/reference.md#9-文件输出与报告生成)。
- **决策分支**：写清条件、各分支处理、跳转规则。
- **报告结构**（若涉及）：除非用户要求简化，需包含摘要、分析、发现、建议、来源（模板见 [reference.md § 文件输出与报告生成](references/reference.md#9-文件输出与报告生成)）。

### ⚠️ 两步交付

1. 先用 `write` 工具将 `workflow.md` **写入文件**（路径与第 6 节一致，例如 `.../plugins/workflows/<id>/workflow.md`）。
   - **⚠️ 路径必须使用绝对路径**：先解析 workspace（从 cwd 向上第一个含 `.flocks` 的目录），再拼接 `/.flocks/plugins/workflows/<id>`。
   - **严禁**使用未展开的相对路径（如 `.flocks/plugins/workflows/<id>/` 相对仓库根随手写入错误位置），否则 WebUI 可能无法从实际扫描目录读到文件。
2. 写入成功后，用 `Question` 工具向用户展示流程摘要并请求确认（"确认工作流" / "修改工作流"）。确认后进入第四阶段生成 `workflow.json`。

---

## 4. 第四阶段：生成完整 workflow.json（机器执行）

根据 `workflow.md` 生成严格可执行的 `workflow.json`。**生成前建议读取 [reference.md § 生成总原则](references/reference.md#1-生成总原则) 和 [reference.md § 生成前检查清单](references/reference.md#11-生成前检查清单)**。

**硬性要求：完整可执行 `workflow.json` 禁止出现 `type="logic"`。** 如果预览 JSON 中存在 `logic` 节点，生成最终 JSON 时必须逐个替换为明确实现：

- 处理/聚合/编排/工具/LLM/API/Agent 调用占位 → 默认替换为中等粒度 `python` 阶段节点
- 条件判断 → `branch`
- 循环控制 → `loop`
- 明确需要独立并行、独立观测、复用已有流程或用户指定的单步能力 → `tool` / `llm` / `http_request` / `subworkflow`

### 4.0 节点生成策略

- **主路径**：最终执行节点优先生成多个中等粒度 `python` 阶段节点；不要机械地把每个工具、LLM、Agent 调用拆成独立节点。
- **Python 阶段节点适用**：一个节点对应一个清晰业务阶段，例如“输入归一化”“情报富集”“上下文收集”“研判分析”“报告落盘”；阶段内可调用工具、LLM、Agent/Skill，并统一输出稳定字段。
- **必须拆分 Python 节点的边界**：超过约 80-120 行、包含多个业务阶段、阶段之间有分支/循环、需要独立观测/重试、输出被多个下游复用、或副作用需要独立确认时，拆成多个 Python 节点或控制节点。
- **独立结构化节点适用**：`branch`/`loop` 控制流必须独立；固定并行 fan-out 的兄弟分支可用多个 Python 节点；`tool`/`llm`/`http_request`/`subworkflow` 仅在用户要求可视化单步调用、需要独立并行/重试/计时、复用已有 workflow、或 HTTP 不能通过注册工具表达时使用。
- **能力来源识别**：当用户提到 MCP、Agent、Skill、API 工具或内置工具时，按 [reference.md § 外部能力调用](references/reference.md#7-外部能力调用mcp--agent--skill--api--内置工具) 识别真实工具名和 schema；默认在 Python 阶段节点中通过 `tool.run_safe()`、`tool.run()`、`llm.ask()` 或 `delegate_task` 调用。
- **并发/多子任务**：真正需要并行或多路结果独立可观测时，用 workflow 图结构 fan-out + `join=true`；每个并行分支优先是一个 Python 阶段节点。不要在 Python 节点里写 `async`、线程池或网络请求来模拟并发。
- **HTTP 调用**：不要在 Python 中 import 网络库直接请求。优先用已注册 API/MCP/内置工具并在 Python 节点内 `tool.run_safe()` 调用；没有注册工具且只是一次性 HTTP 时，才用独立 `http_request`。
- **子流程复用**：用户要求复用已有流程时使用 `type="subworkflow"`；不要在 Python 节点里调用 `run_workflow` 绕过原生子流程节点。
- **禁止巨型节点**：不要把整个 workflow 塞进一个 Python 节点。通常 3-8 个业务阶段节点比 1 个巨型节点更可读、更可测。
- **禁止最终 logic**：`logic` 只能用于第二阶段流程图预览；最终 `nodes[]` 中不得有任何 `type="logic"`。

### 4.1 运行时硬约束

**顶层字段：**

- `start` 必须等于某个 `nodes[i].id`
- `nodes[].id` 必须唯一
- `name`/`description`（可选）用于工作流级别说明
- `version` 会被运行时忽略，不需要生成

**Node 约束**（对应 `flocks/workflow/models.py`）：

- `python`：`code` 必须非空；允许作为阶段编排块调用 `tool.run_safe()`、`tool.run()`、`llm.ask()` 等运行时辅助能力；禁止危险 import/call、硬编码密钥、网络库和系统命令
- `logic`：仅允许简化预览 JSON 使用；完整可执行 `workflow.json` 中禁止出现
- `tool`：`tool_name` 必须非空，`tool_args` 只能使用工具 schema 中声明的参数；输出写入 `output_key`（默认 `result`）
- `llm`：`prompt` 必须非空，必须显式设置 `output_key`
- `http_request`：`method` 和 `url` 必须非空，`response_key` 默认 `response`，同时输出 `status_code`
- `subworkflow`：`workflow_id` 必须非空，可用 `inputs_mapping` / `inputs_const` 规整输入
- **出边选择行为**（关键）：`python`/`tool`/`llm`/`http_request`/`subworkflow` → 所有出边触发；`branch`/`loop` → 通过 `select_key` 取值做 label 匹配选边；`logic` 只用于预览，不参与最终执行设计
- `join=true`：等待所有入边到齐再执行一次

**代码与 lint 约束：**

- 同步 `exec()` 模型，**严禁** `await`/`async def`/`async for`/`async with`。确保节点的代码要可以独立运行。
- 保存 `workflow.json` 时会执行 schema + lint gate；lint error 会阻断写入。
- 生成前主动自检：最终 `nodes[].type` 不得为 `logic`；branch/loop 多出边必须有 `select_key`，默认边最多 1 条，非默认出边必须有 `label`。
- Python 节点会被 AST 检查：不要 import `subprocess`/`requests`/`httpx`/`aiohttp`/`urllib`/`socket`，不要调用 `eval`/`exec`/`compile`/`os.system`/`subprocess.*`，不要硬编码 token/password/secret。

**Edge 约束：**

- JSON 中用 `"from"` 而非 `"from_"`；`from`/`to` 引用存在的 node id；`order` ≥ 0。

### 4.2 映射规则

- `workflow.md` 每步对应一个节点，`id` 用 snake_case。
- md 中写的输出字段，必须在 `outputs[...]` 中体现。
- md 中 `Tool: xxx` 标记 → 默认在对应 Python 阶段节点内用 `tool.run_safe("xxx", ...)` 调用；只有需要独立观测/并行/复用或用户明确要求时才生成 `type="tool"` 节点。
- 下游节点如需结构化输入，优先由上游 Python 阶段节点写出稳定 `outputs[...]`；只有独立结构化节点之间才用 `edge.mapping`/`edge.const` 规整字段或工具参数形状。
- 详细 Mapping 指南见 [reference.md § 数据传递、Edge Mapping 与动态参数](references/reference.md#3-数据传递edge-mapping-与动态参数)。

### 4.3 分支/循环与 Join

- **branch/loop 选边**：`bool` 值 label 用 `"true"`/`"false"`；`str` 值精确匹配；无命中回退到空 label 默认边。上游必须把 `select_key` 所需字段写入 payload。
- **分支汇合（强制）**：
  - 多入边且非互斥 → **必须** `join=true`
  - 判断互斥：所有入边来自同一 branch/loop 的不同 label 出边
  - **昂贵节点保护**：含 `llm.ask()`、`tool.run('write', ...)` 或 `tool.run_safe('write', ...)` 的节点，禁止被两条非互斥路径直达，必须先经 join 节点
  - 推荐模式：join 节点（python, `join=true`）归一化多分支输出 → 再传给后续步骤
- **嵌套工作流**：见 [references/composition.md](references/composition.md)。

### 4.4 Python 节点实现

> Python 节点可以是确定性转换，也可以是阶段编排块。优先按业务阶段合并强相关的小步骤，避免生成过长流程；关键观测点、分支点和副作用点仍应保留为独立节点。

**Python 节点可用辅助函数：**

| 函数 | 说明 |
|------|------|
| `tool.run(name, **inputs)` | 返回 `ToolResult.output`（类型 `Any`，**通常是字符串**），失败抛异常；仅在失败应中断当前阶段时使用 |
| `tool.run_safe(name, **inputs)` | 返回 `{"success": bool, "text": str, "obj": Any, "error": str\|None, "metadata": dict, "title": str\|None}`，永不抛异常；Python 编排块默认优先使用 |
| `llm.ask(prompt)` | 调用 LLM，返回字符串；适合与确定性处理紧密耦合的短推理或阶段内分析 |
| `get_path(path)` | payload 深层取值 |

**返回值类型警告（常见 Bug 源）：**

- `tool.run()` 返回 `Any`，多数工具是字符串，不要直接 `.get()`。
- `tool.run_safe()["obj"]` 也可能是 `str`、`dict`、`list` 或 `None`，取结构化字段前必须 `isinstance`。
- 字符串拼接 / LLM prompt 插值优先用 `result["text"]`；文件最终落盘路径用 `result.get("metadata", {}).get("filepath")`。
- 调用 Agent 时使用 `tool.run_safe("delegate_task", ...)`；`category` 和 `subagent_type` 只能二选一，动态 prompt 可在同一 Python 节点中构造。
- 详细反例和安全解析模板见 [reference.md § 工具调用规范与返回值处理](references/reference.md#6-工具调用规范与返回值处理)。

**数据落盘与传递：**

- **文件输出**：有文件输出时使用 `write` 工具；`filePath` 优先传安全文件名或相对文件名（如 `report.md`），不要在 workflow 中拼接 `default-session` 或用户绝对 outputs 路径
- **最终路径**：`outputs["report_path"]` / `outputs["file_path"]` 必须来自 `write` 返回的 `metadata.filepath`，不要使用传入的候选 `filePath`
- **数据传递**：`inputs` 和 `outputs` 字典，运行时浅合并 `payload = {**inputs, **outputs}`

> **⚠️** 生成后必须使用 `write` 写入到文件，并验证：1) `json.load` 确认 JSON 格式正确；2) 对每个 `type="python"` 节点的 `code` 执行 `compile(code, "<node_id>", "exec")` 确认 Python 语法正确；3) 扫描 `nodes` 确认没有任何 `type="logic"`。若语法报错或存在 `logic`，修复后重新写入。

---

## 5. 修改模式：修改已有工作流

当用户表达的是**修改/调整/优化已有工作流**（而非从零创建）时，进入本模式。

### 5.0 判断是否是修改请求

满足以下任一条件即为修改请求：

- 用户说"修改……"、"调整……"、"把……改成……"、"优化……"、"重构……"
- 已在 ChatTab 上下文中提供了完整 `workflow.json`
- 用户指向某个具体工作流 ID 或名称，要求改动其中某些节点或逻辑

### 5.1 准备

1. **读取现有文件**：优先使用 ChatTab 上下文中已提供的 JSON；若需要也可用 `read` 工具读取 `workflow.json` 和 `workflow.md`。
2. **理解修改意图**：若意图不清晰，先用 `Question` 工具确认：
   - 需要增/删/改哪些节点？
   - 数据流、出边逻辑是否需要同步调整？

### 5.2 先更新 workflow.md（必须）

**修改模式下，必须先更新 MD 文档，经用户确认后再修改 JSON。严禁直接跳到 JSON 修改。**

1. 根据修改意图，更新 `workflow.md`（保留原有结构，仅修改受影响的步骤描述）。
2. 用 `write` 工具将更新后的 `workflow.md` **写入文件**（路径：与同 ID 的 `workflow.json` 所在目录一致，见第 6 节）。
3. 用 `Question` 工具向用户展示变更摘要，询问确认。
4. 用户确认后，进入 5.3 生成变更。

### 5.3 生成变更

**优先最小化变更原则：**

- **单节点改动** → 使用 `edit` 工具精准替换目标字段
- **多节点改动 / 结构重组** → 整体覆写

**遵守所有 workflow.json 约束**（见第 4 节规范）。

### 5.4 验证与写回

修改完成后：
1. `json.load` 确认 JSON 格式正确
2. 对每个 `type="python"` 节点的 `code` 执行 `compile` 验证语法
3. 若验证通过，写入原文件路径

### 5.5 说明变更内容

写回成功后，向用户简述做了哪些改动（diff 式自然语言说明）。

---

## 6. 工作流文件保存目录（创建模式）

### 创建路径（写入）

新建或修改工作流时，写入路径必须在当前项目目录下：

- **项目级（当前 workspace）**：`<workspace>/.flocks/plugins/workflows/<slug-or-folder>/`（`workflow.json`、`workflow.md`、`meta.json` 由 API 写入时可能同目录）
  - ⚠️ 任务输出（报告、artifacts）**不**写入此目录，统一写入 `~/.flocks/workspace/outputs/<YYYY-MM-DD>/<session_id>/`（见全局文件输出约定）


### 读取路径（扫描）

系统会按**从低到高优先级**扫描下列目录（同一逻辑 ID 冲突时**后扫描的覆盖先扫描的**）。写文件时应优先落在列表中**最后的「规范」目录**，避免被后续扫描覆盖或混淆。

**项目（workspace 下）**的`<workspace>/.flocks/plugins/workflows/`，**项目规范路径（新建或修改工作流必须落地于此）**

### ⚠️ 绝对路径规范（重要）

**必须使用绝对路径写入文件**。

解析项目 workspace 并拼接规范目录示例：

```bash
python3 -c "from pathlib import Path; p=Path.cwd(); ws=next((x for x in [p,*p.parents] if (x/'.flocks').is_dir()), p); print(ws/'.flocks/plugins/workflows/<folder>')"
```

**正确示例**：
- `<workspace>/.flocks/plugins/workflows/alert_triage/workflow.json` ✅（项目级）

**错误示例**：
- `.flocks/plugins/workflows/alert_triage/workflow.json` ❌（未展开相对路径，易写错磁盘位置）
- `~/.flocks/plugins/workflows/alert_triage/workflow.json` ❌（用户级路径仅允许历史兼容扫描，不允许作为新建或修改目标）
- 仅因习惯写入 `~/.flocks/workflow/...` 作为**新**工作流首选 ❌（仍可被扫描，但与当前规范及项目级落盘不一致）

---

## 7. 持续执行原则（全局强制）

**在整个 Workflow 创建流程中，以下原则不可违反：**

1. **绝不中途放弃**：任何阶段遇到错误，都必须持续分析原因、尝试不同修复方式，循环重试直到解决。
2. **失败不是终点，是 Debug 的起点**：连续失败时，换思路（检查参数名、输入数据结构、工具调用方式、边的 mapping 等），直到找到根本原因。
3. **Todo 驱动完成**：所有任务项在 Todo 列表中清晰可见，必须逐一完成、逐一标记，**严禁在 Todo 全部 completed 之前宣布任务完成**。
4. **只有以下情况才能停下来询问用户**：
   - 样例数据缺失且无法自动构造
   - 需要用户提供必要的外部凭证或配置（API Key、服务地址等）
   - 需要用户确认流程描述是否正确（场景确认阶段、md 确认阶段）
   - 其他**必须由用户决策**的内容
5. **除上述情况外，所有问题必须自行解决，直到工作流完美运行为止。**

---

## workflow.md 标准模板

```markdown
# [Workflow Name]

## 业务场景
[目标和背景]

## 流程步骤

### 1. [步骤名称]
- **描述**: [操作手册级别描述]
- **工具/模型**: [Tool: xxx / LLM: xxx]
- **输入**: [字段名: 来源和格式]
- **输出**: [字段名: 格式和用途]
- **处理逻辑**:
  - [操作步骤]
  - [工具调用：`result = tool.run_safe('name', ...)`，用 `result["text"]` 取结果]
- **决策分支**（如适用）:
  - 条件 → 分支处理

### 2. [步骤名称]
...
```
