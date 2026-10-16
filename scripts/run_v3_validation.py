"""执行 Stage 4 三类故障的离线局部复测。

脚本只选取代表性用例，不重新消耗 DeepSeek Token。修复前证据读取 Stage 3
去重后的 V2 原始记录，修复后通过同一 Runner/Schema 走 V3 硬化配置；结果追加到
``runs/raw_results.jsonl``，并输出可审计的 JSON 与文本日志。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluator.runner import execute_case, load_cases, load_yaml  # noqa: E402


SELECTED_CASES = {
    "rag_retrieval": "TC-RAG-001",
    "tool_call": "TC-TOOL-004",
    "artifact_or_delivery": "TC-ERR-009",
}


def _latest_unique_v2() -> dict[str, dict[str, Any]]:
    path = ROOT / "runs" / "raw_results.jsonl"
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if item.get("version_tag") == "V2":
            latest[item.get("input", {}).get("case_id", "")] = item
    return latest


def _passed(item: dict[str, Any], check_name: str | None = None) -> bool:
    checks = item.get("evaluation_result", {}).get("deterministic_checks", [])
    if check_name:
        return any(c.get("check_name") == check_name and c.get("passed") for c in checks)
    return bool(checks) and all(c.get("passed") for c in checks)


def _failure_rate(item: dict[str, Any], check_name: str | None = None) -> float:
    return 0.0 if _passed(item, check_name) else 1.0


def main() -> None:
    cases = {case["case_id"]: case for case in load_cases(ROOT / "data" / "test_cases.jsonl")}
    config = load_yaml(ROOT / "configs" / "v3_hardened.yaml")
    baseline = _latest_unique_v2()
    raw_index = ROOT / "runs" / "raw_results.jsonl"
    after: dict[str, dict[str, Any]] = {}
    for fault_type, case_id in SELECTED_CASES.items():
        case = cases[case_id]
        after[fault_type] = execute_case(
            case,
            config=config,
            config_path=str(ROOT / "configs" / "v3_hardened.yaml"),
            root=ROOT,
            raw_index=raw_index,
            offline=True,
        )

    # RAG/工具使用总体验证；交付故障单独按 delivery_status_passed 统计，避免
    # 上游召回失败掩盖 502 重试对交付检查的影响。
    before_after: dict[str, Any] = {}
    for fault_type, case_id in SELECTED_CASES.items():
        before_item = baseline.get(case_id, {})
        after_item = after[fault_type]
        check = "delivery_status_passed" if fault_type == "artifact_or_delivery" else None
        before_rate = _failure_rate(before_item, check)
        after_rate = _failure_rate(after_item, check)
        before_after[fault_type] = {
            "case_id": case_id,
            "before_version": "V2",
            "before_failure_rate": before_rate,
            "before_diagnosed_stage": before_item.get("evaluation_result", {}).get("diagnosed_failure_stage"),
            "after_version": "V3",
            "after_failure_rate": after_rate,
            "after_diagnosed_stage": after_item["evaluation_result"]["diagnosed_failure_stage"],
            "after_checks": {
                c["check_name"]: c["passed"]
                for c in after_item["evaluation_result"]["deterministic_checks"]
            },
            "tool_statuses": [
                {"name": c["name"], "return_status": c["return_status"]}
                for c in after_item["whitebox_trace"]["tool_calls"]
            ],
        }

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "offline_deterministic_validation",
        "selected_cases": SELECTED_CASES,
        "before_after": before_after,
        "v3_run_ids": {fault: item["run_id"] for fault, item in after.items()},
        "notes": [
            "修复前指标来自 Stage 3 去重后的 V2 原始记录。",
            "V3 复测使用相同 TestCaseSchema、Runner 和确定性 Checkers，未调用外部 API。",
            "artifact_or_delivery 的前后错误率按 delivery_status_passed 单项统计，避免上游 RAG 失败混淆。",
        ],
    }
    report_path = ROOT / "reports" / "v3_validation.json"
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log_lines = ["Stage 4 V3 局部复测日志", f"generated_at={result['generated_at']}"]
    for fault, item in before_after.items():
        log_lines.append(
            f"{fault} case={item['case_id']} "
            f"before_error_rate={item['before_failure_rate']:.2%} "
            f"after_error_rate={item['after_failure_rate']:.2%} "
            f"before_stage={item['before_diagnosed_stage']} "
            f"after_stage={item['after_diagnosed_stage']}"
        )
        log_lines.append(f"  checks={json.dumps(item['after_checks'], ensure_ascii=False)}")
        log_lines.append(f"  tool_statuses={json.dumps(item['tool_statuses'], ensure_ascii=False)}")
    (ROOT / "reports" / "v3_validation.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
