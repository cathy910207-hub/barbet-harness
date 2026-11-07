"""統計：成功率、錯誤分類、token、延遲，以及修正案例。"""
import os
from collections import Counter, defaultdict

from .io_utils import read_jsonl, write_json

CAT_ZH = {"doc_retrieval": "文件檢索", "rule_following": "規則執行",
          "tool_selection": "工具選擇", "interpret_fix": "解讀與修正"}


def summarize(run_dir):
    recs = read_jsonl(os.path.join(run_dir, "traces.jsonl"))
    groups = sorted({r["group"] for r in recs})
    cats = list(CAT_ZH)
    by = defaultdict(list)
    for r in recs:
        by[(r["group"], r["category"])].append(r)
        by[(r["group"], "ALL")].append(r)

    def stat(rs):
        n = len(rs)
        if not n:
            return None
        return {"n": n, "success": sum(r["success"] for r in rs),
                "rate": round(sum(r["success"] for r in rs) / n, 3),
                "avg_tokens": round(sum(r["total_tokens"] for r in rs) / n, 1),
                "avg_latency_s": round(sum(r["latency_s"] for r in rs) / n, 3),
                "avg_corrections": round(sum(r["corrections_used"] for r in rs) / n, 2),
                "system_assisted_successes": sum(r["success"] and r["system_assisted"] for r in rs)}

    table = {g: {c: stat(by[(g, c)]) for c in cats + ["ALL"]} for g in groups}
    errors = {g: dict(Counter(r["error_type"] for r in recs if r["group"] == g and not r["success"])) for g in groups}

    # 成對比較：同一題 B vs C
    idx = {(r["task_id"], r["group"]): r for r in recs}
    task_ids = sorted({r["task_id"] for r in recs})
    pair = []
    for t in task_ids:
        b, c = idx.get((t, "B")), idx.get((t, "C"))
        if b and c:
            pair.append({"task_id": t, "extra_tokens": c["total_tokens"] - b["total_tokens"],
                         "extra_latency_s": round(c["latency_s"] - b["latency_s"], 4),
                         "B": b["success"], "C": c["success"]})
    c_cost = None
    if pair:
        c_cost = {"avg_extra_tokens_vs_B": round(sum(p["extra_tokens"] for p in pair) / len(pair), 1),
                  "avg_extra_latency_s_vs_B": round(sum(p["extra_latency_s"] for p in pair) / len(pair), 4)}

    # 案例
    c_recs = [r for r in recs if r["group"] == "C"]
    fixed = [r for r in c_recs if r["success"] and r["corrections_used"] > 0]
    regress_within = [r for r in c_recs if not r["success"] and any(a is True for a in r["attempt_grades_for_analysis_only"])]
    b_to_c_worse = [p for p in pair if p["B"] and not p["C"]]
    b_to_c_better = [p for p in pair if not p["B"] and p["C"]]

    summary = {
        "run_dir": run_dir,
        "model_label": recs[0]["model_label"] if recs else None,
        "is_barbet": all(r["is_barbet"] for r in recs) if recs else None,
        "versions": recs[0]["versions"] if recs else None,
        "success_table": table, "error_types": errors, "c_cost_vs_b": c_cost,
        "cases": {
            "fixed_by_correction": [r["task_id"] for r in fixed],
            "regressed_after_correction": [r["task_id"] for r in regress_within],
            "B_correct_C_wrong": [p["task_id"] for p in b_to_c_worse],
            "B_wrong_C_correct": [p["task_id"] for p in b_to_c_better],
        },
    }
    write_json(os.path.join(run_dir, "summary.json"), summary)
    md = _markdown(summary, groups, cats, fixed, regress_within, recs)
    with open(os.path.join(run_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write(md)
    return summary, md


def _markdown(s, groups, cats, fixed, regressed, recs):
    L = []
    if not s["is_barbet"]:
        L.append(f"> ⚠️ 本次使用的模型是「{s['model_label']}」，**不是 Barbet**。以下數字只用來確認流程正確，不可當作實驗結果。\n")
    v = s["versions"] or {}
    L.append("## 版本紀錄\n")
    for k, val in v.items():
        L.append(f"- {k}：`{val}`")
    L.append("\n## 成功率\n")
    L.append("| 組別 | " + " | ".join(CAT_ZH[c] for c in cats) + " | 全部 |")
    L.append("|---" * (len(cats) + 2) + "|")
    for g in groups:
        row = []
        for c in cats + ["ALL"]:
            st = s["success_table"][g][c]
            row.append(f"{st['success']}/{st['n']}（{st['rate']:.0%}）" if st else "—")
        L.append(f"| {g} | " + " | ".join(row) + " |")
    L.append("\n## 成本\n")
    L.append("| 組別 | 平均 token | 平均延遲（秒） | 平均修正次數 | 靠工具執行完成的成功題數（系統能力） |")
    L.append("|---|---|---|---|---|")
    for g in groups:
        st = s["success_table"][g]["ALL"]
        L.append(f"| {g} | {st['avg_tokens']} | {st['avg_latency_s']} | {st['avg_corrections']} | {st['system_assisted_successes']} |")
    if s["c_cost_vs_b"]:
        L.append(f"\nC 組相較 B 組，平均每題多用 {s['c_cost_vs_b']['avg_extra_tokens_vs_B']} token、"
                 f"多花 {s['c_cost_vs_b']['avg_extra_latency_s_vs_B']} 秒。")
    L.append(f"\nToken 計算方式：`{v.get('token_count_method')}`")
    L.append("\n## 錯誤分類\n")
    for g in groups:
        e = s["error_types"][g]
        L.append(f"- **{g}**：" + ("、".join(f"{k} × {n}" for k, n in sorted(e.items(), key=lambda x: -x[1])) or "無"))
    L.append("\n## 案例\n")
    L.append("### 修正前錯 → 修正後對\n")
    L.extend(_case(r) for r in fixed) if fixed else L.append("（無）")
    L.append("\n### 修正後反而變錯（某一步曾經正確，最終失敗）\n")
    L.extend(_case(r) for r in regressed) if regressed else L.append("（無）")
    c = s["cases"]
    L.append(f"\n- B 對、C 錯：{c['B_correct_C_wrong'] or '無'}")
    L.append(f"- B 錯、C 對：{c['B_wrong_C_correct'] or '無'}")
    return "\n".join(L) + "\n"


def _case(r):
    lines = [f"**{r['task_id']}**（{CAT_ZH[r['category']]}，修正 {r['corrections_used']} 次，最終："
             f"{'成功' if r['success'] else '失敗 ' + str(r['error_type'])}）\n"]
    for st in r["steps"]:
        out = st["output"].replace("\n", " ")
        lines.append(f"- 第 {st['step'] + 1} 步輸出：`{out[:160]}`")
        if st.get("tool_execution"):
            lines.append(f"  - 工具結果：`{st['tool_execution']}`")
        if st.get("runtime_error"):
            lines.append(f"  - 回饋給模型：{st['runtime_error']['message']}")
    return "\n".join(lines) + "\n"
