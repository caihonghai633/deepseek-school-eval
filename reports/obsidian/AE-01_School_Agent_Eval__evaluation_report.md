---
date: 2026-10-16
project: AE-01_School_Agent_Eval
run_id: analysis-20261016T165732Z
version: ALL
pass_rate: n/a
total_cost_usd: 0.40183308
tags: [ai_eval, agent, school_office]
source: "D:/Users/87702/Desktop/测评deepseekapi/reports/evaluation_report.md"
---

# AE-01 评测报告

# AE-01 学校办公 Agent 技术评测报告

## 1. 结论

当前版本**未达到直接上线要求**。

Stage 3 全量 52 条用例的通过率为：V1 **5/52（9.62%）**，V2 **3/52（5.77%）**。V2 虽然降低了 RAG 归因失败数量，但总体通过数比 V1 少 2 条，且工具调用和产物/交付归因失败增加。学校办公场景涉及真实教学文档、学生安全和教务平台回执，当前结果不足以支持生产上线；必须先完成工具参数闸门、交付重试/回执闭环和多轮状态管理的修复，再进行完整回归。

本报告的所有基线数字均来自 `reports/stage3_comparison.json`；V3 局部修复证据单独记录在 `reports/v3_validation.json` 和 `reports/v3_validation.log`，不混入 Stage 3 基线统计。

## 2. Stage 3 基线数据

| 版本 | 用例数 | 通过数 | 通过率 | Prompt Token | Completion Token | 预估成本（USD） | 平均耗时（ms） |
|---|---:|---:|---:|---:|---:|---:|---:|
| V1 | 52 | 5 | 9.62% | 430,236 | 62,037 | 0.18870678 | 13,347.835 |
| V2 | 52 | 3 | 5.77% | 484,508 | 62,830 | 0.20477524 | 12,255.053 |

V2 相对 V1 的变化为：通过率 **-3.85 个百分点**、通过数 **-2**、预估成本 **+0.01606846 USD（约 +8.52%）**，平均耗时 **-1,092.782 ms（约 -8.19%）**。因此，Token/成本确实上升；平均耗时在本批实验中反而下降，不能据此声称 V2 牺牲了实测延迟。

### 2.1 分场景通过情况

| 场景 | V1 通过 | V1 通过率 | V2 通过 | V2 通过率 |
|---|---:|---:|---:|---:|
| 基础指令与多轮 | 3/13 | 23.08% | 0/13 | 0% |
| RAG 与跨会话记忆 | 0/13 | 0% | 1/13 | 7.69% |
| 工具调用与文件处理 | 0/13 | 0% | 0/13 | 0% |
| 异常输入与冲突恢复 | 2/13 | 15.38% | 2/13 | 15.38% |

### 2.2 失败归因分布

| 归因标签 | V1 | V1 占 52 条 | V2 | V2 占 52 条 |
|---|---:|---:|---:|---:|
| `rag_retrieval` | 42 | 80.77% | 37 | 71.15% |
| `tool_call` | 1 | 1.92% | 4 | 7.69% |
| `artifact_or_delivery` | 4 | 7.69% | 7 | 13.46% |
| `precondition_or_memory` | 0 | 0% | 1 | 1.92% |

V2 的变化与 `stage3_comparison.json` 一致：RAG 归因失败 **-5 条**，但工具调用 **+3 条**、产物/交付 **+3 条**，并新增 1 条记忆前置失败；最终总体成功率没有改善。

## 3. 失败主要集中在哪些场景

1. **RAG 召回漏配是第一主因。** V1 有 42 条、V2 有 37 条 `rag_retrieval` 失败，占比分别为 80.77% 和 71.15%。V2 的混合检索确实改善了召回，但仍未达到规则编号、专业术语和多规则合并场景的可用稳定性。
2. **工具参数与顺序约束不足。** `tool_call` 失败从 V1 的 1 条增至 V2 的 4 条（7.69%）。这说明扩大召回和上下文后，模型仍可能遗漏部门编码、文件路径、收件人或必需调用顺序；该类错误应由工具边界 Schema 拦截，而不是依赖模型自觉纠正。
3. **真实文件/平台交付链路脆弱。** `artifact_or_delivery` 失败从 4 条增至 7 条（13.46%）。即使文本生成看似成功，真实 `.docx` 的章节、文件存在性、平台回执和 receipt_id 仍可能失败；这类风险直接影响学校办公结果，不能以“模型已回复完成”替代交付证据。

按场景看，工具调用与文件处理场景 V1/V2 均为 **0/13**，是最明确的上线阻断项；RAG 与跨会话记忆场景仅 V2 通过 1/13，也说明召回与记忆闭环仍不稳定。

## 4. 哪些结论有直接证据，哪些仍是假设

### 4.1 有直接证据的结论

- 通过率、各场景通过数、Token、成本、平均耗时和归因计数均由 `stage3_comparison.json` 汇总而来。
- `whitebox_trace` 中的实际查询词、召回 `rule_id`、工具调用顺序/参数/返回状态、真实 `.docx` 路径和平台回执，是确定性检查器可复核的证据。
- `rag_retrieval` 只有在期望 `rule_id` 未出现在实际召回片段时才成立；`tool_call` 可由缺参、顺序错误或工具返回 `validation_error/runtime_error` 复核；`artifact_or_delivery` 可由真实文件解析和回执状态复核。这些归因不是把所有失败归咎于模型。
- V2 相比 V1 的 RAG 归因失败减少 5 条，但工具与交付归因分别增加 3 条，数据支持“局部改善、整体未达标”的判断。

### 4.2 仍属于假设、需要后续实验验证的结论

- 模型生成文本是否教学上“足够好”、目标表述是否有较高 pedagogical quality，当前确定性检查器不能证明；非空输出和章节存在不等于内容质量达标。
- V2 的失败增加是否由更长上下文、工具选择熵或提示词变化直接导致，Stage 3 汇总数据只能显示相关性，不能证明因果关系。
- 单次实验中的平均耗时差异不能代表长期 SLA；需在相同并发、网络和重试条件下做重复测量。

## 5. 优化后改善了什么，又牺牲了什么

