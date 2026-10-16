"""按任务类型约束学校办公 Agent 的工具工作流。

本模块只负责工作流规划和状态闸门，不实现记忆、RAG、文档或平台业务。Agent
在调用工具前通过 :class:`WorkflowController` 做确定性校验，因此模型即使发出
越序或越权的 Function Calling，也不会直接触发生成、发送等副作用。没有显式
声明 ``check_criteria.workflow`` 的 Stage 1/2 用例会根据既有检查契约兼容推断，
避免改变历史数据的含义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


WorkflowType = Literal["answer_only", "generate_only", "generate_and_send"]


_DOCUMENT_TOOLS = {"generate_docx_plan", "inspect_docx"}
_DELIVERY_TOOLS = {"send_to_platform", "verify_receipt"}
_READ_TOOLS = {"read_memory", "write_memory", "search_rules"}


@dataclass(frozen=True)
class WorkflowPolicy:
    """一个任务类型允许、禁止和必须满足的前置条件。"""

    name: WorkflowType
    allowed_tools: frozenset[str]
    forbidden_tools: frozenset[str]
    requires_send_authorization: bool = False
    required_steps: tuple[str, ...] = ()

    @classmethod
    def for_type(cls, workflow: WorkflowType, *, authorized: bool) -> "WorkflowPolicy":
        if workflow == "answer_only":
            return cls(
                name=workflow,
                allowed_tools=frozenset(_READ_TOOLS),
                forbidden_tools=frozenset(_DOCUMENT_TOOLS | _DELIVERY_TOOLS),
                required_steps=(),
            )
        if workflow == "generate_only":
            return cls(
                name=workflow,
                allowed_tools=frozenset(_READ_TOOLS | _DOCUMENT_TOOLS),
                forbidden_tools=frozenset(_DELIVERY_TOOLS),
                required_steps=("generate_docx_plan", "inspect_docx"),
            )
        return cls(
            name="generate_and_send",
            allowed_tools=frozenset(_READ_TOOLS | _DOCUMENT_TOOLS | _DELIVERY_TOOLS),
            forbidden_tools=frozenset(),
            requires_send_authorization=not authorized,
            required_steps=("generate_docx_plan", "inspect_docx", "send_to_platform", "verify_receipt"),
        )


@dataclass
class WorkflowState:
    """一次 Agent 运行中已经完成的可验证状态。"""

    generated_paths: set[str] = field(default_factory=set)
    inspected_paths: set[str] = field(default_factory=set)
    last_inspection: dict[str, Any] | None = None
    send_accepted: bool = False
    last_receipt_id: str | None = None


class WorkflowController:
    """为每次用例执行生成策略，并在工具边界阻止非法状态迁移。"""

    def __init__(self, policy: WorkflowPolicy, *, root: str | Path, run_dir: str | Path) -> None:
        self.policy = policy
        self.root = Path(root)
        self.run_dir = Path(run_dir)
        self.state = WorkflowState()

    @classmethod
    def from_case(cls, case: dict[str, Any], *, root: str | Path, run_dir: str | Path) -> "WorkflowController":
        criteria = case.get("check_criteria", {})
        explicit = criteria.get("workflow") or criteria.get("workflow_type")
        if explicit not in {"answer_only", "generate_only", "generate_and_send"}:
            order = set(criteria.get("required_tool_order", []))
            artifact = criteria.get("docx_artifact_checks", {})
            platform = criteria.get("platform_delivery_checks", {})
            requires_delivery = bool(
                {"academic_affairs.send", "send_to_platform", "academic_affairs.verify_receipt", "verify_receipt"} & order
            ) or platform.get("require_receipt_id", False) or any(
                status not in {"not_sent"} for status in platform.get("success_status_values", [])
            )
            explicit = "generate_and_send" if requires_delivery else (
                "generate_only" if artifact.get("require_real_file", True) else "answer_only"
            )
        send_authorized = criteria.get("send_authorized")
        if send_authorized is None:
            # 历史用例用 required_tool_order/平台回执契约表达了发送意图；保留其语义。
            send_authorized = explicit == "generate_and_send"
        return cls(
            WorkflowPolicy.for_type(explicit, authorized=bool(send_authorized)),
            root=root,
            run_dir=run_dir,
        )

    @property
    def name(self) -> WorkflowType:
        return self.policy.name

    @staticmethod
    def _tool_path(arguments: dict[str, Any]) -> str | None:
        raw = arguments.get("file_path")
        if not raw:
            return None
        return Path(str(raw)).as_posix()

    def _known_path(self, raw: str) -> str:
        path = Path(raw)
        if not path.is_absolute():
            candidates = [self.root / path, self.run_dir / path]
            for candidate in candidates:
                if candidate.exists():
                    path = candidate
                    break
        try:
            return path.resolve().as_posix()
        except OSError:
            return path.absolute().as_posix()

    def _error(self, code: str, message: str) -> dict[str, Any]:
        return {
            "status": "validation_error",
            "error": "workflow_guard",
            "code": code,
            "workflow": self.policy.name,
            "message": message,
            "required_steps": list(self.policy.required_steps),
        }

    def before_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """校验工具是否处于当前任务允许的状态；返回结构化错误则阻止调用。"""

        if name in {"send_to_platform", "verify_receipt"} and self.policy.name != "generate_and_send":
            return self._error("send_not_allowed", f"工作流 {self.policy.name} 不允许平台投递")
        if name in self.policy.forbidden_tools or name not in self.policy.allowed_tools:
            return self._error("tool_not_allowed", f"工作流 {self.policy.name} 禁止调用 {name}")

        if name == "inspect_docx":
            raw = self._tool_path(arguments)
            if not raw:
                return self._error("document_not_generated", "inspect_docx 前必须提供已生成文件路径")
            path = self._known_path(raw)
            if path not in self.state.generated_paths:
                return self._error("document_not_generated", "inspect_docx 必须在 generate_docx_plan 成功后调用")

        if name == "send_to_platform":
            if self.policy.name != "generate_and_send":
                return self._error("send_not_allowed", f"工作流 {self.policy.name} 不允许平台投递")
            if self.policy.requires_send_authorization:
                return self._error("send_not_authorized", "平台投递缺少用户明确授权")
            raw = self._tool_path(arguments)
            if not raw:
                return self._error("artifact_not_verified", "发送前必须提供已校验的 docx 路径")
            path = self._known_path(raw)
            if path not in self.state.generated_paths:
                return self._error("artifact_not_generated", "发送文件不是本次工作流生成的文件")
            if path not in self.state.inspected_paths:
                return self._error("artifact_not_verified", "发送前必须完成并通过 inspect_docx")

        if name == "verify_receipt" and not self.state.send_accepted:
            return self._error("receipt_not_available", "verify_receipt 前必须获得 accepted 平台回执")
        return None

    def after_tool(self, name: str, arguments: dict[str, Any], result: dict[str, Any]) -> None:
        """将工具的成功结果折叠进状态；失败结果不会被误认为已完成。"""

        status = result.get("status") if isinstance(result, dict) else None
        if name == "generate_docx_plan" and status == "success":
            raw = result.get("file_path")
            if raw:
                self.state.generated_paths.add(self._known_path(str(raw)))
        elif name == "inspect_docx":
            raw = result.get("path") or arguments.get("file_path")
            if raw:
                known = self._known_path(str(raw))
                self.state.last_inspection = result
                if status == "success":
                    self.state.inspected_paths.add(known)
        elif name == "send_to_platform" and status == "accepted" and result.get("receipt_id"):
            self.state.send_accepted = True
            self.state.last_receipt_id = str(result["receipt_id"])

    def describe(self) -> dict[str, Any]:
        """返回可注入系统提示词和原始结果的策略快照。"""

        return {
            "workflow": self.policy.name,
            "allowed_tools": sorted(self.policy.allowed_tools),
            "forbidden_tools": sorted(self.policy.forbidden_tools),
            "required_steps": list(self.policy.required_steps),
            "send_authorization_required": self.policy.requires_send_authorization,
        }
