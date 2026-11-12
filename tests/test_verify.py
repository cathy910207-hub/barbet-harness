"""測試驗證器本身：正確的資料要通過，有問題的資料一定要被抓出來。

執行：python tests/test_verify.py
每一個「錯誤案例」都是故意做壞的一筆資料，驗證器必須把它淘汰，並說出原因。
"""
import copy
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import verify  # noqa: E402
from harness.grader import grade  # noqa: E402
from harness.io_utils import read_jsonl  # noqa: E402

RES = verify.Resources()
import glob  # noqa: E402
DRAFTS = [r for f in sorted(glob.glob(os.path.join(BASE, "data/raw/*_draft.jsonl"))) for r in read_jsonl(f)]
CORR = read_jsonl(os.path.join(BASE, "data/raw/corrections.jsonl"))
CLEAN, _, _ = verify.run(DRAFTS, CORR, RES)        # 套用修正後、已通過驗證的版本
BY_ID = {r["id"]: r for r in CLEAN}
RESULTS = []


def case(name):
    def deco(fn):
        try:
            fn()
            RESULTS.append((True, name, ""))
        except AssertionError as e:
            RESULTS.append((False, name, str(e)))
        return fn
    return deco


def row(i):
    r = copy.deepcopy(BY_ID[i])
    r.pop("revisions", None)
    r["verification"].pop("result", None)
    return r


def rejected_with(rows, keyword, corrections=()):
    cands, rej, _ = verify.run(rows, list(corrections), RES)
    reasons = " | ".join(";".join(r["reject_reasons"]) for r in rej)
    assert rej, f"應該被淘汰，卻全部通過（通過 {len(cands)} 筆）"
    assert keyword in reasons, f"淘汰原因應包含「{keyword}」，實際為：{reasons}"


# ======================= 正確案例：應該通過 =======================
@case("正確：所有草稿＋修正紀錄全部通過，規則執行題共 25 題")
def _():
    assert len(CLEAN) == len(DRAFTS), f"只通過 {len(CLEAN)}/{len(DRAFTS)} 筆"
    n_rule = sum(r["task_family"] == "rule_following" for r in CLEAN)
    assert n_rule == 25, f"規則執行題 {n_rule} 題"


@case("正確：每一題規則題都屬於「換 skill 答案就改變」的對照組")
def _():
    groups = {}
    for r in CLEAN:
        if r["task_family"] == "rule_following":
            groups.setdefault(r["group_id"], []).append(r)
    for g, rs in groups.items():
        assert len(rs) >= 2, f"{g} 只有 1 題，沒有對照版本"
        assert len({r["context"]["skill_id"] for r in rs}) == len(rs), f"{g} 有重複的 skill"
        assert len({r["answer"]["answer_exact"] for r in rs}) == len(rs), f"{g} 換 skill 後答案沒變"


@case("正確：重複執行結果完全相同")
def _():
    again, _, _ = verify.run(DRAFTS, CORR, RES)
    assert again == CLEAN


@case("正確：修正紀錄保留原答案、新答案、原因與修正前後的驗證結果")
def _():
    rev = BY_ID["doc_001"]["revisions"][0]
    assert rev["old"] == ["80"] and rev["new"] == ["80 元"] and rev["reason"]
    assert rev["verification_result"]["before"].startswith("失敗")
    assert rev["verification_result"]["after"] == "通過"


@case("正確：原始草稿檔沒有被修改")
def _():
    assert read_jsonl(os.path.join(BASE, "data/raw/tasks_draft.jsonl"))[0]["answer"]["answer_contains"] == ["80"]


# ======================= 錯誤案例：一定要被抓出來 =======================
@case("錯誤：規則題答案算錯（970 → 1000）")
def _():
    r = row("rule_002"); r["answer"]["answer_exact"] = "退款金額：1000 元"
    rejected_with([r], "重算")


@case("錯誤：新 skill 的答案手算錯誤（已拆封不退款，卻寫 100 元）")
def _():
    r = row("rule_005"); r["answer"]["answer_exact"] = "退款金額：100 元"
    rejected_with([r], "重算")


@case("錯誤：skill 沒有參考實作，無法獨立驗證")
def _():
    RES.skills["skill_mystery_v1"] = {"id": "skill_mystery_v1", "text": "# skill\n1. 隨便算", "output_regex": None, "checks": None}
    try:
        r = row("rule_001"); r["context"]["skill_id"] = "skill_mystery_v1"
        rejected_with([r], "沒有參考實作")
    finally:
        del RES.skills["skill_mystery_v1"]


@case("錯誤：skill 只換名字、內容相同，不算新的 skill")
def _():
    v1 = RES.skills["skill_refund_v1"]
    RES.skills["skill_refund_v9"] = dict(v1, id="skill_refund_v9", text=v1["text"].replace("退款金額計算規則", "退款規則（新版）"))
    try:
        a, b = row("rule_001"), row("rule_001")
        b["id"], b["context"]["skill_id"] = "rule_renamed", "skill_refund_v9"
        errs = verify.check_skill_variants([a, b], RES)
        assert any("只是換了名字" in e for e in errs.get("rule_renamed", [])), f"沒有抓到：{errs}"
    finally:
        del RES.skills["skill_refund_v9"]


@case("錯誤：skill 文字被改了，和參考實作不一致")
def _():
    original = RES.skills["skill_refund_v2"]["text"]
    RES.skills["skill_refund_v2"]["text"] = original.replace("0.8", "0.7")
    try:
        rejected_with([row("rule_002")], "不一致")
    finally:
        RES.skills["skill_refund_v2"]["text"] = original


