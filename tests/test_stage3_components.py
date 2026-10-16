"""Stage 3 组件集成测试（不调用外部 DeepSeek API）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from docx import Document


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluator.checkers import evaluate_case  # noqa: E402
from evaluator.schemas import TestCaseSchema as CaseSchema  # noqa: E402
from sut.rag_engine import KnowledgeBaseRetriever  # noqa: E402
from sut.tools import ToolRuntime  # noqa: E402


def _case(case_id: str = "TC-BASE-001") -> dict:
    for line in (ROOT / "data" / "test_cases.jsonl").read_text(encoding="utf-8").splitlines():
        if json.loads(line)["case_id"] == case_id:
            return CaseSchema.model_validate_json(line).model_dump(mode="json")
    raise AssertionError(case_id)


def test_v1_and_v2_retrievers_emit_trace_and_rule_ids() -> None:
    rules = ROOT / "data" / "knowledge_base" / "rules.jsonl"
    v1 = KnowledgeBaseRetriever(rules, strategy="vector", top_k=2)
    v2 = KnowledgeBaseRetriever(rules, strategy="hybrid", top_k=5)
    query = "SAFETY-BIO-007 危化品 护目镜 通风柜"
    v1_result = v1.search(query)
    v2_result = v2.search(query)
    assert v1_result["strategy"] == "vector"
    assert v2_result["strategy"] == "hybrid"
    assert len(v1_result["retrieved_chunks"]) <= 2
    assert len(v2_result["retrieved_chunks"]) <= 5
    assert "SAFETY-BIO-007" in {chunk["rule_id"] for chunk in v2_result["retrieved_chunks"]}
    assert v2_result["actual_queries"] == [query]


def test_tools_create_and_inspect_real_docx(tmp_path: Path) -> None:
    rules = ROOT / "data" / "knowledge_base" / "rules.jsonl"
    retriever = KnowledgeBaseRetriever(rules, strategy="hybrid", top_k=5)
    runtime = ToolRuntime(ROOT, tmp_path / "run", retriever)
    generated = runtime.generate_docx_plan("一次函数", "八年级", 45, [{"name": "导入"}, {"name": "探究"}])
    inspected = runtime.inspect_docx(generated["file_path"])
    assert generated["status"] == "success"
    assert inspected["status"] == "success"
    assert Path(generated["file_path"]).exists()
    assert len(Document(generated["file_path"]).paragraphs) > 0


def test_checker_attributes_platform_failure_to_delivery(tmp_path: Path) -> None:
    case = _case("TC-ERR-009")
    retriever = KnowledgeBaseRetriever(ROOT / "data" / "knowledge_base" / "rules.jsonl", strategy="hybrid", top_k=5)
    runtime = ToolRuntime(ROOT, tmp_path / "run", retriever, case["pre_conditions"]["fault_injection_config"])
    runtime.search_rules("ADMIN-SEND-001 TOOL-SEND-002")
    result = evaluate_case(case, runtime.whitebox_trace(), "平台交付未确认", root=ROOT, run_dir=tmp_path / "run")
    assert result["diagnosed_failure_stage"] in {"artifact_or_delivery", "tool_call"}

