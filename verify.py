"""自動驗證程式：把原始草稿驗證成候選資料。

輸入（原始資料，永遠不會被覆寫）：
  data/raw/tasks_draft.jsonl   題目草稿
  data/raw/corrections.jsonl   修正紀錄（原答案、修正內容、原因）
輸出：
  data/candidates.jsonl        通過檢查的候選資料
  data/rejected.jsonl          被淘汰的資料與原因
  data/manifest.json           來源、revision、授權、生成方法、檔案雜湊

驗證原則：AI 起草的答案一律不直接採信，每一題都用程式「獨立重算或重查」一次：
  文件題 → 重新到文件庫找證據；規則題 → 用參考實作重算；工具題 → 依 schema 檢查並實際執行工具。

用法：python verify.py            （可重複執行，結果相同）
      python verify.py --strict   （有任何資料被淘汰就回傳非 0，方便放進自動檢查）
"""
import argparse
import copy
import datetime
import json
import os
import re
import sys
from decimal import ROUND_HALF_UP, Decimal

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from harness.grader import normalize  # noqa: E402
from harness.io_utils import dir_hash, file_hash, read_json, read_jsonl, write_json  # noqa: E402
from harness.retriever import load_docs, load_skills  # noqa: E402
from harness.tools import ToolBox  # noqa: E402

VERIFY_VERSION = "verify_v0.2"
REQUIRED_FIELDS = {"id": str, "task_family": str, "group_id": str, "source": dict, "question": str,
                   "answer": dict, "constraints": list, "verification": dict, "split": str}
TASK_FAMILIES = {"doc_retrieval", "rule_following", "tool_selection", "interpret_fix"}

# 規則題的參考實作。每新增一個 skill，就要在這裡寫一份「獨立於 AI 草稿」的計算規則，
# 並且 verify 會檢查 skill 文字裡真的寫著同樣的數字，避免兩邊不一致。
REFUND_REFERENCE = {
    "skill_refund_v1": {"multiplier": "0.9", "fee": 0},
    "skill_refund_v2": {"multiplier": "0.8", "fee": 30},
}


class Resources:
    def __init__(self, base=BASE):
        self.chunks = {c["id"]: c for c in load_docs(os.path.join(base, "data/docs"))}
        self.skills = load_skills(os.path.join(base, "data/skills"))
        self.toolbox = ToolBox(read_json(os.path.join(base, "data/tools/schema.json")),
                               read_json(os.path.join(base, "data/tools/mock_db.json")))


# ---------------------------------------------------------------- 結構檢查
def check_structure(row, res):
    errs = []
    for k, t in REQUIRED_FIELDS.items():
        if k not in row:
            errs.append(f"缺少欄位 {k}")
        elif not isinstance(row[k], t):
            errs.append(f"欄位 {k} 型別應為 {t.__name__}")
    if errs:
        return errs
    if row["task_family"] not in TASK_FAMILIES:
        errs.append(f"未知的 task_family：{row['task_family']}")
    if row["split"] != "unassigned":
        errs.append(f"split 必須是 unassigned（由主線統一切分），收到 {row['split']}")
    if not row["group_id"].strip():
        errs.append("group_id 不可為空")
    for k in ("type", "license", "generation_method"):
        if not row["source"].get(k):
            errs.append(f"source 缺少 {k}")
    if not row["constraints"] or not all(isinstance(c, str) and c for c in row["constraints"]):
        errs.append("constraints 必須是非空的文字清單")
    if row["verification"].get("method") not in METHODS:
        errs.append(f"未知的驗證方法：{row['verification'].get('method')}")
    ctx = row.get("context", {})
    for t in ctx.get("tools", []):
        if t not in res.toolbox.tools:
            errs.append(f"context.tools 中的工具不存在於 schema：{t}")
    if ctx.get("skill_id") and ctx["skill_id"] not in res.skills:
        errs.append(f"找不到 skill：{ctx['skill_id']}")
    return errs


