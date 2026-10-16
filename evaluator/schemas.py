"""
评测数据契约（Pydantic v2）。

本文件只定义输入用例和原始运行结果的稳定数据结构，不实现 Agent、RAG、工具
或评测逻辑。SUT 与 Evaluator 通过这些结构交换数据，从而保证不同版本配置、
失败重跑和离线复盘时仍能使用同一份可验证契约。

路径字段统一使用 POSIX 风格字符串；业务实现应在边界处用 pathlib.Path 解析，
不得拼接或硬编码 Windows 反斜杠路径。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    field_validator,
)


Category = Literal[
    "基础指令与多轮",
    "RAG与跨会话记忆",
    "工具调用与文件处理",
    "异常输入与冲突恢复",
]

# V3 是向后兼容的 Stage 4 加固配置；V1/V2 原始记录仍可正常回放。
VersionTag = Literal["V1", "V2", "V3"]
FailureStage = Literal[
    "none",
    "precondition_or_memory",
    "rag_retrieval",
    "model_generation",
    "tool_call",
    "artifact_or_delivery",
]


class StrictSchema(BaseModel):
    """所有公开 Schema 的共同配置：禁止未知字段，避免结果悄悄丢失。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class MemorySeedSchema(StrictSchema):
    """测试开始前写入的跨会话记忆种子。"""

    memory_id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    value: Any
    scope: Literal["user", "school", "session"] = "user"
    overwrite: bool = False


class FaultInjectionSchema(StrictSchema):
    """可重复的故障注入开关；第一阶段只声明契约，不执行注入。"""

    enabled: bool = False
    injections: list[dict[str, Any]] = Field(default_factory=list)


class PreconditionsSchema(StrictSchema):
    """用例运行前需要准备的记忆和故障环境。"""

    memory_seed: list[MemorySeedSchema] = Field(default_factory=list)
    fault_injection_config: FaultInjectionSchema = Field(
        default_factory=FaultInjectionSchema
    )


class ConversationTurnSchema(StrictSchema):
    """一轮对话输入或可观察的中间消息。"""

    turn_index: PositiveInt
    role: Literal["system", "user", "assistant", "tool"]
    content: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocxCheckCriteriaSchema(StrictSchema):
    """真实 Word 文档产物的确定性检查规则。"""

    require_real_file: bool = True
    required_extension: Literal[".docx"] = ".docx"
    expected_chapter_titles: list[str] = Field(min_length=1)
    require_non_empty_text: bool = True


class PlatformDeliveryCheckSchema(StrictSchema):
    """教务平台交付状态的确定性检查规则。"""

    platform_name: str = Field(min_length=1)
    expected_recipient: str = Field(min_length=1)
    success_status_values: list[str] = Field(min_length=1)
    require_receipt_id: bool = True


class CheckCriteriaSchema(StrictSchema):
    """不依赖模型主观判断的检查清单。"""

    # 按任务类型选择工作流，缺省时由 Agent 根据历史检查契约兼容推断。
    # answer_only：只回答；generate_only：生成并校验本地文档；
    # generate_and_send：校验通过且获授权后才允许平台投递。
    workflow: Literal["answer_only", "generate_only", "generate_and_send"] | None = None
    send_authorized: bool | None = None
    required_tool_order: list[str] = Field(default_factory=list)
    tool_parameter_constraints: dict[str, dict[str, Any]] = Field(
        default_factory=dict
    )
    expected_recall_rule_ids: list[str] = Field(default_factory=list)
    docx_artifact_checks: DocxCheckCriteriaSchema
    platform_delivery_checks: PlatformDeliveryCheckSchema


class TestCaseSchema(StrictSchema):
    """单条可复现测试用例，存储在 data/*.jsonl 中。"""

    case_id: str = Field(
        min_length=3,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]+$",
        description="全局唯一、不可复用的用例编号",
    )
    category: Category
    pre_conditions: PreconditionsSchema
    conversation_turns: list[ConversationTurnSchema] = Field(min_length=1)
    expected_behavior: list[str] = Field(min_length=1)
    check_criteria: CheckCriteriaSchema

    @field_validator("conversation_turns")
    @classmethod
    def turn_indices_are_contiguous(
        cls, turns: list[ConversationTurnSchema]
    ) -> list[ConversationTurnSchema]:
        """保证顺序缺口不会被误认为是 Agent 的多轮行为。"""

        indices = [turn.turn_index for turn in turns]
        if indices != list(range(1, len(indices) + 1)):
            raise ValueError("conversation_turns.turn_index 必须从 1 连续递增")
        return turns