@case("錯誤：split 不是 unassigned")
def _():
    r = row("doc_001"); r["split"] = "train"
    rejected_with([r], "unassigned")


@case("錯誤：缺少 group_id")
def _():
    r = row("doc_001"); del r["group_id"]
    rejected_with([r], "group_id")


@case("錯誤：引用不存在的來源")
def _():
    r = row("doc_001"); r["answer"]["sources"] = ["doc_shipping#p9"]
    rejected_with([r], "來源不存在")


@case("錯誤：答案片段是題目數字的一部分（80 vs 800）")
def _():
    r = row("doc_001"); r["answer"]["answer_contains"] = ["80"]
    rejected_with([r], "數字的一部分")


@case("錯誤：矛盾題的說法和文件不符")
def _():
    r = row("doc_002"); r["answer"]["values"] = ["7 天", "14 天"]
    rejected_with([r], "不一致")


@case("錯誤：「找不到」的題目，其實文件裡有")
def _():
    r = row("doc_003"); r["verification"]["params"]["terms"] = ["免運費"]
    rejected_with([r], "其實出現在文件中")


@case("錯誤：工具參數型別錯誤（數字寫成字串）")
def _():
    r = row("tool_001"); r["answer"]["args"]["amount"] = "100"
    rejected_with([r], "schema")


@case("錯誤：工具結果與答案不符（3200 → 3000）")
def _():
    r = row("tool_001"); r["answer"]["result"]["converted_amount"] = 3000.0
    rejected_with([r], "不符")


@case("錯誤：「沒有適用工具」，但其實有工具能處理")
def _():
    r = row("tool_002"); r["verification"]["params"]["capability_keywords"] = ["訂單"]
    rejected_with([r], "可能其實有適用工具")


@case("錯誤：「缺參數要詢問」，但題目其實給了參數")
def _():
    r = row("tool_003"); r["question"] = "幫我查一下訂單 ORD-123456 現在到哪了。"
    rejected_with([r], "已經有符合格式")


@case("錯誤：「工具失敗」題，但工具其實會成功")
def _():
    r = row("fix_002"); r["answer"]["args"]["order_id"] = "ORD-123456"
    rejected_with([r], "執行成功")


@case("錯誤：「需要修正」題，但題目中的值其實是對的")
def _():
    r = row("fix_001"); r["question"] = "訂單 ORD-123456 現在到哪了？"
    r["verification"]["params"]["value_in_question_must_fail"] = {"order_id": "ORD-123456"}
    rejected_with([r], "不需要修正")


@case("錯誤：反事實對照換了 skill，答案卻相同")
def _():
    a, b = row("rule_001"), row("rule_002")
    b["answer"] = copy.deepcopy(a["answer"])
    rejected_with([a, b], "重算")  # 規則題會先在重算階段被抓到
    # 沒有參考實作可重算的題型，靠群組檢查擋下：直接測群組檢查本身
    errs = verify.check_groups([a, b])
    assert any("答案相同" in e for e in errs.get(b["id"], [])), f"群組檢查沒有抓到：{errs}"


@case("錯誤：相同題目分在不同 group_id（切分時會洩漏）")
def _():
    a, b = row("rule_001"), row("rule_002")
    b["group_id"] = "grp_other"
    rejected_with([a, b], "不同 group_id")


@case("錯誤：id 重複")
def _():
    rejected_with([row("doc_001"), row("doc_001")], "id 重複")


@case("錯誤：修正紀錄的原值與資料不符（不可盲目覆寫）")
def _():
    bad = dict(CORR[0], old=["999"])
    d = copy.deepcopy(next(x for x in DRAFTS if x["id"] == "doc_001"))
    rejected_with([d], "原值與目前資料不符", corrections=[bad])


# ======================= 評分器：要能擋下「看起來對」的錯答 =======================
def g(task_id, answer):
    t = BY_ID[task_id]
    import re
    cites = sorted(set(re.findall(r"([A-Za-z0-9_]+#p\d+)", answer)))
    return grade(t, {"answer": answer, "citations": cites, "tool_calls": []}, RES.toolbox, set(RES.chunks))["success"]


@case("評分：運費 80 元 → 對；800 元、180 元 → 錯")
def _():
    assert g("doc_001", "未滿 1,000 元，運費 80 元 [來源: doc_shipping#p2]")
    assert not g("doc_001", "運費是 800 元 [來源: doc_shipping#p2]")
    assert not g("doc_001", "運費是 180 元 [來源: doc_shipping#p2]")


@case("評分：只說「矛盾」但沒寫出 7 天與 10 天 → 錯")
def _():
    src = "[來源: doc_returns#p1][來源: doc_faq_old#p1]"
    assert not g("doc_002", "兩份文件說法矛盾 " + src)
    assert g("doc_002", "文件矛盾：一份寫 7 天，另一份寫 10 天 " + src)


@case("評分：說「找不到」卻又給肯定答案 → 錯")
def _():
    assert not g("doc_003", "文件中找不到相關資訊，不過應該可以使用 LINE Pay。")
    assert g("doc_003", "參考文件中沒有提到 LINE Pay，我找不到答案。")


if __name__ == "__main__":
    ok = sum(r[0] for r in RESULTS)
    for passed, name, msg in RESULTS:
        print(("✓ " if passed else "✗ ") + name + ("" if passed else f"\n    {msg}"))
    print(f"\n{ok}/{len(RESULTS)} 個測試通過")
    sys.exit(0 if ok == len(RESULTS) else 1)
