"""匯入 Tülu 3 指令遵循資料，轉成「規則執行」題目。

資料來源（主線指定，固定版本）：
  allenai/tulu-3-sft-personas-instruction-following
  revision fe0c7d350c9b4542b8d829a6f1daa1c259f0ba0e（授權 ODC-BY）

做法：
1. 下載固定 revision（在 Colab 執行；或用 --input 指定已下載的 parquet / jsonl）
2. 用固定亂數種子打亂題目順序，依序檢查，直到收滿需要的題數
3. 從題目文字解析每條規則的參數（標籤只有類型，數字與關鍵字要從題目中解析）
4. 只收錄「每一條規則都能解析、也都能用程式檢查」的題目：
   - 標籤中有不支援的規則 → 淘汰
   - 解析不到參數，或解析出多個互相衝突的值 → 淘汰
   - 題目中出現標籤沒有列出的規則（例如要求特定結尾，但標籤沒寫）→ 淘汰
   - 含有「每一句／每一段都要…」這類逐句規則 → 淘汰（無法用整體計數驗證）
5. 資料集裡的參考回答只當作診斷：記錄它是否通過規則檢查，不作為標準答案

輸出：
  data/raw/tulu_draft.jsonl      收錄的題目（交給 verify.py 再驗證一次）
  data/raw/tulu_rejected.jsonl   檢查過但淘汰的題目與原因
  data/raw/tulu_import_log.json  種子、revision、檔案雜湊、各淘汰原因統計
  data/skills/tulu/*.md          每題對應的 skill（規則清單）

用法：
  python importers/tulu_import.py --n 23                     # 在 Colab 下載並匯入
  python importers/tulu_import.py --n 23 --input train.parquet
"""
import argparse
import collections
import glob
import hashlib
import json
import os
import random
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from harness.rule_checks import check_all, validate_rule  # noqa: E402

DATASET = "allenai/tulu-3-sft-personas-instruction-following"
REVISION = "fe0c7d350c9b4542b8d829a6f1daa1c259f0ba0e"
LICENSE = "ODC-BY"
SEED = 20261112

NUM_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
            "eighth": 8, "ninth": 9, "tenth": 10, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5}
NUM = r"(\d+|" + "|".join(NUM_WORDS) + r")"
REL = (r"(at least|no less than|a minimum of|minimum of|at most|no more than|not more than|a maximum of|maximum of|"
       r"not exceed(?:ing)?|less than|fewer than|under|more than|over|exactly|precisely)")
REL_MAP = {"at least": "at least", "no less than": "at least", "a minimum of": "at least", "minimum of": "at least",
           "at most": "at most", "no more than": "at most", "not more than": "at most", "a maximum of": "at most",
           "maximum of": "at most", "not exceed": "at most", "not exceeding": "at most",
           "less than": "less than", "fewer than": "less than", "under": "less than",
           "more than": "more than", "over": "more than", "exactly": "exactly", "precisely": "exactly", None: "exactly"}
Q_OPEN, Q_CLOSE = "\"“'‘", "\"”'’"
QUOTED = r"[\"“'‘]([^\"”'’\n]{1,40}?)[\"”'’]"


class ParseError(Exception):
    pass


def num(s):
    s = s.lower()
    return int(s) if s.isdigit() else NUM_WORDS[s]


def rel(s):
    return REL_MAP[s.lower() if s else None]


def one(values, what):
    """解析結果必須剛好一個（去重後）；零個或互相衝突都算解析失敗。"""
    uniq = []
    for v in values:
        if v not in uniq:
            uniq.append(v)
    if not uniq:
        raise ParseError(f"找不到{what}")
    if len(uniq) > 1:
        raise ParseError(f"{what}有多個不同的值：{uniq}")
    return uniq[0]


def sentences_with(prompt, pattern):
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", prompt) if re.search(pattern, s, re.I)]


