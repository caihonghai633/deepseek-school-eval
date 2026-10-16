# AE-01 学校办公场景大模型 Agent 评测工程师

## Stage 1 技术方案与工程骨架

本阶段只冻结工程边界、数据契约、配置模板和可复盘的样例数据，不实现 Agent
业务逻辑、RAG 执行逻辑或评测运行逻辑。审核通过后，Stage 2 再按本文约定实现。

### 1. 技术栈选型及理由

| 组件 | 选型 | 工程理由 |
| --- | --- | --- |
| 运行时 | Python 3.11+，Linux | SDK、文件解析和测试生态成熟；所有路径在代码边界使用 `pathlib.Path`，不依赖 Windows 路径。 |
| 被测模型接口 | `openai` 官方 Python SDK，`OpenAI(api_key=..., base_url="https://api.deepseek.com")`，模型 `deepseek-chat` | 题目指定的 DeepSeek API 接入方式；将模型、请求参数和 Prompt 快照写入结果，保证 V1/V2 可复现。 |
| 数据契约 | Pydantic v2 | 对用例、Trace、指标和结果做运行前/写入前校验；`extra="forbid"` 防止字段拼写错误后静默丢数据。 |
| 配置 | YAML + PyYAML | V1/V2 的模型参数、System Prompt、RAG 策略和交付校验开关外置，实验差异可审查、可版本化。API Key 只从 `DEEPSEEK_API_KEY` 环境变量读取。 |
| 知识库检索 | `rank-bm25` + 向量索引接口 + `numpy` | V1 使用低 `top_k` 的纯向量基线；V2 使用 BM25 与向量的混合检索并提高 `top_k`，便于量化检索策略改动的收益。 |
| 文件真实性检查 | `python-docx` | 读取真实生成的 `.docx` 文件，检查文件存在、扩展名、非空文本和章节标题；不以模型回复中的路径或 Markdown 文本冒充文件成功。 |
| 指标与报告 | `pandas` | 对多次运行的 Token、耗时、成本、通过率和故障分类做离线聚合；原始证据仍保留在 `runs/`。 |
| 测试 | `pytest` | 覆盖 Schema 校验、确定性检查器、失败重跑和追加写入等工程行为；不把主观 LLM 评分作为唯一通过标准。 |
| 环境加载 | `python-dotenv` | 本地开发时加载环境变量；生产/考核环境仍以外部安全注入为准。 |

核心取舍是“确定性校验优先、白盒可观测性优先”。模型输出可以作为被测行为，
但最终是否通过由可重放的规则检查决定：必需的规则 ID 是否召回、工具是否按顺序
调用且参数符合约束、真实 `.docx` 是否存在并包含章节、平台是否返回可验证回执。
每一次 API 调用记录 prompt/completion Token、毫秒级耗时和成本公式，避免只报一个
主观质量分数。

DeepSeek 客户端的固定接入约束如下，Stage 2 只能通过配置覆盖允许的采样参数，不能
改变 provider、base URL 或模型：

```python
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)
response = client.chat.completions.create(
    model="deepseek-chat",
    messages=messages,
    temperature=config.temperature,
    max_tokens=config.max_tokens,
)
```

### 2. 项目目录结构与职责边界

```text
.
├── configs/
│   ├── v1_baseline.yaml          # V1 基线模型、Prompt、纯向量低 top_k、关闭后置校验
│   └── v2_improved.yaml          # V2 混合检索、高 top_k、开启真实文件/回执校验
├── data/
│   ├── knowledge_base/           # 学校教学规范、规则索引和跨会话记忆存储（当前留空）
│   └── sample_test_case.jsonl    # 一条符合 TestCaseSchema 的高质量示例用例
├── sut/                          # System Under Test：只实现学校办公 Agent 能力
│   ├── agent.py                  # DeepSeek 调用与 Agent 编排边界
│   ├── rag_engine.py             # 规则检索、混合排序和 RAG Trace 边界
│   └── tools.py                  # .docx 生成/解析、教务处投递工具适配边界
├── evaluator/                    # Evaluator：只观察、检查、归因和统计
│   ├── schemas.py                # Pydantic v2 输入/输出数据契约
│   ├── runner.py                 # 配置加载、调用 SUT、采集指标、追加保存
│   ├── checkers.py               # 记忆、RAG、工具、文档、平台的确定性检查
│   └── analyzer.py               # 故障归因、V1/V2 对比和报告汇总
├── runs/
│   └── sample_raw_result.jsonl   # 一条不可变原始运行记录（含失败交付示例）
├── reports/                      # 只放聚合后的最终评测报告（当前留空）
├── requirements.txt              # 轻量 Linux 依赖
└── STAGE1_PROPOSAL.md            # 本提交文档
```

