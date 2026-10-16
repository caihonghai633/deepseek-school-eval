"""Stage 2 数据集契约测试。

测试故意从 JSONL 原文调用 ``TestCaseSchema.model_validate_json``，而不是只验证
生成器内存对象，确保提交到磁盘的每一行都符合 Stage 1 冻结契约。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluator.schemas import TestCaseSchema as CaseSchema  # noqa: E402


CASES_PATH = ROOT / "data" / "test_cases.jsonl"
RULES_PATH = ROOT / "data" / "knowledge_base" / "rules.jsonl"
EXPECTED_CATEGORIES = {
    "基础指令与多轮",
    "RAG与跨会话记忆",
    "工具调用与文件处理",
    "异常输入与冲突恢复",
}


def read_case_lines() -> list[str]:
    """返回非空 JSONL 行，便于每个测试复用同一份磁盘输入。"""

    return [
        line
        for line in CASES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_rule_ids() -> set[str]:
    return {
        json.loads(line)["rule_id"]
        for line in RULES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def test_dataset_has_at_least_50_unique_schema_valid_cases() -> None:
    """每一行都必须能用冻结 Schema 严格解析，且 case_id 全局唯一。"""

    lines = read_case_lines()
    assert len(lines) >= 50

    parsed = [CaseSchema.model_validate_json(line) for line in lines]
    case_ids = [case.case_id for case in parsed]
    assert len(case_ids) == len(set(case_ids))
    assert len(parsed) == 52


def test_all_four_categories_are_balanced() -> None:
    """四类各 13 条，既满足覆盖要求也避免某类样本过少。"""

    counts = Counter(
        CaseSchema.model_validate_json(line).category for line in read_case_lines()
    )
    assert set(counts) == EXPECTED_CATEGORIES
    assert all(count >= 12 for count in counts.values())
    assert counts == {
        "基础指令与多轮": 13,
        "RAG与跨会话记忆": 13,
        "工具调用与文件处理": 13,
        "异常输入与冲突恢复": 13,
    }


def test_expected_recall_rule_ids_exist_in_knowledge_base() -> None:
    """所有用例引用的规则必须能在 rules.jsonl 中定位。"""

    rule_ids = read_rule_ids()
    referenced = {
        rule_id
        for line in read_case_lines()
        for rule_id in CaseSchema.model_validate_json(line)
        .check_criteria.expected_recall_rule_ids
    }
    assert referenced
    assert referenced <= rule_ids


def test_conversation_text_is_not_duplicated() -> None:
    """禁止把同一段对话复制成多个样本。"""

    texts = [
        "\n".join(turn.content for turn in CaseSchema.model_validate_json(line).conversation_turns)
        for line in read_case_lines()
    ]
    assert len(texts) == len(set(texts))


@pytest.mark.parametrize("rule_line", read_rule_ids())
def test_rule_id_format_is_stable(rule_line: str) -> None:
    """规则编号作为白盒证据键，至少保证非空且无空白。"""

    assert rule_line == rule_line.strip()
    assert len(rule_line) >= 5

