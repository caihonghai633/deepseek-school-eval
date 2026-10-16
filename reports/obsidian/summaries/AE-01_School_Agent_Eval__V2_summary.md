---
date: 2026-10-16
project: AE-01_School_Agent_Eval
run_id: run_20261016T162403409133Z_v2_tc-err-013_attempt_1_8e90abb4
version: V2
pass_rate: "5.77%"
total_cost_usd: 0.20477524
tags: [ai_eval, agent, school_office]
total_cases: 52
passed_cases: 3
---

# V2 汇总

| 指标 | 值 |
|---|---:|
| 用例数 | 52 |
| 通过数 | 3 |
| 通过率 | 5.77% |
| Prompt tokens | 484508 |
| Completion tokens | 62830 |
| 预估成本 | $0.20477524 |
| 平均耗时 | 12255.053 ms |
| 运行笔记数 | 52 |

## 场景通过情况

| 场景 | 通过 | 总数 |
|---|---:|---:|
| 基础指令与多轮 | 0 | 13 |
| RAG与跨会话记忆 | 1 | 13 |
| 工具调用与文件处理 | 0 | 13 |
| 异常输入与冲突恢复 | 2 | 13 |

## 故障归因

- `precondition_or_memory`：1
- `artifact_or_delivery`：7
- `rag_retrieval`：37
- `tool_call`：4

## 关联笔记

- [[AE-01_School_Agent_Eval__evaluation_report]]
