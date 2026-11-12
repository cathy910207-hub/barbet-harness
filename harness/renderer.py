"""把任務、文件、skill、工具、對話紀錄組成 prompt。

⚠️ SimpleRenderer 是「暫用格式」（simple_v0_TEMP）。
   正式實驗必須改用主線提供的 renderer，並在 config.json 的 renderer.name 記錄版本。
   替換方式：寫一個新的 class，實作 render(...)，再於 runner.make_renderer() 註冊。

注意：render() 只會收到 public_view（已移除 gold 的題目），結構上不可能把標準答案放進 prompt。
"""
import json

SYSTEM = (
    "你是一個客服助理。每次回覆只能做以下其中一件事，並嚴格使用指定格式：\n"
    "1. 最終回答：<answer>回答內容</answer>\n"
    "2. 呼叫工具：<tool_call>{\"name\": \"工具名稱\", \"arguments\": {參數}}</tool_call>\n"
    "3. 缺少必要資訊時詢問使用者：<ask>問題</ask>\n"
    "規則：\n"
    "- 回答若依據參考文件，必須在答案中標註來源，例如 [來源: doc_id#p1]。\n"
    "- 參考文件中找不到答案時，直接說明找不到，不可編造內容或來源。\n"
    "- 參考文件彼此矛盾時，指出矛盾並列出各自的來源。\n"
    "- 只能使用「可用工具」中列出的工具；沒有適用工具時，直接說明無法處理。\n"
    "- 工具回傳失敗時，不可宣稱已完成。"
)


class SimpleRenderer:
    name = "simple_v0_TEMP"

    def __init__(self, few_shot=None):
        self.few_shot = few_shot or []

    def render(self, task_view, context_chunks, skill, tool_specs, history):
        parts = [f"【系統】\n{SYSTEM}"]
        for ex in self.few_shot:
            parts.append(f"【範例】\n使用者：{ex['user']}\n助理：{ex['assistant']}")
        if context_chunks:
            docs = "\n".join(f"[{c['id']}] {c['text']}" for c in context_chunks)
            parts.append(f"【參考文件】\n{docs}")
        if skill:
            parts.append(f"【規則（skill）】\n{skill['text']}")
        if tool_specs:
            parts.append("【可用工具】\n" + json.dumps(tool_specs, ensure_ascii=False, indent=1))
        else:
            parts.append("【可用工具】\n（目前沒有可用工具）")
        parts.append(f"【使用者】\n{task_view['question']}")
        for h in history:
            label = {"assistant": "助理", "tool_result": "工具結果", "feedback": "系統回饋"}[h["role"]]
            parts.append(f"【{label}】\n{h['content']}")
        parts.append("【助理】\n")
        return "\n\n".join(parts)
