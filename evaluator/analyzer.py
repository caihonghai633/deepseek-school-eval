"""离线聚合运行结果，生成 V1/V2 对照报告。"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load_results(path: str | Path | None = None) -> list[dict[str, Any]]:
    result_path = Path(path) if path else ROOT / "runs" / "raw_results.jsonl"
    if not result_path.is_absolute():
        result_path = ROOT / result_path
    if not result_path.exists():
        return []
    results: list[dict[str, Any]] = []
    for line in result_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            results.append(json.loads(line))
    return results


def latest_unique_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 ``(version_tag, case_id)`` 取最后一次记录。

    原始索引允许同一用例存在冒烟运行、失败重跑和最终运行；报告默认只统计每个
    版本/用例的最新 attempt，同时保留 raw_results.jsonl 的所有不可变记录。
    """

    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for result in results:
        key = (
            str(result.get("version_tag", "unknown")),
            str(result.get("input", {}).get("case_id", result.get("run_id", "unknown"))),
        )
        latest[key] = result
    return list(latest.values())


def is_passed(result: dict[str, Any]) -> bool:
    return all(item.get("passed") for item in result.get("evaluation_result", {}).get("deterministic_checks", []))


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        by_version[result.get("version_tag", "unknown")].append(result)
    version_summary: dict[str, Any] = {}
    for version, items in sorted(by_version.items()):
        categories: dict[str, dict[str, int]] = {}
        failure_counts: Counter[str] = Counter()
        for item in items:
            category = item.get("input", {}).get("test_case", {}).get("category", "unknown")
            bucket = categories.setdefault(category, {"total": 0, "passed": 0})
            bucket["total"] += 1
            if is_passed(item):
                bucket["passed"] += 1
            stage = item.get("evaluation_result", {}).get("diagnosed_failure_stage", "unknown")
            if stage != "none":
                failure_counts[stage] += 1
        version_summary[version] = {
            "total_cases": len(items),
            "passed_cases": sum(is_passed(item) for item in items),
            "success_rate": round(sum(is_passed(item) for item in items) / len(items), 4) if items else 0.0,
            "by_category": categories,
            "failure_stage_counts": dict(failure_counts),
            "prompt_tokens": sum(item.get("execution_metrics", {}).get("prompt_tokens", 0) for item in items),
            "completion_tokens": sum(item.get("execution_metrics", {}).get("completion_tokens", 0) for item in items),
            "estimated_cost_usd": round(sum(item.get("execution_metrics", {}).get("estimated_cost_usd", 0.0) for item in items), 8),
            "average_duration_ms": round(sum(item.get("execution_metrics", {}).get("duration_ms", 0.0) for item in items) / len(items), 3) if items else 0.0,
        }
    comparison: dict[str, Any] = {}
    if "V1" in version_summary and "V2" in version_summary:
        comparison = {
            "success_rate_delta_v2_minus_v1": round(version_summary["V2"]["success_rate"] - version_summary["V1"]["success_rate"], 4),
            "passed_cases_delta_v2_minus_v1": version_summary["V2"]["passed_cases"] - version_summary["V1"]["passed_cases"],
            "failure_stage_delta_v2_minus_v1": {
                stage: version_summary["V2"]["failure_stage_counts"].get(stage, 0) - version_summary["V1"]["failure_stage_counts"].get(stage, 0)
                for stage in set(version_summary["V1"]["failure_stage_counts"]) | set(version_summary["V2"]["failure_stage_counts"])
            },
        }
    return {"total_records": len(results), "by_version": version_summary, "comparison": comparison}


def main() -> None:
    parser = argparse.ArgumentParser(description="聚合 runs/raw_results.jsonl")
    parser.add_argument("--input", default="runs/raw_results.jsonl")
    parser.add_argument("--output", default="reports/stage3_comparison.json")
    args = parser.parse_args()
    summary = summarize(latest_unique_results(load_results(args.input)))
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    # 汇总 JSON 写入成功后再生成 Obsidian 笔记；导出失败不会回滚或覆盖原始结果。
    # 这样既保持 Analyzer 原有 stdout JSON 兼容性，也让每次汇总自动留下可双链检索的知识库。
    from evaluator.obsidian_exporter import export_analysis_notes
    from evaluator.git_manager import archive_evaluation

    export_result = export_analysis_notes(
        summary_path=output_path,
        raw_results_path=args.input,
        report_path=ROOT / "reports" / "evaluation_report.md",
        output_dir=ROOT / "reports" / "obsidian",
    )
    versions = sorted(summary.get("by_version", {}))
    if len(versions) == 1:
        archive_version = versions[0]
        archive_pass_rate: str | float = summary["by_version"][archive_version].get("success_rate", 0.0)
    else:
        archive_version = "ANALYSIS"
        archive_pass_rate = "mixed"
    analysis_run_id = f"analysis-{summary.get('generated_at', '').replace(':', '').replace('+00:00', 'Z') or 'latest'}"
    git_result = archive_evaluation(
        root=ROOT,
        version=archive_version,
        pass_rate=archive_pass_rate,
        run_id=analysis_run_id,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps({"obsidian_export": export_result}, ensure_ascii=False, indent=2))
    print(json.dumps({"git_archive": git_result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

