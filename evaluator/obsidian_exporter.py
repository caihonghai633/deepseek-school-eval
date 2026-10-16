"""将 AE-01 评测结果导出为 Obsidian 可索引的 Markdown 笔记。

导出器不改变 ``runs/raw_results.jsonl`` 或 Analyzer 的汇总 JSON，并会幂等地在
``reports/evaluation_report.md`` 末尾维护一个产物链接区块；除此之外生成一个
独立的 Obsidian 知识库目录：

* ``evaluation_report.md``：带 YAML Frontmatter 的最终技术报告副本；
* ``summaries/``：按版本聚合的指标笔记；
* ``runs/``：每个不可变 ``run_id`` 一条笔记，包含耗时、Token、成本、检查结果
  和指向报告/版本汇总的双链；
* ``index.md``：Obsidian 首页，使用 ``[[wikilink]]`` 连接上述内容。

所有输入都按 UTF-8 读取，路径由 ``pathlib.Path`` 处理。导出失败不会修改原始
运行记录；Analyzer 可在汇总完成后调用 :func:`export_analysis_notes` 完成自动化。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
PROJECT_NAME = "AE-01_School_Agent_Eval"
DEFAULT_TAGS = ("ai_eval", "agent", "school_office")


def _as_path(path: str | Path, *, base: Path = ROOT) -> Path:
    value = Path(path)
    return value if value.is_absolute() else base / value


def _safe_filename(value: str, fallback: str = "note") -> str:
    """生成跨平台、可作为 Obsidian 文件名的相对安全名称。"""

    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value))
    return cleaned.strip("._") or fallback


def _date_from_run_id(run_id: str) -> str:
    match = re.search(r"run_(\d{8}T\d{6,}Z)", run_id)
    if match:
        token = match.group(1)
        for fmt in ("%Y%m%dT%H%M%S%fZ", "%Y%m%dT%H%M%SZ"):
            try:
                return datetime.strptime(token, fmt).date().isoformat()
            except ValueError:
                continue
    return datetime.now(timezone.utc).date().isoformat()


def _percent(value: float | int | None) -> str:
    return f"{float(value or 0.0) * 100:.2f}%"


def _run_passed(result: dict[str, Any]) -> bool:
    checks = result.get("evaluation_result", {}).get("deterministic_checks", [])
    return bool(checks) and all(bool(check.get("passed")) for check in checks)


def _yaml_text(value: Any) -> str:
    """输出适合 Frontmatter 的单行 YAML 标量。"""

    text = str(value)
    # Frontmatter 中带百分号、冒号或特殊字符的值必须加引号。
    if not text or re.search(r"[\s:#%\[\]\{\}]", text) or text.lower() in {"null", "true", "false"}:
        return json.dumps(text, ensure_ascii=False)
    return text


def render_frontmatter(
    *,
    date: str,
    run_id: str,
    version: str,
    pass_rate: str,
    total_cost_usd: float,
    tags: Iterable[str] = DEFAULT_TAGS,
    extra: dict[str, Any] | None = None,
) -> str:
    """渲染标准 Obsidian YAML Frontmatter。

    ``pass_rate`` 以字符串写入，确保 Dataview 不把 ``9.62%`` 误读为 YAML
    非法数值；成本保留 8 位小数以便后续聚合。
    """

    tag_values = ", ".join(_yaml_text(tag) for tag in tags)
    lines = [
        "---",
        f"date: {_yaml_text(date)}",
        f"project: {_yaml_text(PROJECT_NAME)}",
        f"run_id: {_yaml_text(run_id)}",
        f"version: {_yaml_text(version)}",
        f"pass_rate: {_yaml_text(pass_rate)}",
        f"total_cost_usd: {float(total_cost_usd):.8f}",
        f"tags: [{tag_values}]",
    ]
    for key, value in (extra or {}).items():
        if key in {"date", "project", "run_id", "version", "pass_rate", "total_cost_usd", "tags"}:
            continue
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (int, float)):
            rendered = str(value)
        else:
            rendered = _yaml_text(value)
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    return "\n".join(lines)


def _write_note(path: Path, *, frontmatter: str, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = frontmatter.rstrip() + "\n\n" + body.strip() + "\n"
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _load_results(path: str | Path) -> list[dict[str, Any]]:
    result_path = _as_path(path)
    if not result_path.exists():
        return []
    return [
        json.loads(line)
        for line in result_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _resolve_artifact_path(raw_path: str, *, root: Path = ROOT) -> Path:
    """把 Trace 中的绝对/相对产物路径统一解析为工作区路径。"""

    candidate = Path(str(raw_path))
    if candidate.is_absolute():
        return candidate
    return root / candidate


def _relative_path_link(target: str | Path, from_file: Path) -> str:
    """生成 Obsidian/Markdown 可打开的 POSIX 相对路径。"""

    target_path = Path(target)
    if not target_path.is_absolute():
        target_path = ROOT / target_path
    try:
        relative = os.path.relpath(target_path, start=from_file.parent)
    except ValueError:
        # Windows 不同盘符无法生成相对路径；保留 POSIX 化绝对路径作为降级。
        relative = target_path.as_posix()
    return Path(relative).as_posix()


def _artifact_link_block(results: Iterable[dict[str, Any]], *, from_file: Path) -> str:
    """渲染原始 JSONL、逐次 raw_result 和 docx 产物的相对链接区块。"""

    lines = [
        "<!-- AE-01 OBSIDIAN ARTIFACT LINKS START -->",
        "## 原始记录与产物链接",
        "",
        f"- [全部原始 JSONL]({_relative_path_link(ROOT / 'runs' / 'raw_results.jsonl', from_file)})",
    ]
    for result in results:
        run_id = str(result.get("run_id", "unknown-run"))
        raw_result = ROOT / "runs" / run_id / "raw_result.json"
        raw_link = _relative_path_link(raw_result, from_file)
        version = result.get("version_tag", "unknown")
        case_id = result.get("input", {}).get("case_id", "unknown")
        lines.append(f"- `{version}` / `{case_id}` / `{run_id}`：[原始记录]({raw_link})")
        for raw_docx in result.get("whitebox_trace", {}).get("generated_docx_paths", []):
            docx_path = _resolve_artifact_path(str(raw_docx))
            docx_link = _relative_path_link(docx_path, from_file)
            lines.append(f"  - [真实 .docx 产物]({docx_link})")
    lines.append("<!-- AE-01 OBSIDIAN ARTIFACT LINKS END -->")
    return "\n".join(lines)


def _strip_artifact_link_block(text: str) -> str:
    pattern = re.compile(
        r"\n?<!-- AE-01 OBSIDIAN ARTIFACT LINKS START -->.*?<!-- AE-01 OBSIDIAN ARTIFACT LINKS END -->\n?",
        flags=re.DOTALL,
    )
    return pattern.sub("\n", text).rstrip()


def _update_source_report_links(report_path: str | Path, results: Iterable[dict[str, Any]]) -> str:
    """幂等地把链接区块插入原始 ``reports/evaluation_report.md``。"""

    path = _as_path(report_path)
    original = path.read_text(encoding="utf-8") if path.exists() else "# AE-01 Evaluation Report"
    cleaned = _strip_artifact_link_block(original)
    updated = cleaned.rstrip() + "\n\n" + _artifact_link_block(results, from_file=path) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8", newline="\n")
    return path.as_posix()


def _render_run_body(
    result: dict[str, Any],
    *,
    report_stem: str,
    summary_stem: str,
    note_path: Path,
) -> str:
    metrics = result.get("execution_metrics", {})
    evaluation = result.get("evaluation_result", {})
    trace = result.get("whitebox_trace", {})
    checks = evaluation.get("deterministic_checks", [])
    check_lines = "\n".join(
        f"- {'✅' if check.get('passed') else '❌'} `{check.get('check_name', 'unknown')}`"
        for check in checks
    ) or "- 无确定性检查记录"
    tool_names = [call.get("name") for call in trace.get("tool_calls", [])]
    retrieved_ids = [chunk.get("rule_id") for chunk in trace.get("rag", {}).get("retrieved_chunks", [])]
    raw_result_link = _relative_path_link(
        ROOT / "runs" / str(result.get("run_id", "unknown-run")) / "raw_result.json", note_path
    )
    raw_index_link = _relative_path_link(ROOT / "runs" / "raw_results.jsonl", note_path)
    docx_links = [
        f"[{Path(str(raw_path)).name}]({_relative_path_link(_resolve_artifact_path(str(raw_path)), note_path)})"
        for raw_path in trace.get("generated_docx_paths", [])
    ]
    return f"""# Run `{result.get('run_id', 'unknown')}`

