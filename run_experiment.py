"""執行實驗。

用法：
  python run_experiment.py                         # 用 config.json，跑 A/B/C 全部題目
  python run_experiment.py --groups B,C --limit 3  # 只跑 B、C 組前 3 題
  python run_experiment.py --dummy-script tests/dummy_script.json   # 用假模型劇本測試流程
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness.io_utils import read_json  # noqa: E402
from harness.metrics import summarize  # noqa: E402
from harness.runner import Harness  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--groups", default="A,B,C")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tasks", default=None, help="只跑指定題號，逗號分隔")
    ap.add_argument("--dummy-script", default=None)
    args = ap.parse_args()

    base = os.path.dirname(os.path.abspath(__file__))
    cfg = read_json(os.path.join(base, args.config))
    if args.dummy_script:
        cfg["model"]["type"] = "dummy"
        cfg["model"]["dummy_script"] = os.path.join(base, args.dummy_script)
    h = Harness(cfg, base_dir=base)
    task_ids = args.tasks.split(",") if args.tasks else None
    out_dir, _ = h.run_all(groups=args.groups.split(","), limit=args.limit, task_ids=task_ids)
    _, md = summarize(out_dir)
    print("\n" + md)
    print(f"結果已存到：{out_dir}")


if __name__ == "__main__":
    main()