SUT 与 Evaluator 严格解耦：SUT 负责“产生行为和白盒事件”，不判定自己是否通过；
Evaluator 负责“读取行为、产物和 Trace 并作确定性判定”，不修改 SUT 的决策。这样
同一个 SUT 可以在 V1/V2、多种故障注入和离线重放中重复评测，评测规则升级也不会
污染原始运行记录。`knowledge_base/` 目前没有填入学校真实资料，避免在方案审查
阶段混入未经批准的业务内容。

### 3. 测试用例组织与原始结果存储

#### 3.1 用例规范

用例采用 JSON Lines：一行一个不可变 `case_id`，可按文件或目录拆分，读取后统一
通过 `TestCaseSchema` 校验。每条用例包含：

1. `category`：四类固定值之一——基础指令与多轮、RAG 与跨会话记忆、工具调用与文件处理、异常输入与冲突恢复。
2. `pre_conditions`：记忆种子和故障注入声明；运行器先准备环境，再开始对话。
3. `conversation_turns`：带连续 `turn_index` 的消息序列，支持多轮和跨会话场景。
4. `expected_behavior`：面向业务的期望行为，供检查项映射，不替代确定性证据。
5. `check_criteria`：工具顺序、参数约束、期望规则 ID、真实 `.docx` 章节和平台成功状态。

`evaluator/schemas.py` 中的核心定义如下（完整定义已随工程提交）：

```python
class TestCaseSchema(StrictSchema):
    case_id: str
    category: Literal[
        "基础指令与多轮", "RAG与跨会话记忆",
        "工具调用与文件处理", "异常输入与冲突恢复",
    ]
    pre_conditions: PreconditionsSchema
    conversation_turns: list[ConversationTurnSchema]
    expected_behavior: list[str]
    check_criteria: CheckCriteriaSchema

class CheckCriteriaSchema(StrictSchema):
    required_tool_order: list[str]
    tool_parameter_constraints: dict[str, dict[str, Any]]
    expected_recall_rule_ids: list[str]
    docx_artifact_checks: DocxCheckCriteriaSchema
    platform_delivery_checks: PlatformDeliveryCheckSchema
```

随附的 `data/sample_test_case.jsonl` 覆盖“教案生成 + 安全规范 RAG + 发往教务处”
场景：预置教案章节偏好和学校名称，要求召回 `SAFETY-BIO-007`、`RULE-DOCX-002`，
规定 `memory.read → rag.search → document.generate_docx → document.inspect_docx →
academic_affairs.send → academic_affairs.verify_receipt` 的顺序，并检查真实文件和
平台回执。用例数据不包含 API Key。

#### 3.2 原始运行结果契约

每次运行都保存输入快照、模型配置、指标、白盒 Trace 和确定性结论。核心结构为：

```python
class RawEvalResultSchema(StrictSchema):
    run_id: str
    parent_run_id: str | None
    attempt_number: PositiveInt
    version_tag: Literal["V1", "V2"]
    input: dict[str, Any]
    output: dict[str, Any]
    model_configuration: ModelConfigSchema  # JSON 序列化键为 "model_config"
    execution_metrics: ExecutionMetricsSchema
    whitebox_trace: WhiteboxTraceSchema
    evaluation_result: EvaluationResultSchema
```

Pydantic v2 将 `model_config` 保留给模型自身配置，因此 Python 属性使用
`model_configuration`，通过 `validation_alias/serialization_alias` 接受和输出要求的
JSON 字段 `model_config`；样例 JSONL 仍直接展示 `"model_config"`，不改变对外契约。

`whitebox_trace` 的最小证据字段包括：

```json
{
  "memory_events": [{"event": "read", "memory_id": "...", "status": "success"}],
  "rag": {
    "actual_queries": ["实际发出的检索词"],
    "retrieved_chunks": [{"rule_id": "SAFETY-BIO-007", "score": 0.91}]
  },
  "tool_calls": [{
    "name": "document.generate_docx",
    "parameters": {"output_path": "runs/.../artifacts/lesson_plan.docx"},
    "return_status": "success"
  }],
  "generated_docx_paths": ["runs/.../artifacts/lesson_plan.docx"]
}
```