> 这是不可变原始运行记录的 Obsidian 索引副本；详细 JSON 仍以运行目录中的 `raw_result.json` 为准。

## 核心指标

| 指标 | 值 |
|---|---:|
| 版本 | `{result.get('version_tag', 'unknown')}` |
| 用例 | `{result.get('input', {}).get('case_id', 'unknown')}` |
| 通过 | `{'是' if _run_passed(result) else '否'}` |
| 耗时 | {float(metrics.get('duration_ms', 0.0)):.3f} ms |
| Prompt tokens | {int(metrics.get('prompt_tokens', 0))} |
| Completion tokens | {int(metrics.get('completion_tokens', 0))} |
| 预估成本 | ${float(metrics.get('estimated_cost_usd', 0.0)):.8f} |
| 故障归因 | `{evaluation.get('diagnosed_failure_stage', 'unknown')}` |

## 确定性检查

{check_lines}

## 白盒 Trace 摘要

- 召回规则：{', '.join(f'`{rule_id}`' for rule_id in retrieved_ids) or '无'}
- 工具链：{' → '.join(f'`{name}`' for name in tool_names) or '无'}
- 生成文件：{', '.join(f'`{path}`' for path in trace.get('generated_docx_paths', [])) or '无'}
- 失败原因：{'；'.join(evaluation.get('failure_reasons', [])) or '无'}