# ---------------------------------------------------------------- 各標籤的解析器
def p_count(unit_pat, rtype, extra=None):
    def parse(prompt):
        neg_after = r"(?!\s+(?:in|with|written in|that are|that is)\s+(?:all\s+)?(?:capital|uppercase|caps|capitalized))"
        pat = rf"(?:{REL}\s+)?{NUM}[\s-]+{unit_pat}\b{neg_after}"
        found = [(rel(m.group(1)), num(m.group(2))) for m in re.finditer(pat, prompt, re.I)]
        r, n = one(found, f"{rtype} 的數量")
        params = {"relation": r, "n": n}
        if extra:
            params.update(extra(prompt))
        return [{"type": rtype, "params": params}]
    return parse


def _para_sep(prompt):
    return {"separator": "***" if "***" in prompt else "blank_line"}


def p_paragraphs(prompt):
    rules = p_count(r"(?:distinct\s+|separate\s+)?paragraphs?", "paragraph_count", _para_sep)(prompt)
    if rules[0]["params"]["relation"] != "exactly":
        raise ParseError("段落數只支援「剛好 N 段」")
    rules[0]["params"].pop("relation")
    return rules


def p_bullets(prompt):
    if not re.search(r"bullet\s*(?:points?|items?)|bullets\b|bullet-point", prompt, re.I):
        raise ParseError("只寫「bullet lists」，無法確定是指條列項目數還是清單數")
    pat = rf"(?:{REL}\s+)?{NUM}\s+(?:\w+\s+)?(?:bullet\s*(?:points?|items?)|bullets\b|bullet-point)"
    found = [(rel(m.group(1)), num(m.group(2))) for m in re.finditer(pat, prompt, re.I)]
    r, n = one(found, "條列數量")
    if r != "exactly":
        raise ParseError("條列數只支援「剛好 N 項」")
    return [{"type": "bullet_count", "params": {"n": n}}]


def p_title(prompt):
    if not re.search(r"<<|angular bracket|angle bracket", prompt, re.I):
        raise ParseError("題目沒有說明標題格式（<<title>>），無法確定檢查方式")
    return [{"type": "title", "params": {}}]


def p_include(prompt):
    sents = sentences_with(prompt, r"\b(include|includ|use|using|mention|incorporate|contain|feature)\w*\b.*\bkeywords?\b|\bkeywords?\b")
    words = [w.strip() for s in sents for w in re.findall(QUOTED, s)]
    if not words:
        raise ParseError("找不到要包含的關鍵字（需以引號標示）")
    return [{"type": "include_keywords", "params": {"keywords": list(dict.fromkeys(words))}}]


def p_exclude(prompt):
    sents = sentences_with(prompt, r"exclud|without (?:using|mentioning|the words?)|do not (?:use|include|mention)|don't (?:use|include|mention)|avoid(?:ing)? (?:using|the words?)|must not (?:use|include|contain)|not (?:use|include) the words?")
    words = [w.strip() for s in sents for w in re.findall(QUOTED, s)]
    if not words:
        raise ParseError("找不到要排除的字（需以引號標示）")
    return [{"type": "exclude_keywords", "params": {"keywords": list(dict.fromkeys(words))}}]


def p_keyword_freq(prompt):
    pat = rf"{QUOTED}[^.\n]{{0,50}}?(?:{REL}\s+)?{NUM}\s+times"
    found = [(m.group(1).strip(), rel(m.group(2)), num(m.group(3))) for m in re.finditer(pat, prompt, re.I)]
    found = [f for f in found if len(f[0]) > 1]  # 單一字母是字母頻率，不是關鍵字頻率
    rules = []
    for kw, r, n in dict.fromkeys(found):
        rules.append({"type": "keyword_frequency", "params": {"keyword": kw, "relation": r, "n": n}})
    if not rules:
        raise ParseError("找不到關鍵字出現次數")
    if len({r["params"]["keyword"].lower() for r in rules}) != len(rules):
        raise ParseError("同一個關鍵字有多個不同的次數要求")
    return rules