V2 的混合检索和更高 `top_k` 对 RAG 有实质但有限的改善：`rag_retrieval` 从 42 条降至 37 条，且 RAG 场景出现 1 条通过记录。但它带来了 54,272 个额外 Prompt Token、0.01606846 USD 的批次成本增量（约 8.52%），并没有转化为总体成功率提升。

下游代价也被暴露出来：工具调用失败从 1 条升至 4 条，产物/交付失败从 4 条升至 7 条。也就是说，V2 的召回增强把更多规则带入决策空间，却没有同步固化工具参数校验和平台交付闭环。耗时方面，本批 V2 平均值较低，不能声称有实测耗时牺牲；但成本上升和下游脆弱性已经是明确的工程代价。

## 6. 下一步最值得投入资源的三个问题

1. **固化工具调用参数 Schema 校验。** 在 Agent 与工具函数之间增加版本化、拒绝未知/缺失字段的边界校验，尤其锁定 `department_code`、`recipient`、`file_path`、`title`、`metadata`，并用调用顺序状态机阻止越过 `.docx` 校验直接投递。
2. **建立交付重试与回执闭环。** 平台 502/超时采用有上限的指数退避；每次尝试写入 Trace；重试耗尽时保留并校验本地文件，返回明确的 `degraded_local_saved`，禁止伪造 `accepted/delivered` 或 receipt_id。
3. **优化多轮对话状态机与上下文管理。** 将年级、课题、课时、教学目标、已确认规则和记忆读写状态结构化保存；每轮修改只更新明确字段，并在最终工具调用前重新验证，降低多轮遗忘和规则冲突风险。

## 7. 上线门槛与复验要求

在重新评估前，至少应满足：全量 52 条用例重新跑通；工具调用与文件处理场景不再出现无回执成功；所有期望规则 ID 均有 Trace 证据；真实 `.docx` 章节和敏感词检查通过；失败重跑追加写入且不覆盖原始失败记录。V3 局部修复已按上述契约保留独立证据，完整上线结论仍需全量 V3 回归后再作出。

<!-- AE-01 OBSIDIAN ARTIFACT LINKS START -->
## 原始记录与产物链接

