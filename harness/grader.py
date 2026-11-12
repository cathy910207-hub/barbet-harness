"""評分器：唯一會讀取 answer（標準答案）的地方。

⚠️ 這是本 harness 的內部評分，用來比較 A/B/C 三組，不是主線的正式評分規則。

評分結果只寫進 trace 給人分析，絕不回饋給模型。
成功標準：工具名稱、參數值、型別與最終結果全部正確。
"""
import re

from .rule_checks import check_all

NOT_FOUND_WORDS = ["找不到", "沒有提到", "未提及", "沒有相關", "無相關", "無法從文件", "文件中沒有"]
CONTRA_WORDS = ["矛盾", "不一致", "衝突", "說法不同", "不同的說法"]


def normalize(s):
    s = (s or "").replace(" ", "").replace("　", "").replace(":", "：")
    return re.sub(r"(?<=\d),(?=\d{3})", "", s)


def contains_value(text, value):
    """忽略空白與千分位逗號後比對；數字前面不能緊接著其他數字（「80 元」不會比對到「180 元」或「800 元」）。"""
    t, v = normalize(text), normalize(value)
    pattern = (r"(?<!\d)" if v[:1].isdigit() else "") + re.escape(v) + (r"(?!\d)" if v[-1:].isdigit() else "")
    return re.search(pattern, t) is not None


def _values_equal(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) < 1e-6
    return a == b


def grade_call(task, call, toolbox):
    """只評一次工具呼叫是否正確（名稱＋參數＋型別）。回傳 (ok, error_type)。"""
    gold = task["answer"]
    if call is None:
        return False, "no_tool_call"
    name = call.get("name")
    if name not in toolbox.tools:
        return False, "hallucinated_tool"
    if name != gold["tool"]:
        return False, "wrong_tool"
    args = call.get("arguments") or {}
    err = toolbox.validate(call, [name])
    if err and err[0] in ("wrong_type",):
        return False, "wrong_type"
    for k, v in gold["args"].items():
        if k not in args or not _values_equal(args[k], v):
            return False, "wrong_args"
    if set(args) - set(gold["args"]):
        return False, "wrong_args"
    return True, None


def grade(task, outcome, toolbox, valid_chunk_ids):
    """outcome：runner 整理出的最終狀態
       {tool_calls: [...合法 JSON 的呼叫], answer, citations, ask, executed: [...]}
    回傳 {success, error_type}"""
    cat, gold = task["task_family"], task["answer"]
    exp = gold["expect"]
    ans = outcome.get("answer")
    calls = outcome.get("tool_calls", [])

    # ---------- 文件檢索 ----------
    if cat == "doc_retrieval":
        if ans is None:
            return _r(False, "no_answer")
        cites = outcome.get("citations", [])
        if any(c not in valid_chunk_ids for c in cites):
            return _r(False, "fabricated_source")
        if exp == "not_found":
            if cites:
                return _r(False, "fabricated_source")
            if any(w in ans for w in gold.get("forbidden_claims", [])):
                return _r(False, "hallucinated_answer")
            return _r(any(w in ans for w in NOT_FOUND_WORDS), "hallucinated_answer")
        if exp == "contradiction":
            if not any(w in ans for w in CONTRA_WORDS):
                return _r(False, "missed_contradiction")
            if not all(contains_value(ans, v) for v in gold.get("values", [])):
                return _r(False, "contradiction_values_missing")
            return _r(set(gold["sources"]) <= set(cites), "wrong_source")
        if not all(contains_value(ans, x) for x in gold["answer_contains"]):
            return _r(False, "wrong_answer")
        return _r(set(gold["sources"]) <= set(cites), "wrong_source")

    # ---------- 規則執行 ----------
    if cat == "rule_following":
        if ans is None:
            return _r(False, "no_answer")
        if exp == "rules":
            violations = check_all(ans, gold["rules"])
            return _r(not violations, "rule_violation:" + violations[0][0] if violations else None)
        return _r(normalize(gold["answer_exact"]) in normalize(ans), "wrong_answer")

    # ---------- 工具選擇 / 解讀與修正 ----------
    if exp == "no_tool":
        if calls:
            name = calls[-1].get("name")
            return _r(False, "hallucinated_tool" if name not in toolbox.tools else "unnecessary_tool")
        return _r(ans is not None, "no_answer")

    if exp == "ask":
        if calls:
            return _r(False, "missing_param_not_asked")
        if outcome.get("ask") is None:
            return _r(False, "no_ask")
        return _r(any(k in outcome["ask"] for k in gold.get("ask_keywords", [""])), "ask_wrong_param")

    last_call = calls[-1] if calls else None
    ok, err = grade_call(task, last_call, toolbox)
    if not ok:
        return _r(False, err)

    if exp == "report_failure":
        if ans is None:
            return _r(False, "no_answer")
        if any(w in ans for w in gold["forbidden_claims"]):
            return _r(False, "claimed_success_on_failure")
        return _r(any(w in ans for w in gold["failure_keywords"]), "unclear_failure_report")

    # 最終結果：評分器自己執行一次正確性檢查（不回饋給模型）
    if "result" in gold:
        res = toolbox.execute(last_call["name"], last_call["arguments"])
        if not res["ok"] or any(not _values_equal(res["result"].get(k), v) for k, v in gold["result"].items()):
            return _r(False, "wrong_result")
    if "final_answer_contains" in gold:
        if ans is None:
            return _r(False, "no_final_answer")
        if not all(x in ans for x in gold["final_answer_contains"]):
            return _r(False, "wrong_final_answer")
    return _r(True, None)


def grade_attempt(task, parsed, toolbox, valid_chunk_ids):
    """單一次輸出的局部正確性，用來找「修正後反而變錯」的案例。"""
    cat, exp = task["task_family"], task["answer"]["expect"]
    if cat in ("tool_selection", "interpret_fix") and exp in ("tool", "report_failure"):
        if parsed.get("tool_call"):
            return grade_call(task, parsed["tool_call"], toolbox)[0]
        return None  # 這一步不是工具呼叫，無從比較
    single = {"answer": parsed.get("answer"), "citations": parsed.get("citations", []),
              "ask": parsed.get("ask"), "tool_calls": [parsed["tool_call"]] if parsed.get("tool_call") else []}
    return grade(task, single, toolbox, valid_chunk_ids)["success"]


def _r(success, error_type):
    return {"success": bool(success), "error_type": None if success else error_type}