def p_letter_freq(prompt):
    pats = [rf"letter\s+[\"“'‘]?([A-Za-z])[\"”'’]?[^.\n]{{0,60}}?(?:{REL}\s+)?{NUM}\s+times",
            rf"(?:{REL}\s+)?{NUM}\s+times[^.\n]{{0,30}}?letter\s+[\"“'‘]?([A-Za-z])\b"]
    found = []
    for m in re.finditer(pats[0], prompt, re.I):
        found.append((m.group(1).lower(), rel(m.group(2)), num(m.group(3))))
    for m in re.finditer(pats[1], prompt, re.I):
        found.append((m.group(3).lower(), rel(m.group(1)), num(m.group(2))))
    letter, r, n = one(found, "字母出現次數")
    return [{"type": "letter_frequency", "params": {"letter": letter, "relation": r, "n": n}}]


def p_capital_freq(prompt):
    caps = r"(?:capital(?:ized)?(?:\s+letters)?|uppercase(?:\s+letters)?|all\s+caps|caps)"
    pats = [rf"(?:{REL}\s+)?{NUM}\s+(?:\w+\s+)?words?\s+(?:in|with|written in|that are|that is)\s+(?:all\s+)?{caps}",
            rf"(?:{REL}\s+)?{NUM}\s+(?:all[- ])?(?:capital(?:ized)?|uppercase|caps)\s+words",
            rf"words\s+(?:in|with|written in)\s+(?:all\s+)?{caps}[^.\n]{{0,40}}?(?:{REL}\s+)?{NUM}\s+times",
            rf"(?:all[- ])?(?:capital(?:ized)?|uppercase)\s+words[^.\n]{{0,40}}?(?:{REL}\s+)?{NUM}\s+times"]
    found = []
    for pat in pats:
        found += [(rel(m.group(1)), num(m.group(2))) for m in re.finditer(pat, prompt, re.I)]
    r, n = one(found, "全大寫單字數")
    return [{"type": "capital_word_frequency", "params": {"relation": r, "n": n}}]


def p_end(prompt):
    pat = (r"(?:end|conclude|finish|close)(?:s|ing)?\s+(?:your\s+\w+\s+|the\s+\w+\s+|it\s+|each\s+\w+\s+)?with\s+"
           r"(?:the\s+)?(?:exact\s+|specific\s+|following\s+)?(?:phrase|sentence|words?|line|statement)?:?\s*"
           r"[\"“]([^\"”\n]{2,120})[\"”]")
    phrase = one([m.group(1).strip() for m in re.finditer(pat, prompt, re.I)], "結尾句")
    if re.search(r"(?:each|every)\s+(?:paragraph|section|sentence|verse|stanza)\s+(?:should|must)?\s*(?:end|conclude)", prompt, re.I):
        raise ParseError("結尾規則是逐段的，無法用整體檢查")
    return [{"type": "end_phrase", "params": {"phrase": phrase}}]


def p_postscript(prompt):
    m = re.findall(r"\b(P\.\s?P\.\s?S|P\.\s?S)\.?", prompt)
    marker = one([x.replace(" ", "") + "." for x in m], "附註標記（P.S.）") if m else "P.S."
    if not re.search(r"P\.\s?S|postscript", prompt, re.I):
        raise ParseError("找不到附註規則")
    return [{"type": "postscript", "params": {"marker": marker}}]


def p_highlight(prompt):
    pat = rf"highlight(?:ed)?\s+(?:(at least)\s+)?{NUM}\s+(?:\w+\s+)?(?:sections?|parts?|phrases?|points?|words?)"
    found = [(rel(m.group(1) or "at least"), num(m.group(2))) for m in re.finditer(pat, prompt, re.I)]
    r, n = one(found, "強調段落數")
    return [{"type": "highlighted_sections", "params": {"relation": r, "n": n}}]


def p_lower(prompt):
    return [{"type": "all_lowercase", "params": {}}, {"type": "english_only", "params": {}}]