- [全部原始 JSONL](../../runs/raw_results.jsonl)
- `V1` / `TC-BASE-001` / `run_20261016T154959564531Z_v1_tc-base-001_attempt_1_50c63c15`：[原始记录](../../runs/run_20261016T154959564531Z_v1_tc-base-001_attempt_1_50c63c15/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T154959564531Z_v1_tc-base-001_attempt_1_50c63c15/artifacts/七年级_光合作用.docx)
- `V1` / `TC-BASE-001` / `run_20261016T155128076789Z_v1_tc-base-001_attempt_1_a2bfbac0`：[原始记录](../../runs/run_20261016T155128076789Z_v1_tc-base-001_attempt_1_a2bfbac0/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T155128076789Z_v1_tc-base-001_attempt_1_a2bfbac0/artifacts/七年级_普通班_光合作用.docx)
- `V2` / `TC-BASE-001` / `run_20261016T155532449522Z_v2_tc-base-001_attempt_1_2dce517f`：[原始记录](../../runs/run_20261016T155532449522Z_v2_tc-base-001_attempt_1_2dce517f/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T155532449522Z_v2_tc-base-001_attempt_1_2dce517f/artifacts/七年级_七年级生物_光合作用_教案.docx)
- `V1` / `TC-BASE-001` / `run_20261016T155954430793Z_v1_tc-base-001_attempt_1_b8f2b59b`：[原始记录](../../runs/run_20261016T155954430793Z_v1_tc-base-001_attempt_1_b8f2b59b/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T155954430793Z_v1_tc-base-001_attempt_1_b8f2b59b/artifacts/七年级_普通班_光合作用.docx)
- `V1` / `TC-BASE-002` / `run_20261016T160010826778Z_v1_tc-base-002_attempt_1_33e4bcd4`：[原始记录](../../runs/run_20261016T160010826778Z_v1_tc-base-002_attempt_1_33e4bcd4/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160010826778Z_v1_tc-base-002_attempt_1_33e4bcd4/artifacts/八年级_八年级_数学_一次函数_docx.docx)
- `V1` / `TC-BASE-003` / `run_20261016T160028895612Z_v1_tc-base-003_attempt_1_f0a804dc`：[原始记录](../../runs/run_20261016T160028895612Z_v1_tc-base-003_attempt_1_f0a804dc/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160028895612Z_v1_tc-base-003_attempt_1_f0a804dc/artifacts/五年级_草船借箭_教案_家长开放日版.docx)
- `V1` / `TC-BASE-004` / `run_20261016T160046453786Z_v1_tc-base-004_attempt_1_c836e258`：[原始记录](../../runs/run_20261016T160046453786Z_v1_tc-base-004_attempt_1_c836e258/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160046453786Z_v1_tc-base-004_attempt_1_c836e258/artifacts/六年级_少年闰土.docx)
- `V1` / `TC-BASE-005` / `run_20261016T160110445050Z_v1_tc-base-005_attempt_1_882e05c6`：[原始记录](../../runs/run_20261016T160110445050Z_v1_tc-base-005_attempt_1_882e05c6/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160110445050Z_v1_tc-base-005_attempt_1_882e05c6/artifacts/七年级_七年级体育_篮球传接球_45分钟.docx)
- `V1` / `TC-BASE-006` / `run_20261016T160129989883Z_v1_tc-base-006_attempt_1_b3754fc5`：[原始记录](../../runs/run_20261016T160129989883Z_v1_tc-base-006_attempt_1_b3754fc5/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160129989883Z_v1_tc-base-006_attempt_1_b3754fc5/artifacts/九年级_探究酶的作用_过氧化氢酶催化分解实验.docx)
- `V1` / `TC-BASE-007` / `run_20261016T160146961531Z_v1_tc-base-007_attempt_1_95a1b12d`：[原始记录](../../runs/run_20261016T160146961531Z_v1_tc-base-007_attempt_1_95a1b12d/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160146961531Z_v1_tc-base-007_attempt_1_95a1b12d/artifacts/四年级_观潮_阅读教案_按时间顺序梳理景象变化.docx)
- `V1` / `TC-BASE-008` / `run_20261016T160204533726Z_v1_tc-base-008_attempt_1_c3653260`：[原始记录](../../runs/run_20261016T160204533726Z_v1_tc-base-008_attempt_1_c3653260/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160204533726Z_v1_tc-base-008_attempt_1_c3653260/artifacts/八年级_勾股定理.docx)
- `V1` / `TC-BASE-009` / `run_20261016T160223328264Z_v1_tc-base-009_attempt_1_4aeefc3b`：[原始记录](../../runs/run_20261016T160223328264Z_v1_tc-base-009_attempt_1_4aeefc3b/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160223328264Z_v1_tc-base-009_attempt_1_4aeefc3b/artifacts/七年级_网络安全与同伴互助.docx)
- `V1` / `TC-BASE-010` / `run_20261016T160238096484Z_v1_tc-base-010_attempt_1_15883e57`：[原始记录](../../runs/run_20261016T160238096484Z_v1_tc-base-010_attempt_1_15883e57/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160238096484Z_v1_tc-base-010_attempt_1_15883e57/artifacts/六年级_校园雨水统计_六年级科学与数学融合课.docx)
- `V1` / `TC-BASE-011` / `run_20261016T160256679012Z_v1_tc-base-011_attempt_1_a4f74c78`：[原始记录](../../runs/run_20261016T160256679012Z_v1_tc-base-011_attempt_1_a4f74c78/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160256679012Z_v1_tc-base-011_attempt_1_a4f74c78/artifacts/七年级_丝绸之路.docx)
- `V1` / `TC-BASE-012` / `run_20261016T160312132428Z_v1_tc-base-012_attempt_1_8a24c190`：[原始记录](../../runs/run_20261016T160312132428Z_v1_tc-base-012_attempt_1_8a24c190/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160312132428Z_v1_tc-base-012_attempt_1_8a24c190/artifacts/九年级_金属的化学性质.docx)
- `V1` / `TC-BASE-013` / `run_20261016T160326351037Z_v1_tc-base-013_attempt_1_d6a2f93d`：[原始记录](../../runs/run_20261016T160326351037Z_v1_tc-base-013_attempt_1_d6a2f93d/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160326351037Z_v1_tc-base-013_attempt_1_d6a2f93d/artifacts/八年级_八年级体育与健康_雾霾天气室内低强度循环训练.docx)
- `V1` / `TC-RAG-001` / `run_20261016T160340379721Z_v1_tc-rag-001_attempt_1_86a7cff4`：[原始记录](../../runs/run_20261016T160340379721Z_v1_tc-rag-001_attempt_1_86a7cff4/raw_result.json)
- `V1` / `TC-RAG-002` / `run_20261016T160349750224Z_v1_tc-rag-002_attempt_1_2a28fe0f`：[原始记录](../../runs/run_20261016T160349750224Z_v1_tc-rag-002_attempt_1_2a28fe0f/raw_result.json)
- `V1` / `TC-RAG-003` / `run_20261016T160359115438Z_v1_tc-rag-003_attempt_1_87e14a90`：[原始记录](../../runs/run_20261016T160359115438Z_v1_tc-rag-003_attempt_1_87e14a90/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160359115438Z_v1_tc-rag-003_attempt_1_87e14a90/artifacts/初中一年级_体育课见习教案_半月板康复生安全见习安排.docx)
- `V1` / `TC-RAG-004` / `run_20261016T160413740414Z_v1_tc-rag-004_attempt_1_17ee4582`：[原始记录](../../runs/run_20261016T160413740414Z_v1_tc-rag-004_attempt_1_17ee4582/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160413740414Z_v1_tc-rag-004_attempt_1_17ee4582/artifacts/七年级_七年级地理_地球的公转与四季变化.docx)
- `V1` / `TC-RAG-005` / `run_20261016T160426938190Z_v1_tc-rag-005_attempt_1_3fe065be`：[原始记录](../../runs/run_20261016T160426938190Z_v1_tc-rag-005_attempt_1_3fe065be/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160426938190Z_v1_tc-rag-005_attempt_1_3fe065be/artifacts/七年级_七年级_道德与法治_学会尊重他人.docx)
- `V1` / `TC-RAG-006` / `run_20261016T160446672707Z_v1_tc-rag-006_attempt_1_2081dd73`：[原始记录](../../runs/run_20261016T160446672707Z_v1_tc-rag-006_attempt_1_2081dd73/raw_result.json)
- `V1` / `TC-RAG-007` / `run_20261016T160456936027Z_v1_tc-rag-007_attempt_1_071be6d6`：[原始记录](../../runs/run_20261016T160456936027Z_v1_tc-rag-007_attempt_1_071be6d6/raw_result.json)
- `V1` / `TC-RAG-008` / `run_20261016T160507065046Z_v1_tc-rag-008_attempt_1_a7116959`：[原始记录](../../runs/run_20261016T160507065046Z_v1_tc-rag-008_attempt_1_a7116959/raw_result.json)
- `V1` / `TC-RAG-009` / `run_20261016T160520039623Z_v1_tc-rag-009_attempt_1_a2362c1e`：[原始记录](../../runs/run_20261016T160520039623Z_v1_tc-rag-009_attempt_1_a2362c1e/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160520039623Z_v1_tc-rag-009_attempt_1_a2362c1e/artifacts/九年级_酸碱溶液配制与性质探究_安全实验课.docx)
- `V1` / `TC-RAG-010` / `run_20261016T160536003601Z_v1_tc-rag-010_attempt_1_71aef6a9`：[原始记录](../../runs/run_20261016T160536003601Z_v1_tc-rag-010_attempt_1_71aef6a9/raw_result.json)
- `V1` / `TC-RAG-011` / `run_20261016T160547528371Z_v1_tc-rag-011_attempt_1_9a29395d`：[原始记录](../../runs/run_20261016T160547528371Z_v1_tc-rag-011_attempt_1_9a29395d/raw_result.json)
- `V1` / `TC-RAG-012` / `run_20261016T160553822746Z_v1_tc-rag-012_attempt_1_9e51e8f6`：[原始记录](../../runs/run_20261016T160553822746Z_v1_tc-rag-012_attempt_1_9e51e8f6/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160553822746Z_v1_tc-rag-012_attempt_1_9e51e8f6/artifacts/八年级_雾霾天室内科学课教案_含危化品防护与脱敏处理.docx)
- `V1` / `TC-RAG-013` / `run_20261016T160613302324Z_v1_tc-rag-013_attempt_1_56dc61eb`：[原始记录](../../runs/run_20261016T160613302324Z_v1_tc-rag-013_attempt_1_56dc61eb/raw_result.json)
- `V1` / `TC-TOOL-001` / `run_20261016T160623542858Z_v1_tc-tool-001_attempt_1_6975b345`：[原始记录](../../runs/run_20261016T160623542858Z_v1_tc-tool-001_attempt_1_6975b345/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160623542858Z_v1_tc-tool-001_attempt_1_6975b345/artifacts/七年级_七年级数学_一元一次方程.docx)
- `V1` / `TC-TOOL-002` / `run_20261016T160634218411Z_v1_tc-tool-002_attempt_1_1e9be1c9`：[原始记录](../../runs/run_20261016T160634218411Z_v1_tc-tool-002_attempt_1_1e9be1c9/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160634218411Z_v1_tc-tool-002_attempt_1_1e9be1c9/artifacts/八年级_八年级物理_浮力_教案.docx)
  - [真实 .docx 产物](../../runs/run_20261016T160634218411Z_v1_tc-tool-002_attempt_1_1e9be1c9/artifacts/八年级_八年级_物理_浮力.docx)
