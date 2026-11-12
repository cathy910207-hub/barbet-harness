"""測試 Tülu 題目解析器：能解析的要解析正確，有疑慮的要淘汰。

題目是仿照資料集風格自行撰寫的測試案例（不是資料集原文）。
執行：python tests/test_tulu_parse.py
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from importers.tulu_import import parse_row  # noqa: E402

R = lambda t, **p: {"type": t, "params": p}  # noqa: E731

ACCEPT = [
    ("關鍵字＋全大寫字數＋不用逗號",
     'Write a fan chant for the Riverside Hawks. Include the keywords "roar", "victory", and "spirit". '
     'Use exactly 4 words in all capital letters and do not use any commas.',
     ["punctuation:use no comma", "case: frequency of capital words", "include keywords"],
     [R("no_comma"), R("capital_word_frequency", relation="exactly", n=4),
      R("include_keywords", keywords=["roar", "victory", "spirit"])]),
    ("排除字",
     'Analyze the team captain\'s defensive play without using the words "block" or "steal".',
     ["keywords:exclude words"],
     [R("exclude_keywords", keywords=["block", "steal"])]),
    ("段落以 *** 分隔",
     "Write an essay about city parks in 3 paragraphs separated by ***.",
     ["length constraints:number of paragraphs"],
     [R("paragraph_count", n=3, separator="***")]),
    ("至多 N 字",
     "Summarize the history of tea in no more than 120 words.",
     ["length constraints:number of words"],
     [R("word_count", relation="at most", n=120)]),
    ("英文全小寫",
     "Describe your favorite dance style in english and all lowercase letters.",
     ["case:in english and lowercase"],
     [R("all_lowercase"), R("english_only")]),
    ("第 N 段開頭字（含 4-paragraph 寫法）",
     'Write a 4-paragraph artist statement. The second paragraph must start with the word "Inspired".',
     ["length constraints:first word of the nth paragraph"],
     [R("first_word_of_paragraph", nth=2, word="Inspired", n_paragraphs=4)]),
    ("關鍵字次數＋結尾句",
     'Write a short story about a sprinter. Use the word "speed" exactly three times. '
     'End your story with "And the crowd went silent."',
     ["keywords:frequency", "startend:end checker"],
     [R("keyword_frequency", keyword="speed", relation="exactly", n=3),
      R("end_phrase", phrase="And the crowd went silent.")]),
    ("字母頻率",
     "Write a product description where the letter 'z' appears at least 5 times.",
     ["keywords:letter frequency"],
     [R("letter_frequency", letter="z", relation="at least", n=5)]),
    ("標題（有說明 <<>> 格式）＋句數",
     "Write song lyrics in exactly 8 sentences with a title wrapped in double angular brackets, i.e. <<title>>.",
     ["length constraints:number of sentences", "format:title"],
     [R("sentence_count", relation="exactly", n=8), R("title")]),
]

REJECT = [
    ("逐句規則＋未標記的結尾句",
     "Describe in 8 sentences the most memorable race. Each sentence must contain the letter 'r' at least 5 times. "
     'End with "That was the day."',
     ["length constraints:number of sentences", "keywords:letter frequency"], "逐句"),
    ("只寫 bullet lists，意思不明確",
     "Provide two bullet lists of new dance technologies.",
     ["format:number of bullet lists"], "bullet lists"),
    ("標題沒有說明格式",
     "Write an article with a catchy title about urban farming.",
     ["format:title"], "標題格式"),
    ("句數互相衝突",
     "Write 5 sentences about the ocean. Make sure the whole answer has 7 sentences.",
     ["length constraints:number of sentences"], "多個不同的值"),
    ("不支援的標籤",
     "Answer with one of the following options: yes, no, maybe.",
     ["detectable_format:constrained response"], "不支援的規則標籤"),
    ("要求特定語言",
     "Write a poem in Spanish with no commas.",
     ["punctuation:use no comma"], "特定語言"),
    ("標籤沒列出的規則（不用逗號）",
     'Write a toast for a wedding. Include the keywords "love" and "journey". Do not use any commas.',
     ["include keywords"], "未標記"),
    ("關鍵字沒有加引號",
     "Write about gardening and include the keywords soil and water.",
     ["include keywords"], "找不到要包含的關鍵字"),
]


def main():
    fails = []
    for name, prompt, tags, expected in ACCEPT:
        rules, errs = parse_row(prompt, tags)
        if errs:
            fails.append(f"應收錄卻被淘汰：{name} → {errs}")
        elif sorted(map(str, rules)) != sorted(map(str, expected)):
            fails.append(f"解析結果不符：{name}\n    得到 {rules}\n    預期 {expected}")
    for name, prompt, tags, keyword in REJECT:
        rules, errs = parse_row(prompt, tags)
        if not errs:
            fails.append(f"應淘汰卻被收錄：{name} → {rules}")
        elif not any(keyword in e for e in errs):
            fails.append(f"淘汰原因不符：{name}（預期包含「{keyword}」）→ {errs}")
    total = len(ACCEPT) + len(REJECT)
    for f in fails:
        print("✗ " + f)
    print(f"{total - len(fails)}/{total} 個解析案例通過")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
