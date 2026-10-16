"""批量评测运行器。

运行器加载 YAML、逐条执行 TestCaseSchema、采集耗时/Token/成本和白盒 Trace，
然后以独立 ``run_id`` 目录和追加式 ``runs/raw_results.jsonl`` 保存不可变原始结果。
``--retry-from`` 只创建新 attempt，永远不覆盖失败运行。
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from evaluator.checkers import evaluate_case
from evaluator.git_manager import archive_evaluation
from evaluator.schemas import RawEvalResultSchema, TestCaseSchema
from sut.agent import SchoolOfficeAgent


ROOT = Path(__file__).resolve().parents[1]
INPUT_PRICE_USD_PER_MILLION = float(os.getenv("DEEPSEEK_INPUT_PRICE_USD_PER_MILLION", "0.28"))
OUTPUT_PRICE_USD_PER_MILLION = float(os.getenv("DEEPSEEK_OUTPUT_PRICE_USD_PER_MILLION", "1.10"))


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def load_yaml(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"配置必须是对象: {config_path}")
    payload["_config_path"] = config_path.as_posix()
    return payload


def load_cases(path: str | Path | None = None) -> list[dict[str, Any]]:
    cases_path = Path(path) if path else ROOT / "data" / "test_cases.jsonl"
    if not cases_path.is_absolute():
        cases_path = ROOT / cases_path
    cases: list[dict[str, Any]] = []
    for line in cases_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(TestCaseSchema.model_validate_json(line).model_dump(mode="json"))
    return cases


def make_run_id(case_id: str, version_tag: str, attempt: int) -> str:
    return f"run_{utc_stamp()}_{version_tag.lower()}_{case_id.lower()}_attempt_{attempt}_{uuid.uuid4().hex[:8]}"


def find_previous(raw_path: Path, run_id: str) -> dict[str, Any]:
    candidate = raw_path / "raw_result.json" if raw_path.is_dir() else raw_path
    if candidate.exists():
        return json.loads(candidate.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"找不到 retry-from 原始记录: {raw_path}")


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return round(
        prompt_tokens / 1_000_000 * INPUT_PRICE_USD_PER_MILLION
        + completion_tokens / 1_000_000 * OUTPUT_PRICE_USD_PER_MILLION,
        8,
    )


def execute_case(
    case: dict[str, Any],
    *,
    config: dict[str, Any],
    config_path: str,
    root: Path,
    raw_index: Path,
    parent_run_id: str | None = None,
    attempt_number: int = 1,
    offline: bool = False,
) -> dict[str, Any]:
    version_tag = str(config.get("version_tag", "V1"))
    run_id = make_run_id(case["case_id"], version_tag, attempt_number)
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    agent = SchoolOfficeAgent(
        config=config,
        root=root,
        run_dir=run_dir,
        fault_injection_config=case.get("pre_conditions", {}).get("fault_injection_config", {}),
        offline=offline,
    )
    agent_run = agent.run_case(case)
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    trace = agent.runtime.whitebox_trace()
    evaluation = evaluate_case(case, trace, agent_run.output_text, root=root, run_dir=run_dir)
    model_config = {
        "provider": config.get("api", {}).get("provider", "deepseek"),
        "base_url": config.get("api", {}).get("base_url", "https://api.deepseek.com"),
        "model": config.get("api", {}).get("model", "deepseek-chat"),
        "api_key_env": config.get("api", {}).get("api_key_env", "DEEPSEEK_API_KEY"),
        "temperature": config.get("api", {}).get("request", {}).get("temperature", 0.1),
        "max_tokens": config.get("api", {}).get("request", {}).get("max_tokens", 2048),
        "seed": config.get("api", {}).get("request", {}).get("seed"),
        "extra_headers": {},
    }
    metrics = {
        "duration_ms": duration_ms,
        "prompt_tokens": agent_run.prompt_tokens,
        "completion_tokens": agent_run.completion_tokens,
        "total_tokens": agent_run.prompt_tokens + agent_run.completion_tokens,
        "estimated_cost_usd": estimate_cost(agent_run.prompt_tokens, agent_run.completion_tokens),
        "cost_formula": f"prompt_tokens * {INPUT_PRICE_USD_PER_MILLION}/1e6 + completion_tokens * {OUTPUT_PRICE_USD_PER_MILLION}/1e6 USD",
    }
    raw_payload = {
        "run_id": run_id,
        "parent_run_id": parent_run_id,
        "attempt_number": attempt_number,
        "version_tag": version_tag,
        "input": {"case_id": case["case_id"], "test_case": case, "config_path": config_path},
        "output": {"assistant_message": agent_run.output_text, "mode": agent_run.mode, "api_error": agent_run.api_error, "docx_paths": trace["generated_docx_paths"]},
        "model_config": model_config,
        "execution_metrics": metrics,
        "whitebox_trace": trace,
        "evaluation_result": evaluation,
    }
    validated = RawEvalResultSchema.model_validate(raw_payload)
    serialised = validated.model_dump(mode="json", by_alias=True)
    (run_dir / "raw_result.json").write_text(json.dumps(serialised, ensure_ascii=False, indent=2), encoding="utf-8")
    with raw_index.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(serialised, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
    return serialised


def aggregate_summary(results: Iterable[dict[str, Any]], config_path: str, output_path: Path) -> dict[str, Any]:
    items = list(results)
    passed = [item for item in items if all(check["passed"] for check in item["evaluation_result"]["deterministic_checks"])]
    by_category: dict[str, dict[str, int]] = {}
    failures: dict[str, int] = {}
    for item in items:
        category = item["input"]["test_case"]["category"]
        bucket = by_category.setdefault(category, {"total": 0, "passed": 0})
        bucket["total"] += 1
        if item in passed:
            bucket["passed"] += 1
        stage = item["evaluation_result"]["diagnosed_failure_stage"]
        if stage != "none":
            failures[stage] = failures.get(stage, 0) + 1
    summary = {
        "config_path": config_path,
        "version_tag": items[0]["version_tag"] if items else None,
        "total_cases": len(items),
        "passed_cases": len(passed),
        "success_rate": round(len(passed) / len(items), 4) if items else 0.0,
        "total_prompt_tokens": sum(item["execution_metrics"]["prompt_tokens"] for item in items),
        "total_completion_tokens": sum(item["execution_metrics"]["completion_tokens"] for item in items),
        "total_estimated_cost_usd": round(sum(item["execution_metrics"]["estimated_cost_usd"] for item in items), 8),
        "by_category": by_category,
        "failure_stage_counts": failures,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def run_batch(config_path: str | Path, *, cases_path: str | Path | None = None, retry_from: str | Path | None = None, offline: bool = False, limit: int | None = None, skip_existing: bool = False) -> dict[str, Any]:
    config = load_yaml(config_path)
    cases = load_cases(cases_path)
    parent_run_id: str | None = None
    attempt = 1
    if retry_from:
        previous = find_previous(Path(retry_from), str(retry_from))
        parent_run_id = previous["run_id"]
        attempt = int(previous.get("attempt_number", 1)) + 1
        cases = [previous["input"]["test_case"]]
    if limit is not None:
        cases = cases[:limit]
    raw_index = ROOT / "runs" / "raw_results.jsonl"
    if skip_existing and raw_index.exists() and not retry_from:
        existing_ids = {
            (item.get("version_tag"), item.get("input", {}).get("case_id"))
            for item in (json.loads(line) for line in raw_index.read_text(encoding="utf-8").splitlines() if line.strip())
        }
        cases = [case for case in cases if (str(config.get("version_tag", "V1")), case["case_id"]) not in existing_ids]
    results: list[dict[str, Any]] = []
    for case in cases:
        results.append(
            execute_case(
                case,
                config=config,
                config_path=str(config.get("_config_path", config_path)),
                root=ROOT,
                raw_index=raw_index,
                parent_run_id=parent_run_id,
                attempt_number=attempt,
                offline=offline,
            )
        )
    summary_path = ROOT / "runs" / f"summary_metrics_{config['version_tag']}_{utc_stamp()}.json"
    summary = aggregate_summary(results, str(config.get("_config_path", config_path)), summary_path)
    # 便于自动化脚本稳定读取最新一版摘要；原始运行目录和 JSONL 不会被覆盖。
    (ROOT / "runs" / f"summary_metrics_{config['version_tag']}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    # Git 归档是旁路运维动作；未初始化仓库、无 Git 或离线 push 只返回状态，
    # 不影响本批评测结果和原始 JSONL 的落盘。
    git_result = archive_evaluation(
        root=ROOT,
        version=str(config.get("version_tag", "unknown")),
        pass_rate=summary.get("success_rate", 0.0),
        run_id=str(results[-1].get("run_id", f"batch-{config.get('version_tag', 'unknown')}")) if results else f"batch-{config.get('version_tag', 'unknown')}",
        config=config,
    )
    summary["git_archive"] = git_result
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AE-01 Stage 3 批量评测运行器")
    parser.add_argument("--config", required=True, help="YAML 配置路径")
    parser.add_argument("--retry-from", default=None, help="失败运行目录或 raw_result.json 路径")
    parser.add_argument("--cases", default=None, help="可选测试集 JSONL 路径")
    parser.add_argument("--offline", action="store_true", help="跳过 DeepSeek，使用确定性降级编排")
    parser.add_argument("--limit", type=int, default=None, help="仅运行前 N 条，用于快速冒烟")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    summary = run_batch(args.config, cases_path=args.cases, retry_from=args.retry_from, offline=args.offline, limit=args.limit)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