- `V1` / `TC-TOOL-003` / `run_20261016T160648916042Z_v1_tc-tool-003_attempt_1_df8728e9`：[原始记录](../../runs/run_20261016T160648916042Z_v1_tc-tool-003_attempt_1_df8728e9/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160648916042Z_v1_tc-tool-003_attempt_1_df8728e9/artifacts/七年级_同伴冲突调解.docx)
- `V1` / `TC-TOOL-004` / `run_20261016T160701613145Z_v1_tc-tool-004_attempt_1_43042e9f`：[原始记录](../../runs/run_20261016T160701613145Z_v1_tc-tool-004_attempt_1_43042e9f/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160701613145Z_v1_tc-tool-004_attempt_1_43042e9f/artifacts/七年级_七年级英语教案_Unit_1_My_Name_s_Gina_Section_A.docx)
- `V1` / `TC-TOOL-005` / `run_20261016T160714866796Z_v1_tc-tool-005_attempt_1_e61270b0`：[原始记录](../../runs/run_20261016T160714866796Z_v1_tc-tool-005_attempt_1_e61270b0/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160714866796Z_v1_tc-tool-005_attempt_1_e61270b0/artifacts/九年级_金属的化学性质.docx)
- `V1` / `TC-TOOL-006` / `run_20261016T160730956762Z_v1_tc-tool-006_attempt_1_567a9f73`：[原始记录](../../runs/run_20261016T160730956762Z_v1_tc-tool-006_attempt_1_567a9f73/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160730956762Z_v1_tc-tool-006_attempt_1_567a9f73/artifacts/七年级_春.docx)
  - [真实 .docx 产物](../../runs/run_20261016T160730956762Z_v1_tc-tool-006_attempt_1_567a9f73/artifacts/七年级_整式乘法.docx)
- `V1` / `TC-TOOL-007` / `run_20261016T160745147296Z_v1_tc-tool-007_attempt_1_da5fbb74`：[原始记录](../../runs/run_20261016T160745147296Z_v1_tc-tool-007_attempt_1_da5fbb74/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160745147296Z_v1_tc-tool-007_attempt_1_da5fbb74/artifacts/高中二年级_教师版教案_生物化学单元_试剂配制与安全操作.docx)
  - [真实 .docx 产物](../../runs/run_20261016T160745147296Z_v1_tc-tool-007_attempt_1_da5fbb74/artifacts/高中二年级_学生版实验提示单_试剂配制与安全操作.docx)