# ---------------------------------------------------------------- 各驗證方法
def _text(res, cid):
    c = res.chunks.get(cid)
    return normalize(c["text"]) if c else None


def _ambiguous_number(item, question):
    """答案片段是純數字，且題目裡有包含它的更長數字（例如答案 80、題目 800）。"""
    item_n = normalize(item)
    if not re.fullmatch(r"\d+(\.\d+)?", item_n):
        return False
    return any(item_n in num and item_n != num for num in re.findall(r"\d+", normalize(question)))


def v_doc_evidence(row, res, p):
    ans, errs = row["answer"], []
    srcs = ans.get("sources", [])
    if not srcs:
        errs.append("answer.sources 為空")
    for s in srcs:
        if s not in res.chunks:
            errs.append(f"來源不存在：{s}")
    for cid, needles in p.get("evidence", {}).items():
        t = _text(res, cid)
        if t is None:
            errs.append(f"證據段落不存在：{cid}")
            continue
        for n in needles:
            if normalize(n) not in t:
                errs.append(f"證據「{n}」不在 {cid} 中")
    cited = [_text(res, s) or "" for s in srcs]
    for item in ans.get("answer_contains", []):
        if not any(normalize(item) in t for t in cited):
            errs.append(f"答案片段「{item}」在引用的來源中找不到")
        if _ambiguous_number(item, row["question"]):
            errs.append(f"答案片段「{item}」是題目中某個數字的一部分，評分時會誤判（例如答案寫成題目裡的數字也算對）")
    return errs


def v_doc_contradiction(row, res, p):
    ans, errs = row["answer"], []
    claims = p.get("claims", {})
    if len(claims) < 2:
        errs.append("矛盾題至少需要兩個來源的說法")
    if set(claims) != set(ans.get("sources", [])):
        errs.append("answer.sources 必須與驗證用的矛盾來源完全一致")
    for cid, claim in claims.items():
        t = _text(res, cid)
        if t is None:
            errs.append(f"來源不存在：{cid}")
        elif normalize(claim) not in t:
            errs.append(f"「{claim}」不在 {cid} 中")
    if len({normalize(v) for v in claims.values()}) < 2:
        errs.append("各來源說法相同，不構成矛盾")
    if "values" not in ans:
        errs.append("缺少 answer.values，無法確認模型是否寫出各來源的說法")
    elif {normalize(v) for v in ans["values"]} != {normalize(v) for v in claims.values()}:
        errs.append("answer.values 與文件中的實際說法不一致")
    return errs


def v_doc_absent(row, res, p):
    errs = []
    if row["answer"].get("expect") != "not_found":
        errs.append("answer.expect 應為 not_found")
    for term in p.get("terms", []):
        key = normalize(term).lower()
        hits = [cid for cid, c in res.chunks.items() if key in normalize(c["text"]).lower()]
        if hits:
            errs.append(f"「{term}」其實出現在文件中：{hits}，不能當作找不到的題目")
    if not row["answer"].get("forbidden_claims"):
        errs.append("缺少 answer.forbidden_claims，無法擋下「說找不到卻又給答案」的回答")
    return errs


def v_recompute_refund(row, res, p):
    errs = []
    sid = row.get("context", {}).get("skill_id")
    if sid not in REFUND_REFERENCE:
        return [f"skill {sid} 沒有參考實作，無法獨立驗證"]
    ref = REFUND_REFERENCE[sid]
    skill_text = res.skills[sid]["text"]
    if ref["multiplier"] not in skill_text:
        errs.append(f"skill 文字中找不到倍率 {ref['multiplier']}，參考實作與 skill 不一致")
    if ref["fee"] and f"{ref['fee']} 元" not in skill_text:
        errs.append(f"skill 文字中找不到手續費 {ref['fee']} 元，參考實作與 skill 不一致")
    price = Decimal(str(p["price"]))
    value = price if not p.get("opened") else price * Decimal(ref["multiplier"]) - ref["fee"]
    expected = f"退款金額：{int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))} 元"
    got = row["answer"].get("answer_exact")
    if normalize(got) != normalize(expected):
        errs.append(f"答案與重算結果不符：草稿為「{got}」，重算為「{expected}」")
    if got and normalize(got) in normalize(row["question"]):
        errs.append("答案直接出現在題目中")
    return errs