class ModelConfigSchema(StrictSchema):
    """一次运行实际使用的模型和请求参数快照。"""

    provider: Literal["deepseek"] = "deepseek"
    base_url: str = "https://api.deepseek.com"
    model: Literal["deepseek-chat"] = "deepseek-chat"
    api_key_env: str = "DEEPSEEK_API_KEY"
    temperature: float = Field(ge=0, le=2)
    max_tokens: PositiveInt
    seed: int | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)


class ExecutionMetricsSchema(StrictSchema):
    """可审计的耗时、Token 和成本指标。"""

    duration_ms: float = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    cost_formula: str = Field(min_length=1)

    @field_validator("total_tokens")
    @classmethod
    def total_tokens_match_parts(cls, value: int, info: Any) -> int:
        """在字段可用时校验总 Token，防止成本报表基数错误。"""

        prompt = info.data.get("prompt_tokens")
        completion = info.data.get("completion_tokens")
        if prompt is not None and completion is not None and value != prompt + completion:
            raise ValueError("total_tokens 必须等于 prompt_tokens + completion_tokens")
        return value


class MemoryTraceEventSchema(StrictSchema):
    """白盒记忆读写事件。"""

    event: Literal["read", "write"]
    memory_id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    value_digest: str | None = None
    status: Literal["success", "miss", "error"]
    timestamp: datetime


class RetrievedChunkSchema(StrictSchema):
    """一次 RAG 召回的片段和排序分数。"""

    chunk_id: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    score: float
    rank: PositiveInt
    retrieval_source: Literal["vector", "bm25", "hybrid"]


class RagTraceSchema(StrictSchema):
    """实际发出的检索词、策略和召回证据。"""

    actual_queries: list[str] = Field(min_length=1)
    strategy: Literal["vector", "bm25", "hybrid"]
    top_k: PositiveInt
    retrieved_chunks: list[RetrievedChunkSchema] = Field(default_factory=list)


class ToolCallTraceSchema(StrictSchema):
    """工具调用名称、参数和返回状态的白盒记录。"""

    call_index: PositiveInt
    name: str = Field(min_length=1)
    parameters: dict[str, Any]
    return_status: Literal["success", "validation_error", "runtime_error"]
    response_excerpt: str | None = None
    timestamp: datetime


class WhiteboxTraceSchema(StrictSchema):
    """贯穿记忆、RAG、工具和文件产物的可观测链路。"""

    memory_events: list[MemoryTraceEventSchema] = Field(default_factory=list)
    rag: RagTraceSchema
    tool_calls: list[ToolCallTraceSchema] = Field(default_factory=list)
    generated_docx_paths: list[str] = Field(default_factory=list)


class DeterministicCheckSchema(StrictSchema):
    """单个确定性断言的结果。"""

    check_name: str = Field(min_length=1)
    passed: bool
    expected: Any = None
    observed: Any = None
    evidence: str | None = None


class EvaluationResultSchema(StrictSchema):
    """可重放的分项检查和故障归因结果。"""

    deterministic_checks: list[DeterministicCheckSchema] = Field(min_length=1)
    diagnosed_failure_stage: FailureStage
    failure_reasons: list[str] = Field(default_factory=list)


class RawEvalResultSchema(StrictSchema):
    """单次运行的不可变原始记录，存储在 runs/*.jsonl。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        serialize_by_alias=True,
    )

    run_id: str = Field(min_length=1)
    parent_run_id: str | None = None
    attempt_number: PositiveInt
    version_tag: VersionTag
    input: dict[str, Any] = Field(
        description="实际送入 SUT 的用例、对话和配置快照"
    )
    output: dict[str, Any] = Field(
        description="SUT 对外输出和交付摘要的原始快照"
    )

    # Pydantic v2 将 ``model_config`` 保留给模型配置本身，不能直接作为 Python
    # 字段名。因此内部字段叫 model_configuration，输入和输出均使用要求中的
    # JSON 键 ``model_config``，从而不牺牲数据契约的字段名称。
    model_configuration: ModelConfigSchema = Field(
        validation_alias=AliasChoices("model_config", "model_configuration"),
        serialization_alias="model_config",
    )
    execution_metrics: ExecutionMetricsSchema
    whitebox_trace: WhiteboxTraceSchema
    evaluation_result: EvaluationResultSchema