def p_upper(prompt):
    return [{"type": "all_uppercase", "params": {}}, {"type": "english_only", "params": {}}]


def p_first_word(prompt):
    n_par = p_count(r"(?:distinct\s+|separate\s+)?paragraphs?", "paragraph_count")(prompt)[0]["params"]
    if n_par["relation"] != "exactly":
        raise ParseError("段落數不是固定值")
    pat = (rf"(\d+(?:st|nd|rd|th)|{'|'.join(ORDINALS)})\s+paragraph[^.\n]{{0,40}}?(?:start|begin)s?\s+with\s+"
           rf"(?:the\s+word\s+)?[\"“'‘]?([A-Za-z]+)")
    found = [(ORDINALS[m.group(1).lower()], m.group(2)) for m in re.finditer(pat, prompt, re.I)]
    nth, word = one(found, "第 N 段的開頭字")
    return [{"type": "first_word_of_paragraph", "params": {"nth": nth, "word": word, "n_paragraphs": n_par["n"]}}]


def p_json(prompt):
    return [{"type": "json_format", "params": {}}]


def p_quotes(prompt):
    return [{"type": "wrap_in_quotes", "params": {}}]


def p_no_comma(prompt):
    return [{"type": "no_comma", "params": {}}]


# 標籤（正規化後）→ 解析器
TAG_PARSERS = {
    "punctuation:use no comma": p_no_comma,
    "length constraints:number of words": p_count(r"words?", "word_count"),
    "length constraints:number of sentences": p_count(r"sentences?", "sentence_count"),
    "length constraints:number of paragraphs": p_paragraphs,
    "length constraints:first word of the nth paragraph": p_first_word,
    "format:number of bullet lists": p_bullets,
    "format:title": p_title,
    "format:number of highlighted sections": p_highlight,
    "format:use json format": p_json,
    "keywords:exclude words": p_exclude,
    "keywords:forbidden words": p_exclude,
    "include keywords": p_include,
    "keywords:existence": p_include,
    "keywords:include keywords": p_include,
    "keywords:frequency": p_keyword_freq,
    "keywords:letter frequency": p_letter_freq,
    "case:frequency of capital words": p_capital_freq,
    "case:in english and lowercase": p_lower,
    "in english and lowercase": p_lower,
    "case:in english and capital": p_upper,
    "in english and capital": p_upper,
    "startend:end checker": p_end,
    "end checker": p_end,
    "detectable content:postscript": p_postscript,
    "content:postscript": p_postscript,
    "postscript": p_postscript,
    "startend:quotation": p_quotes,
    "use quotation": p_quotes,
}


def norm_tag(t):
    t = re.sub(r"\s*:\s*", ":", t.strip().lower())
    t = re.sub(r"[_]+", " ", t)
    return re.sub(r"\s+", " ", t)