- `V1` / `TC-TOOL-008` / `run_20261016T160802407142Z_v1_tc-tool-008_attempt_1_91854288`：[原始记录](../../runs/run_20261016T160802407142Z_v1_tc-tool-008_attempt_1_91854288/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160802407142Z_v1_tc-tool-008_attempt_1_91854288/artifacts/八年级_八年级生物实验教案_观察植物细胞的结构.docx)
- `V1` / `TC-TOOL-009` / `run_20261016T160815614277Z_v1_tc-tool-009_attempt_1_4556d1c8`：[原始记录](../../runs/run_20261016T160815614277Z_v1_tc-tool-009_attempt_1_4556d1c8/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160815614277Z_v1_tc-tool-009_attempt_1_4556d1c8/artifacts/初中一年级_班会教案_班级活动安全与过敏应急安排_脱敏版.docx)
- `V1` / `TC-TOOL-010` / `run_20261016T160841166179Z_v1_tc-tool-010_attempt_1_635bd76d`：[原始记录](../../runs/run_20261016T160841166179Z_v1_tc-tool-010_attempt_1_635bd76d/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160841166179Z_v1_tc-tool-010_attempt_1_635bd76d/artifacts/七年级_校园雨水统计_v2.docx)
- `V1` / `TC-TOOL-011` / `run_20261016T160853472520Z_v1_tc-tool-011_attempt_1_db30fe52`：[原始记录](../../runs/run_20261016T160853472520Z_v1_tc-tool-011_attempt_1_db30fe52/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160853472520Z_v1_tc-tool-011_attempt_1_db30fe52/artifacts/九年级_电路故障分析.docx)
- `V1` / `TC-TOOL-012` / `run_20261016T160905697609Z_v1_tc-tool-012_attempt_1_affd0498`：[原始记录](../../runs/run_20261016T160905697609Z_v1_tc-tool-012_attempt_1_affd0498/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160905697609Z_v1_tc-tool-012_attempt_1_affd0498/artifacts/八年级_改革开放.docx)
- `V1` / `TC-TOOL-013` / `run_20261016T160917749338Z_v1_tc-tool-013_attempt_1_58e98d44`：[原始记录](../../runs/run_20261016T160917749338Z_v1_tc-tool-013_attempt_1_58e98d44/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160917749338Z_v1_tc-tool-013_attempt_1_58e98d44/artifacts/九年级_九年级化学酸碱实验教案.docx)
- `V1` / `TC-ERR-001` / `run_20261016T160930358955Z_v1_tc-err-001_attempt_1_9c5f9e12`：[原始记录](../../runs/run_20261016T160930358955Z_v1_tc-err-001_attempt_1_9c5f9e12/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160930358955Z_v1_tc-err-001_attempt_1_9c5f9e12/artifacts/五年级_分数乘法.docx)
- `V1` / `TC-ERR-002` / `run_20261016T160942301632Z_v1_tc-err-002_attempt_1_043fd5a2`：[原始记录](../../runs/run_20261016T160942301632Z_v1_tc-err-002_attempt_1_043fd5a2/raw_result.json)
- `V1` / `TC-ERR-003` / `run_20261016T160948203570Z_v1_tc-err-003_attempt_1_a7dc86eb`：[原始记录](../../runs/run_20261016T160948203570Z_v1_tc-err-003_attempt_1_a7dc86eb/raw_result.json)
- `V1` / `TC-ERR-004` / `run_20261016T160954917445Z_v1_tc-err-004_attempt_1_e4c2f881`：[原始记录](../../runs/run_20261016T160954917445Z_v1_tc-err-004_attempt_1_e4c2f881/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T160954917445Z_v1_tc-err-004_attempt_1_e4c2f881/artifacts/九年级_九年级生物实验课_挥发性试剂观察实验_教师现场指导版.docx)
- `V1` / `TC-ERR-005` / `run_20261016T161005994544Z_v1_tc-err-005_attempt_1_2f45fc49`：[原始记录](../../runs/run_20261016T161005994544Z_v1_tc-err-005_attempt_1_2f45fc49/raw_result.json)
- `V1` / `TC-ERR-006` / `run_20261016T161240280282Z_v1_tc-err-006_attempt_1_c8519872`：[原始记录](../../runs/run_20261016T161240280282Z_v1_tc-err-006_attempt_1_c8519872/raw_result.json)
- `V1` / `TC-ERR-007` / `run_20261016T161245678340Z_v1_tc-err-007_attempt_1_140f5bab`：[原始记录](../../runs/run_20261016T161245678340Z_v1_tc-err-007_attempt_1_140f5bab/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161245678340Z_v1_tc-err-007_attempt_1_140f5bab/artifacts/八年级_八年级实验课材料清单_含无乳胶手套要求.docx)
- `V1` / `TC-ERR-008` / `run_20261016T161258975203Z_v1_tc-err-008_attempt_1_9906ab56`：[原始记录](../../runs/run_20261016T161258975203Z_v1_tc-err-008_attempt_1_9906ab56/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161258975203Z_v1_tc-err-008_attempt_1_9906ab56/artifacts/七年级_七年级语文_春_教学设计.docx)
- `V1` / `TC-ERR-009` / `run_20261016T161313338362Z_v1_tc-err-009_attempt_1_e75bf962`：[原始记录](../../runs/run_20261016T161313338362Z_v1_tc-err-009_attempt_1_e75bf962/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161313338362Z_v1_tc-err-009_attempt_1_e75bf962/artifacts/九年级_九年级_化学_质量守恒定律.docx)
  - [真实 .docx 产物](../../runs/run_20261016T161313338362Z_v1_tc-err-009_attempt_1_e75bf962/artifacts/九年级_化学_质量守恒定律.docx)
