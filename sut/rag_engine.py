"""学校办公知识库检索实现。

V1 使用基于词项/字符 n-gram 的向量式相似度并限制低 ``top_k``；V2 使用
``rank_bm25`` 与向量分数加权融合。每次检索都返回实际查询词、策略、排序分数
和 rule_id，供白盒 Trace 与离线故障归因使用。所有路径由 pathlib.Path 处理。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    from rank_bm25 import BM25Okapi
except ImportError:  # pragma: no cover - 最小安装环境的离线回退
    BM25Okapi = None  # type: ignore[assignment,misc]


_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """同时保留英文编号、中文单字和常见词，兼顾 rule_id 与中文短语。"""

    return _TOKEN_RE.findall(text.lower().strip())


@dataclass(frozen=True)
class RuleDocument:
    """知识库中的一条结构化规则。"""

    rule_id: str
    title: str
    department: str
    keywords: tuple[str, ...]
    content: str
    effective_date: str | None = None

    @property
    def searchable_text(self) -> str:
        return " ".join(
            [self.rule_id, self.title, self.department, *self.keywords, self.content]
        )


class KnowledgeBaseRetriever:
    """可配置的 V1/V2 规则检索器。"""

    def __init__(
        self,
        rules_path: str | Path,
        *,
        strategy: str = "vector",
        top_k: int = 2,
        score_threshold: float = 0.0,
        vector_weight: float = 0.6,
        bm25_weight: float = 0.4,
        synonym_expansion: bool = False,
        exact_rule_boost: float = 8.0,
    ) -> None:
        self.rules_path = Path(rules_path)
        self.strategy = "hybrid" if strategy == "hybrid" else "vector"
        self.top_k = max(1, int(top_k))
        self.score_threshold = float(score_threshold)
        self.vector_weight = float(vector_weight)
        self.bm25_weight = float(bm25_weight)
        self.synonym_expansion = bool(synonym_expansion)
        self.exact_rule_boost = float(exact_rule_boost)
        self.synonyms: dict[str, tuple[str, ...]] = {
            "危化品": ("危险化学品", "挥发性试剂", "试剂防护", "通风柜"),
            "危化": ("危险化学品", "化学实验", "护目镜", "手套"),
            "半月板": ("康复生", "伤病学生", "见习", "跑跳", "对抗"),
            "雾霾": ("pm2.5", "空气质量", "户外活动", "转室内"),
            "回执": ("receipt_id", "accepted", "delivered", "投递状态"),
            "脱敏": ("学生隐私", "最小必要", "学生编号"),
            "部门编码": ("department_code", "JWC", "ZZC", "GRADE"),
            "教案格式": ("一级标题", "课时", "章节顺序", "docx"),
        }
        self.rules = self._load_rules()
        self._documents = [rule.searchable_text for rule in self.rules]
        self._tokenized_documents = [tokenize(text) for text in self._documents]
        self._bm25 = (
            BM25Okapi(self._tokenized_documents) if BM25Okapi and self._documents else None
        )

    def _load_rules(self) -> list[RuleDocument]:
        import json

        rules: list[RuleDocument] = []
        for line_number, line in enumerate(
            self.rules_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            payload = json.loads(line)
            required = ("rule_id", "title", "department", "keywords", "content")
            missing = [field for field in required if field not in payload]
            if missing:
                raise ValueError(f"规则源第 {line_number} 行缺少字段: {missing}")
            rules.append(
                RuleDocument(
                    rule_id=str(payload["rule_id"]),
                    title=str(payload["title"]),
                    department=str(payload["department"]),
                    keywords=tuple(str(item) for item in payload["keywords"]),
                    content=str(payload["content"]),
                    effective_date=payload.get("effective_date"),
                )
            )
        if not rules:
            raise ValueError(f"知识库为空: {self.rules_path}")
        return rules

    @staticmethod
    def _vector_score(query_tokens: Iterable[str], document: RuleDocument) -> float:
        """用加权词项覆盖率近似向量余弦，显式奖励 rule_id/关键词命中。"""

        query = set(query_tokens)
        if not query:
            return 0.0
        title_tokens = set(tokenize(document.title))
        keyword_tokens = {token for keyword in document.keywords for token in tokenize(keyword)}
        doc_tokens = set(tokenize(document.searchable_text))
        overlap = len(query & doc_tokens) / math.sqrt(len(query) * max(len(doc_tokens), 1))
        title_overlap = len(query & title_tokens) / max(len(query), 1)
        keyword_overlap = len(query & keyword_tokens) / max(len(query), 1)
        exact_id = 1.0 if document.rule_id.lower() in " ".join(query_tokens).lower() else 0.0
        return min(1.0, overlap + 0.35 * title_overlap + 0.45 * keyword_overlap + exact_id)

    def _expand_query(self, query: str) -> str:
        """V3 同义词扩展，保留原始词以便白盒 Trace 审计。"""

        if not self.synonym_expansion:
            return query
        additions: list[str] = []
        lowered = query.lower()
        for source, synonyms in self.synonyms.items():
            if source.lower() in lowered or any(item.lower() in lowered for item in synonyms):
                additions.extend(synonyms)
        return " ".join([query, *dict.fromkeys(additions)])

    def _fallback_bm25(self, query_tokens: list[str]) -> list[float]:
        """rank-bm25 缺失时的确定性词频近似，保持相同输出接口。"""

        query = set(query_tokens)
        scores: list[float] = []
        for document_tokens in self._tokenized_documents:
            term_frequency = sum(document_tokens.count(token) for token in query)
            denominator = len(document_tokens) + 0.5 * len(query)
            scores.append(term_frequency / max(denominator, 1.0))
        maximum = max(scores, default=0.0)
        return [score / maximum if maximum else 0.0 for score in scores]

    def search(self, query: str, *, top_k: int | None = None) -> dict[str, Any]:
        """检索规则并返回可直接写入 ``WhiteboxTraceSchema.rag`` 的字典。"""

        original_query = " ".join(str(query).split())
        actual_query = self._expand_query(original_query)
        query_tokens = tokenize(actual_query)
        vector_scores = [self._vector_score(query_tokens, rule) for rule in self.rules]
        if self._bm25 is not None and query_tokens:
            raw_bm25 = self._bm25.get_scores(query_tokens).tolist()
            maximum = max(raw_bm25, default=0.0)
            bm25_scores = [score / maximum if maximum else 0.0 for score in raw_bm25]
        else:
            bm25_scores = self._fallback_bm25(query_tokens)

        if self.strategy == "hybrid":
            denominator = max(self.vector_weight + self.bm25_weight, 1e-9)
            scores = [
                (self.vector_weight * vector + self.bm25_weight * bm25) / denominator
                for vector, bm25 in zip(vector_scores, bm25_scores)
            ]
        else:
            scores = vector_scores

        requested_top_k = max(1, int(top_k or self.top_k))
        exact_rule_ids = {
            rule.rule_id.lower()
            for rule in self.rules
            if rule.rule_id.lower() in original_query.lower()
        }
        if exact_rule_ids:
            scores = [
                score + (self.exact_rule_boost if rule.rule_id.lower() in exact_rule_ids else 0.0)
                for score, rule in zip(scores, self.rules)
            ]
        ranked = sorted(
            enumerate(self.rules),
            key=lambda item: (scores[item[0]], vector_scores[item[0]], item[1].rule_id),
            reverse=True,
        )
        selected: list[dict[str, Any]] = []
        for index, rule in ranked:
            score = float(scores[index])
            if score < self.score_threshold and selected:
                continue
            if score <= 0 and selected:
                continue
            selected.append(
                {
                    "chunk_id": f"kb-{rule.rule_id.lower()}-{len(selected) + 1:02d}",
                    "rule_id": rule.rule_id,
                    "text": rule.content,
                    "score": round(score, 6),
                    "rank": len(selected) + 1,
                    "retrieval_source": self.strategy,
                }
            )
            if len(selected) >= requested_top_k:
                break

        return {
            "actual_queries": [original_query, actual_query] if actual_query != original_query else [original_query],
            "strategy": self.strategy,
            "top_k": requested_top_k,
            "retrieved_chunks": selected,
        }


def from_config(config: dict[str, Any], root: str | Path) -> KnowledgeBaseRetriever:
    """根据 YAML 配置创建检索器。"""

    root_path = Path(root)
    rag_config = config.get("rag", {})
    weights = rag_config.get("hybrid_weights", {})
    return KnowledgeBaseRetriever(
        root_path / "data" / "knowledge_base" / "rules.jsonl",
        strategy=str(rag_config.get("strategy", "vector")),
        top_k=int(rag_config.get("top_k", 2)),
        score_threshold=float(rag_config.get("score_threshold", 0.0)),
        vector_weight=float(weights.get("vector", 1.0)),
        bm25_weight=float(weights.get("bm25", 0.0)),
        synonym_expansion=bool(rag_config.get("synonym_expansion", False)),
        exact_rule_boost=float(rag_config.get("exact_rule_boost", 8.0)),
    )