def _call(row):
    return {"name": row["answer"].get("tool"), "arguments": row["answer"].get("args", {})}


def v_execute_tool(row, res, p):
    errs, ans, tb = [], row["answer"], res.toolbox
    offered = row.get("context", {}).get("tools", [])
    err = tb.validate(_call(row), offered)
    if err:
        return [f"標準答案的工具呼叫不符合 schema：{err[1]}"]
    out = tb.execute(ans["tool"], ans["args"])
    if not out["ok"]:
        return [f"標準答案的工具呼叫執行失敗：{out['error']}"]
    for k, v in ans.get("result", {}).items():
        if out["result"].get(k) != v:
            errs.append(f"工具實際回傳 {k}={out['result'].get(k)}，與答案 {v} 不符")
    blob = " ".join(str(v) for v in out["result"].values())
    for s in ans.get("final_answer_contains", []):
        if s not in blob:
            errs.append(f"最終回答需包含「{s}」，但工具結果中沒有")
    for k, bad in p.get("value_in_question_must_fail", {}).items():
        if bad not in row["question"]:
            errs.append(f"題目中沒有出現待修正的值「{bad}」")
        args = dict(ans["args"], **{k: bad})
        if tb.validate({"name": ans["tool"], "arguments": args}, offered) is None:
            errs.append(f"「{bad}」其實能通過 schema 檢查，這題不需要修正")
    return errs


def v_execute_tool_expect_failure(row, res, p):
    ans, tb = row["answer"], res.toolbox
    err = tb.validate(_call(row), row.get("context", {}).get("tools", []))
    if err:
        return [f"標準答案的工具呼叫不符合 schema：{err[1]}"]
    out = tb.execute(ans["tool"], ans["args"])
    errs = []
    if out["ok"]:
        errs.append("工具其實執行成功，這題不是工具失敗情境")
    if not ans.get("failure_keywords") or not ans.get("forbidden_claims"):
        errs.append("缺少 failure_keywords 或 forbidden_claims")
    return errs


def v_no_applicable_tool(row, res, p):
    errs = []
    if row["answer"].get("expect") != "no_tool":
        errs.append("answer.expect 應為 no_tool")
    for name, t in res.toolbox.tools.items():
        blob = name + t["description"]
        for kw in p.get("capability_keywords", []):
            if kw in blob:
                errs.append(f"工具 {name} 的描述含有「{kw}」，可能其實有適用工具")
    return errs


def v_missing_required_param(row, res, p):
    errs, tb = [], res.toolbox
    tool, param = p["tool"], p["param"]
    if tool not in row.get("context", {}).get("tools", []):
        errs.append(f"題目沒有提供工具 {tool}")
    spec = tb.tools.get(tool, {}).get("parameters", {})
    if param not in spec.get("required", []):
        errs.append(f"{param} 不是 {tool} 的必要參數")
    pat = spec.get("properties", {}).get(param, {}).get("pattern")
    if pat and re.search(pat.strip("^$"), row["question"]):
        errs.append(f"題目中其實已經有符合格式的 {param}")
    if re.search(r"\d{4,}", row["question"]):
        errs.append("題目中出現長串數字，可能其實提供了參數")
    if row["answer"].get("expect") != "ask" or param not in row["answer"].get("missing", []):
        errs.append("answer 應為 expect=ask 並在 missing 中列出缺少的參數")
    return errs


