---
date: 2026-10-16
project: AE-01_School_Agent_Eval
run_id: run_20261016T161346863743Z_v1_tc-err-013_attempt_1_75539ee1
version: V1
pass_rate: "9.62%"
total_cost_usd: 0.18870678
tags: [ai_eval, agent, school_office]
total_cases: 52
passed_cases: 5
---

# V1 汇总

| 指标 | 值 |
|---|---:|
| 用例数 | 52 |
| 通过数 | 5 |
| 通过率 | 9.62% |
| Prompt tokens | 430236 |
| Completion tokens | 62037 |
| 预估成本 | $0.18870678 |
| 平均耗时 | 13347.835 ms |
| 运行笔记数 | 54 |

## 场景通过情况

| 场景 | 通过 | 总数 |
|---|---:|---:|
| 基础指令与多轮 | 3 | 13 |
| RAG与跨会话记忆 | 0 | 13 |
| 工具调用与文件处理 | 0 | 13 |
| 异常输入与冲突恢复 | 2 | 13 |

## 故障归因

- `artifact_or_delivery`：4
- `rag_retrieval`：42
- `tool_call`：1

## 关联笔记

- [[AE-01_School_Agent_Eval__evaluation_report]]