- `V1` / `TC-ERR-010` / `run_20261016T161325551886Z_v1_tc-err-010_attempt_1_675bc618`：[原始记录](../../runs/run_20261016T161325551886Z_v1_tc-err-010_attempt_1_675bc618/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161325551886Z_v1_tc-err-010_attempt_1_675bc618/artifacts/八年级_八年级数学教案_一次函数的图象与性质.docx)
- `V1` / `TC-ERR-011` / `run_20261016T161336278513Z_v1_tc-err-011_attempt_1_bddaca8f`：[原始记录](../../runs/run_20261016T161336278513Z_v1_tc-err-011_attempt_1_bddaca8f/raw_result.json)
- `V1` / `TC-ERR-012` / `run_20261016T161341039874Z_v1_tc-err-012_attempt_1_c197a95f`：[原始记录](../../runs/run_20261016T161341039874Z_v1_tc-err-012_attempt_1_c197a95f/raw_result.json)
- `V1` / `TC-ERR-013` / `run_20261016T161346863743Z_v1_tc-err-013_attempt_1_75539ee1`：[原始记录](../../runs/run_20261016T161346863743Z_v1_tc-err-013_attempt_1_75539ee1/raw_result.json)
- `V2` / `TC-BASE-002` / `run_20261016T161352464620Z_v2_tc-base-002_attempt_1_07d696f4`：[原始记录](../../runs/run_20261016T161352464620Z_v2_tc-base-002_attempt_1_07d696f4/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161352464620Z_v2_tc-base-002_attempt_1_07d696f4/artifacts/八年级_一次函数.docx)
- `V2` / `TC-BASE-003` / `run_20261016T161407163819Z_v2_tc-base-003_attempt_1_79acbb4d`：[原始记录](../../runs/run_20261016T161407163819Z_v2_tc-base-003_attempt_1_79acbb4d/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161407163819Z_v2_tc-base-003_attempt_1_79acbb4d/artifacts/五年级_草船借箭.docx)
- `V2` / `TC-BASE-004` / `run_20261016T161422974831Z_v2_tc-base-004_attempt_1_c153819d`：[原始记录](../../runs/run_20261016T161422974831Z_v2_tc-base-004_attempt_1_c153819d/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161422974831Z_v2_tc-base-004_attempt_1_c153819d/artifacts/六年级_少年闰土.docx)
- `V2` / `TC-BASE-005` / `run_20261016T161441579171Z_v2_tc-base-005_attempt_1_0055ad30`：[原始记录](../../runs/run_20261016T161441579171Z_v2_tc-base-005_attempt_1_0055ad30/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161441579171Z_v2_tc-base-005_attempt_1_0055ad30/artifacts/七年级_七年级体育与健康_篮球传接球_45分钟.docx)
- `V2` / `TC-BASE-006` / `run_20261016T161455340512Z_v2_tc-base-006_attempt_1_882bf49e`：[原始记录](../../runs/run_20261016T161455340512Z_v2_tc-base-006_attempt_1_882bf49e/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161455340512Z_v2_tc-base-006_attempt_1_882bf49e/artifacts/九年级_探究酶的作用_九年级生物实验课.docx)
- `V2` / `TC-BASE-007` / `run_20261016T161511410930Z_v2_tc-base-007_attempt_1_751a7443`：[原始记录](../../runs/run_20261016T161511410930Z_v2_tc-base-007_attempt_1_751a7443/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161511410930Z_v2_tc-base-007_attempt_1_751a7443/artifacts/四年级_观潮.docx)
- `V2` / `TC-BASE-008` / `run_20261016T161528208226Z_v2_tc-base-008_attempt_1_73f4463b`：[原始记录](../../runs/run_20261016T161528208226Z_v2_tc-base-008_attempt_1_73f4463b/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161528208226Z_v2_tc-base-008_attempt_1_73f4463b/artifacts/八年级_勾股定理.docx)
- `V2` / `TC-BASE-009` / `run_20261016T161538486835Z_v2_tc-base-009_attempt_1_e87c235f`：[原始记录](../../runs/run_20261016T161538486835Z_v2_tc-base-009_attempt_1_e87c235f/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161538486835Z_v2_tc-base-009_attempt_1_e87c235f/artifacts/七年级_网络安全与同伴互助.docx)
- `V2` / `TC-BASE-010` / `run_20261016T161553921939Z_v2_tc-base-010_attempt_1_4fbeb850`：[原始记录](../../runs/run_20261016T161553921939Z_v2_tc-base-010_attempt_1_4fbeb850/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161553921939Z_v2_tc-base-010_attempt_1_4fbeb850/artifacts/六年级_校园雨水统计.docx)
- `V2` / `TC-BASE-011` / `run_20261016T161611402909Z_v2_tc-base-011_attempt_1_b2e6b9f1`：[原始记录](../../runs/run_20261016T161611402909Z_v2_tc-base-011_attempt_1_b2e6b9f1/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161611402909Z_v2_tc-base-011_attempt_1_b2e6b9f1/artifacts/七年级_丝绸之路_教案_v1.docx)
- `V2` / `TC-BASE-012` / `run_20261016T161623777558Z_v2_tc-base-012_attempt_1_d867e1ef`：[原始记录](../../runs/run_20261016T161623777558Z_v2_tc-base-012_attempt_1_d867e1ef/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161623777558Z_v2_tc-base-012_attempt_1_d867e1ef/artifacts/九年级_金属的化学性质.docx)
- `V2` / `TC-BASE-013` / `run_20261016T161638129322Z_v2_tc-base-013_attempt_1_ba557a0d`：[原始记录](../../runs/run_20261016T161638129322Z_v2_tc-base-013_attempt_1_ba557a0d/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161638129322Z_v2_tc-base-013_attempt_1_ba557a0d/artifacts/八年级_八年级体育与健康_雾霾天气室内低强度循环体能训练.docx)
- `V2` / `TC-RAG-001` / `run_20261016T161652133360Z_v2_tc-rag-001_attempt_1_287cd663`：[原始记录](../../runs/run_20261016T161652133360Z_v2_tc-rag-001_attempt_1_287cd663/raw_result.json)
- `V2` / `TC-RAG-002` / `run_20261016T161658634861Z_v2_tc-rag-002_attempt_1_bf76a54d`：[原始记录](../../runs/run_20261016T161658634861Z_v2_tc-rag-002_attempt_1_bf76a54d/raw_result.json)
- `V2` / `TC-RAG-003` / `run_20261016T161709859999Z_v2_tc-rag-003_attempt_1_17369637`：[原始记录](../../runs/run_20261016T161709859999Z_v2_tc-rag-003_attempt_1_17369637/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161709859999Z_v2_tc-rag-003_attempt_1_17369637/artifacts/初中一年级_半月板康复生见习安排_体育课.docx)
- `V2` / `TC-RAG-004` / `run_20261016T161721519833Z_v2_tc-rag-004_attempt_1_b8968bf6`：[原始记录](../../runs/run_20261016T161721519833Z_v2_tc-rag-004_attempt_1_b8968bf6/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161721519833Z_v2_tc-rag-004_attempt_1_b8968bf6/artifacts/七年级_七年级_地理_地球的运动.docx)
- `V2` / `TC-RAG-005` / `run_20261016T161730686315Z_v2_tc-rag-005_attempt_1_6548d355`：[原始记录](../../runs/run_20261016T161730686315Z_v2_tc-rag-005_attempt_1_6548d355/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161730686315Z_v2_tc-rag-005_attempt_1_6548d355/artifacts/七年级_七年级_道德与法治_学会尊重他人.docx)
- `V2` / `TC-RAG-006` / `run_20261016T161744762506Z_v2_tc-rag-006_attempt_1_a04ab8a0`：[原始记录](../../runs/run_20261016T161744762506Z_v2_tc-rag-006_attempt_1_a04ab8a0/raw_result.json)
- `V2` / `TC-RAG-007` / `run_20261016T161752958653Z_v2_tc-rag-007_attempt_1_808281c4`：[原始记录](../../runs/run_20261016T161752958653Z_v2_tc-rag-007_attempt_1_808281c4/raw_result.json)
- `V2` / `TC-RAG-008` / `run_20261016T161801978794Z_v2_tc-rag-008_attempt_1_b2fd0bd5`：[原始记录](../../runs/run_20261016T161801978794Z_v2_tc-rag-008_attempt_1_b2fd0bd5/raw_result.json)
- `V2` / `TC-RAG-009` / `run_20261016T161807714423Z_v2_tc-rag-009_attempt_1_3fd9ea66`：[原始记录](../../runs/run_20261016T161807714423Z_v2_tc-rag-009_attempt_1_3fd9ea66/raw_result.json)
- `V2` / `TC-RAG-010` / `run_20261016T161813648968Z_v2_tc-rag-010_attempt_1_e92f3e32`：[原始记录](../../runs/run_20261016T161813648968Z_v2_tc-rag-010_attempt_1_e92f3e32/raw_result.json)
- `V2` / `TC-RAG-011` / `run_20261016T161820526326Z_v2_tc-rag-011_attempt_1_391c8779`：[原始记录](../../runs/run_20261016T161820526326Z_v2_tc-rag-011_attempt_1_391c8779/raw_result.json)
- `V2` / `TC-RAG-012` / `run_20261016T161827777137Z_v2_tc-rag-012_attempt_1_027013b4`：[原始记录](../../runs/run_20261016T161827777137Z_v2_tc-rag-012_attempt_1_027013b4/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161827777137Z_v2_tc-rag-012_attempt_1_027013b4/artifacts/七年级_雾霾天室内综合实践课_空气与呼吸健康探究.docx)
- `V2` / `TC-RAG-013` / `run_20261016T161847366469Z_v2_tc-rag-013_attempt_1_bcee6a05`：[原始记录](../../runs/run_20261016T161847366469Z_v2_tc-rag-013_attempt_1_bcee6a05/raw_result.json)
- `V2` / `TC-TOOL-001` / `run_20261016T161854569570Z_v2_tc-tool-001_attempt_1_bf011b8a`：[原始记录](../../runs/run_20261016T161854569570Z_v2_tc-tool-001_attempt_1_bf011b8a/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161854569570Z_v2_tc-tool-001_attempt_1_bf011b8a/artifacts/七年级_七年级_数学_一元一次方程_docx.docx)
- `V2` / `TC-TOOL-002` / `run_20261016T161903470226Z_v2_tc-tool-002_attempt_1_80cb7b28`：[原始记录](../../runs/run_20261016T161903470226Z_v2_tc-tool-002_attempt_1_80cb7b28/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161903470226Z_v2_tc-tool-002_attempt_1_80cb7b28/artifacts/八年级_八年级物理_浮力_教案.docx)
- `V2` / `TC-TOOL-003` / `run_20261016T161916808185Z_v2_tc-tool-003_attempt_1_55eaee33`：[原始记录](../../runs/run_20261016T161916808185Z_v2_tc-tool-003_attempt_1_55eaee33/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161916808185Z_v2_tc-tool-003_attempt_1_55eaee33/artifacts/七年级_七年级_班会_同伴冲突调解.docx)
  - [真实 .docx 产物](../../runs/run_20261016T161916808185Z_v2_tc-tool-003_attempt_1_55eaee33/artifacts/七年级_班会_同伴冲突调解.docx)