METHODS = {
    "doc_evidence": v_doc_evidence, "doc_contradiction": v_doc_contradiction, "doc_absent": v_doc_absent,
    "recompute_refund": v_recompute_refund, "execute_tool": v_execute_tool,
    "execute_tool_expect_failure": v_execute_tool_expect_failure,
    "no_applicable_tool": v_no_applicable_tool, "missing_required_param": v_missing_required_param,
}


def verify_one(row, res):
    errs = check_structure(row, res)
    if errs:
        return errs
    v = row["verification"]
    return METHODS[v["method"]](row, res, v.get("params", {}))


# ---------------------------------------------------------------- 修正紀錄
def _get(obj, path):
    for k in path.split("."):
        if not isinstance(obj, dict) or k not in obj:
            return None
        obj = obj[k]
    return obj


def _set(obj, path, value):
    keys = path.split(".")
    for k in keys[:-1]:
        obj = obj.setdefault(k, {})
    obj[keys[-1]] = value


def apply_corrections(row, corrections, res):
    """依序套用修正。原始資料不改動，回傳 (修正後的資料, 修正紀錄, 錯誤)。"""
    cur = copy.deepcopy(row)
    revisions, errs = [], []
    for c in corrections:
        if _get(cur, c["path"]) != c["old"]:
            errs.append(f"修正 {c['correction_id']} 的原值與目前資料不符，未套用")
            continue
        before = verify_one(cur, res)
        _set(cur, c["path"], c["new"])
        after = verify_one(cur, res)
        revisions.append({
            **{k: c[k] for k in ("correction_id", "path", "old", "new", "reason", "author", "date")},
            "verification_result": {
                "before": "通過" if not before else "失敗：" + "；".join(before),
                "after": "通過" if not after else "失敗：" + "；".join(after),
            },
        })
    return cur, revisions, errs


# ---------------------------------------------------------------- 群組檢查
def check_groups(rows):
    """回傳 {id: [錯誤]}。
    - 同一個題目出現在不同 group_id：切分時可能一份進訓練、一份進測試，造成洩漏
    - 同一組裡題目相同、情境不同（反事實對照）：答案必須不同，否則換規則沒有效果
    - 同一組裡題目與情境完全相同：重複資料"""
    out = {}
    by_q = {}
    for r in rows:
        by_q.setdefault(normalize(r["question"]), []).append(r)
    for same in by_q.values():
        groups = {r["group_id"] for r in same}
        if len(groups) > 1:
            for r in same:
                out.setdefault(r["id"], []).append(f"相同題目分散在不同 group_id：{sorted(groups)}")
            continue
        seen = {}
        for r in same:
            key = json.dumps(r.get("context", {}), sort_keys=True, ensure_ascii=False)
            if key in seen:
                out.setdefault(r["id"], []).append(f"與 {seen[key]} 題目及情境完全相同（重複資料）")
            else:
                seen[key] = r["id"]
        answers = {}
        for r in same:
            answers.setdefault(json.dumps(r["answer"], sort_keys=True, ensure_ascii=False), []).append(r["id"])
        if len(same) > 1:
            for ids in answers.values():
                if len(ids) > 1:
                    ctxs = {json.dumps(r.get("context", {}), sort_keys=True) for r in same if r["id"] in ids}
                    if len(ctxs) > 1:
                        for i in ids:
                            out.setdefault(i, []).append(f"反事實對照 {ids} 換了情境但答案相同")
    return out


