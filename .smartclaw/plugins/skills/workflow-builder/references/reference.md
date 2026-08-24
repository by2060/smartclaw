# Workflow Generator 详细参考

> 本文件包含 workflow 生成的详细约束、示例和模板。SKILL.md 中有引用指向此文件的各节。

## 目录

1. [生成总原则](#1-生成总原则)
2. [节点类型与最小示例](#2-节点类型与最小示例)
3. [数据传递、Edge Mapping 与动态参数](#3-数据传递edge-mapping-与动态参数)
4. [控制流：出边、分支、循环、Join](#4-控制流出边分支循环join)
5. [Tool / LLM / Python / HTTP 决策指南](#5-tool--llm--python--http-决策指南)
6. [工具调用规范与返回值处理](#6-工具调用规范与返回值处理)
7. [外部能力调用：MCP / Agent / Skill / API / 内置工具](#7-外部能力调用mcp--agent--skill--api--内置工具)
8. [并发与多子任务模式](#8-并发与多子任务模式)
9. [文件输出与报告生成](#9-文件输出与报告生成)
10. [workflow.json 骨架模板](#10-workflowjson-骨架模板)
11. [生成前检查清单](#11-生成前检查清单)

> 兼容说明：为避免 SKILL.md 或历史文档中的旧锚点失效，本文在相关章节前保留了旧章节锚点别名。

---

## 1. 生成总原则

生成最终 `workflow.json` 时，默认优先使用多个中等粒度 `type="python"` 业务阶段节点。目标是流程图短、清晰、可调试，同时避免把所有逻辑塞进一个巨大节点。Python 阶段节点可以在阶段内调用工具、LLM、Agent/Skill 等外部能力，并统一做输入归一化、错误包络和阶段输出。

核心规则：

1. **Python 阶段节点优先**：一个节点应代表一个清晰业务阶段，而不是机械代表一次工具或 LLM 调用。
2. **不要生成巨型 Python**：不要把整个 workflow 塞进一个 Python 节点；超过约 80-120 行、包含多个业务阶段、阶段之间有分支/循环、需要独立观测/重试、或输出被多个下游复用时必须拆分。
3. **结构化节点用于边界**：`branch` / `loop` 控制流必须独立；`tool`、`llm`、`http_request`、`subworkflow` 仅在需要单独可视化、并行、重试/计时、复用已有 workflow、或用户明确要求时使用。
4. **阶段内调用外部能力**：同一阶段内强相关的工具、LLM、Agent/Skill 调用，默认在 Python 中用 `tool.run_safe()`、`tool.run()`、`llm.ask()`、`delegate_task` 等辅助能力完成，并统一输出阶段结果。
5. **工具 schema 必须核验**：独立 `tool` 节点或 Python 阶段节点内调用工具前，都必须读取当前工具列表和参数 schema；参数名只允许使用声明过的参数。
6. **动态参数要显式处理**：`tool_args` 不做 Jinja2 渲染；独立节点用上游 payload 或 `edge.mapping`，Python 阶段节点内直接构造参数。
7. **多分支汇合要显式 join**：非互斥多入边汇聚时必须使用 `join: true`，尤其是下游会调用 LLM、写文件或触发昂贵工具时。
8. **文件输出统一走 write 工具**：节点有任何文件输出时，通过 `write` 写入 session outputs，并以 `write` 返回的 `metadata.filepath` 为最终路径。
9. **最终禁止 logic**：`logic` 只允许用于简化预览、快速原型或草稿流程图；完整可执行 `workflow.json` 的 `nodes[]` 中不得包含 `type="logic"`。

---

## 2. 节点类型与最小示例

| 类型 | 必填字段 | 行为说明 |
| :--- | :--- | :--- |
| **tool** | `tool_name` | 仅在需要独立观测、并行、重试/计时或用户明确要求时使用。默认把工具调用放入 Python 阶段节点。`tool_args` 必须严格匹配工具 schema，输出写入 `output_key`（默认 `result`）。 |
| **llm** | `prompt` | 仅在需要独立观测、并行、多下游复用或用户明确要求时使用。默认在 Python 阶段节点中用 `llm.ask()` 做阶段内分析。必须显式设置 `output_key`。 |
| **http_request** | `method`, `url` | 用于没有注册工具的一次性 HTTP 调用。默认优先创建/使用注册工具并在 Python 阶段节点中调用。输出包含 `response_key`（默认 `response`）和 `status_code`。认证密钥不得硬编码。 |
| **subworkflow** | `workflow_id` | 复用已有 workflow 时使用。可用 `inputs_mapping` 和 `inputs_const` 控制输入。 |
| **branch** | `select_key` | 根据 `select_key` 值匹配 `edge.label` 跳转。 |
| **loop** | `select_key` | 语义同 branch，用于循环（继续/退出）。 |
| **logic** | `description` | 仅限简化预览/快速原型/草稿流程图；最终完整 `workflow.json` 禁止使用。 |
| **python** | `code` | 默认执行节点类型。用于业务阶段编排、确定性数据处理、工具/LLM/Agent 调用、结果归一化和落盘；禁止网络库、系统命令和硬编码密钥。 |

### 2.1 节点最小示例

以下示例均为可放入 `nodes[]` 的片段。生成真实 workflow 时，仍需按上下游数据补齐 `edges`、`edge.mapping`、`description` 和测试样例。

**tool 节点：确定性工具调用**

```json
{
  "id": "search_web",
  "type": "tool",
  "tool_name": "websearch",
  "tool_args": { "numResults": 5 },
  "output_key": "search_text",
  "description": "Tool: websearch。动态参数 query 从输入 payload 传入，numResults 固定为 5。"
}
```

**llm 节点：摘要、抽取、判断、报告生成**

```json
{
  "id": "analyze_alert",
  "type": "llm",
  "prompt": "请基于以下告警上下文输出 Markdown 分析：\n\n告警：{{ alert }}\n情报：{{ intel_text }}",
  "output_key": "analysis_markdown",
  "description": "LLM: 汇总告警和情报，生成结构化分析。"
}
```

**http_request 节点：直接 HTTP 请求**

```json
{
  "id": "query_asset_api",
  "type": "http_request",
  "method": "GET",
  "url": "https://api.example.com/assets/{{ asset_id }}",
  "headers": { "Accept": "application/json" },
  "response_key": "asset_response",
  "description": "HTTP: 查询资产详情。认证信息必须来自安全配置或上游输入，禁止硬编码密钥。"
}
```

**subworkflow 节点：复用已有 workflow**

```json
{
  "id": "run_host_triage",
  "type": "subworkflow",
  "workflow_id": "host_triage",
  "inputs_mapping": {
    "host": "target_host",
    "username": "ssh_user"
  },
  "inputs_const": {
    "mode": "quick"
  },
  "output_key": "triage_output",
  "description": "Subworkflow: 调用 host_triage 子流程完成主机快速排查。"
}
```

**branch 节点：一次性条件分支**

```json
{
  "id": "route_by_risk",
  "type": "branch",
  "select_key": "risk_level",
  "description": "根据 risk_level 选择高风险或普通处理路径。"
}
```

配套出边：

```json
{ "from": "route_by_risk", "to": "handle_high_risk", "label": "High" },
{ "from": "route_by_risk", "to": "handle_normal", "label": "" }
```

**loop 节点：继续/退出式循环**

```json
{
  "id": "continue_loop",
  "type": "loop",
  "select_key": "loop_status",
  "description": "根据 loop_status 判断继续处理下一项或退出循环。"
}
```

配套出边：

```json
{ "from": "continue_loop", "to": "process_next_item", "label": "continue" },
{ "from": "continue_loop", "to": "summarize_items", "label": "done" }
```

**logic 节点：仅用于预览、原型或草稿**

> 不要把下面的 `logic` 示例复制到最终完整 `workflow.json`。最终文件中必须替换为 `python`、`branch`、`loop`、`llm`、`tool`、`http_request` 或 `subworkflow`。

```json
{
  "id": "judge_need_review",
  "type": "logic",
  "description": "根据 risk_score 和 business_criticality 判断是否需要人工复核，输出 need_review=true/false。",
  "select_key": "need_review"
}
```

> 最终可执行 workflow 中禁止保留 `logic`；预览阶段的 `logic` 必须在最终生成时替换为明确节点。

**python 节点：确定性数据转换**

```json
{
  "id": "normalize_alert",
  "type": "python",
  "description": "确定性清洗告警字段，输出 risk_level 和 normalized_alert。",
  "code": "alert = inputs.get('alert') if isinstance(inputs.get('alert'), dict) else {}\nseverity = str(alert.get('severity') or '').lower()\noutputs['normalized_alert'] = alert\noutputs['risk_level'] = 'High' if severity in {'high', 'critical'} else 'Normal'"
}
```

**join 变体：等待多入边汇合后执行一次**

```json
{
  "id": "merge_parallel_results",
  "type": "python",
  "join": true,
  "join_mode": "namespace",
  "join_namespace_key": "__by_source__",
  "description": "等待多个并行分支完成，并按来源命名空间汇总结果。",
  "code": "by_source = inputs.get('__by_source__') or {}\noutputs['merged_results'] = by_source\noutputs['merged_count'] = len(by_source)"
}
```

> `join` 不是独立节点类型，而是节点属性。多条非互斥入边汇合时必须使用，尤其是下游会调用 LLM、写文件或触发昂贵工具时。

---

## 3. 数据传递、Edge Mapping 与动态参数

### 3.1 Payload 合并与动态参数

普通节点执行后，引擎会浅合并 `payload = {**inputs, **outputs}`。如果下游可直接消费完整 payload 且不会造成字段冲突，可以不写 `edge.mapping`。

`tool_args` 不做 Jinja2 渲染。运行时会执行 `{**tool_args, **inputs}` 浅合并，所以上游 `inputs` 会覆盖同名静态参数。动态参数必须来自上游 payload 或 `edge.mapping`；只有 `llm.prompt`、`http_request.url` 和字符串 `http_request.body` 支持 Jinja2 模板。

### 3.2 Edge Mapping 规则

- `edge.mapping`：字段传递与重命名（下游 key → 上游 payload 路径）
- `edge.const`：注入常量参数
- **点路径支持**：`mapping: { "user_id": "data.user.id" }`
- **根路径引用**：`mapping: { "full_data": "$" }`

何时写 mapping：

- 下游只需上游 payload 的一部分字段
- 字段需要重命名（上游 key 与下游期望 key 不一致）
- 下游要 `tool.run(..., **inputs)`，需把 inputs 规整到匹配工具参数形状
- 工具动态参数名与上游字段名不同，需对齐到工具 schema 参数名

何时不写 mapping：

- 下游可直接消费完整 payload，且不会造成字段冲突

避免脆弱映射：

- 不要映射“上游不一定产出的 key”（尤其是预览阶段 `logic` 节点推断输出），否则下游拿不到该字段会 KeyError
- 传递对象时确保上游一定写出 `outputs["xxx"]`；否则按扁平 key 逐个映射

### 3.3 工具参数对齐最佳实践

1. 在 `workflow.md` 的输入中直接使用工具参数名。
2. 默认在 Python 阶段节点内调用工具：`result = tool.run_safe("tool_name", arg=value)`；动态参数在 Python 中显式构造。
3. 只有需要独立观测、并行、重试/计时或用户明确要求时才生成独立 `tool` 节点；`tool_args` 只放静态参数，动态参数通过上游 payload 或 `edge.mapping` 传入。
4. 用 `edge.mapping` 完成独立结构化节点之间的字段转换，避免把无关 payload 透传给工具。
5. 仅简化预览/快速原型时使用 `logic` 节点；最终完整 workflow 必须把 `logic` 替换成 `python`、`branch`、`loop` 或必要的外部能力节点。
6. Python 阶段节点中默认用 `result["text"]` 取结果，仅在明确需要结构化数据且已做类型检查时才用 `result["obj"]`。

---

## 4. 控制流：出边、分支、循环、Join

### 4.1 出边选择行为

这是引擎 `_select_edges` 的真实行为，**生成时必须遵守**：

| 节点类型 | 出边选择方式 |
| :--- | :--- |
| `python`/`tool`/`llm`/`http_request`/`subworkflow` | 执行后**所有出边**都触发（不做 label 匹配） |
| `logic` | 仅说明预览/草稿行为；最终完整 `workflow.json` 不允许出现。若预览运行，走 **label 匹配**（与 branch/loop 相同）。 |
| `branch`/`loop` | 不执行 `code`，通过 `select_key`（默认 `"result"`）取值选边 |

label 匹配规则：

- `bool` 值：label 必须用 `"true"` / `"false"`
- `str` 值：label 必须与该字符串完全一致
- `None` 或无命中：回退到空 label 默认边（最多 1 条）

### 4.1.1 并行 fan-out 规则

并行 fan-out 与分支选择是两种不同语义：

- fan-out：一个上游节点执行后同时触发多条出边。
- branch/loop/logic：根据 `select_key` 和 `edge.label` 只选择匹配的出边。

因此，在最终可执行 workflow 中，不能用 `logic` / `branch` / `loop` 作为并行 fan-out 起点。需要并行创建多个下游节点时，上游必须是会触发所有出边的类型：`python`、`tool`、`llm`、`http_request` 或 `subworkflow`。

简化预览 JSON 只是页面结构草稿，可以用 `logic` 节点表达待确认的多路结构；不要为了让预览过 lint 塞入 no-op `python` 节点。预览 JSON 顶层必须包含：

```json
{
  "metadata": {
    "preview": true,
    "stage": "preview"
  }
}
```

保存 artifact 时，workflow lint gate 会把预览草稿中的 `logic` 多出边选择问题降级为 warning；显式 validate、注册、运行、发布以及最终完整 `workflow.json` 仍然严格执行 fan-out 语义。

不要这样写：

```json
{
  "id": "receive_input",
  "type": "logic",
  "description": "接收输入后进入多个并行分支"
}
```

上面的 `logic` 多出边会被 lint 判定为缺少 `select_key`、缺少 label、多个默认边，并且运行时也不会表达并行广播。

### 4.2 分支生成

**在 workflow.md 中描述**：

```markdown
### X. [步骤名称]
- **决策分支**:
  - 条件：`if risk_level == "High"`
  - 分支1（高风险）：执行操作 A
  - 分支2（其他）：执行操作 B
```

**在 workflow.json 中实现**：

```json
{
  "id": "check_risk",
  "type": "branch",
  "select_key": "risk_level",
  "description": "根据风险等级进行分支判断"
}
```

出边示例：

```json
{ "from": "check_risk", "to": "handle_high_risk", "label": "High" },
{ "from": "check_risk", "to": "handle_normal", "label": "" }
```

### 4.3 Join 节点

通过 `join: true` 标记。引擎等待所有入边到达后合并 payload 并执行一次。

- `join_mode`: `flat`（默认）或 `namespace`
- `join_conflict`: `overwrite`（默认）或 `error`

分支汇合强制规则：

1. **多入边汇聚 → 必须 `join=true`**：节点有 ≥2 条来自不同源的入边，且非互斥分支时必须设置 `join=true`。
2. **互斥判断标准**：所有入边来自同一 branch/loop 节点的不同 label 出边 → 互斥，不需要 join。
3. **昂贵节点保护**：含 `llm.ask()`、`tool.run('write', ...)` 或 `tool.run_safe('write', ...)` 的节点，禁止被两条非互斥路径直达。必须先经 `join=true` 汇合节点。
4. **推荐模式**：在汇合点放一个轻量 `python` 节点（`join=true`）归一化多分支输出；即使只需等待汇合且无需转换，最终 workflow 也使用最小 `python` join 节点，不使用 `logic`。

简化预览中的汇合节点可以继续用 `logic` 占位，也不必为了保存草稿强行加 `join: true` 或 no-op `python`。当顶层 `metadata.preview=true` / `metadata.stage="preview"` 存在时，artifact 保存路径会把预览草稿里的 `multi_incoming_no_join` 降级为 warning。最终完整 `workflow.json` 必须按上面的规则补 `join: true`，且最终节点不得使用 `logic`。

---

## 5. Tool / LLM / Python / HTTP 决策指南

每步明确标注它属于哪个 Python 阶段节点；只有满足边界条件时才标注为独立结构化节点。

决策优先级（高→低）：

1. **用户显式指定时必须遵守**：工具名/参数与可用 schema 不一致时提出替代方案。
2. **默认生成 Python 阶段节点**：
   - 每个节点对应一个业务阶段，而不是一次工具/LLM 调用。
   - 阶段内可顺序调用少量工具、LLM、Agent/Skill，并统一做输入归一化、兜底、错误包络、结果合并。
   - 常见阶段：输入归一化、情报富集、上下文收集、研判分析、报告落盘、通知/工单创建。
3. **必须拆分为多个 Python 节点的场景**：
   - 单个节点代码超过约 80-120 行，或包含多个业务阶段。
   - 阶段之间需要 `branch` / `loop` 控制流。
   - 某阶段需要单独重试、观测耗时、审计副作用，或输出会被多个下游复用。
   - 某阶段失败后有独立降级路径，不能只在一个大节点里吞掉错误。
4. **独立结构化节点的场景**：
   - `branch` / `loop` 控制流必须独立。
   - 固定并行 fan-out 需要多个兄弟分支时，每个分支优先是 Python 阶段节点；简单单步且用户要求可视化时才用 `tool` / `llm` / `http_request`。
   - 复用已有 workflow 时使用 `subworkflow`。
   - 没有注册工具且必须直连一次性 HTTP 时使用 `http_request`；复杂鉴权或复用需求高时先创建/使用注册工具。
5. **Tool 调用规则**：
   - 默认在 Python 阶段节点内用 `tool.run_safe("tool_name", ...)` 调用。
   - 独立 `tool` 节点只用于需要独立并行、重试/计时、图中展示或多下游复用的单步能力。
   - 无论 Python 内调用还是独立 `tool` 节点，参数都必须按 schema 传参。
6. **LLM 调用规则**：
   - 默认在 Python 阶段节点内用 `llm.ask(prompt)` 做阶段内分析、摘要、报告生成或结构化抽取。
   - 独立 `llm` 节点只用于并行多视角分析、需要单独复用/观测，或用户明确要求图中展示 LLM 调用。
7. **禁止**：不要用 LLM 臆测可通过工具核验的事实；不要在 Python 中 import 网络库或手写 MCP/API client；不要为了短图把多个无关业务阶段塞进一个节点。

若需结构化 JSON 输出，在 prompt 中要求“纯 JSON 字符串”，优先在同一或后续 Python 阶段节点中用 `json.loads` 解析；解析逻辑复杂或输出被多个下游复用时拆成单独 Python 节点。

---

## 6. 工具调用规范与返回值处理

> 以下为示例模式，实际工具名和参数以 `registry.py` 为准。

### 6.1 返回值类型陷阱（必读）

`tool.run()` 返回 `ToolResult.output`，类型是 `Any`。**大多数工具返回字符串**（格式化文本），少数返回 dict/list。**严禁假设返回值是 dict 并直接调用 `.get()`**。

```python
# ❌ 常见错误：导致 AttributeError: 'str' object has no attribute 'get'
result = tool.run('threatbook_ip', ip=ip)
threats = result.get("threats")  # result 是 str，不是 dict！

# ❌ 常见错误：run_safe 的 obj 也可能是 str
result = tool.run_safe('threatbook_ip', ip=ip)
threats = result["obj"].get("threats")  # obj 可能是 str！

# ✅ 推荐写法：使用 text（永远是 str）
result = tool.run_safe('threatbook_ip', ip=ip)
outputs['intel_text'] = result['text']  # 安全

# ✅ 需要结构化数据时：先检查类型
result = tool.run_safe('threatbook_ip', ip=ip)
obj = result['obj']
if isinstance(obj, dict):
    outputs['threats'] = obj.get('threats', [])
elif isinstance(obj, str):
    import json
    try:
        parsed = json.loads(obj)
        outputs['threats'] = parsed.get('threats', []) if isinstance(parsed, dict) else []
    except json.JSONDecodeError:
        outputs['threats'] = []
else:
    outputs['threats'] = []
```

### 6.2 标准调用模式

**workflow.md 中的写法**：

```markdown
- **工具/模型**: Tool: websearch
- **处理逻辑**:
  - 默认在 Python 阶段节点中调用 `tool.run_safe('websearch', ...)`
  - 只有该搜索需要独立并行、重试/计时、图中展示或多下游复用时，才使用独立 `tool` 节点
  - 输出字段：`search_text`
  - 如需解析结构化对象，在同一或后续 Python 阶段节点中做类型检查
```

**workflow.json 独立 tool 节点（例外：关键步骤必须可视化时使用）**：

```json
{
  "id": "search",
  "type": "tool",
  "tool_name": "websearch",
  "tool_args": { "numResults": 5 },
  "output_key": "search_text",
  "description": "Tool: websearch。query 从输入 payload 透传，numResults 固定为 5。"
}
```

**Python 阶段节点内调用工具（默认写法）**：

```json
{
  "id": "search_stage_python",
  "type": "python",
  "description": "阶段编排: 归一化 query 并调用 websearch。",
  "code": "query = str(inputs.get('query') or '').strip()\nresult = tool.run_safe('websearch', query=query, numResults=5)\noutputs['search_text'] = result['text']\noutputs['search_error'] = result['error']"
}
```

---

## 7. 外部能力调用：MCP / Agent / Skill / API / 内置工具

### 7.1 总原则

workflow 原生节点类型没有 `mcp`、`agent`、`skill`、`api`。这些能力在 `workflow.json` 中默认放入 Python 阶段节点内调用；只有需要独立并行、重试/计时、图中展示、多下游复用，或用户明确要求时，才生成独立结构化节点。

| 目标能力 | 独立节点写法（例外） | Python 阶段节点写法（默认） |
| :--- | :--- | :--- |
| 内置工具 | `tool` 节点，`tool_name` 直接写注册工具名 | `tool.run_safe("tool_name", **args)` |
| MCP 工具 | MCP server 工具注册进 ToolRegistry 后用 `tool` 节点 | `tool.run_safe("server_tool", **args)`，不要手写 MCP client |
| API 工具 | API provider/tool YAML 注册后用 `tool` 节点 | `tool.run_safe("api_tool", **args)` |
| 一次性 HTTP API | `http_request` 节点 | 不要在 Python 中 import 网络库；复杂场景先创建 API 工具 |
| Agent / 子任务 | `tool` 节点调用 `delegate_task` | `tool.run_safe("delegate_task", prompt=..., category=... 或 subagent_type=...)` |
| Skill 指令 | `delegate_task.load_skills` 或 `skill` 工具 | 在 `delegate_task` 参数中传 `load_skills`，或 `tool.run_safe("skill", name=...)` |
| 已有工作流 | `subworkflow` 节点 | 优先不用 Python 调 `run_workflow`；需要复用时使用原生 `subworkflow` |

生成前必须核验当前工具列表和参数 schema。工具来源只影响如何识别工具；落到 workflow 后，默认在 Python 阶段节点里用正确工具名和参数显式调用。独立 `tool` 节点只在边界场景使用，并且必须正确设置 `tool_name`、`tool_args`、`output_key` 和输入 payload。

### 7.1.1 Python 阶段节点调用示例（默认）

下面示例展示外部能力在 Python 节点中的默认调用方式。示例工具名必须替换为当前 ToolRegistry 中真实存在的注册名，参数名必须与对应 schema 完全一致。

**内置工具 / MCP 工具 / API 工具：统一用 `tool.run_safe()` 调用**

```json
{
  "id": "collect_external_context",
  "type": "python",
  "description": "阶段编排: 调用已注册的 MCP/API/内置工具并统一归一化结果。",
  "code": "target_ip = str(inputs.get('target_ip') or '').strip()\nhost = str(inputs.get('host') or '').strip()\nintel = tool.run_safe('ThreatBook_ip_query', ip=target_ip)\nasset = tool.run_safe('asset_center_query_host', host=host)\noutputs['intel_success'] = intel['success']\noutputs['intel_text'] = intel['text']\noutputs['intel_error'] = intel['error']\noutputs['asset_success'] = asset['success']\noutputs['asset_text'] = asset['text']\noutputs['asset_error'] = asset['error']"
}
```

**Agent / Skill / LLM：在同一个业务阶段中构造 prompt、注入 skill、汇总结果**

```json
{
  "id": "delegate_and_summarize",
  "type": "python",
  "description": "阶段编排: 委托子 Agent，并用 LLM 汇总研判结果。",
  "code": "alert = inputs.get('normalized_alert') or {}\nintel_text = str(inputs.get('intel_text') or '')\nprompt = f\"请基于告警和外部情报给出调查结论。\\n告警: {alert}\\n情报: {intel_text}\"\nagent = tool.run_safe('delegate_task', prompt=prompt, subagent_type='explore', run_in_background=False, load_skills=['workflow-builder'])\nsummary_prompt = f\"请把以下子 Agent 结果整理为 Markdown 摘要，只保留结论、证据和建议。\\n\\n{agent['text']}\"\nsummary = llm.ask(summary_prompt)\noutputs['agent_success'] = agent['success']\noutputs['agent_text'] = agent['text']\noutputs['agent_error'] = agent['error']\noutputs['summary'] = summary"
}
```

**直接加载 Skill 指令文本：仅当后续 LLM/Agent 需要读取 skill 内容时使用**

```json
{
  "id": "load_skill_for_prompt",
  "type": "python",
  "description": "阶段编排: 读取 skill 指令文本并交给后续提示词使用。",
  "code": "skill_result = tool.run_safe('skill', name='workflow-builder')\nskill_text = skill_result['text']\nprompt = '请基于以下 skill 规则检查 workflow 设计是否合规：\\n\\n' + skill_text\noutputs['skill_load_success'] = skill_result['success']\noutputs['skill_context'] = skill_text\noutputs['review_prompt'] = prompt\noutputs['skill_error'] = skill_result['error']"
}
```

**内置写文件工具：需要暴露最终文件路径时读取 `metadata.filepath`**

```json
{
  "id": "write_report_stage",
  "type": "python",
  "description": "阶段编排: 生成报告并通过内置 write 工具落盘。",
  "code": "summary = str(inputs.get('summary') or '')\nwrite = tool.run_safe('write', filePath='final_report.md', content=summary)\nmetadata = write.get('metadata') or {}\noutputs['write_success'] = write['success']\noutputs['write_error'] = write['error']\noutputs['report_path'] = metadata.get('filepath')"
}
```

一次性 HTTP API 不要在 Python 节点中 `import requests` / `urllib` 手写网络调用；优先创建/使用已注册 API 工具并按上面方式 `tool.run_safe()` 调用。确实只是临时、无复杂鉴权的 REST 请求时，用独立 `http_request` 节点。

### 7.2 内置工具、MCP 工具与 API 工具

内置工具按注册名直接写入 `tool_name`。MCP 工具连接成功后会被适配为 SmartClaw Tool 并注册到 ToolRegistry，默认注册名模式是 `{server_name}_{mcp_tool_name}`，例如 server `ThreatBook` 的 MCP tool `ip_query` 会注册为 `ThreatBook_ip_query`。已通过 API 工具机制创建的工具会从项目级 `.smartclaw/plugins/tools/api/<provider>/<tool>.yaml` 注册进 ToolRegistry，`source` 为 `api`。实际工具名必须以当前工具列表为准。

独立 `tool` 节点模板（仅用于需要独立观测/并行/重试/计时或用户明确要求的例外场景）：

```json
{
  "id": "query_external_tool",
  "type": "tool",
  "tool_name": "registered_tool_name",
  "tool_args": {},
  "output_key": "tool_output",
  "description": "Tool: 调用已注册工具。动态参数由上游 payload 或 edge.mapping 传入。"
}
```

配套边示例：

```json
{ "from": "normalize_input", "to": "query_external_tool", "mapping": { "ip": "src_ip" } }
```

示例：

```json
{
  "id": "query_mcp_intel",
  "type": "tool",
  "tool_name": "ThreatBook_ip_query",
  "tool_args": {},
  "output_key": "intel_text",
  "description": "MCP 工具: 调用 ThreatBook_ip_query 查询 IP 情报。ip 参数由上游映射传入。"
}
```

```json
{
  "id": "query_asset_api_tool",
  "type": "tool",
  "tool_name": "asset_center_query_host",
  "tool_args": {},
  "output_key": "asset_info",
  "description": "API 工具: 调用已注册的 asset_center_query_host。host 参数从上游 payload 映射。"
}
```

不要生成 `type="mcp"`；也不要在 Python 节点里手写 MCP client。若 MCP 工具未出现在工具列表中，先提示需要配置/启用 MCP server，而不是编造工具名。已有稳定 API 集成默认在 Python 阶段节点中用 `tool.run_safe()` 调用；需要独立并行/观测时才用独立 `tool` 节点。临时、无状态、无复杂鉴权的 REST 调用可用 `http_request`；复杂鉴权或复用需求高时先创建 API 工具。

### 7.3 直接 HTTP

```json
{
  "id": "call_raw_api",
  "type": "http_request",
  "method": "GET",
  "url": "{{ api_base_url }}/v1/incidents/{{ incident_id }}",
  "headers": { "Accept": "application/json" },
  "response_key": "incident_response",
  "description": "直接 HTTP: 查询事件详情。api_base_url 和 incident_id 来自输入 payload。"
}
```

### 7.4 Agent / 子任务

workflow 没有 `agent` 节点。需要把复杂研究、长任务或需要独立上下文的步骤交给子 Agent 时，使用 `delegate_task`。默认在 Python 阶段节点中构造 prompt 并调用 `tool.run_safe("delegate_task", ...)`；只有需要独立并行、重试/计时、图中展示或多下游复用时，才生成独立 `tool` 节点。

`delegate_task` 必须提供 `prompt`，并且 `category` 与 `subagent_type` 只能二选一。`run_in_background` 默认 `false`；如果后续节点依赖子任务结果，保持同步执行。

独立 `tool` 节点中，动态 prompt 推荐先由上游节点生成 `agent_prompt`，再用 `edge.mapping` 映射给 `delegate_task.prompt`。Python 编排块中可以直接构造 prompt 并调用 `tool.run_safe("delegate_task", ...)`。

```json
{
  "id": "build_agent_prompt",
  "type": "python",
  "description": "构造子 Agent 任务 prompt。",
  "code": "alert = inputs.get('normalized_alert') or {}\nintel = inputs.get('intel_text') or ''\noutputs['agent_prompt'] = f\"请基于以下告警和情报给出调查结论。\\n告警: {alert}\\n情报: {intel}\""
}
```

```json
{
  "id": "delegate_deep_analysis",
  "type": "tool",
  "tool_name": "delegate_task",
  "tool_args": {
    "category": "deep",
    "run_in_background": false,
    "load_skills": []
  },
  "output_key": "agent_result",
  "description": "Agent: 委托 deep 类别子任务。prompt 由上游 agent_prompt 映射传入。"
}
```

配套边示例：

```json
{ "from": "build_agent_prompt", "to": "delegate_deep_analysis", "mapping": { "prompt": "agent_prompt" } }
```

如果需要调用已有 Agent，用 `subagent_type` 替代 `category`，不要同时填写两者。`subagent_type` 必须是当前系统中已存在且可委托的 subagent 名称，例如内置 `explore`、`librarian`、`oracle`、`self_enhance`，或项目级 `.smartclaw/plugins/agents/<name>/agent.yaml` 中定义且 `delegatable: true` 的 agent。不要把 agent 名写进 `load_skills`。

调用已有 agent 的示例：

```json
{
  "id": "delegate_existing_explore_agent",
  "type": "tool",
  "tool_name": "delegate_task",
  "tool_args": {
    "subagent_type": "explore",
    "run_in_background": false,
    "load_skills": []
  },
  "output_key": "explore_agent_result",
  "description": "Agent: 调用已有 explore agent 调研代码或上下文。prompt 由上游 agent_prompt 映射传入。"
}
```

配套边示例：

```json
{ "from": "build_agent_prompt", "to": "delegate_existing_explore_agent", "mapping": { "prompt": "agent_prompt" } }
```

如果 prompt 是固定内容，也可以直接放在 `tool_args.prompt` 中；如果 prompt 需要引用上游字段，不要在 `tool_args.prompt` 写 Jinja2，先用上游 `python` 或 `llm` 节点生成字符串，再通过 `edge.mapping` 传入。

Python 编排块内调用 Agent 示例：

```json
{
  "id": "enrich_with_agent",
  "type": "python",
  "description": "阶段编排: 构造 prompt 并委托子 Agent 进行深度分析。",
  "code": "alert = inputs.get('normalized_alert') or {}\nintel = inputs.get('intel_text') or ''\nprompt = f\"请基于告警和情报给出调查结论。\\n告警: {alert}\\n情报: {intel}\"\nresult = tool.run_safe('delegate_task', prompt=prompt, category='deep', run_in_background=False, load_skills=[])\noutputs['agent_success'] = result['success']\noutputs['agent_text'] = result['text']\noutputs['agent_error'] = result['error']"
}
```

### 7.5 Skill

Skill 是给 Agent 使用的指令包，不是业务系统能力本身。常见写法有两种：

1. 委托子 Agent 时用 `delegate_task.load_skills` 注入 skill，让子 Agent 按该 skill 执行。

```json
{
  "id": "delegate_with_skill",
  "type": "tool",
  "tool_name": "delegate_task",
  "tool_args": {
    "category": "deep",
    "run_in_background": false,
    "load_skills": ["workflow-builder"]
  },
  "output_key": "delegated_workflow_design",
  "description": "Skill: 把 workflow-builder skill 注入给子 Agent。prompt 由上游映射传入。"
}
```

2. 仅当下游 LLM 需要读取某个 skill 的指令文本时，才直接调用 `skill` 工具加载内容。

```json
{
  "id": "load_skill_context",
  "type": "tool",
  "tool_name": "skill",
  "tool_args": { "name": "workflow-builder" },
  "output_key": "skill_context",
  "description": "Skill: 加载 workflow-builder 的指令文本，供后续 Python 阶段节点或 LLM 分析参考。"
}
```

`smartclaw_skills` 用于查找、安装、检查和移除 skill，属于管理动作；不要把它作为普通业务执行节点，除非工作流目标本身就是管理 skill。

### 7.6 子流程

复用已有工作流时使用 `subworkflow` 节点，而不是 `tool` 节点调用 `run_workflow`。`subworkflow` 会加载子 workflow，并用 `inputs_mapping` / `inputs_const` 控制输入形状。

```json
{
  "id": "run_host_triage",
  "type": "subworkflow",
  "workflow_id": "host_triage",
  "inputs_mapping": { "host": "target_host" },
  "inputs_const": { "mode": "quick" },
  "output_key": "host_triage_output",
  "description": "Subworkflow: 复用已有 host_triage 工作流。"
}
```

---

## 8. 并发与多子任务模式

### 8.1 总原则

真正需要并发调用多个 LLM、工具、HTTP API、子工作流或 Agent，并且需要独立观测或独立失败处理时，优先用 workflow 图结构表达：

1. 上游节点准备共享上下文。
2. 从同一上游节点拉多条出边到多个兄弟节点。
3. 每个兄弟节点优先是一个 Python 阶段节点，独立执行一个 LLM/工具/API/Agent 子任务或一组强相关子调用。
4. 下游用 `join: true` 汇合，再做归一化、总结或落盘。

引擎行为：`python` / `tool` / `llm` / `http_request` / `subworkflow` 节点会触发所有出边；同一轮 ready 的多个节点在 `max_parallel_workers > 1` 时会并行执行。默认运行路径通常会配置多个 worker，但 workflow 定义本身不要依赖具体 worker 数保证时序。

同一阶段内的少量同步调用可以放入 Python 阶段节点顺序执行。不要在单个 Python 节点里实现并发：

- 不要写 `async` / `await`，lint 会阻断。
- 不要用 `ThreadPoolExecutor` / `multiprocessing` / 手写网络请求来绕过结构化节点。
- 不要在单个 Python 节点里大批量调用 LLM 或 MCP/API 工具；数量多、可并行、需要独立观测时拆成多个 Python 分支节点。只有单步能力非常简单且用户要求图中展示时，才拆成 `llm` / `tool` / `http_request` 节点。

### 8.2 并发调用多个 LLM

适合：同一份上下文需要多个分析视角，例如风险判断、处置建议、证据摘要并行生成。

```json
{
  "id": "prepare_analysis_context",
  "type": "python",
  "description": "整理共享分析上下文，供多个 Python 分支节点并发使用。",
  "code": "alert = inputs.get('normalized_alert') or {}\nintel = inputs.get('intel_text') or ''\noutputs['analysis_context'] = {'alert': alert, 'intel_text': intel}"
}
```

```json
{
  "id": "risk_assessment_stage",
  "type": "python",
  "description": "Python 分支: 调用 LLM 生成风险判断。",
  "code": "context = inputs.get('analysis_context') or {}\nprompt = f\"请基于以下上下文判断风险等级和理由，输出 Markdown：\\n\\n{context}\"\noutputs['risk_assessment'] = llm.ask(prompt)"
}
```

```json
{
  "id": "remediation_plan_stage",
  "type": "python",
  "description": "Python 分支: 调用 LLM 生成处置建议。",
  "code": "context = inputs.get('analysis_context') or {}\nprompt = f\"请基于以下上下文给出处置建议，输出 Markdown：\\n\\n{context}\"\noutputs['remediation_plan'] = llm.ask(prompt)"
}
```

汇合节点推荐使用 `join_mode: "namespace"`，避免多个分支输出字段冲突：

```json
{
  "id": "merge_llm_outputs",
  "type": "python",
  "join": true,
  "join_mode": "namespace",
  "join_namespace_key": "__by_source__",
  "description": "等待多个 LLM 分支完成，按来源命名空间汇总。",
  "code": "by_source = inputs.get('__by_source__') or {}\noutputs['risk_assessment'] = (by_source.get('risk_assessment_stage') or {}).get('risk_assessment', '')\noutputs['remediation_plan'] = (by_source.get('remediation_plan_stage') or {}).get('remediation_plan', '')\noutputs['parallel_llm_outputs'] = by_source"
}
```

配套边示例：

```json
[
  { "from": "prepare_analysis_context", "to": "risk_assessment_stage" },
  { "from": "prepare_analysis_context", "to": "remediation_plan_stage" },
  { "from": "risk_assessment_stage", "to": "merge_llm_outputs" },
  { "from": "remediation_plan_stage", "to": "merge_llm_outputs" }
]
```

### 8.3 并发调用多个 Agent

适合：多个独立调查方向，例如一个 Agent 查代码上下文，一个 Agent 查外部资料，一个 Agent 做高阶推理。每个兄弟节点优先是一个 Python 分支节点，在节点内调用 `tool.run_safe("delegate_task", ...)`；只有用户明确要求可视化单步 delegate 调用时，才生成独立 `tool` 节点。

若后续节点需要结果，`delegate_task.run_in_background` 保持 `false`，让兄弟节点各自同步返回，再通过 join 汇合。

```json
{
  "id": "prepare_agent_tasks",
  "type": "python",
  "description": "构造多个 Agent 的任务 prompt。",
  "code": "target = inputs.get('target') or ''\noutputs['explore_prompt'] = f'围绕 {target} 查找项目内相关实现、配置和调用链。'\noutputs['oracle_prompt'] = f'基于当前证据评估 {target} 的风险、假设和下一步验证建议。'"
}
```

`delegate_explore_agent` / `delegate_oracle_agent` 节点默认生成为 Python 分支节点，仅 `subagent_type`、输出字段和 prompt 来源不同：前者建议 `subagent_type="explore"`、输出 `explore_result`；后者建议 `subagent_type="oracle"`、输出 `oracle_result`。

```json
{
  "id": "delegate_explore_agent",
  "type": "python",
  "description": "Python 分支: 委托 explore Agent 调研项目上下文。",
  "code": "prompt = inputs.get('explore_prompt') or ''\nresult = tool.run_safe('delegate_task', prompt=prompt, subagent_type='explore', run_in_background=False, load_skills=[])\noutputs['explore_success'] = result['success']\noutputs['explore_result'] = result['text']\noutputs['explore_error'] = result['error']"
}
```

```json
{
  "id": "delegate_oracle_agent",
  "type": "python",
  "description": "Python 分支: 委托 oracle Agent 做风险和假设评估。",
  "code": "prompt = inputs.get('oracle_prompt') or ''\nresult = tool.run_safe('delegate_task', prompt=prompt, subagent_type='oracle', run_in_background=False, load_skills=[])\noutputs['oracle_success'] = result['success']\noutputs['oracle_result'] = result['text']\noutputs['oracle_error'] = result['error']"
}
```

```json
{
  "id": "merge_agent_outputs",
  "type": "python",
  "join": true,
  "join_mode": "namespace",
  "join_namespace_key": "__by_source__",
  "description": "等待多个 Agent 分支完成，并保留各来源输出。",
  "code": "by_source = inputs.get('__by_source__') or {}\noutputs['agent_outputs'] = by_source\noutputs['explore_result'] = (by_source.get('delegate_explore_agent') or {}).get('explore_result', '')\noutputs['oracle_result'] = (by_source.get('delegate_oracle_agent') or {}).get('oracle_result', '')"
}
```

配套边示例：

```json
[
  { "from": "prepare_agent_tasks", "to": "delegate_explore_agent", "mapping": { "prompt": "explore_prompt" } },
  { "from": "prepare_agent_tasks", "to": "delegate_oracle_agent", "mapping": { "prompt": "oracle_prompt" } },
  { "from": "delegate_explore_agent", "to": "merge_agent_outputs" },
  { "from": "delegate_oracle_agent", "to": "merge_agent_outputs" }
]
```

`run_in_background: true` 只适合 fire-and-forget 或后续有明确任务轮询/续接机制的工作流；普通分析报告类 workflow 不要使用，否则 join 得到的可能只是任务标识而不是最终结论。

### 8.4 并发调用多个工具或 API

适合：多个互不依赖的情报源、资产源、日志源并行查询。若需要并发和独立观测，每个外部系统优先一个 Python 分支节点，节点内部用 `tool.run_safe()` 调用对应注册工具。没有注册工具且只能直连一次性 HTTP 时，使用独立 `http_request` 节点。若只是同一阶段内少量顺序富集，可用一个 Python 阶段节点收拢。

例如 `query_threatbook_stage` 可生成一个 Python 分支节点，在节点内调用 `tool.run_safe("ThreatBook_ip_query", ...)` 并输出 `threatbook_text`；`query_asset_center_stage` 可生成另一个 Python 分支节点，在节点内调用 `tool.run_safe("asset_center_query_host", ...)` 并输出 `asset_info`。二者从同一上游节点并发出发，最后汇合：

```json
{
  "id": "merge_lookup_outputs",
  "type": "python",
  "join": true,
  "join_mode": "namespace",
  "join_namespace_key": "__by_source__",
  "description": "汇合多个工具/API 查询结果。",
  "code": "by_source = inputs.get('__by_source__') or {}\noutputs['lookup_outputs'] = by_source\noutputs['threatbook_text'] = (by_source.get('query_threatbook_stage') or {}).get('threatbook_text', '')\noutputs['asset_info'] = (by_source.get('query_asset_center_stage') or {}).get('asset_info')"
}
```

### 8.5 动态数量任务

workflow 图适合固定数量或少量可枚举的并发分支。如果输入是动态列表：

- 少量固定上限：显式生成 `item_1`、`item_2` 等分支，并在上游为空时输出空结果。
- 大量或不定数量：优先创建一个批量工具/API，或把批量处理封装为 `subworkflow` / 专用工具。
- 不要求并发：使用 `loop` 表达逐项处理，再在末尾汇总。

不要为了动态 fan-out 在 Python 节点里绕过 lint、直接调网络或大批量调 LLM。无法由现有节点表达时，先说明限制，并建议新增专用工具或子工作流。

---

## 9. 文件输出与报告生成

### 9.1 文件输出规则

节点有任何文件输出时，统一通过 `write` 工具写入 session outputs，最终路径以 `write` 返回的 `metadata.filepath` 为准。

- `filePath` 优先传安全文件名或相对文件名（如 `report.md`），让 `write` 工具按当前 session 路由到 `~/.smartclaw/workspace/outputs/<YYYY-MM-DD>/<session_id>/`
- 如果需要把最终路径暴露给下游或最终结果，必须用 `tool.run_safe('write', ...)`，并从 `result.get('metadata', {}).get('filepath')` 读取
- **禁止**返回传入的候选 `filePath` 作为最终路径，因为 `write` 可能发生 session 重写、去重或 sandbox 映射
- **禁止**在 workflow 中拼接 `default-session`、用户绝对 outputs 路径或项目相对路径（如裸 `artifacts/`），避免污染代码仓库或返回不存在的路径

### 9.2 报告生成最佳实践

默认生成详细结构化报告，除非用户明确要求简化。

标准报告结构：

```markdown
# [报告标题]

## 执行摘要
[1-2 段概述关键发现和结论]

## 详细分析
[按维度展开，如：告警详情、威胁情报、内部上下文]

## 关键发现
- 发现点 1：具体描述
- 发现点 2：具体描述

## 风险评估
- 风险等级：[Low/Medium/High/Critical]
- 风险说明：[详细说明]

## 建议与行动项
1. [具体建议 1]
2. [具体建议 2]

## 数据来源
- [使用的数据源和工具]
```

报告生成节点的 `description` 应明确包含：报告章节、信息类型、格式要求（Markdown）、详细程度。

---

## 10. workflow.json 骨架模板

最小可执行 workflow 结构（业务阶段级节点）：

```json
{
  "name": "my_workflow",
  "description": "工作流用途说明（可选）",
  "start": "collect_search_context",
  "nodes": [
    {
      "id": "collect_search_context",
      "type": "python",
      "description": "阶段编排: 归一化 query，调用 websearch，并产出分支字段。",
      "code": "query = str(inputs.get('query') or '').strip()\nresult = tool.run_safe('websearch', query=query, numResults=5)\ntext = str(result['text'] or '').strip()\noutputs['query'] = query\noutputs['search_text'] = text\noutputs['search_success'] = result['success']\noutputs['search_error'] = result['error']\noutputs['has_results'] = bool(text)"
    },
    {
      "id": "check_results",
      "type": "branch",
      "select_key": "has_results",
      "description": "根据搜索是否有结果分支。"
    },
    {
      "id": "summarize_and_write",
      "type": "python",
      "description": "阶段编排: 调用 LLM 生成摘要，调用 write 工具落盘，并返回最终路径。",
      "code": "search_text = str(inputs.get('search_text') or '')\nprompt = '请基于以下资料生成 Markdown 摘要：\\n\\n' + search_text\nsummary = llm.ask(prompt)\nwrite = tool.run_safe('write', filePath='summarize_output.md', content=summary)\nmetadata = write.get('metadata') or {}\noutputs['summary'] = summary\noutputs['write_success'] = write['success']\noutputs['write_error'] = write['error']\noutputs['report_path'] = metadata.get('filepath')\noutputs['requested_report_path'] = 'summarize_output.md'"
    },
    {
      "id": "fallback",
      "type": "python",
      "description": "无结果时的兜底处理。",
      "code": "outputs['summary'] = '未找到相关结果'"
    }
  ],
  "edges": [
    { "from": "collect_search_context", "to": "check_results" },
    { "from": "check_results", "to": "summarize_and_write", "label": "true" },
    { "from": "check_results", "to": "fallback", "label": "false" }
  ]
}
```

---

## 11. 生成前检查清单

生成涉及外部能力或复杂控制流的节点前，逐项确认：

1. 是否已优先使用多个中等粒度 Python 阶段节点，而不是一堆单步 `tool`/`llm` 节点或一个巨型 Python 节点。
2. 每个 Python 节点是否只覆盖一个清晰业务阶段；超过约 80-120 行、跨多个阶段、需要独立观测/重试或多下游复用时是否已拆分。
3. 当前工具列表中是否已有可用工具；默认在 Python 阶段节点中用 `tool.run_safe()` 调用，且参数名与 schema 完全一致。
4. 独立 `tool` / `llm` / `http_request` / `subworkflow` 节点是否有明确理由：并行、独立观测、重试/计时、复用已有 workflow，或用户明确要求。
5. 是否误用了 Jinja2 到 `tool_args`；如需模板，改成上游 Python 节点构造字段。
6. 是否把 Agent 写成了不存在的 `agent` 节点；应在 Python 节点中调用 `tool.run_safe("delegate_task", ...)`，或在需要独立观测时用 `tool` 节点调用 `delegate_task`。
7. 是否把 Skill 当作业务执行节点；优先通过 `delegate_task.load_skills` 注入。
8. 是否应复用已有 workflow；应使用 `subworkflow`，不要调用 `run_workflow`。
9. 最终 `nodes[]` 中是否不存在任何 `type="logic"`；若存在，必须替换为明确节点。
10. 多出边的 `branch`/`loop` 是否设置 `select_key`，非默认出边是否设置 `label`。
11. 非互斥多入边是否使用 `join: true`，昂贵节点是否已被 join 保护。
12. 文件输出是否通过 `write` 工具，并使用 `metadata.filepath` 作为最终路径。
