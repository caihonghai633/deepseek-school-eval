"""任务类型工作流的正反测试。

这些测试离线运行、使用临时运行目录，不依赖 DeepSeek API 或外部平台。断言同时
检查底层副作用工具是否真正执行，以及被状态机拦截时返回的结构化错误。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

from sut.agent import SchoolOfficeAgent


ROOT = Path(__file__).resolve().parents[1]


def _config() -> dict:
    config = yaml.safe_load((ROOT / "configs" / "v3_hardened.yaml").read_text(encoding="utf-8"))
    return copy.deepcopy(config)


def _case(
    workflow: str,
    *,
    send_authorized: bool | None = None,
    require_file: bool | None = None,
    text: str = "请生成《一次函数》七年级教案，课时45分钟。",
) -> dict:
    if require_file is None:
        require_file = workflow != "answer_only"
    platform_send = workflow == "generate_and_send"
    criteria = {
        "workflow": workflow,
        "send_authorized": send_authorized,
        "required_tool_order": (
            ["rag.search", "document.generate_docx", "document.inspect_docx", "academic_affairs.send", "academic_affairs.verify_receipt"]
            if platform_send
            else (["rag.search", "document.generate_docx", "document.inspect_docx"] if require_file else [])
        ),
        "tool_parameter_constraints": {},
        "expected_recall_rule_ids": [],
        "docx_artifact_checks": {
            "require_real_file": require_file,
            "required_extension": ".docx",
            "expected_chapter_titles": ["教学目标", "教学重难点", "安全措施与见习生安排", "教学过程", "课后反思"] if require_file else ["无需文件"],
            "require_non_empty_text": require_file,
        },
        "platform_delivery_checks": {
            "platform_name": "教学管理平台" if platform_send else "未投递（仅本地报告）",
            "expected_recipient": "教务处李老师" if platform_send else "无",
            "success_status_values": ["accepted", "delivered"] if platform_send else ["not_sent"],
            "require_receipt_id": platform_send,
        },
    }
    return {
        "case_id": f"TEST-{workflow}",
        "category": "基础指令与多轮",
        "pre_conditions": {"memory_seed": [], "fault_injection_config": {"enabled": False, "injections": []}},
        "conversation_turns": [{"turn_index": 1, "role": "user", "content": text}],
        "expected_behavior": ["按指定工作流执行"],
        "check_criteria": criteria,
    }


def _agent(tmp_path: Path) -> SchoolOfficeAgent:
    return SchoolOfficeAgent(config=_config(), root=ROOT, run_dir=tmp_path / "run", offline=True)


def _calls(agent: SchoolOfficeAgent) -> list[dict]:
    return agent.runtime.tool_calls


def test_answer_only_positive_has_no_document_or_delivery_side_effect(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    run = agent.run_case(_case("answer_only", require_file=False, text="请回答什么是形成性评价？"))
    names = [call["name"] for call in _calls(agent)]
    assert run.output_text
    assert not any(name in {"document.generate_docx", "document.inspect_docx", "academic_affairs.send", "academic_affairs.verify_receipt"} for name in names)


def test_answer_only_negative_tool_attempt_is_structured_and_blocked(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.prepare_workflow(_case("answer_only", require_file=False))
    result = agent.dispatch_tool(
        "generate_docx_plan",
        {"title": "不应生成", "grade": "七年级", "duration_mins": 45, "teaching_steps": []},
    )
    assert result["status"] == "validation_error"
    assert result["error"] == "workflow_guard"
    assert result["code"] == "tool_not_allowed"
    send_result = agent.dispatch_tool(
        "send_to_platform",
        {"file_path": "not-created.docx", "target_department": {"department_code": "JWC", "recipient": "教务处李老师"}, "title": "不应发送", "metadata": {}},
    )
    assert send_result["status"] == "validation_error"
    assert send_result["code"] == "send_not_allowed"
    assert not any(call["name"] == "document.generate_docx" and call["return_status"] == "success" for call in _calls(agent))
    assert not any(call["name"] == "academic_affairs.send" for call in _calls(agent))


def test_generate_only_positive_stops_after_verified_document(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.run_case(_case("generate_only"))
    calls = _calls(agent)
    names = [call["name"] for call in calls]
    assert "document.generate_docx" in names
    assert "document.inspect_docx" in names
    assert not any(name in {"academic_affairs.send", "academic_affairs.verify_receipt"} for name in names)
    assert all(call["return_status"] == "success" for call in calls if call["name"] in {"document.generate_docx", "document.inspect_docx"})


def test_generate_only_negative_send_is_blocked_after_inspection(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.run_case(_case("generate_only"))
    generated = next(path for path in agent.runtime.generated_docx_paths)
    result = agent.dispatch_tool(
        "send_to_platform",
        {"file_path": generated, "target_department": {"department_code": "JWC", "recipient": "教务处李老师"}, "title": "一次函数", "metadata": {}},
    )
    assert result["status"] == "validation_error"
    assert result["code"] == "send_not_allowed"
    assert not any(call["name"] == "academic_affairs.send" and call["return_status"] == "success" for call in _calls(agent))


def test_generate_and_send_positive_requires_verified_artifact_then_receipt(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.run_case(_case("generate_and_send", send_authorized=True))
    calls = _calls(agent)
    names = [call["name"] for call in calls]
    assert names.index("document.inspect_docx") < names.index("academic_affairs.send") < names.index("academic_affairs.verify_receipt")
    assert any(call["name"] == "academic_affairs.send" and call["return_status"] == "success" for call in calls)


def test_generate_and_send_negative_without_authorization_has_no_platform_side_effect(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.run_case(_case("generate_and_send", send_authorized=False))
    calls = _calls(agent)
    blocked = [call for call in calls if call["name"] == "send_to_platform"]
    assert blocked and json.loads(blocked[-1]["response_excerpt"])["code"] == "send_not_authorized"
    assert not any(call["name"] == "academic_affairs.send" and call["return_status"] == "success" for call in calls)


def test_failed_document_inspection_blocks_send_with_structured_error(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    case = _case("generate_and_send", send_authorized=True)
    agent.prepare_workflow(case)
    generated = agent.dispatch_tool(
        "generate_docx_plan",
        {"title": "一次函数", "grade": "七年级", "duration_mins": 45, "teaching_steps": [{"name": "导入"}]},
    )
    Path(generated["file_path"]).write_bytes(b"not a docx")
    inspected = agent.dispatch_tool("inspect_docx", {"file_path": generated["file_path"], "required_chapters": ["教学目标"]})
    assert inspected["status"] in {"runtime_error", "validation_error"}
    send_result = agent.dispatch_tool(
        "send_to_platform",
        {"file_path": generated["file_path"], "target_department": {"department_code": "JWC", "recipient": "教务处李老师"}, "title": "一次函数", "metadata": {"case_id": case["case_id"]}},
    )
    assert send_result["status"] == "validation_error"
    assert send_result["error"] == "workflow_guard"
    assert send_result["code"] == "artifact_not_verified"
    assert not any(call["name"] == "academic_affairs.send" for call in _calls(agent))