- `V2` / `TC-TOOL-004` / `run_20261016T161930040614Z_v2_tc-tool-004_attempt_1_02a706d2`：[原始记录](../../runs/run_20261016T161930040614Z_v2_tc-tool-004_attempt_1_02a706d2/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161930040614Z_v2_tc-tool-004_attempt_1_02a706d2/artifacts/七年级_七年级英语_Unit_4_Where_is_my_schoolbag.docx)
- `V2` / `TC-TOOL-005` / `run_20261016T161947281793Z_v2_tc-tool-005_attempt_1_4120faaa`：[原始记录](../../runs/run_20261016T161947281793Z_v2_tc-tool-005_attempt_1_4120faaa/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T161947281793Z_v2_tc-tool-005_attempt_1_4120faaa/artifacts/九年级_九年级化学_金属的化学性质_实验教案.docx)
- `V2` / `TC-TOOL-006` / `run_20261016T162008110831Z_v2_tc-tool-006_attempt_1_822289f4`：[原始记录](../../runs/run_20261016T162008110831Z_v2_tc-tool-006_attempt_1_822289f4/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T162008110831Z_v2_tc-tool-006_attempt_1_822289f4/artifacts/七年级_春.docx)
  - [真实 .docx 产物](../../runs/run_20261016T162008110831Z_v2_tc-tool-006_attempt_1_822289f4/artifacts/七年级_整式乘法.docx)
- `V2` / `TC-TOOL-007` / `run_20261016T162028285107Z_v2_tc-tool-007_attempt_1_f2ba716c`：[原始记录](../../runs/run_20261016T162028285107Z_v2_tc-tool-007_attempt_1_f2ba716c/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T162028285107Z_v2_tc-tool-007_attempt_1_f2ba716c/artifacts/七年级_七年级_生物_绿色植物的生活需要水_教师版教案.docx)
  - [真实 .docx 产物](../../runs/run_20261016T162028285107Z_v2_tc-tool-007_attempt_1_f2ba716c/artifacts/七年级_七年级_生物_绿色植物的生活需要水_学生版实验提示单.docx)
- `V2` / `TC-TOOL-008` / `run_20261016T162043979801Z_v2_tc-tool-008_attempt_1_217264f6`：[原始记录](../../runs/run_20261016T162043979801Z_v2_tc-tool-008_attempt_1_217264f6/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T162043979801Z_v2_tc-tool-008_attempt_1_217264f6/artifacts/八年级_观察植物细胞的结构.docx)
- `V2` / `TC-TOOL-009` / `run_20261016T162059951928Z_v2_tc-tool-009_attempt_1_5a1224ab`：[原始记录](../../runs/run_20261016T162059951928Z_v2_tc-tool-009_attempt_1_5a1224ab/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T162059951928Z_v2_tc-tool-009_attempt_1_5a1224ab/artifacts/七年级_班会教案_班级安全与过敏应急安排.docx)
- `V2` / `TC-TOOL-010` / `run_20261016T162115613828Z_v2_tc-tool-010_attempt_1_02fb15e7`：[原始记录](../../runs/run_20261016T162115613828Z_v2_tc-tool-010_attempt_1_02fb15e7/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T162115613828Z_v2_tc-tool-010_attempt_1_02fb15e7/artifacts/七年级_校园雨水统计_v2.docx)
- `V2` / `TC-TOOL-011` / `run_20261016T162131400456Z_v2_tc-tool-011_attempt_1_f5799692`：[原始记录](../../runs/run_20261016T162131400456Z_v2_tc-tool-011_attempt_1_f5799692/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T162131400456Z_v2_tc-tool-011_attempt_1_f5799692/artifacts/九年级_电路故障分析.docx)
- `V2` / `TC-TOOL-012` / `run_20261016T162140552046Z_v2_tc-tool-012_attempt_1_9b7f38e9`：[原始记录](../../runs/run_20261016T162140552046Z_v2_tc-tool-012_attempt_1_9b7f38e9/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T162140552046Z_v2_tc-tool-012_attempt_1_9b7f38e9/artifacts/八年级_八年级_历史_改革开放_docx.docx)
- `V2` / `TC-TOOL-013` / `run_20261016T162151756771Z_v2_tc-tool-013_attempt_1_46944ec5`：[原始记录](../../runs/run_20261016T162151756771Z_v2_tc-tool-013_attempt_1_46944ec5/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T162151756771Z_v2_tc-tool-013_attempt_1_46944ec5/artifacts/九年级_九年级_化学_酸碱实验.docx)
  - [真实 .docx 产物](../../runs/run_20261016T162151756771Z_v2_tc-tool-013_attempt_1_46944ec5/artifacts/九年级_化学_酸碱实验.docx)
