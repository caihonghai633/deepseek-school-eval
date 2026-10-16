"""一键运行 V1/V2 全量对照实验并输出离线比较结果。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluator.analyzer import latest_unique_results, load_results, summarize
from evaluator.runner import run_batch


def main() -> None:
    parser = argparse.ArgumentParser(description="AE-01 V1/V2 对照实验")
    parser.add_argument("--offline", action="store_true", help="使用确定性工具编排，不调用 DeepSeek")
    parser.add_argument("--limit", type=int, default=None, help="每个版本只运行前 N 条，用于冒烟")
    args = parser.parse_args()
    summaries = []
    for config_path in ("configs/v1_baseline.yaml", "configs/v2_improved.yaml"):
        summaries.append(run_batch(config_path, offline=args.offline, limit=args.limit, skip_existing=True))
    comparison = summarize(latest_unique_results(load_results()))
    output_path = ROOT / "reports" / "stage3_comparison.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"batch_summaries": summaries, "comparison": comparison}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