# 題目裡出現這些說法，但沒有對應的已解析規則 → 代表有未標記的規則
UNTAGGED_SIGNALS = [
    ("no_comma", r"\bcommas?\b"),
    ("end_phrase", r"\b(?:end|conclude|finish)(?:s|ing)?\s+(?:your\s+\w+\s+|the\s+\w+\s+|it\s+)?with\b"),
    ("postscript", r"\bP\.\s?S\b|postscript"),
    ("title", r"<<|angular bracket|angle bracket"),
    ("json_format", r"\bJSON\b"),
    ("wrap_in_quotes", r"double quotation|quotation marks"),
    ("highlighted_sections", r"\bhighlight"),
    ("all_lowercase", r"lower\s*case"),
    ("capital", r"\bcapital(?:ized)?\b|upper\s*case|all caps|\bCAPS\b"),
    ("word_count", rf"{NUM}[\s-]+words?\b"),
    ("sentence_count", rf"{NUM}[\s-]+sentences?\b"),
    ("paragraph_count", rf"{NUM}[\s-]+(?:distinct\s+|separate\s+)?paragraphs?\b|\*\*\*"),
    ("bullet_count", r"\bbullet"),
    ("letter_frequency", r"\bletter\s+[\"“'‘]?[A-Za-z][\"”'’]?(?:\s|$)"),
    ("frequency", r"\btimes\b"),
    ("include_keywords", r"\bkeywords?\b"),
    ("exclude_keywords", r"(?:exclud\w*|without (?:using|mentioning)|do not (?:use|include|mention)|don't (?:use|include|mention)|"
                         r"avoid(?:ing)? (?:using|mentioning)?|must not (?:use|include|contain))\s*(?:the\s+)?(?:words?|terms?|[\"“'‘])"),
]
SIGNAL_SATISFIED_BY = {
    "capital": {"all_uppercase", "capital_word_frequency"},
    "frequency": {"keyword_frequency", "letter_frequency", "capital_word_frequency"},
    "paragraph_count": {"paragraph_count", "first_word_of_paragraph"},
    "word_count": {"word_count", "capital_word_frequency"},
}
UNSUPPORTED_SIGNALS = [
    (r"\b(?:each|every|per)\s+(?:sentence|paragraph|bullet(?:\s+point)?|line|section|verse|stanza|item)\b", "含逐句／逐段規則，無法用整體計數驗證"),
    (r"repeat the (?:request|prompt|question|instruction)", "要求重複題目（不支援）"),
    (r"\bSECTION\s+X\b|\bSection\s+\d|mark the beginning of each section", "要求分節標記（不支援）"),
    (r"placeholders?|\[[a-z _]+\]", "要求佔位符（不支援）"),
    (r"one of the following (?:exact )?(?:options|responses)|choose one from", "要求從選項中選一個（不支援）"),
    (r"\bin (?:spanish|french|german|italian|portuguese|chinese|japanese|korean|arabic|hindi|russian)\b|language", "要求特定語言（不支援）"),
    (r"two (?:different )?responses|separated by six asterisk", "要求兩個回答（不支援）"),
    (r"\b(?:start|begin)(?:s|ning)?\s+(?:your|the)\s+(?:response|answer|reply|text|essay|article|story)\s+with\b", "要求特定開頭（不支援）"),
]


def parse_row(prompt, tags):
    """回傳 (rules, 錯誤清單)。錯誤清單非空代表淘汰。"""
    errs, rules = [], []
    for t in tags:
        key = norm_tag(t)
        parser = TAG_PARSERS.get(key)
        if parser is None:
            errs.append(f"不支援的規則標籤：{t}")
            continue
        try:
            rules += parser(prompt)
        except ParseError as e:
            errs.append(f"標籤「{t}」解析失敗：{e}")
    for pat, reason in UNSUPPORTED_SIGNALS:
        if re.search(pat, prompt, re.I):
            errs.append(reason)
    produced = {r["type"] for r in rules}
    for name, pat in UNTAGGED_SIGNALS:
        if re.search(pat, prompt, re.I):
            ok_types = SIGNAL_SATISFIED_BY.get(name, {name})
            if not produced & ok_types:
                errs.append(f"題目中疑似有未標記的規則（{name}），標籤沒有列出")
    for r in rules:
        errs += validate_rule(r)
    # 去除重複規則（例如兩個標籤都產生 english_only）
    uniq = []
    for r in rules:
        if r not in uniq:
            uniq.append(r)
    return uniq, errs