`execution_metrics` 强制保存 `duration_ms`、`prompt_tokens`、`completion_tokens`、
`total_tokens`、`estimated_cost_usd` 和成本公式；Schema 还校验总 Token 等于输入与输出
之和。这样报告可以重新按当时的计价配置计算，不把“耗时/费用未知”当成通过。

#### 3.3 五类故障的白盒归因

`evaluation_result.diagnosed_failure_stage` 采用固定标签，配合 Trace 按下表归因：

| 标签 | 关键证据 | 结论示例 |
| --- | --- | --- |
| `precondition_or_memory` | `memory_events` 缺少必需 read、状态为 `miss/error` 或种子未写入 | 记忆准备/读写链路失败，不把未注入的偏好归咎于模型。 |
| `rag_retrieval` | `rag.actual_queries` 不符合预期，或 `retrieved_chunks` 为空/缺少必需 rule ID | 规范没有被召回；即使最终答案缺规范，也先归因检索链路。 |
| `model_generation` | 记忆已读、规则已召回、工具调用前置证据均通过，但模型生成内容缺少被要求的结构/约束 | 才将问题归入生成阶段，并保留召回片段和最终消息作证据。 |
| `tool_call` | 工具顺序不符、参数约束不符，或 `return_status=validation_error/runtime_error` | 工具编排/适配失败，不笼统称为“模型幻觉”。 |
| `artifact_or_delivery` | `.docx` 不存在/无法解析/缺章节，或平台状态不在允许集合、无 `receipt_id` | 文件或外部交付失败；模型文本正确也不能掩盖交付失败。 |

因此，“RAG 未检索到”“工具参数错误”和“生成成功但交付失败”分别有可观察的
证据位置：前者看 `rag.retrieved_chunks`，中者看对应 `tool_calls.parameters` 与
`return_status`，后者看 `generated_docx_paths`、文档解析检查和平台回执。样例
`runs/sample_raw_result.jsonl` 正是第三种情况：两个规则已召回，工具参数及真实
`.docx` 检查通过，只有 `academic_affairs.verify_receipt` 返回 HTTP 502，因此
`diagnosed_failure_stage` 为 `artifact_or_delivery`，而不是 `model_generation`。

#### 3.4 失败重跑与只追加存储

每次尝试拥有唯一 `run_id`，在 `runs/<run_id>/` 建立独立目录；目录内保存该尝试的
原始 JSON、Trace 和 `artifacts/` 产物。失败重跑生成新的 `run_id`，并在新记录中：

- `parent_run_id` 指向失败的原始 `run_id`；
- `attempt_number` 从 1 递增；
- `version_tag` 和 `model_config` 保存本次实际配置，而不是引用当前配置文件；
- 向总索引 `runs/raw_results.jsonl` 以追加方式写入一整行，同时在新目录保存本次产物。

推荐的实际布局为：

```text
runs/
├── raw_results.jsonl                         # 只追加的全量索引
├── run_20261016T083000Z_ae01_lp_001_attempt_1/
│   ├── raw_result.json                       # 不可变单次快照
│   └── artifacts/lesson_plan.docx
└── run_20261016T083500Z_ae01_lp_001_attempt_2/
    ├── raw_result.json
    └── artifacts/lesson_plan.docx
```

运行器只允许使用 `Path` 创建目录和以追加模式写 JSONL；禁止用同名文件覆盖旧记录，
也禁止在重跑时清理上一次的 `artifacts/`。即使第二次成功，第一次失败的 Trace、
耗时、Token 和平台错误仍可独立审计。`parent_run_id + attempt_number` 使分析器能够
计算重跑收益、识别是配置修复还是偶然成功，并可按 `case_id/version_tag` 做横向比较。

### Stage 1 交付边界

本次提交已经创建目录、Pydantic v2 Schema、V1/V2 配置模板、示例用例、示例原始结果、
依赖清单和六个职责清晰的占位模块；没有提前实现 Agent 业务逻辑或评测执行逻辑。
考官审核通过后，下一阶段按本数据契约实现 DeepSeek 调用、RAG、工具适配、检查器和
追加式运行器，并先以 `pytest` 验证数据契约和失败重跑不覆盖原始记录。

