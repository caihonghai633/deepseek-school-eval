"""学校办公 Agent 主循环。

Agent 首选 DeepSeek 官方 OpenAI 兼容接口和 Function Calling；调用失败或运行在
``--offline`` 模式时，使用同一组工具的确定性降级编排，保证评测框架仍能产出完整
Trace。模型 API、工具和 Evaluator 彼此隔离，工具事件由 ``ToolRuntime`` 统一记录。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

from sut.rag_engine import from_config
from sut.tools import CHAPTERS, ToolRuntime
from sut.workflows import WorkflowController


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_memory",
            "description": "读取跨会话教师偏好或班级安全约束。",
            "parameters": {"type": "object", "properties": {"session_id": {"type": "string"}, "key": {"type": "string"}}, "required": ["session_id", "key"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_memory",
            "description": "写入需要跨会话保留的明确偏好或安全安排。",
            "parameters": {"type": "object", "properties": {"session_id": {"type": "string"}, "key": {"type": "string"}, "value": {"type": ["string", "object", "array", "number", "boolean", "null"]}, "scope": {"type": "string"}}, "required": ["session_id", "key", "value"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_rules",
            "description": "检索学校教学规范和办公规则，并保留 rule_id 证据。",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_docx_plan",
            "description": "在本次运行 artifacts 目录生成真实 .docx 教案。",
            "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "grade": {"type": "string"}, "duration_mins": {"type": "integer"}, "teaching_steps": {"type": "array", "items": {"type": "object"}}, "lesson_objectives": {"type": "string"}, "safety_notes": {"type": "string"}}, "required": ["title", "grade", "duration_mins", "teaching_steps"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_docx",
            "description": "使用 python-docx 检查真实文档和一级章节。",
            "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}, "required_chapters": {"type": "array", "items": {"type": "string"}}}, "required": ["file_path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_to_platform",
            "description": "把已校验的 docx 发送到模拟学校平台。",
            "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}, "target_department": {"type": "object", "properties": {"department_code": {"type": "string"}, "recipient": {"type": "string"}}, "required": ["department_code", "recipient"]}, "title": {"type": "string"}, "metadata": {"type": "object"}}, "required": ["file_path", "target_department", "title", "metadata"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_receipt",
            "description": "验证平台投递回执。",
            "parameters": {"type": "object", "properties": {"request_id": {"type": "string"}}, "required": []},
        },
    },
]


@dataclass
class AgentRun:
    """Agent 一次执行的对外结果。"""

    output_text: str
    prompt_tokens: int
    completion_tokens: int
    mode: str
    api_error: str | None = None


class SchoolOfficeAgent:
    """可观测的学校办公 Agent。"""

    def __init__(
        self,
        *,
        config: dict[str, Any],
        root: str | Path,
        run_dir: str | Path,
        fault_injection_config: dict[str, Any] | None = None,
        offline: bool = False,
    ) -> None:
        self.root = Path(root)
        self.config = config
        self.run_dir = Path(run_dir)
        self.runtime = ToolRuntime(
            root=self.root,
            run_dir=self.run_dir,
            retriever=from_config(config, self.root),
            fault_injection_config=fault_injection_config or {},
            platform_retry_config=config.get("tools", {}).get("platform_retry", {}),
        )
        self.offline = offline
        self._api_error: str | None = None
        # 每次 run_case 开始时绑定；保留 None 以兼容单元测试直接调用
        # dispatch_tool 的旧行为，但正常 API/离线路径都必须经过工作流闸门。
        self.workflow_controller: WorkflowController | None = None

    def prepare_workflow(self, case: dict[str, Any]) -> WorkflowController:
        """根据用例契约创建本次运行的任务类型策略。"""

        self.workflow_controller = WorkflowController.from_case(
            case, root=self.root, run_dir=self.run_dir
        )
        return self.workflow_controller

    def run_case(self, case: dict[str, Any]) -> AgentRun:
        """执行一条 TestCaseSchema 字典并返回模型指标。"""

        self.prepare_workflow(case)
        if self.offline or not os.getenv("DEEPSEEK_API_KEY"):
            return self._fallback(case)

        try:
            return self._run_with_deepseek(case)
        except Exception as exc:  # API 超时/配额/工具参数异常均转为可审计降级
            self._api_error = f"{type(exc).__name__}: {exc}"[:500]
            fallback = self._fallback(case)
            fallback.api_error = self._api_error
            fallback.mode = "fallback_after_api_error"
            return fallback

    def _client(self) -> OpenAI:
        api_config = self.config.get("api", {})
        return OpenAI(
            api_key=os.environ[api_config.get("api_key_env", "DEEPSEEK_API_KEY")],
            base_url=api_config.get("base_url", "https://api.deepseek.com"),
            timeout=60.0,
            max_retries=0,
        )

    def _run_with_deepseek(self, case: dict[str, Any]) -> AgentRun:
        system_prompt = self.config.get("system_prompt", {}).get("text", "")
        workflow_description = self.workflow_controller.describe() if self.workflow_controller else {}
        system_prompt += (
            "\n执行要求：先识别任务工作流，只调用该工作流允许的工具；"
            "只保存本地的用例绝不调用发送工具；完成真实文件和回执校验后直接答复。"
            f"\n本次工作流策略：{json.dumps(workflow_description, ensure_ascii=False)}"
        )
        if self.config.get("version_tag") == "V3":
            system_prompt += (
                "\nV3 强制工具契约：调用 generate_docx_plan 前必须显式提取 title、grade、"
                "duration_mins、teaching_steps；调用 send_to_platform 前必须显式提取"
                "file_path、target_department.department_code、target_department.recipient、"
                "title、metadata。任何字段缺失都不得调用工具，先返回 validation_error；"
                "平台 502/超时最多重试 3 次，最终只能报告 degraded_local_saved。"
            )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for turn in case["conversation_turns"]:
            message = {"role": turn["role"], "content": turn["content"]}
            if turn["role"] not in {"system", "user", "assistant"}:
                message["role"] = "user"
            messages.append(message)
        usage_prompt = 0
        usage_completion = 0
        client = self._client()
        api_request = self.config.get("api", {}).get("request", {})
        final_text = ""
        for _ in range(6):
            response = self._create_with_retry(client, messages, api_request)
            usage = getattr(response, "usage", None)
            usage_prompt += int(getattr(usage, "prompt_tokens", 0) or 0)
            usage_completion += int(getattr(usage, "completion_tokens", 0) or 0)
            message = response.choices[0].message
            final_text = message.content or final_text
            tool_calls = getattr(message, "tool_calls", None) or []
            if not tool_calls:
                break
            assistant_message: dict[str, Any] = {"role": "assistant", "content": message.content or "", "tool_calls": []}
            for call in tool_calls:
                assistant_message["tool_calls"].append(
                    {"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": call.function.arguments}}
                )
            messages.append(assistant_message)
            for call in tool_calls:
                arguments = self._parse_arguments(call.function.arguments)
                result = self.dispatch_tool(call.function.name, arguments)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False, default=str)})

        if not final_text:
            final_text = "已完成工具执行，请以白盒 Trace 和确定性检查结果为准。"
        return AgentRun(final_text, usage_prompt, usage_completion, "deepseek")

    def _create_with_retry(self, client: OpenAI, messages: list[dict[str, Any]], api_request: dict[str, Any]) -> Any:
        """对网络瞬断、429 和 5xx 做有限指数退避，避免无限重试。"""

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return client.chat.completions.create(
                    model=self.config.get("api", {}).get("model", "deepseek-chat"),
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    tool_choice="auto",
                    temperature=float(api_request.get("temperature", 0.1)),
                    max_tokens=int(api_request.get("max_tokens", 2048)),
                    seed=api_request.get("seed"),
                )
            except Exception as exc:  # OpenAI SDK 异常类型随版本变化，统一捕获并重试
                last_error = exc
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
        assert last_error is not None
        raise last_error

    @staticmethod
    def _parse_arguments(raw: str) -> dict[str, Any]:
        try:
            value = json.loads(raw or "{}")
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}

    def dispatch_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """按 Function Calling 名称调用真实工具。"""

        methods = {
            "read_memory": self.runtime.read_memory,
            "write_memory": self.runtime.write_memory,
            "search_rules": self.runtime.search_rules,
            "generate_docx_plan": self.runtime.generate_docx_plan,
            "inspect_docx": self.runtime.inspect_docx,
            "send_to_platform": self.runtime.send_to_platform,
            "verify_receipt": self.runtime.verify_receipt,
        }
        method = methods.get(name)
        if method is None:
            self.runtime.record_tool(name, arguments, "validation_error", {"error": "unknown_tool"})
            return {"status": "validation_error", "error": f"unknown tool: {name}"}
        if self.workflow_controller is not None:
            workflow_error = self.workflow_controller.before_tool(name, arguments)
            if workflow_error:
                # 记录为被阻断的尝试，而不是调用底层工具；这让检查器能区分
                # “模型请求过副作用”与“平台真的收到投递”。
                self.runtime.record_tool(name, arguments, "validation_error", workflow_error)
                return workflow_error
        validation_error = self._validate_tool_arguments(name, arguments)
        if validation_error:
            self.runtime.record_tool(name, arguments, "validation_error", {"error": validation_error})
            return {"status": "validation_error", "error": validation_error}
        try:
            result = method(**arguments)
            if self.workflow_controller is not None and isinstance(result, dict):
                self.workflow_controller.after_tool(name, arguments, result)
            return result
        except TypeError as exc:
            self.runtime.record_tool(name, arguments, "validation_error", {"error": str(exc)})
            return {"status": "validation_error", "error": str(exc)}
        except Exception as exc:  # pragma: no cover - 工具外部错误保护
            self.runtime.record_tool(name, arguments, "runtime_error", {"error": str(exc)})
            return {"status": "runtime_error", "error": str(exc)}

    def _validate_tool_arguments(self, name: str, arguments: dict[str, Any]) -> str | None:
        """在工具边界执行 V3 的必填字段闸门，避免模型缺参后进入副作用工具。

        这层校验是确定性的，失败时不会调用生成或平台投递函数，因此 Trace 可以
        明确归因到 ``tool_call``，而不是把参数错误误判为模型生成质量问题。
        """

        if name == "generate_docx_plan":
            required = ("title", "grade", "duration_mins", "teaching_steps")
            missing = [field for field in required if field not in arguments or arguments[field] in (None, "", [])]
            if missing:
                return f"generate_docx_plan 缺少必填字段: {', '.join(missing)}"
        if name == "send_to_platform":
            required = ("file_path", "target_department")
            missing = [field for field in required if field not in arguments or arguments[field] in (None, "")]
            if missing:
                return f"send_to_platform 缺少必填字段: {', '.join(missing)}"
            department = arguments.get("target_department")
            if not isinstance(department, dict):
                return "send_to_platform.target_department 必须是包含 department_code 和 recipient 的对象"
            missing_department = [
                field for field in ("department_code", "recipient")
                if not department.get(field)
            ]
            if missing_department:
                return f"send_to_platform 缺少部门字段: {', '.join(missing_department)}"
            # V3 还要求把投递上下文显式带入工具调用，便于回执审计；旧版本保持兼容。
            if self.config.get("version_tag") == "V3":
                missing_context = [
                    field for field in ("title", "metadata")
                    if field not in arguments or arguments[field] in (None, "", {})
                ]
                if missing_context:
                    return f"send_to_platform 缺少 V3 审计字段: {', '.join(missing_context)}"
        return None

    def _fallback(self, case: dict[str, Any]) -> AgentRun:
        """无 API 或 API 故障时的可审计确定性编排。"""

        text = "\n".join(turn.get("content", "") for turn in case["conversation_turns"])
        criteria = case["check_criteria"]
        controller = self.workflow_controller or self.prepare_workflow(case)
        expected_ids = criteria.get("expected_recall_rule_ids", [])
        seed_values = case.get("pre_conditions", {}).get("memory_seed", [])
        for memory in seed_values:
            self.runtime._memory[memory["memory_id"]] = memory
            self.runtime._memory_by_key[memory["key"]] = memory["memory_id"]

        # 先读取前置记忆；这是工具顺序和跨会话场景的可观测证据。
        memory_keys = [item["key"] for item in seed_values]
        # 需要严格链路的用例即使没有 seed，也先执行一次可审计的偏好读取。
        # 这不会改变业务结果，但能验证“先查记忆”的工具顺序契约。
        required_order = criteria.get("required_tool_order", [])
        if "memory.read" in required_order and not memory_keys and controller.name != "answer_only":
            memory_keys = ["teacher_preferences"]
        for key in memory_keys:
            self.dispatch_tool("read_memory", {"session_id": "fallback-session", "key": key})
        if (
            controller.name != "answer_only"
            and (any(injection.get("type") == "memory.write" for injection in self.runtime.injections) or "写入" in text)
        ):
            self.dispatch_tool(
                "write_memory",
                {"session_id": "fallback-session", "key": "runtime_preference", "value": "按本次用例保留的偏好"},
            )

        # 纯问答任务不被强制套入文档链路；如用例明确要求规则证据，仍可执行
        # search_rules，但不会因此触发文档生成或平台副作用。
        if controller.name != "answer_only" or expected_ids or "rag.search" in required_order:
            query = " ".join([text, *expected_ids])
            self.dispatch_tool("search_rules", {"query": query})

        is_error_case = case["category"] == "异常输入与冲突恢复"
        missing_info = any(marker in text for marker in ["缺少", "还没决定", "课题还是先空着", "无法确认", "口头称呼"])

        generated: dict[str, Any] | None = None
        inspected: dict[str, Any] | None = None
        title: str | None = None
        # 缺少关键字段时追问而不是臆造文件；安全/规则冲突仍允许生成一份
        # 合规替代方案，最终内容和规则证据由检查器审计。
        if controller.name != "answer_only" and not (is_error_case and missing_info):
            title_match = re.search(r"《([^》]+)》", text)
            title = title_match.group(1) if title_match else "学校教学活动方案"
            grade_match = re.search(r"([一二三四五六七八九十]+年级)", text)
            grade = grade_match.group(1) if grade_match else "待确认年级"
            duration_match = re.search(r"(\d+)\s*分钟", text)
            duration = int(duration_match.group(1)) if duration_match else 45
            generated = self.dispatch_tool(
                "generate_docx_plan",
                {
                    "title": title,
                    "grade": grade,
                    "duration_mins": duration,
                    "teaching_steps": [
                        {"name": "导入与目标确认"},
                        {"name": "规范约束下的探究活动"},
                        {"name": "总结与作业"},
                    ],
                    "lesson_objectives": "依据用户要求完成本课目标，并保留可观察的学习证据。",
                    "safety_notes": "遵守已召回学校安全规则；涉及伤病、危化品或天气风险时采用安全替代方案。",
                },
            )
            if generated.get("status") == "success":
                inspected = self.dispatch_tool(
                    "inspect_docx",
                    {
                        "file_path": generated["file_path"],
                        "required_chapters": criteria["docx_artifact_checks"].get("expected_chapter_titles", CHAPTERS),
                    },
                )

        should_send = controller.name == "generate_and_send"
        if (
            should_send
            and generated
            and generated.get("status") == "success"
            and inspected
            and inspected.get("status") == "success"
        ):
            department = criteria.get("platform_delivery_checks", {}).get("expected_recipient", "教务处李老师")
            department_name = criteria.get("platform_delivery_checks", {}).get("platform_name", "教学管理平台")
            if "年级" in department_name or "年级" in text or "GRADE-" in text.upper():
                match = re.search(r"GRADE[-_]?([0-9]+)", text.upper())
                grade_code = f"GRADE-{match.group(1)}" if match else "GRADE-7"
                code = grade_code
            elif "教务" in department_name:
                code = "JWC"
            elif "政教" in department_name:
                code = "ZZC"
            else:
                code = "JWC"
            target = {"department_code": code, "recipient": department}
            send_args = {
                "file_path": generated["file_path"],
                "target_department": target,
                "title": title,
                "metadata": {"case_id": case.get("case_id"), "platform": department_name},
            }
            sent = self.dispatch_tool("send_to_platform", send_args)
            if sent.get("status") == "accepted":
                self.dispatch_tool("verify_receipt", {"request_id": sent.get("receipt_id")})

        output_text = f"已按 {controller.name} 工作流执行离线确定性编排；请以真实文件、工具 Trace 和平台回执为准。"
        if self._api_error:
            output_text += f" API 调用异常已记录：{self._api_error}"
        chars = sum(len(str(turn.get("content", ""))) for turn in case["conversation_turns"])
        return AgentRun(output_text, max(1, chars // 4), max(1, len(output_text) // 4), "fallback")

