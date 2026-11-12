"""可用程式驗證的寫作規則（IFEval 類型）。

每條規則表示成 {"type": 規則類型, "params": {...}}，例如：
  {"type": "word_count", "params": {"relation": "at most", "n": 100}}

check(text, rule) → (是否通過, 說明)。說明只描述「回答違反了哪條已提供的規則」，
不包含任何標準答案，所以可以安全地當作 C 組的修正回饋。

這份檔案同時被三個地方使用：
  verify.py（檢查規則參數是否合法）、grader.py（評分）、verifier.py（C 組回饋）。
"""
import json
import re

RELATIONS = {
    "at least": lambda x, n: x >= n,
    "at most": lambda x, n: x <= n,
    "exactly": lambda x, n: x == n,
    "less than": lambda x, n: x < n,
    "more than": lambda x, n: x > n,
}
REL_ZH = {"at least": "至少", "at most": "至多", "exactly": "剛好", "less than": "少於", "more than": "多於"}


def _rel(count, params, unit):
    rel, n = params["relation"], params["n"]
    ok = RELATIONS[rel](count, n)
    return ok, f"{unit}數為 {count}，規則要求{REL_ZH[rel]} {n}"


def _words(text):
    return re.findall(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?", text)


def _sentences(text):
    parts = re.split(r"(?<=[.!?])[\"'”’)\]]*\s+", text.strip())
    return [p for p in parts if re.search(r"[A-Za-z0-9]", p)]


def _paragraphs(text, separator):
    if separator == "***":
        parts = re.split(r"\s*\*\*\*\s*", text.strip())
    else:
        parts = re.split(r"\n\s*\n", text.strip())
    return [p for p in parts if p.strip()]


def _bullets(text):
    return [l for l in text.splitlines() if re.match(r"^\s*[\*\-]\s+\S", l) and not re.match(r"^\s*\*\*", l)]


def _strip_fence(text):
    t = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
    return m.group(1) if m else t


# ---------------------------------------------------------------- 各規則
def no_comma(text, p):
    n = text.count(",") + text.count("，")
    return n == 0, f"回答中有 {n} 個逗號，規則要求不能使用逗號"


def all_lowercase(text, p):
    bad = [w for w in _words(text) if w != w.lower()][:5]
    return not bad, f"規則要求全部小寫，但出現大寫：{bad}"


def all_uppercase(text, p):
    bad = [w for w in _words(text) if w != w.upper()][:5]
    return not bad, f"規則要求全部大寫，但出現小寫：{bad}"


def word_count(text, p):
    return _rel(len(_words(text)), p, "英文字")


def sentence_count(text, p):
    return _rel(len(_sentences(text)), p, "句")


def paragraph_count(text, p):
    paras = _paragraphs(text, p.get("separator", "blank_line"))
    sep = "以 *** 分隔" if p.get("separator") == "***" else "以空行分隔"
    ok, msg = _rel(len(paras), dict(p, relation=p.get("relation", "exactly")), f"段落（{sep}）")
    return ok, msg


def bullet_count(text, p):
    return _rel(len(_bullets(text)), dict(p, relation=p.get("relation", "exactly")), "條列項目（以 * 或 - 開頭）")


def title(text, p):
    ok = re.search(r"<<[^\n<>]+>>", text) is not None
    return ok, "規則要求標題以雙尖括號包住，例如 <<title>>，但回答中沒有"


def include_keywords(text, p):
    low = text.lower()
    miss = [k for k in p["keywords"] if not re.search(r"\b" + re.escape(k.lower()) + r"\b", low)]
    return not miss, f"缺少必須出現的關鍵字：{miss}"


def exclude_keywords(text, p):
    low = text.lower()
    hit = [k for k in p["keywords"] if re.search(r"\b" + re.escape(k.lower()) + r"\b", low)]
    return not hit, f"出現了不可使用的字：{hit}"


def keyword_frequency(text, p):
    n = len(re.findall(r"\b" + re.escape(p["keyword"].lower()) + r"\b", text.lower()))
    return _rel(n, p, f"「{p['keyword']}」出現次")


def letter_frequency(text, p):
    n = text.lower().count(p["letter"].lower())
    return _rel(n, p, f"字母「{p['letter']}」出現次")


def capital_word_frequency(text, p):
    n = sum(1 for w in _words(text) if w.isupper())
    return _rel(n, p, "全大寫單字")


def end_phrase(text, p):
    ok = text.strip().lower().rstrip("\"'”’").endswith(p["phrase"].strip().lower().rstrip("\"'”’"))
    return ok, f"規則要求以「{p['phrase']}」結尾"


def postscript(text, p):
    marker = re.escape(p.get("marker", "P.S.")).replace(r"\ ", r"\s*")
    ok = re.search(r"(?im)^\s*" + marker, text) is not None
    return ok, f"規則要求在結尾加上以「{p.get('marker', 'P.S.')}」開頭的附註"


def highlighted_sections(text, p):
    n = len([h for h in re.findall(r"\*([^\n\*]+)\*", text) if h.strip()])
    return _rel(n, dict(p, relation=p.get("relation", "at least")), "以 *星號* 強調的段落")


def json_format(text, p):
    try:
        json.loads(_strip_fence(text))
        return True, ""
    except Exception:
        return False, "規則要求整個回答是合法的 JSON"


def english_only(text, p):
    """全大寫／全小寫規則要搭配這條：中文沒有大小寫，否則用中文回答會「自動通過」。"""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False, "回答中沒有任何英文字母，規則要求用英文回答"
    ratio = sum(c.isascii() for c in letters) / len(letters)
    return ratio >= 0.95, f"英文字母只占 {ratio:.0%}，規則要求用英文回答"


def wrap_in_quotes(text, p):
    t = text.strip()
    return len(t) > 1 and t[0] == '"' and t[-1] == '"', "規則要求整個回答以雙引號包住"


def first_word_of_paragraph(text, p):
    paras = _paragraphs(text, "blank_line")
    if len(paras) != p["n_paragraphs"]:
        return False, f"段落數為 {len(paras)}，規則要求剛好 {p['n_paragraphs']}"
    if p["nth"] > len(paras):
        return False, f"沒有第 {p['nth']} 段"
    first = re.sub(r"^[^A-Za-z]+", "", paras[p["nth"] - 1]).split()
    w = re.sub(r"[^A-Za-z]", "", first[0]).lower() if first else ""
    return w == p["word"].lower(), f"第 {p['nth']} 段應以「{p['word']}」開頭，實際為「{w}」"


CHECKERS = {
    "no_comma": (no_comma, {}),
    "all_lowercase": (all_lowercase, {}),
    "all_uppercase": (all_uppercase, {}),
    "word_count": (word_count, {"relation": str, "n": int}),
    "sentence_count": (sentence_count, {"relation": str, "n": int}),
    "paragraph_count": (paragraph_count, {"n": int}),
    "bullet_count": (bullet_count, {"n": int}),
    "title": (title, {}),
    "include_keywords": (include_keywords, {"keywords": list}),
    "exclude_keywords": (exclude_keywords, {"keywords": list}),
    "keyword_frequency": (keyword_frequency, {"keyword": str, "relation": str, "n": int}),
    "letter_frequency": (letter_frequency, {"letter": str, "relation": str, "n": int}),
    "capital_word_frequency": (capital_word_frequency, {"relation": str, "n": int}),
    "end_phrase": (end_phrase, {"phrase": str}),
    "postscript": (postscript, {}),
    "highlighted_sections": (highlighted_sections, {"n": int}),
    "json_format": (json_format, {}),
    "english_only": (english_only, {}),
    "wrap_in_quotes": (wrap_in_quotes, {}),
    "first_word_of_paragraph": (first_word_of_paragraph, {"nth": int, "word": str, "n_paragraphs": int}),
}


def validate_rule(rule):
    """回傳錯誤清單；空清單代表規則格式正確。"""
    if not isinstance(rule, dict) or rule.get("type") not in CHECKERS:
        return [f"未知的規則類型：{rule.get('type') if isinstance(rule, dict) else rule}"]
    _, need = CHECKERS[rule["type"]]
    p, errs = rule.get("params", {}), []
    for k, t in need.items():
        if k not in p:
            errs.append(f"{rule['type']} 缺少參數 {k}")
        elif not isinstance(p[k], t) or isinstance(p[k], bool):
            errs.append(f"{rule['type']} 的參數 {k} 型別應為 {t.__name__}")
    if "relation" in p and p["relation"] not in RELATIONS:
        errs.append(f"{rule['type']} 的 relation 不合法：{p['relation']}")
    if isinstance(p.get("n"), int) and p["n"] < 0:
        errs.append(f"{rule['type']} 的 n 不可為負數")
    if rule["type"] == "letter_frequency" and len(p.get("letter", "")) != 1:
        errs.append("letter_frequency 的 letter 必須是單一字母")
    if rule["type"] in ("include_keywords", "exclude_keywords") and not p.get("keywords"):
        errs.append(f"{rule['type']} 的 keywords 不可為空")
    return errs


def check(text, rule):
    fn, _ = CHECKERS[rule["type"]]
    ok, msg = fn(text or "", rule.get("params", {}))
    return ok, ("" if ok else msg)


def check_all(text, rules):
    """回傳所有違反的規則：[(type, 說明)]"""
    out = []
    for r in rules:
        ok, msg = check(text, r)
        if not ok:
            out.append((r["type"], msg))
    return out