## 原始记录与文件产物

- [本次原始 JSON 记录]({raw_result_link})
- [原始 JSONL 索引]({raw_index_link})
- 真实 `.docx`：{', '.join(docx_links) or '无'}

## 关联笔记

- [[{report_stem}]]
- [[{summary_stem}]]
"""


def export_run_notes(
    results: Iterable[dict[str, Any]],
    *,
    output_dir: str | Path,
    report_stem: str,
    summary_stems: dict[str, str],
    tags: Iterable[str] = DEFAULT_TAGS,
) -> list[str]:
    """为每条原始运行记录生成一条带 Frontmatter 的 Obsidian 笔记。"""

    destination = _as_path(output_dir)
    written: list[str] = []
    for result in results:
        run_id = str(result.get("run_id", "unknown-run"))
        version = str(result.get("version_tag", "unknown"))
        metrics = result.get("execution_metrics", {})
        passed = _run_passed(result)
        summary_stem = summary_stems.get(version, f"{PROJECT_NAME}__{version}_summary")
        frontmatter = render_frontmatter(
            date=_date_from_run_id(run_id),
            run_id=run_id,
            version=version,
            pass_rate="100.00%" if passed else "0.00%",
            total_cost_usd=float(metrics.get("estimated_cost_usd", 0.0)),
            tags=tags,
            extra={
                "case_id": result.get("input", {}).get("case_id", "unknown"),
                "attempt_number": result.get("attempt_number", 1),
                "diagnosed_failure_stage": result.get("evaluation_result", {}).get("diagnosed_failure_stage", "unknown"),
            },
        )
        path = destination / "runs" / f"{_safe_filename(run_id)}.md"
        _write_note(
            path,
            frontmatter=frontmatter,
            body=_render_run_body(result, report_stem=report_stem, summary_stem=summary_stem, note_path=path),
        )
        written.append(path.as_posix())
    return written


def export_evaluation_report(
    report_path: str | Path,
    *,
    output_dir: str | Path,
    run_id: str,
    version: str = "ALL",
    pass_rate: str = "n/a",
    total_cost_usd: float = 0.0,
    tags: Iterable[str] = DEFAULT_TAGS,
    results: Iterable[dict[str, Any]] = (),
) -> str:
    """将最终报告包装成带 Frontmatter 的 Obsidian 笔记。"""

    source = _as_path(report_path)
    report_text = source.read_text(encoding="utf-8") if source.exists() else "# Evaluation report\n\n报告文件尚未生成。"
    stem = f"{PROJECT_NAME}__evaluation_report"
    path = _as_path(output_dir) / f"{stem}.md"
    frontmatter = render_frontmatter(
        date=datetime.now(timezone.utc).date().isoformat(),
        run_id=run_id,
        version=version,
        pass_rate=pass_rate,
        total_cost_usd=total_cost_usd,
        tags=tags,
        extra={"source": source.as_posix()},
    )
    clean_report = _strip_artifact_link_block(report_text)
    artifact_links = _artifact_link_block(results, from_file=path)
    _write_note(path, frontmatter=frontmatter, body=f"# AE-01 评测报告\n\n{clean_report}\n\n{artifact_links}\n")
    return path.as_posix()


def _render_summary_body(version: str, summary: dict[str, Any], *, report_stem: str, run_note_count: int) -> str:
    categories = summary.get("by_category", {})
    category_lines = "\n".join(
        f"| {category} | {values.get('passed', 0)} | {values.get('total', 0)} |"
        for category, values in categories.items()
    ) or "| 无 | 0 | 0 |"
    failures = summary.get("failure_stage_counts", {})
    failure_lines = "\n".join(f"- `{stage}`：{count}" for stage, count in failures.items()) or "- 无"
    return f"""# {version} 汇总

