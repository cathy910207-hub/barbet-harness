"""流程檢查。執行：python tests/test_pipeline.py"""
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from harness.io_utils import read_json, read_jsonl  # noqa: E402
from harness.metrics import summarize  # noqa: E402
from harness.runner import Harness, public_view  # noqa: E402


def main():
    cfg = read_json(os.path.join(BASE, "config.json"))
    cfg["model"]["dummy_script"] = os.path.join(BASE, "tests/dummy_script.json")
    cfg["paths"]["runs"] = "runs/_test"
    h = Harness(cfg, base_dir=BASE)
    out_dir, recs = h.run_all()
    summary, _ = summarize(out_dir)
    tasks = {t["id"]: t for t in read_jsonl(os.path.join(BASE, "data/tasks.jsonl"))}
    idx = {(r["task_id"], r["group"]): r for r in recs}

    # 1. 標準答案不會出現在 prompt 裡（結構上 public_view 移除 gold）
    for t in tasks.values():
        assert "gold" not in public_view(t)
    for r in recs:
        gold_blob = json.dumps(tasks[r["task_id"]]["gold"], ensure_ascii=False)
        for s in r["steps"]:
            assert gold_blob not in s["prompt"], f"{r['task_id']} 的 prompt 含有 gold"
            assert "answer_exact" not in s["prompt"] and "final_answer_contains" not in s["prompt"]

    # 2. A 組沒有文件與 skill；B、C 組有
    assert idx[("doc_001", "A")]["retrieved"] == []
    assert any(c["id"] == "doc_shipping#p2" for c in idx[("doc_001", "B")]["retrieved"])
    assert idx[("rule_002", "B")]["skill_id"] == "skill_refund_v2"

    # 3. skill 替換：同一題、不同 skill，標準答案不同
    assert tasks["rule_001"]["input"]["query"] == tasks["rule_002"]["input"]["query"]
    assert tasks["rule_001"]["gold"] != tasks["rule_002"]["gold"]
    assert not idx[("rule_002", "B")]["success"]  # 劇本中 B 沿用舊規則 → 應判錯

    # 4. 各種必測情境的判定
    assert idx[("doc_003", "A")]["error_type"] == "fabricated_source"
    assert idx[("doc_003", "B")]["success"]
    assert idx[("doc_002", "B")]["error_type"] == "missed_contradiction"
    assert idx[("tool_002", "A")]["error_type"] == "hallucinated_tool"
    assert idx[("tool_003", "A")]["error_type"] == "missing_param_not_asked"
    assert idx[("tool_001", "A")]["error_type"] == "wrong_type"

    # 5. C 組修正流程
    fix1 = idx[("fix_001", "C")]
    assert fix1["success"] and fix1["corrections_used"] == 1
    assert idx[("doc_001", "C")]["success"] and idx[("doc_001", "C")]["corrections_used"] == 1
    fix2 = idx[("fix_002", "C")]
    assert not fix2["success"] and fix2["corrections_used"] == 2
    assert "fix_002" in summary["cases"]["regressed_after_correction"]
    assert "fix_001" in summary["cases"]["fixed_by_correction"]

    # 6. 回饋訊息只含執行期資訊（不含標準答案的值）
    for r in recs:
        for s in r["steps"]:
            if s.get("runtime_error"):
                msg = s["runtime_error"]["message"]
                g = tasks[r["task_id"]]["gold"]
                for v in (g.get("args") or {}).values():
                    assert str(v) not in msg or str(v) in s["output"], f"{r['task_id']} 回饋洩漏答案：{msg}"

    # 7. 系統能力標記
    assert idx[("rule_002", "C")]["system_assisted_compute"]
    assert not idx[("rule_002", "B")]["system_assisted"]

    shutil.rmtree(os.path.join(BASE, "runs/_test"))
    print("全部檢查通過 ✓")


if __name__ == "__main__":
    main()