# ---------------------------------------------------------------- skill 與題目
def describe(rule):
    p = rule["params"]
    t = rule["type"]
    d = {
        "no_comma": "Do not use any commas.",
        "all_lowercase": "Write the entire response in lowercase letters.",
        "all_uppercase": "Write the entire response in capital letters.",
        "english_only": "Respond in English.",
        "title": "Include a title wrapped in double angular brackets, e.g. <<title>>.",
        "json_format": "Format the entire response as valid JSON.",
        "wrap_in_quotes": "Wrap the entire response in double quotation marks.",
    }
    if t in d:
        return d[t]
    if t in ("word_count", "sentence_count"):
        return f"Use {p['relation']} {p['n']} {'words' if t == 'word_count' else 'sentences'}."
    if t == "paragraph_count":
        sep = "separated by ***" if p.get("separator") == "***" else "separated by blank lines"
        return f"Write exactly {p['n']} paragraphs, {sep}."
    if t == "bullet_count":
        return f"Include exactly {p['n']} bullet points, each starting with '* '."
    if t == "include_keywords":
        return "Include the keywords: " + ", ".join(f'"{k}"' for k in p["keywords"]) + "."
    if t == "exclude_keywords":
        return "Do not use the words: " + ", ".join(f'"{k}"' for k in p["keywords"]) + "."
    if t == "keyword_frequency":
        return f"Use the word \"{p['keyword']}\" {p['relation']} {p['n']} times."
    if t == "letter_frequency":
        return f"Use the letter \"{p['letter']}\" {p['relation']} {p['n']} times."
    if t == "capital_word_frequency":
        return f"Use {p['relation']} {p['n']} words written entirely in capital letters."
    if t == "end_phrase":
        return f"End the response with the exact phrase \"{p['phrase']}\"."
    if t == "postscript":
        return f"Add a postscript at the end starting with {p['marker']}"
    if t == "highlighted_sections":
        return f"Highlight {p['relation']} {p['n']} sections with markdown, e.g. *highlighted section*."
    if t == "first_word_of_paragraph":
        return (f"Write exactly {p['n_paragraphs']} paragraphs separated by blank lines; "
                f"paragraph {p['nth']} must start with the word \"{p['word']}\".")
    raise ValueError(t)


def skill_markdown(skill_id, rules, row_id):
    lines = [f"# skill: writing rules ({row_id})",
             f"<!-- skill_id: {skill_id} -->",
             "<!-- checks: " + json.dumps(rules, ensure_ascii=False) + " -->", ""]
    lines += [f"{i}. {describe(r)}" for i, r in enumerate(rules, 1)]
    return "\n".join(lines) + "\n"


def short_id(row_id):
    """用完整的資料集 id（去掉前綴），不可截斷，否則不同題目可能撞在同一個 id。"""
    return re.sub(r"[^A-Za-z0-9]", "_", re.sub(r"^personas_IF_", "", row_id))


def to_task(row, rules, ref_violations):
    sid = short_id(row["id"])
    return {
        "id": f"rule_tulu_{sid}",
        "task_family": "rule_following",
        "group_id": f"grp_tulu_{row['id']}",
        "source": {"type": "public_dataset", "name": DATASET, "revision": REVISION, "license": LICENSE,
                   "row_id": row["id"], "created_by": "Allen AI（資料集）；規則參數由程式解析",
                   "reviewed_by": None,
                   "generation_method": f"固定 revision，以種子 {SEED} 抽樣；規則參數由 importers/tulu_import.py 從題目文字解析；資料集參考回答不作為標準答案"},
        "question": row["prompt"],
        "context": {"tools": [], "skill_id": f"skill_tulu_{sid}"},
        "answer": {"expect": "rules", "rules": rules},
        "constraints": list(row["constraints"]),
        "verification": {"method": "rule_checks", "params": {
            "reference_response_diagnostic": {
                "passed_all_rules": not ref_violations,
                "violations": [v[0] for v in ref_violations],
                "note": "資料集參考回答是否通過解析出的規則，僅供判斷解析是否合理，不影響收錄"}}},
        "split": "unassigned",
        "tags": ["tulu", "instruction_following"],
    }


