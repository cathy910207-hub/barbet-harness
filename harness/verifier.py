"""執行期驗證器（C 組回饋用）。

只能使用「執行當下本來就拿得到」的資訊：工具 schema、工具執行結果、文件庫有哪些段落、
skill 規定的輸出格式。這個檔案刻意不 import 任何讀取 gold 的程式。
"""
import re


def verify_answer(parsed, valid_chunk_ids, skill):
    """回傳 None 代表沒有發現問題；否則回傳 (錯誤類型, 回饋訊息)。"""
    bad = [c for c in parsed["citations"] if c not in valid_chunk_ids]
    if bad:
        return ("fabricated_source", f"引用的來源不存在於文件庫：{', '.join(bad)}")
    if skill and skill.get("output_regex") and parsed["answer"] is not None:
        if not re.search(skill["output_regex"], parsed["answer"]):
            return ("format_error", "回答不符合 skill 規定的輸出格式，請依照規則中的「輸出格式」重新回答")
    return None
