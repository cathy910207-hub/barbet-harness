"""測試規則檢查器：每種規則都要有「應該通過」與「應該失敗」的回答。

執行：python tests/test_rule_checks.py
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from harness.rule_checks import CHECKERS, check, validate_rule  # noqa: E402

R = lambda t, **p: {"type": t, "params": p}  # noqa: E731

# (規則, 應該通過的回答, 應該失敗的回答)
CASES = [
    (R("no_comma"), "Go team go", "Go, team"),
    (R("all_lowercase"), "we dance all night.", "We dance all night."),
    (R("all_uppercase"), "WE DANCE ALL NIGHT.", "WE dance."),
    (R("word_count", relation="at most", n=5), "one two three four five", "one two three four five six"),
    (R("word_count", relation="at least", n=3), "one two three", "one two"),
    (R("sentence_count", relation="exactly", n=2), "First one. Second one!", "Only one."),
    (R("sentence_count", relation="exactly", n=3), 'He said "go." Then we ran. It worked?', "One. Two."),
    (R("paragraph_count", n=2, separator="blank_line"), "Para one.\n\nPara two.", "Para one.\nStill one."),
    (R("paragraph_count", n=3, separator="***"), "A\n***\nB\n***\nC", "A\n***\nB"),
    (R("bullet_count", n=3), "Intro\n* a\n* b\n- c", "* a\n* b"),
    (R("bullet_count", n=1), "**Bold heading**\n* only item", "**Bold heading**\nno bullets"),
    (R("title"), "<<My Song>>\nLa la", "My Song\nLa la"),
    (R("include_keywords", keywords=["roar", "victory"]), "We ROAR to victory", "We roar loudly"),
    (R("exclude_keywords", keywords=["block"]), "She defends well", "She can Block shots"),
    (R("exclude_keywords", keywords=["art"]), "A smart start", "Fine art matters"),
    (R("keyword_frequency", keyword="speed", relation="exactly", n=2), "speed and Speed", "speed speed speed"),
    (R("letter_frequency", letter="r", relation="at least", n=3), "Raceror", "Run"),
    (R("capital_word_frequency", relation="exactly", n=2), "GO TEAM win", "GO team win"),
    (R("capital_word_frequency", relation="less than", n=2), "One BIG idea", "BIG BOLD IDEA"),
    (R("end_phrase", phrase="The end."), "Story here. The End.", "The end. More text."),
    (R("postscript", marker="P.S."), "Body\n\nP.S. see you", "Body only, PS no"),
    (R("highlighted_sections", n=2), "*one* and *two*", "*one* only"),
    (R("json_format"), '```json\n{"a": 1}\n```', "{a: 1}"),
    (R("english_only"), "all in english here", "全部用中文回答"),
    (R("wrap_in_quotes"), '"quoted"', "not quoted"),
    (R("first_word_of_paragraph", nth=2, word="Inspired", n_paragraphs=2),
     "Intro.\n\nInspired by light.", "Intro.\n\nLight inspired me."),
]

INVALID_RULES = [
    {"type": "unknown_rule", "params": {}},
    R("word_count", relation="about", n=5),
    R("word_count", relation="at most"),
    R("word_count", relation="at most", n="5"),
    R("letter_frequency", letter="ab", relation="at least", n=1),
    R("include_keywords", keywords=[]),
    R("sentence_count", relation="exactly", n=True),
]


def main():
    fails = []
    for rule, good, bad in CASES:
        if validate_rule(rule):
            fails.append(f"合法規則被判為不合法：{rule} → {validate_rule(rule)}")
        ok, msg = check(good, rule)
        if not ok:
            fails.append(f"{rule['type']} 應通過卻失敗：{good!r}（{msg}）")
        ok, msg = check(bad, rule)
        if ok:
            fails.append(f"{rule['type']} 應失敗卻通過：{bad!r}")
        elif not msg:
            fails.append(f"{rule['type']} 失敗時沒有說明原因")
    for rule in INVALID_RULES:
        if not validate_rule(rule):
            fails.append(f"不合法的規則沒有被抓到：{rule}")
    covered = {r["type"] for r, _, _ in CASES}
    missing = set(CHECKERS) - covered
    if missing:
        fails.append(f"以下規則沒有測試案例：{sorted(missing)}")

    total = len(CASES) * 2 + len(INVALID_RULES) + 1
    for f in fails:
        print("✗ " + f)
    print(f"{total - len(fails)}/{total} 個檢查通過（{len(CHECKERS)} 種規則）")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
