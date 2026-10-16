# DeepSeek 模型学校工作评测

针对 **DeepSeek 模型在学校行政/教学办公场景**下的能力评测框架。

## 评测目标

模拟政教处、教务、宿管等学校的真实文档与信息处理任务，衡量模型（及 Agent 配置）在实际办公场景下的准确性、规范性与稳定性。

## 仓库结构

| 目录 | 说明 |
|---|---|
| `sut/` | 被测系统（System Under Test）——Agent 主体、RAG 引擎、工具集 |
| `evaluator/` | 评测器——运行器、检查项、分析器、Obsidian 导出器 |
| `data/` | 测试用例集（`test_cases.jsonl`）与知识库规则 |
| `configs/` | 三档配置：`v1_baseline` / `v2_improved` / `v3_hardened` |
| `scripts/` | 数据集构建、实验运行、V3 验证脚本 |
| `tests/` | 单元测试 |
| `reports/` | 评测报告与 Obsidian 导出（含 V1/V2/V3 对比） |

## 快速开始

```bash
pip install -r requirements.txt

# 运行实验（需配置 DEEPSEEK_API_KEY）
python scripts/run_experiments.py

# 运行测试
pytest tests/
```

## 配置

环境变量通过 `.env` 提供（已被 gitignore 排除）：

```
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

## 版本对比

| 版本 | 配置文件 | 说明 |
|---|---|---|
| V1 | `configs/v1_baseline.yaml` | 基线配置 |
| V2 | `configs/v2_improved.yaml` | 改进配置 |
| V3 | `configs/v3_hardened.yaml` | 加固配置 |

详细对比见 `reports/stage3_comparison.json` 与 `reports/obsidian/summaries/`。

---
*公开仓库 · 欢迎使用与改进*