# ---------------------------------------------------------------- 主流程
def run(drafts, corrections, res):
    corr_by = {}
    for c in corrections:
        corr_by.setdefault(c["task_id"], []).append(c)
    known_ids = {d.get("id") for d in drafts}
    orphan = [c["correction_id"] for c in corrections if c["task_id"] not in known_ids]

    seen, stage1, rejected = set(), [], []
    for d in drafts:
        did = d.get("id")
        if did in seen:
            rejected.append(dict(d, reject_stage="structure", reject_reasons=[f"id 重複：{did}"]))
            continue
        seen.add(did)
        row, revisions, corr_errs = apply_corrections(d, corr_by.get(did, []), res)
        errs = corr_errs + verify_one(row, res)
        row["revisions"] = revisions
        if errs:
            rejected.append(dict(row, reject_stage="verification", reject_reasons=errs))
        else:
            stage1.append(row)

    group_errs = check_groups(stage1)
    candidates = []
    for row in stage1:
        if row["id"] in group_errs:
            rejected.append(dict(row, reject_stage="group", reject_reasons=group_errs[row["id"]]))
        else:
            v = row["verification"]
            row["verification"] = dict(v, result={"passed": True, "verifier": VERIFY_VERSION})
            candidates.append(row)
    return candidates, rejected, orphan


def build_manifest(paths, candidates, rejected, orphan):
    sources = {}
    for r in candidates + rejected:
        s = r.get("source", {})
        key = s.get("type", "unknown") + "|" + str(s.get("name", ""))
        e = sources.setdefault(key, {k: s.get(k) for k in ("type", "name", "revision", "license", "generation_method", "created_by")})
        e["count"] = e.get("count", 0) + 1
    return {
        "verifier": VERIFY_VERSION,
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "counts": {"candidates": len(candidates), "rejected": len(rejected),
                   "by_task_family": {f: sum(r["task_family"] == f for r in candidates) for f in sorted(TASK_FAMILIES)}},
        "sources": list(sources.values()),
        "planned_sources": [{
            "name": "allenai/tulu-3-sft-personas-instruction-following",
            "revision": "fe0c7d350c9b4542b8d829a6f1daa1c259f0ba0e",
            "license": "ODC-BY",
            "status": "主線已選定，尚未匯入",
            "intended_use": "規則執行（rule_following）題目來源；參考回答不作為標準答案",
        }],
        "orphan_corrections": orphan,
        "file_hashes": {
            "inputs": {"tasks_draft": file_hash(paths["drafts"]), "corrections": file_hash(paths["corrections"]),
                       "docs": dir_hash(os.path.join(BASE, "data/docs")),
                       "skills": dir_hash(os.path.join(BASE, "data/skills")),
                       "tool_schema": file_hash(os.path.join(BASE, "data/tools/schema.json")),
                       "mock_db": file_hash(os.path.join(BASE, "data/tools/mock_db.json")),
                       "verify.py": file_hash(os.path.abspath(__file__))},
            "outputs": {"candidates": file_hash(paths["candidates"]), "rejected": file_hash(paths["rejected"])},
        },
    }


def _write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    paths = {"drafts": os.path.join(BASE, "data/raw/tasks_draft.jsonl"),
             "corrections": os.path.join(BASE, "data/raw/corrections.jsonl"),
             "candidates": os.path.join(BASE, "data/candidates.jsonl"),
             "rejected": os.path.join(BASE, "data/rejected.jsonl"),
             "manifest": os.path.join(BASE, "data/manifest.json")}
    res = Resources()
    candidates, rejected, orphan = run(read_jsonl(paths["drafts"]), read_jsonl(paths["corrections"]), res)
    _write_jsonl(paths["candidates"], candidates)
    _write_jsonl(paths["rejected"], rejected)
    write_json(paths["manifest"], build_manifest(paths, candidates, rejected, orphan))

    print(f"通過：{len(candidates)} 筆　淘汰：{len(rejected)} 筆")
    for r in rejected:
        print(f"  ✗ {r.get('id')}（{r['reject_stage']}）：" + "；".join(r["reject_reasons"]))
    for r in candidates:
        for rev in r["revisions"]:
            vr = rev["verification_result"]
            print(f"  ✎ {r['id']} {rev['correction_id']}：修正前 {vr['before']} → 修正後 {vr['after']}")
    if orphan:
        print(f"  ⚠ 找不到對應題目的修正：{orphan}")
    if args.strict and (rejected or orphan):
        sys.exit(1)


if __name__ == "__main__":
    main()
