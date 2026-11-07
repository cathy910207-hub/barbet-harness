"""模型介面。

- DummyModel：假模型，只用來測試流程。它的結果「不是」Barbet 的結果，報告中會明確標示。
- BarbetModel：用 Hugging Face transformers 載入主線指定的 checkpoint。
  ⚠️ 尚未在真正的 Barbet checkpoint 上測試；拿到 checkpoint 後請先跑 1～2 題確認。
"""
import time
from dataclasses import dataclass


@dataclass
class GenResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float


class TokenCounter:
    """有 tokenizer 就用真的 tokenizer 算；沒有就用字元數近似，並標示 method。"""

    def __init__(self, name_or_path=None):
        self.tok = None
        self.method = "approx_chars"
        if name_or_path:
            try:
                from transformers import AutoTokenizer
                self.tok = AutoTokenizer.from_pretrained(name_or_path)
                self.method = f"tokenizer:{name_or_path}"
            except Exception as e:
                print(f"[TokenCounter] 無法載入 tokenizer（{e}），改用字元數近似")

    def count(self, text):
        if self.tok is not None:
            return len(self.tok.encode(text, add_special_tokens=False))
        return len(text)


class DummyModel:
    """假模型。
    script 格式：{task_id: {group: [第1次輸出, 第2次輸出, ...]}}
    沒有劇本的題目一律回答「我不知道」。
    """
    is_barbet = False

    def __init__(self, counter, label="DUMMY", script=None):
        self.counter = counter
        self.label = label
        self.script = script or {}
        self._queue = []

    def begin(self, task_id, group):
        self._queue = list(self.script.get(task_id, {}).get(group, []))

    def generate(self, prompt, decoding):
        t0 = time.time()
        text = self._queue.pop(0) if self._queue else "<answer>（DUMMY）我不知道</answer>"
        return GenResult(text, self.counter.count(prompt), self.counter.count(text), time.time() - t0)


class BarbetModel:
    is_barbet = True

    def __init__(self, checkpoint, counter, revision=None, device="cpu", label="Barbet"):
        if not checkpoint:
            raise ValueError("尚未設定 Barbet checkpoint。請向主線取得固定版本後填入 config.json → model.checkpoint")
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(checkpoint, revision=revision)
        self.model = AutoModelForCausalLM.from_pretrained(checkpoint, revision=revision, torch_dtype=torch.float32)
        self.model.to(device).eval()
        self.device = device
        self.counter = counter
        self.label = label

    def begin(self, task_id, group):
        pass

    def generate(self, prompt, decoding):
        from transformers import StoppingCriteria, StoppingCriteriaList
        tok, stops = self.tok, decoding.get("stop") or []

        class StopOnStrings(StoppingCriteria):
            def __init__(self, start):
                self.start = start

            def __call__(self, input_ids, scores, **kw):
                tail = tok.decode(input_ids[0, self.start:], skip_special_tokens=False)
                return any(s in tail for s in stops)

        ids = tok(prompt, return_tensors="pt").input_ids.to(self.device)
        kwargs = {"max_new_tokens": decoding.get("max_new_tokens", 256),
                  "do_sample": bool(decoding.get("do_sample")),
                  "stopping_criteria": StoppingCriteriaList([StopOnStrings(ids.shape[1])]),
                  "pad_token_id": tok.pad_token_id or tok.eos_token_id}
        if kwargs["do_sample"]:
            for k in ("temperature", "top_p"):
                if decoding.get(k) is not None:
                    kwargs[k] = decoding[k]
        t0 = time.time()
        with self.torch.no_grad():
            out = self.model.generate(ids, **kwargs)
        new_ids = out[0, ids.shape[1]:]
        text = tok.decode(new_ids, skip_special_tokens=True)
        for s in stops:  # 截到第一個停止字串（含）
            if s in text:
                text = text[: text.index(s) + len(s)]
                break
        return GenResult(text, ids.shape[1], len(new_ids), time.time() - t0)


def make_model(cfg, counter):
    m = cfg["model"]
    if m["type"] == "dummy":
        script = None
        if m.get("dummy_script"):
            from .io_utils import read_json
            script = read_json(m["dummy_script"])
        return DummyModel(counter, label=m.get("label", "DUMMY"), script=script)
    if m["type"] == "barbet":
        return BarbetModel(m["checkpoint"], counter, revision=m.get("revision"),
                           device=m.get("device", "cpu"), label=m.get("label", "Barbet"))
    raise ValueError(f"未知的 model.type：{m['type']}")
