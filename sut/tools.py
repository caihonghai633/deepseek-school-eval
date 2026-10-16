"""学校办公 Agent 的可观测工具集。

工具在真实文件系统中读写记忆和 ``.docx``，并将每次调用写入运行时 Trace。平台
投递是本地模拟适配器：通过用例的 fault injection 配置可稳定复现参数错误、502
和超时。工具层不做模型质量判断，只返回结构化结果和确定性状态。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from docx import Document

from sut.rag_engine import KnowledgeBaseRetriever


CHAPTERS = [
    "教学目标",
    "教学重难点",
    "安全措施与见习生安排",
    "教学过程",
    "课后反思",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def value_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def safe_filename(value: str) -> str:
    """保留中文、数字、下划线和连字符，避免路径穿越。"""

    cleaned = re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", str(value), flags=re.UNICODE)
    return cleaned.strip("._") or "lesson_plan"


@dataclass
class ToolRuntime:
    """单次运行的工具依赖和白盒事件收集器。"""

    root: Path
    run_dir: Path
    retriever: KnowledgeBaseRetriever
    fault_injection_config: dict[str, Any] = field(default_factory=dict)
    platform_retry_config: dict[str, Any] = field(default_factory=dict)
    memory_events: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    generated_docx_paths: list[str] = field(default_factory=list)
    rag_trace: dict[str, Any] | None = None
    _call_index: int = 0
    _memory: dict[str, dict[str, Any]] = field(default_factory=dict)
    _memory_by_key: dict[str, str] = field(default_factory=dict)
    _platform_attempts: int = 0

    def __post_init__(self) -> None:
        self.artifacts_dir = self.run_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._load_seed_memories()

    @property
    def injections(self) -> list[dict[str, Any]]:
        return list(self.fault_injection_config.get("injections", []))

    def _find_injection(self, injection_type: str, **criteria: Any) -> dict[str, Any] | None:
        for injection in self.injections:
            if injection.get("type") != injection_type:
                continue
            if all(key not in injection or injection.get(key) == value for key, value in criteria.items() if value is not None):
                return injection
        return None

    def _load_seed_memories(self) -> None:
        seed_path = self.root / "data" / "knowledge_base" / "seed_memories.json"
        if not seed_path.exists():
            return
        payload = json.loads(seed_path.read_text(encoding="utf-8"))
        for memory in payload.get("memories", []):
            memory_id = str(memory["memory_id"])
            self._memory[memory_id] = dict(memory)
            self._memory_by_key[str(memory["key"])] = memory_id

    def _persist_memory_snapshot(self) -> None:
        snapshot = {
            "memories": list(self._memory.values()),
            "updated_at": utc_now().isoformat(),
        }
        (self.artifacts_dir / "memory_snapshot.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def record_memory_event(
        self,
        *,
        event: str,
        memory_id: str,
        key: str,
        status: str,
        value: Any = None,
    ) -> None:
        self.memory_events.append(
            {
                "event": event,
                "memory_id": memory_id,
                "key": key,
                "value_digest": value_digest(value) if value is not None else None,
                "status": status,
                "timestamp": utc_now(),
            }
        )

    def record_tool(
        self,
        name: str,
        parameters: dict[str, Any],
        status: str,
        response: Any = None,
    ) -> None:
        self._call_index += 1
        excerpt = None
        if response is not None:
            excerpt = json.dumps(response, ensure_ascii=False, default=str)[:1000]
        self.tool_calls.append(
            {
                "call_index": self._call_index,
                "name": name,
                "parameters": parameters,
                "return_status": status,
                "response_excerpt": excerpt,
                "timestamp": utc_now(),
            }
        )

    def search_rules(self, query: str) -> dict[str, Any]:
        result = self.retriever.search(query)
        if self.rag_trace is None:
            self.rag_trace = {
                "actual_queries": [],
                "strategy": result["strategy"],
                "top_k": result["top_k"],
                "retrieved_chunks": [],
            }
        self.rag_trace["actual_queries"].extend(result["actual_queries"])
        self.rag_trace["retrieved_chunks"].extend(result["retrieved_chunks"])
        self.record_tool("rag.search", {"query": query, "top_k": result["top_k"]}, "success", result)
        return result

    def read_memory(self, session_id: str, key: str) -> dict[str, Any]:
        injection = self._find_injection("memory.read", key=key)
        memory_id = self._memory_by_key.get(key, f"memory-missing-{key}")
        if injection and injection.get("outcome") in {"miss", "error"}:
            status = str(injection["outcome"])
            self.record_memory_event(event="read", memory_id=memory_id, key=key, status=status)
            tool_status = "runtime_error" if status == "error" else "success"
            self.record_tool("memory.read", {"session_id": session_id, "key": key}, tool_status, {"status": status})
            return {"status": status, "key": key, "memory_id": memory_id, "value": None}

        memory_id = self._memory_by_key.get(key)
        if not memory_id:
            self.record_memory_event(event="read", memory_id=f"memory-missing-{key}", key=key, status="miss")
            self.record_tool("memory.read", {"session_id": session_id, "key": key}, "success", {"status": "miss"})
            return {"status": "miss", "key": key, "memory_id": None, "value": None}

        memory = self._memory[memory_id]
        self.record_memory_event(event="read", memory_id=memory_id, key=key, status="success", value=memory.get("value"))
        response = {"status": "success", "memory_id": memory_id, "key": key, "value": memory.get("value")}
        self.record_tool("memory.read", {"session_id": session_id, "key": key}, "success", response)
        return response

    def write_memory(self, session_id: str, key: str, value: Any, scope: str = "user") -> dict[str, Any]:
        injection = self._find_injection("memory.write", key=key)
        memory_id = (injection or {}).get("memory_id", f"mem-runtime-{safe_filename(key)}")
        if injection and injection.get("outcome") == "error":
            response = {"status": "error", "error": injection.get("error", "memory_write_failed")}
            self.record_memory_event(event="write", memory_id=memory_id, key=key, status="error", value=value)
            self.record_tool("memory.write", {"session_id": session_id, "key": key, "scope": scope}, "runtime_error", response)
            return response

        memory = {
            "memory_id": memory_id,
            "key": key,
            "value": value,
            "scope": scope,
            "source": "runtime_write",
        }
        self._memory[memory_id] = memory
        self._memory_by_key[key] = memory_id
        self._persist_memory_snapshot()
        response = {"status": "success", "memory_id": memory_id, "key": key}
        self.record_memory_event(event="write", memory_id=memory_id, key=key, status="success", value=value)
        self.record_tool("memory.write", {"session_id": session_id, "key": key, "scope": scope}, "success", response)
        return response

    def generate_docx_plan(
        self,
        title: str,
        grade: str,
        duration_mins: int,
        teaching_steps: Any,
        lesson_objectives: str = "完成本课核心知识理解并能在练习中应用。",
        safety_notes: str = "按学校安全规范组织活动，课前说明风险并保留必要的见习安排。",
    ) -> dict[str, Any]:
        parameters = {
            "title": title,
            "grade": grade,
            "duration_mins": duration_mins,
            "teaching_steps": teaching_steps,
            "lesson_objectives": lesson_objectives,
        }
        if not isinstance(duration_mins, int) or duration_mins <= 0:
            response = {"status": "validation_error", "error": "duration_mins must be positive"}
            self.record_tool("document.generate_docx", parameters, "validation_error", response)
            return response

        filename = f"{safe_filename(grade)}_{safe_filename(title)}.docx"
        path = self.artifacts_dir / filename
        document = Document()
        document.add_heading(title, level=0)
        document.add_paragraph(f"年级：{grade}    课时时长：{duration_mins}分钟")
        sections = {
            "教学目标": lesson_objectives,
            "教学重难点": "重点：围绕课题形成可观察的学习成果。难点：将知识转化为课堂任务和证据。",
            "安全措施与见习生安排": safety_notes,
            "教学过程": self._steps_text(teaching_steps),
            "课后反思": "记录目标达成证据、学生困难和下一课的具体改进动作。",
        }
        for heading in CHAPTERS:
            document.add_heading(heading, level=1)
            document.add_paragraph(str(sections[heading]))
        document.save(path)
        path_string = path.as_posix()
        self.generated_docx_paths.append(path_string)
        response = {"status": "success", "file_path": path_string, "chapters": CHAPTERS}
        self.record_tool("document.generate_docx", parameters | {"output_path": path_string}, "success", response)
        return response

    @staticmethod
    def _steps_text(teaching_steps: Any) -> str:
        if isinstance(teaching_steps, list):
            return "\n".join(
                f"{index}. {step.get('name', step) if isinstance(step, dict) else step}"
                for index, step in enumerate(teaching_steps, start=1)
            )
        if isinstance(teaching_steps, dict):
            return "\n".join(f"{key}：{value}" for key, value in teaching_steps.items())
        return str(teaching_steps)

    def inspect_docx(self, file_path: str, required_chapters: list[str] | None = None) -> dict[str, Any]:
        parameters = {"path": file_path, "required_chapters": required_chapters or CHAPTERS}
        path = Path(file_path)
        if not path.is_absolute():
            candidate = self.root / path
            if candidate.exists():
                path = candidate
            else:
                path = self.run_dir / path
        if not path.exists() or path.suffix.lower() != ".docx":
            response = {"status": "validation_error", "exists": path.exists(), "error": "real .docx not found"}
            self.record_tool("document.inspect_docx", parameters, "validation_error", response)
            return response
        try:
            document = Document(path)
            paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
            headings = [paragraph.text for paragraph in document.paragraphs if paragraph.style.name.startswith("Heading 1")]
            expected = required_chapters or CHAPTERS
            missing = [chapter for chapter in expected if chapter not in headings]
            response = {"status": "success" if not missing and paragraphs else "validation_error", "exists": True, "non_empty": bool(paragraphs), "headings": headings, "missing_chapters": missing, "path": path.as_posix()}
            status = "success" if response["status"] == "success" else "validation_error"
            self.record_tool("document.inspect_docx", parameters, status, response)
            return response
        except Exception as exc:  # pragma: no cover - 损坏文档分支
            response = {"status": "runtime_error", "error": str(exc)}
            self.record_tool("document.inspect_docx", parameters, "runtime_error", response)
            return response

    def send_to_platform(
        self,
        file_path: str,
        target_department: Any,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """投递已校验文档；V3 对瞬时 502/超时执行有限重试并保留本地降级产物。"""

        parameters = {
            "file_path": file_path,
            "target_department": target_department,
            "title": title,
            "metadata": metadata or {},
        }
        path = Path(file_path)
        if not path.is_absolute():
            candidate = self.root / path
            path = candidate if candidate.exists() else self.run_dir / path
        department = self._normalise_department(target_department)
        if not path.exists() or path.suffix.lower() != ".docx":
            response = {"status": "validation_error", "error": "existing .docx file_path required"}
            self.record_tool("academic_affairs.send", parameters, "validation_error", response)
            return response
        if department is None:
            response = {"status": "validation_error", "error": "department_code and recipient are required"}
            self.record_tool("academic_affairs.send", parameters, "validation_error", response)
            return response

        retry_enabled = bool(self.platform_retry_config.get("enabled", False))
        max_retries = max(1, int(self.platform_retry_config.get("max_retries", 3))) if retry_enabled else 1
        injection = self._find_injection("platform.send")
        for attempt in range(1, max_retries + 1):
            self._platform_attempts += 1
            transient_failure = False
            if injection and injection.get("outcome") == "http_error":
                failures_before_success = injection.get("failures_before_success")
                if failures_before_success is None:
                    # V3 将未标记为 permanent 的模拟 502 视作瞬时网关故障。
                    failures_before_success = 1 if retry_enabled and not injection.get("permanent", False) else max_retries
                transient_failure = attempt <= int(failures_before_success)
                if transient_failure:
                    response = {"status": "http_error", "status_code": injection.get("status_code", 502), "receipt_id": None, "attempt": attempt}
                    self.record_tool("academic_affairs.send", parameters | department | {"attempt": attempt}, "runtime_error", response)
                    continue
            if injection and injection.get("outcome") == "timeout":
                failures_before_success = int(injection.get("failures_before_success", max_retries if injection.get("permanent", False) else 1))
                transient_failure = attempt <= failures_before_success
                if transient_failure:
                    response = {"status": "timeout", "receipt_id": None, "attempt": attempt}
                    self.record_tool("academic_affairs.send", parameters | department | {"attempt": attempt}, "runtime_error", response)
                    continue
            response = {"status": "accepted", "receipt_id": f"AAS-{self._call_index + 1:06d}", "recipient": department["recipient"], "department_code": department["department_code"], "attempt": attempt}
            self.record_tool("academic_affairs.send", parameters | department | {"attempt": attempt}, "success", response)
            return response

        # 重试耗尽：保留并强校验本地文件，明确降级而非伪造交付成功。
        fallback_path = self.artifacts_dir / "delivery_fallback.json"
        fallback_payload = {
            "status": "degraded_local_saved",
            "file_path": path.as_posix(),
            "reason": "platform_retry_exhausted",
            "max_retries": max_retries,
            "local_file_exists": path.exists(),
            "timestamp": utc_now().isoformat(),
        }
        fallback_path.write_text(json.dumps(fallback_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.record_tool("academic_affairs.send", parameters | department | {"fallback_path": fallback_path.as_posix()}, "runtime_error", fallback_payload)
        return fallback_payload

    def verify_receipt(self, request_id: str | None = None) -> dict[str, Any]:
        parameters = {"request_id": request_id}
        injection = self._find_injection("platform.verify_receipt")
        if injection and injection.get("outcome") == "timeout":
            response = {"status": "timeout", "receipt_id": None}
            self.record_tool("academic_affairs.verify_receipt", parameters, "runtime_error", response)
            return response
        response = {"status": "delivered", "receipt_id": request_id}
        self.record_tool("academic_affairs.verify_receipt", parameters, "success", response)
        return response

    @staticmethod
    def _normalise_department(value: Any) -> dict[str, str] | None:
        if isinstance(value, dict):
            code = value.get("department_code")
            recipient = value.get("recipient")
            if code and recipient:
                return {"department_code": str(code), "recipient": str(recipient)}
            return None
        text = str(value)
        mapping = {
            "教务处": ("JWC", "教务处李老师"),
            "政教处": ("ZZC", "政教处王老师"),
            "七年级组": ("GRADE-7", "七年级组长赵老师"),
        }
        for label, (code, recipient) in mapping.items():
            if label in text:
                return {"department_code": code, "recipient": recipient}
        return None

    def whitebox_trace(self) -> dict[str, Any]:
        rag = self.rag_trace or {
            "actual_queries": [""],
            "strategy": self.retriever.strategy,
            "top_k": self.retriever.top_k,
            "retrieved_chunks": [],
        }
        return {
            "memory_events": self.memory_events,
            "rag": rag,
            "tool_calls": self.tool_calls,
            "generated_docx_paths": self.generated_docx_paths,
        }