| 指标 | 值 |
|---|---:|
| 用例数 | {summary.get('total_cases', 0)} |
| 通过数 | {summary.get('passed_cases', 0)} |
| 通过率 | {_percent(summary.get('success_rate', 0.0))} |
| Prompt tokens | {summary.get('prompt_tokens', 0)} |
| Completion tokens | {summary.get('completion_tokens', 0)} |
| 预估成本 | ${float(summary.get('estimated_cost_usd', 0.0)):.8f} |
| 平均耗时 | {float(summary.get('average_duration_ms', 0.0)):.3f} ms |
| 运行笔记数 | {run_note_count} |

## 场景通过情况

| 场景 | 通过 | 总数 |
|---|---:|---:|
{category_lines}

## 故障归因

{failure_lines}

## 关联笔记

- [[{report_stem}]]
"""


def _summary_from_results(items: list[dict[str, Any]]) -> dict[str, Any]:
    """为尚未进入 Analyzer 汇总文件的新版本生成最小聚合指标。"""

    categories: dict[str, dict[str, int]] = {}
    failures: dict[str, int] = {}
    passed_count = 0
    for item in items:
        category = item.get("input", {}).get("test_case", {}).get("category", "unknown")
        bucket = categories.setdefault(category, {"total": 0, "passed": 0})
        bucket["total"] += 1
        passed = _run_passed(item)
        if passed:
            passed_count += 1
            bucket["passed"] += 1
        stage = item.get("evaluation_result", {}).get("diagnosed_failure_stage", "unknown")
        if stage != "none":
            failures[stage] = failures.get(stage, 0) + 1
    durations = [float(item.get("execution_metrics", {}).get("duration_ms", 0.0)) for item in items]
    return {
        "total_cases": len(items),
        "passed_cases": passed_count,
        "success_rate": passed_count / len(items) if items else 0.0,
        "by_category": categories,
        "failure_stage_counts": failures,
        "prompt_tokens": sum(int(item.get("execution_metrics", {}).get("prompt_tokens", 0)) for item in items),
        "completion_tokens": sum(int(item.get("execution_metrics", {}).get("completion_tokens", 0)) for item in items),
        "estimated_cost_usd": sum(float(item.get("execution_metrics", {}).get("estimated_cost_usd", 0.0)) for item in items),
        "average_duration_ms": sum(durations) / len(durations) if durations else 0.0,
    }


def export_analysis_notes(
    *,
    summary_path: str | Path = ROOT / "reports" / "stage3_comparison.json",
    raw_results_path: str | Path = ROOT / "runs" / "raw_results.jsonl",
    report_path: str | Path = ROOT / "reports" / "evaluation_report.md",
    output_dir: str | Path = ROOT / "reports" / "obsidian",
    project: str = PROJECT_NAME,
    tags: Iterable[str] = DEFAULT_TAGS,
) -> dict[str, Any]:
    """Analyzer 完成汇总后调用的统一导出入口。"""

    global PROJECT_NAME
    previous_project = PROJECT_NAME
    PROJECT_NAME = project
    try:
        destination = _as_path(output_dir)
        summary_path_resolved = _as_path(summary_path)
        summary_payload = _load_json(summary_path_resolved, {})
        results = _load_results(raw_results_path)
        report_stem = f"{PROJECT_NAME}__evaluation_report"
        _update_source_report_links(report_path, results)

        version_summaries = dict(summary_payload.get("by_version", {}))
        # Stage 4/V3 可能先产生原始记录，随后才由 Analyzer 汇总；导出器仍然
        # 为这些新版本生成汇总笔记，避免运行笔记出现失效的 [[V3_summary]] 双链。
        results_by_version: dict[str, list[dict[str, Any]]] = {}
        for item in results:
            results_by_version.setdefault(str(item.get("version_tag", "unknown")), []).append(item)
        for version, version_results in results_by_version.items():
            version_summaries.setdefault(version, _summary_from_results(version_results))
        summary_stems = {
            version: f"{PROJECT_NAME}__{_safe_filename(version)}_summary"
            for version in version_summaries
        }
        summary_note_paths: list[str] = []
        summary_run_ids: dict[str, str] = {}
        for version, version_summary in sorted(version_summaries.items()):
            version_results = [item for item in results if str(item.get("version_tag")) == str(version)]
            latest_run_id = str(version_results[-1].get("run_id")) if version_results else f"summary-{version.lower()}"
            summary_run_ids[version] = latest_run_id
            path = destination / "summaries" / f"{summary_stems[version]}.md"
            frontmatter = render_frontmatter(
                date=datetime.now(timezone.utc).date().isoformat(),
                run_id=latest_run_id,
                version=str(version),
                pass_rate=_percent(version_summary.get("success_rate", 0.0)),
                total_cost_usd=float(version_summary.get("estimated_cost_usd", 0.0)),
                tags=tags,
                extra={"total_cases": version_summary.get("total_cases", 0), "passed_cases": version_summary.get("passed_cases", 0)},
            )
            _write_note(path, frontmatter=frontmatter, body=_render_summary_body(str(version), version_summary, report_stem=report_stem, run_note_count=len(version_results)))
            summary_note_paths.append(path.as_posix())

        run_note_paths = export_run_notes(
            results,
            output_dir=destination,
            report_stem=report_stem,
            summary_stems=summary_stems,
            tags=tags,
        )
        report_path_written = export_evaluation_report(
            report_path,
            output_dir=destination,
            run_id=f"analysis-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            version="ALL",
            pass_rate="n/a",
            total_cost_usd=sum(float(item.get("execution_metrics", {}).get("estimated_cost_usd", 0.0)) for item in results),
            tags=tags,
            results=results,
        )

        index_path = destination / "index.md"
        index_frontmatter = render_frontmatter(
            date=datetime.now(timezone.utc).date().isoformat(),
            run_id=f"analysis-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            version="ALL",
            pass_rate="n/a",
            total_cost_usd=sum(float(item.get("execution_metrics", {}).get("estimated_cost_usd", 0.0)) for item in results),
            tags=tags,
        )
        summary_links = "\n".join(f"- [[{stem}]]" for stem in summary_stems.values()) or "- 无版本汇总"
        index_body = f"""# {PROJECT_NAME}

自动生成的 AE-01 学校办公 Agent 评测 Obsidian 索引。

## 评测报告

- [[{report_stem}]]

## 版本汇总

{summary_links}

## 运行笔记

- 运行笔记数量：{len(run_note_paths)}
- 原始 JSONL：`runs/raw_results.jsonl`
"""
        _write_note(index_path, frontmatter=index_frontmatter, body=index_body)

        return {
            "output_dir": destination.as_posix(),
            "report_note": report_path_written,
            "summary_notes": summary_note_paths,
            "run_notes": run_note_paths,
            "index_note": index_path.as_posix(),
            "summary_run_ids": summary_run_ids,
            "run_note_count": len(run_note_paths),
        }
    finally:
        PROJECT_NAME = previous_project


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 Obsidian 评测笔记")
    parser.add_argument("--summary", default="reports/stage3_comparison.json")
    parser.add_argument("--raw-results", default="runs/raw_results.jsonl")
    parser.add_argument("--report", default="reports/evaluation_report.md")
    parser.add_argument("--output-dir", default="reports/obsidian")
    args = parser.parse_args()
    print(json.dumps(export_analysis_notes(summary_path=args.summary, raw_results_path=args.raw_results, report_path=args.report, output_dir=args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