- `V2` / `TC-ERR-001` / `run_20261016T162208788512Z_v2_tc-err-001_attempt_1_99f8ae29`：[原始记录](../../runs/run_20261016T162208788512Z_v2_tc-err-001_attempt_1_99f8ae29/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T162208788512Z_v2_tc-err-001_attempt_1_99f8ae29/artifacts/六年级_分数乘法.docx)
- `V2` / `TC-ERR-002` / `run_20261016T162217965531Z_v2_tc-err-002_attempt_1_3558d456`：[原始记录](../../runs/run_20261016T162217965531Z_v2_tc-err-002_attempt_1_3558d456/raw_result.json)
- `V2` / `TC-ERR-003` / `run_20261016T162223232366Z_v2_tc-err-003_attempt_1_ae299611`：[原始记录](../../runs/run_20261016T162223232366Z_v2_tc-err-003_attempt_1_ae299611/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T162223232366Z_v2_tc-err-003_attempt_1_ae299611/artifacts/七年级_七年级一班篮球训练教案.docx)
- `V2` / `TC-ERR-004` / `run_20261016T162237832369Z_v2_tc-err-004_attempt_1_bce6364d`：[原始记录](../../runs/run_20261016T162237832369Z_v2_tc-err-004_attempt_1_bce6364d/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T162237832369Z_v2_tc-err-004_attempt_1_bce6364d/artifacts/九年级_九年级生物实验_挥发性试剂观察_教师现场指导版.docx)
- `V2` / `TC-ERR-005` / `run_20261016T162249798928Z_v2_tc-err-005_attempt_1_9c1026a7`：[原始记录](../../runs/run_20261016T162249798928Z_v2_tc-err-005_attempt_1_9c1026a7/raw_result.json)
- `V2` / `TC-ERR-006` / `run_20261016T162258909549Z_v2_tc-err-006_attempt_1_369aec2a`：[原始记录](../../runs/run_20261016T162258909549Z_v2_tc-err-006_attempt_1_369aec2a/raw_result.json)
- `V2` / `TC-ERR-007` / `run_20261016T162304556175Z_v2_tc-err-007_attempt_1_bf240d0a`：[原始记录](../../runs/run_20261016T162304556175Z_v2_tc-err-007_attempt_1_bf240d0a/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T162304556175Z_v2_tc-err-007_attempt_1_bf240d0a/artifacts/八年级_八年级实验课材料清单_含无乳胶手套要求.docx)
- `V2` / `TC-ERR-008` / `run_20261016T162316534676Z_v2_tc-err-008_attempt_1_fd47186c`：[原始记录](../../runs/run_20261016T162316534676Z_v2_tc-err-008_attempt_1_fd47186c/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T162316534676Z_v2_tc-err-008_attempt_1_fd47186c/artifacts/七年级_七年级_语文_春_docx.docx)
- `V2` / `TC-ERR-009` / `run_20261016T162327978851Z_v2_tc-err-009_attempt_1_b5b8ba52`：[原始记录](../../runs/run_20261016T162327978851Z_v2_tc-err-009_attempt_1_b5b8ba52/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T162327978851Z_v2_tc-err-009_attempt_1_b5b8ba52/artifacts/九年级_九年级_化学_酸碱中和反应.docx)
  - [真实 .docx 产物](../../runs/run_20261016T162327978851Z_v2_tc-err-009_attempt_1_b5b8ba52/artifacts/九年级_化学_酸碱中和反应.docx)
- `V2` / `TC-ERR-010` / `run_20261016T162339972395Z_v2_tc-err-010_attempt_1_a16e6205`：[原始记录](../../runs/run_20261016T162339972395Z_v2_tc-err-010_attempt_1_a16e6205/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T162339972395Z_v2_tc-err-010_attempt_1_a16e6205/artifacts/八年级_八年级_数学_一次函数_docx.docx)
- `V2` / `TC-ERR-011` / `run_20261016T162351633609Z_v2_tc-err-011_attempt_1_7073e88f`：[原始记录](../../runs/run_20261016T162351633609Z_v2_tc-err-011_attempt_1_7073e88f/raw_result.json)
- `V2` / `TC-ERR-012` / `run_20261016T162357574965Z_v2_tc-err-012_attempt_1_b123b39b`：[原始记录](../../runs/run_20261016T162357574965Z_v2_tc-err-012_attempt_1_b123b39b/raw_result.json)
- `V2` / `TC-ERR-013` / `run_20261016T162403409133Z_v2_tc-err-013_attempt_1_8e90abb4`：[原始记录](../../runs/run_20261016T162403409133Z_v2_tc-err-013_attempt_1_8e90abb4/raw_result.json)
- `V3` / `TC-RAG-001` / `run_20261016T163750719706Z_v3_tc-rag-001_attempt_1_34cd56aa`：[原始记录](../../runs/run_20261016T163750719706Z_v3_tc-rag-001_attempt_1_34cd56aa/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T163750719706Z_v3_tc-rag-001_attempt_1_34cd56aa/artifacts/九年级_学校教学活动方案.docx)
- `V3` / `TC-TOOL-004` / `run_20261016T163750801421Z_v3_tc-tool-004_attempt_1_dba18936`：[原始记录](../../runs/run_20261016T163750801421Z_v3_tc-tool-004_attempt_1_dba18936/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T163750801421Z_v3_tc-tool-004_attempt_1_dba18936/artifacts/七年级_学校教学活动方案.docx)
- `V3` / `TC-ERR-009` / `run_20261016T163750879723Z_v3_tc-err-009_attempt_1_eb1b4893`：[原始记录](../../runs/run_20261016T163750879723Z_v3_tc-err-009_attempt_1_eb1b4893/raw_result.json)
  - [真实 .docx 产物](../../runs/run_20261016T163750879723Z_v3_tc-err-009_attempt_1_eb1b4893/artifacts/九年级_学校教学活动方案.docx)
<!-- AE-01 OBSIDIAN ARTIFACT LINKS END -->
