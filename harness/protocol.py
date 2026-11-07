"""模型輸出的格式（暫定，取得主線 renderer 後要一起替換）。

模型每一次只做一件事：
  最終回答     <answer>……</answer>
  呼叫工具     <tool_call>{"name": "...", "arguments": {...}}</tool_call>
  詢問使用者   <ask>……</ask>
答案中引用文件時寫成 [來源: doc_shipping#p2]
"""
import json
import re

CITATION_RE = re.compile(r"([A-Za-z0-9_]+#p\d+)")


def _grab(tag, text):
    m = re.search(rf"<{tag}>(.*?)(?:</{tag}>|$)", text, re.S)
    return m.group(1).strip() if m else None


def parse_output(text):
    """把模型輸出解析成結構。只看第一個出現的動作。"""
    out = {"kind": None, "answer": None, "ask": None, "tool_call": None,
           "tool_call_raw": None, "parse_error": None, "citations": []}
    positions = {t: text.find(f"<{t}>") for t in ("answer", "tool_call", "ask")}
    present = {t: p for t, p in positions.items() if p >= 0}
    if not present:
        out["kind"] = "invalid"
        out["parse_error"] = "輸出格式不符：找不到 <answer>、<tool_call> 或 <ask>"
        return out
    first = min(present, key=present.get)
    out["kind"] = first
    body = _grab(first, text)
    if first == "answer":
        out["answer"] = body
        out["citations"] = sorted(set(CITATION_RE.findall(body or "")))
    elif first == "ask":
        out["ask"] = body
    else:
        out["tool_call_raw"] = body
        try:
            call = json.loads(body)
            out["tool_call"] = call
        except json.JSONDecodeError as e:
            out["parse_error"] = f"工具呼叫不是合法的 JSON：{e.msg}（第 {e.pos} 個字元）"
    return out