# ---------------------------------------------------------------- 讀取資料
def load(input_path=None):
    if input_path is None:
        from huggingface_hub import snapshot_download  # Colab 已內建；不需要 token
        local = snapshot_download(DATASET, repo_type="dataset", revision=REVISION, allow_patterns=["*.parquet"])
        files = sorted(glob.glob(os.path.join(local, "**", "*.parquet"), recursive=True))
    else:
        files = [input_path]
    rows = []
    for f in files:
        if f.endswith(".jsonl"):
            with open(f, encoding="utf-8") as fh:
                rows += [json.loads(l) for l in fh if l.strip()]
        else:
            import pandas as pd
            rows += pd.read_parquet(f).to_dict("records")
    h = hashlib.sha256()
    for f in files:
        with open(f, "rb") as fh:
            h.update(fh.read())
    for r in rows:
        r["constraints"] = [str(c) for c in list(r.get("constraints") or [])]
        r["messages"] = [dict(m) for m in list(r.get("messages") or [])]
    return rows, [os.path.basename(f) for f in files], h.hexdigest()[:16]


def reference_response(row):
    for m in row.get("messages", []):
        if m.get("role") == "assistant":
            return m.get("content", "")
    return ""


def run(rows, n_needed, seed=SEED):
    order = list(range(len(rows)))
    random.Random(seed).shuffle(order)
    accepted, rejected = [], []
    reasons = collections.Counter()
    for idx in order:
        if len(accepted) >= n_needed:
            break
        row = rows[idx]
        rules, errs = parse_row(row["prompt"], row["constraints"])
        if not rules and not errs:
            errs = ["沒有任何規則"]
        if errs:
            rejected.append({"row_id": row["id"], "stage": "import", "constraints": row["constraints"],
                             "prompt": row["prompt"], "reject_reasons": errs})
            for e in errs:
                reasons[re.sub(r"：.*", "", e)] += 1
            continue
        accepted.append((row, rules, check_all(reference_response(row), rules)))
    return accepted, rejected, reasons, len(accepted) + len(rejected)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=23, help="要收錄幾題")
    ap.add_argument("--input", default=None, help="已下載的 parquet 或 jsonl；不指定就從 Hugging Face 下載固定 revision")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    rows, files, data_hash = load(args.input)
    accepted, rejected, reasons, examined = run(rows, args.n, args.seed)

    skill_dir = os.path.join(BASE, "data/skills/tulu")
    os.makedirs(skill_dir, exist_ok=True)
    for f in glob.glob(os.path.join(skill_dir, "*.md")):
        os.remove(f)
    tasks = []
    for row, rules, ref_v in accepted:
        t = to_task(row, rules, ref_v)
        with open(os.path.join(skill_dir, t["context"]["skill_id"] + ".md"), "w", encoding="utf-8") as fh:
            fh.write(skill_markdown(t["context"]["skill_id"], rules, row["id"]))
        tasks.append(t)

    raw = os.path.join(BASE, "data/raw")
    with open(os.path.join(raw, "tulu_draft.jsonl"), "w", encoding="utf-8") as fh:
        for t in tasks:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")
    with open(os.path.join(raw, "tulu_rejected.jsonl"), "w", encoding="utf-8") as fh:
        for r in rejected:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    log = {"dataset": DATASET, "revision": REVISION, "license": LICENSE, "files": files,
           "data_sha256_16": data_hash, "total_rows": len(rows), "seed": args.seed,
           "requested": args.n, "examined": examined, "accepted": len(tasks), "rejected": len(rejected),
           "reject_reason_counts": dict(reasons.most_common()),
           "reference_response_pass_rate": round(sum(not a[2] for a in accepted) / max(len(accepted), 1), 3)}
    with open(os.path.join(raw, "tulu_import_log.json"), "w", encoding="utf-8") as fh:
        json.dump(log, fh, ensure_ascii=False, indent=2)

    print(f"共 {len(rows)} 題，檢查 {examined} 題：收錄 {len(tasks)}、淘汰 {len(rejected)}")
    print("淘汰原因：")
    for k, v in reasons.most_common(12):
        print(f"  {v:>4}  {k}")
    print(f"參考回答通過全部規則的比例：{log['reference_response_pass_rate']:.0%}")
    if len(tasks) < args.n:
        print(f"⚠ 只收到 {len(tasks)} 題，少於需要的 {args.n} 題")


if __name__ == "__main__":
    main()
