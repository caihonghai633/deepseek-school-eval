"""确定性评测检查器。

检查器只读取 TestCaseSchema、Agent 白盒 Trace、真实文件和模拟平台返回，不调用
模型进行主观打分。输出字段可直接写入 ``EvaluationResultSchema``，并按优先级把
失败归因到记忆、RAG、模型生成、工具调用或文件/交付链路。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from docx import Document


CHECK_NAMES = [
    "tool_selection_passed",
    "tool_args_passed",
    "rag_recall_passed",
    "artifact_format_passed",
    "delivery_status_passed",
    "model_generation_passed",
]


def _check(name: str, passed: bool, expected: Any, observed: Any, evidence: str) -> dict[str, Any]:
    return {
        "check_name": name,
        "passed": bool(passed),
        "expected": expected,
        "observed": observed,
        "evidence": evidence,
    }


def _tool_calls(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return list(trace.get("tool_calls", []))


def check_tool_selection(criteria: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    expected = list(criteria.get("required_tool_order", []))
    observed = [call.get("name") for call in _tool_calls(trace)]
    if not expected:
        passed = True
    else:
        cursor = 0
        passed = True
        for name in expected:
            try:
                cursor = observed.index(name, cursor) + 1
            except ValueError:
                passed = False
                break
    return _check("tool_selection_passed", passed, expected, observed, "whitebox_trace.tool_calls")


def check_workflow_constraints(criteria: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    """验证显式任务工作流没有产生未授权的副作用调用。

    旧用例没有 ``workflow`` 字段时返回兼容通过；新用例则同时检查运行时工具
    名称和返回状态，避免把“被 Agent 请求但被闸门拦截”误报为真实交付成功。
    """

    workflow = criteria.get("workflow") or criteria.get("workflow_type")
    calls = _tool_calls(trace)
    observed = [str(call.get("name")) for call in calls]
    if not workflow:
        return _check("workflow_constraints_passed", True, "legacy_inference", observed, "未显式声明 workflow，保持历史契约")

    forbidden_by_workflow = {
        "answer_only": {"document.generate_docx", "document.inspect_docx", "academic_affairs.send", "academic_affairs.verify_receipt"},
        "generate_only": {"academic_affairs.send", "academic_affairs.verify_receipt"},
        "generate_and_send": set(),
    }
    forbidden = [
        name for call, name in zip(calls, observed)
        if name in forbidden_by_workflow.get(workflow, set()) and not _is_workflow_guard(call)
    ]
    blocked = [
        name for call, name in zip(calls, observed)
        if name in forbidden_by_workflow.get(workflow, set()) and _is_workflow_guard(call)
    ]
    reasons: list[str] = []
    if forbidden:
        reasons.append(f"工作流 {workflow} 禁止调用: {forbidden}")

    inspect_success = any(
        call.get("name") == "document.inspect_docx" and call.get("return_status") == "success"
        for call in calls
    )
    successful_sends = [
        call for call in calls
        if call.get("name") == "academic_affairs.send" and call.get("return_status") == "success"
    ]
    if workflow == "generate_and_send":
        if criteria.get("send_authorized") is False and successful_sends:
            reasons.append("send_authorized=false 时不应有成功投递")
        if successful_sends and not inspect_success:
            reasons.append("文档检查未成功即发生平台投递")

    passed = not reasons
    return _check(
        "workflow_constraints_passed",
        passed,
        {"workflow": workflow, "forbidden_tools": sorted(forbidden_by_workflow.get(workflow, set()))},
        {"observed_tools": observed, "blocked_tools": blocked, "successful_send_count": len(successful_sends), "reasons": reasons},
        "whitebox_trace.tool_calls + workflow policy",
    )


def _value_matches(observed: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        if not isinstance(observed, dict):
            return False
        return all(_value_matches(observed.get(key), value) for key, value in expected.items())
    if isinstance(expected, list):
        return all(item in (observed or []) for item in expected)
    return observed == expected


def check_tool_args(criteria: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    constraints = criteria.get("tool_parameter_constraints", {})
    calls = _tool_calls(trace)
    failures: list[str] = []
    observed: dict[str, Any] = {}
    for tool_name, expected_constraints in constraints.items():
        matching = [call for call in calls if call.get("name") == tool_name]
        observed[tool_name] = [call.get("parameters", {}) for call in matching]
        if not matching:
            failures.append(f"缺少工具调用 {tool_name}")
            continue
        parameters = matching[-1].get("parameters", {})
        for key, expected in expected_constraints.items():
            if key in {"extension", "required_extension"}:
                actual_paths = [parameters.get("file_path"), parameters.get("output_path")]
                if not any(isinstance(path, str) and path.lower().endswith(str(expected).lower()) for path in actual_paths):
                    failures.append(f"{tool_name}.{key} 不满足 {expected}")
            elif key == "must_write_under":
                path = str(parameters.get("output_path", parameters.get("file_path", "")))
                if str(expected).replace("\\", "/") not in path.replace("\\", "/"):
                    failures.append(f"{tool_name} 路径不在 {expected} 下")
            elif key == "path_prefix":
                path = str(parameters.get("output_path", parameters.get("file_path", ""))).replace("\\", "/")
                if not path.startswith(str(expected)):
                    failures.append(f"{tool_name} 路径前缀错误")
            elif key == "title_equals_filename_stem":
                path = Path(str(parameters.get("file_path", "")))
                if expected and parameters.get("title") and parameters.get("title") != path.stem:
                    failures.append(f"{tool_name} title 与文件名不一致")
            elif key in {"must_be_redacted", "no_health_diagnosis"}:
                if expected and not parameters.get(key, False):
                    failures.append(f"{tool_name} 缺少 {key}")
            elif key == "metadata_required":
                metadata = parameters.get("metadata", {})
                if not all(field in metadata or field in parameters for field in expected):
                    failures.append(f"{tool_name} 缺少元数据 {expected}")
            elif key == "requires_receipt_id":
                # 该约束由 delivery_status 检查；工具参数阶段只确认开关存在。
                continue
            elif key in {"must_not_infer_success", "append_only_attempts", "preserve_existing", "atomic"}:
                continue
            elif key == "retry_must_add":
                if not any(_value_matches(call.get("parameters", {}), expected) for call in matching):
                    failures.append(f"{tool_name} 重试参数未补齐 {expected}")
            elif key in parameters:
                if not _value_matches(parameters.get(key), expected):
                    failures.append(f"{tool_name}.{key}={parameters.get(key)!r} != {expected!r}")
            elif key not in {"output_dir", "min_files", "all_extension", "required_chapter_count", "must_fail_if_missing", "max_retries"}:
                failures.append(f"{tool_name} 缺少参数 {key}")
    # 工作流闸门返回 validation_error 是预期的安全拒绝，不应被误判为
    # 参数格式错误；真正进入工具边界的 validation_error 仍然使本项失败。
    passed = not failures and all(
        call.get("return_status") != "validation_error" or _is_workflow_guard(call)
        for call in calls
    )
    return _check("tool_args_passed", passed, constraints, {"calls": observed, "failures": failures}, "whitebox_trace.tool_calls.parameters")


def check_rag_recall(criteria: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    expected = list(criteria.get("expected_recall_rule_ids", []))
    observed = [chunk.get("rule_id") for chunk in trace.get("rag", {}).get("retrieved_chunks", [])]
    missing = [rule_id for rule_id in expected if rule_id not in observed]
    return _check("rag_recall_passed", not missing, expected, {"rule_ids": observed, "missing": missing}, "whitebox_trace.rag.retrieved_chunks")


def _resolve_path(path_value: str, root: Path, run_dir: Path) -> Path:
    candidate = Path(path_value)
    if candidate.is_absolute():
        return candidate
    root_candidate = root / candidate
    return root_candidate if root_candidate.exists() else run_dir / candidate


def check_artifact_format(criteria: dict[str, Any], trace: dict[str, Any], root: str | Path, run_dir: str | Path) -> dict[str, Any]:
    artifact = criteria.get("docx_artifact_checks", {})
    if not artifact.get("require_real_file", True):
        return _check("artifact_format_passed", True, "no artifact required", trace.get("generated_docx_paths", []), "check_criteria.docx_artifact_checks.require_real_file=false")
    paths = list(trace.get("generated_docx_paths", []))
    expected_chapters = list(artifact.get("expected_chapter_titles", []))
    failures: list[str] = []
    observed: dict[str, Any] = {"paths": paths, "files": []}
    if not paths:
        failures.append("没有生成真实文件")
    for raw_path in paths:
        path = _resolve_path(raw_path, Path(root), Path(run_dir))
        item: dict[str, Any] = {"path": path.as_posix(), "exists": path.exists(), "suffix": path.suffix.lower()}
        if not path.exists():
            failures.append(f"文件不存在: {raw_path}")
            observed["files"].append(item)
            continue
        if path.suffix.lower() != artifact.get("required_extension", ".docx"):
            failures.append(f"扩展名错误: {raw_path}")
        try:
            document = Document(path)
            paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
            headings = [paragraph.text for paragraph in document.paragraphs if paragraph.style.name.startswith("Heading 1")]
            item.update({"non_empty": bool(paragraphs), "headings": headings})
            if artifact.get("require_non_empty_text", True) and not paragraphs:
                failures.append(f"文件为空: {raw_path}")
            missing = [chapter for chapter in expected_chapters if chapter not in headings]
            if missing:
                failures.append(f"缺少一级标题: {missing}")
            full_text = "\n".join(paragraphs)
            forbidden = [term for term in ["真实姓名", "完整学号", "学生诊断", "未经脱敏"] if term in full_text]
            if forbidden:
                failures.append(f"包含隐私/禁忌词: {forbidden}")
            item["forbidden_terms"] = forbidden
        except Exception as exc:
            failures.append(f"无法解析 docx: {exc}")
        observed["files"].append(item)
    return _check("artifact_format_passed", not failures, expected_chapters, {**observed, "failures": failures}, "真实 .docx + python-docx 解析")


def _response_json(call: dict[str, Any]) -> dict[str, Any]:
    raw = call.get("response_excerpt") or "{}"
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _is_workflow_guard(call: dict[str, Any]) -> bool:
    """返回调用是否被工作流闸门拦截，而非进入底层业务工具。"""

    return call.get("return_status") == "validation_error" and _response_json(call).get("error") == "workflow_guard"


def check_delivery_status(criteria: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    platform = criteria.get("platform_delivery_checks", {})
    expected_statuses = list(platform.get("success_status_values", []))
    if expected_statuses == ["not_sent"] and not platform.get("require_receipt_id", False):
        calls = _tool_calls(trace)
        unexpected = [
            call.get("name")
            for call in calls
            if call.get("name") in {"academic_affairs.send", "send_to_platform", "academic_affairs.verify_receipt"}
            and not _is_workflow_guard(call)
        ]
        return _check("delivery_status_passed", not unexpected, "not_sent", {"status": "not_sent", "unexpected_calls": unexpected}, "用例明确要求只保存本地")
    calls = _tool_calls(trace)
    delivery_calls = [call for call in calls if call.get("name") in {"academic_affairs.send", "send_to_platform", "academic_affairs.verify_receipt"}]
    responses = [_response_json(call) for call in delivery_calls]
    statuses = [response.get("status") for response in responses]
    receipts = [response.get("receipt_id") for response in responses if response.get("receipt_id")]
    passed = any(status in expected_statuses for status in statuses) and (not platform.get("require_receipt_id") or bool(receipts))
    return _check("delivery_status_passed", passed, {"statuses": expected_statuses, "recipient": platform.get("expected_recipient")}, {"statuses": statuses, "receipt_ids": receipts}, "whitebox_trace.tool_calls delivery response")


def check_model_generation(output_text: str, criteria: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    # 生成阶段的确定性下限是 Agent 必须产生可审计的非空响应；文件质量由 artifact checker 负责。
    passed = bool(str(output_text or "").strip())
    return _check("model_generation_passed", passed, "non-empty assistant output", output_text, "Agent output_text")


def diagnose_failure(checks: list[dict[str, Any]], trace: dict[str, Any], criteria: dict[str, Any]) -> tuple[str, list[str]]:
    failed = {item["check_name"]: item for item in checks if not item["passed"]}
    if not failed:
        return "none", []
    reasons = [f"{name}: {item.get('observed')}" for name, item in failed.items()]
    required_memory_keys = {item.get("key") for item in criteria.get("_required_memory_keys", [])}
    # 只有用例声明的前置记忆或故障注入记忆 miss/error 才能归因到前置条件；
    # Agent 主动探测一个不存在的可选偏好不应污染用例结果。
    memory_fail = any(
        event.get("status") in {"miss", "error"}
        and bool(required_memory_keys)
        and event.get("key") in required_memory_keys
        for event in trace.get("memory_events", [])
    )
    if memory_fail:
        return "precondition_or_memory", reasons
    if "rag_recall_passed" in failed:
        return "rag_retrieval", reasons
    if "tool_selection_passed" in failed or "workflow_constraints_passed" in failed or "tool_args_passed" in failed or any(
        call.get("return_status") in {"validation_error", "runtime_error"} and not _is_workflow_guard(call)
        for call in _tool_calls(trace)
    ):
        return "tool_call", reasons
    if "artifact_format_passed" in failed or "delivery_status_passed" in failed:
        return "artifact_or_delivery", reasons
    if "model_generation_passed" in failed:
        return "model_generation", reasons
    return "none", reasons


def evaluate_case(
    case: dict[str, Any],
    trace: dict[str, Any],
    output_text: str,
    *,
    root: str | Path,
    run_dir: str | Path,
) -> dict[str, Any]:
    """执行全部确定性检查并返回 EvaluationResultSchema 字典。"""

    criteria = case["check_criteria"]
    criteria = dict(criteria)
    required_keys = [item.get("key") for item in case.get("pre_conditions", {}).get("memory_seed", [])]
    required_keys.extend(
        injection.get("key")
        for injection in case.get("pre_conditions", {}).get("fault_injection_config", {}).get("injections", [])
        if injection.get("type") == "memory.read" and injection.get("key")
    )
    criteria["_required_memory_keys"] = [{"key": key} for key in required_keys]
    checks = [
        check_tool_selection(criteria, trace),
        check_workflow_constraints(criteria, trace),
        check_tool_args(criteria, trace),
        check_rag_recall(criteria, trace),
        check_artifact_format(criteria, trace, root, run_dir),
        check_delivery_status(criteria, trace),
        check_model_generation(output_text, criteria, trace),
    ]
    stage, reasons = diagnose_failure(checks, trace, criteria)
    return {
        "deterministic_checks": checks,
        "diagnosed_failure_stage": stage,
        "failure_reasons": reasons,
    }

