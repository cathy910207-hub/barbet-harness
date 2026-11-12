"""A/B/C 三組的執行流程（Harness）。

A：Barbet 原生回答（只看題目本身，含題目指定的工具清單，但工具不會被執行）
B：A ＋ 檢索到的文件或適用 skill
C：B ＋ 工具執行與驗證 ＋ 最多 N 次修正（N = config.c_group.max_corrections）

標準流程：檢索 → 組裝 prompt → 模型生成 → 執行／驗證 → 回饋錯誤 → 有限修正
"""
import copy
import random
import time
import uuid

from . import grader
from .io_utils import (append_jsonl, dir_hash, file_hash, obj_hash, read_json, read_jsonl, write_json)
from .model import TokenCounter, make_model
from .protocol import parse_output
from .renderer import SimpleRenderer
from .retriever import BM25, load_docs, load_skills
from .tools import ToolBox
from .verifier import verify_answer

import os


PUBLIC_FIELDS = ("id", "task_family", "question", "context")


def public_view(task):
    """給模型看的題目：只保留白名單欄位（題號、類型、題目、情境）。
    answer、verification、constraints、revisions 等一律不會進到 prompt，
    這是防止標準答案洩漏的結構性保護。"""
    return copy.deepcopy({k: task[k] for k in PUBLIC_FIELDS if k in task})


def make_renderer(cfg):
    r = cfg["renderer"]
    if r["name"] == "simple_v0_TEMP":
        return SimpleRenderer(few_shot=r.get("few_shot"))
    raise ValueError(f"尚未註冊的 renderer：{r['name']}")


class Harness:
    def __init__(self, cfg, base_dir="."):
        self.cfg = cfg
        p = {k: os.path.join(base_dir, v) for k, v in cfg["paths"].items()}
        self.paths = p
        self.chunks = load_docs(p["docs"])
        self.chunk_ids = {c["id"] for c in self.chunks}
        self.bm25 = BM25(self.chunks)
        self.skills = load_skills(p["skills"])
        self.toolbox = ToolBox(read_json(p["tool_schema"]), read_json(p["mock_db"]))
        self.counter = TokenCounter(cfg["tokenizer"].get("name_or_path"))
        self.model = make_model(cfg, self.counter)
        self.renderer = make_renderer(cfg)
        self.versions = {
            "tasks": file_hash(p["tasks"]),
            "docs": dir_hash(p["docs"]),
            "skills": dir_hash(p["skills"]),
            "tool_schema": self.toolbox.schema_version + "@" + file_hash(p["tool_schema"]),
            "mock_db": self.toolbox.db_version + "@" + file_hash(p["mock_db"]),
            "config": obj_hash(cfg),
            "renderer": self.renderer.name,
            "model": cfg["model"].get("label"),
            "checkpoint": cfg["model"].get("checkpoint"),
            "token_count_method": self.counter.method,
        }

    # ---------- 1. 檢索 ----------
    def retrieve(self, view):
        rc = self.cfg["retrieval"]
        ctx = view.get("context", {})
        skill = self.skills.get(ctx.get("skill_id")) if ctx.get("skill_id") else None
        if view["task_family"] != "doc_retrieval":
            return [], skill
        hits = self.bm25.search(view["question"], top_k=rc["top_k"])
        n_dis = rc.get("num_distractors", 0)
        if n_dis:
            hit_ids = {h["id"] for h in hits}
            pool = [c for c in self.chunks if c["id"] not in hit_ids]
            rnd = random.Random(view["id"])  # 固定亂數，確保可重現
            distractors = rnd.sample(pool, min(n_dis, len(pool)))
            pos = rc.get("position", "default")
            if pos == "start":
                hits = hits + distractors
            elif pos == "end":
                hits = distractors + hits
            elif pos == "middle":
                half = len(distractors) // 2
                hits = distractors[:half] + hits + distractors[half:]
            else:
                hits = hits + distractors
        return hits, skill

    # ---------- 執行單題單組 ----------
    def run_task(self, task, group, run_id):
        view = public_view(task)
        dec = self.cfg["decoding"]
        budget = self.cfg["budget"]["max_total_tokens_per_task"]
        max_corr = self.cfg["c_group"]["max_corrections"] if group == "C" else 0

        context, skill = ([], None) if group == "A" else self.retrieve(view)
        tool_names = list(view.get("context", {}).get("tools", []))
        if group == "C":
            tool_names += [t for t in self.cfg["c_group"]["extra_tools"] if t not in tool_names]
        tool_specs = self.toolbox.describe(tool_names)

        self.model.begin(task["id"], group)
        history, steps = [], []
        tool_calls, executed = [], []
        final = {"answer": None, "citations": [], "ask": None}
        corrections, total_tokens, total_latency = 0, 0, 0.0
        stop_reason, forced_final = None, False
        max_steps = 2 * (max_corr + 1) + 2

        for step_no in range(max_steps):
            # 2. 組裝 prompt  3. 模型生成
            prompt = self.renderer.render(view, context, skill, tool_specs, history)
            g = self.model.generate(prompt, dec)
            total_tokens += g.prompt_tokens + g.completion_tokens
            total_latency += g.latency_s
            parsed = parse_output(g.text)
            if parsed["tool_call"]:
                tool_calls.append(parsed["tool_call"])
            step = {"step": step_no, "prompt": prompt, "output": g.text, "parsed": parsed,
                    "prompt_tokens": g.prompt_tokens, "completion_tokens": g.completion_tokens,
                    "latency_s": round(g.latency_s, 4),
                    "is_correction": bool(history) and history[-1]["role"] == "feedback"}
            history.append({"role": "assistant", "content": g.text})

            if total_tokens > budget:
                steps.append(step)
                stop_reason = "budget_exceeded"
                break

            # A / B：只生成一次，不執行、不回饋
            if group in ("A", "B"):
                final.update({k: parsed[k] for k in ("answer", "citations", "ask")})
                steps.append(step)
                stop_reason = "single_pass"
                break

            # ---------- C 組 ----------
            # 4. 執行／驗證
            error = None
            if parsed["kind"] == "tool_call":
                if forced_final:
                    steps.append(step)
                    stop_reason = "tool_call_after_limit"
                    break
                if parsed["parse_error"]:
                    error = ("parse_error", parsed["parse_error"])
                else:
                    error = self.toolbox.validate(parsed["tool_call"], tool_names)
                    if not error:
                        call = parsed["tool_call"]
                        res = self.toolbox.execute(call["name"], call.get("arguments", {}))
                        step["tool_execution"] = res
                        executed.append({"call": call, "result": res,
                                         "computes": self.toolbox.tools[call["name"]].get("computes", False)})
                        if res["ok"]:
                            history.append({"role": "tool_result", "content": str(res["result"])})
                            steps.append(step)
                            continue  # 工具成功：讓模型讀結果後回答（不算修正）
                        error = ("tool_error", f"工具執行失敗：{res['error']}")
            elif parsed["kind"] == "answer":
                final.update({"answer": parsed["answer"], "citations": parsed["citations"]})
                error = verify_answer(parsed, self.chunk_ids, skill)
                if not error or forced_final:
                    steps.append(step)
                    stop_reason = "answered"
                    break
            elif parsed["kind"] == "ask":
                final["ask"] = parsed["ask"]
                steps.append(step)
                stop_reason = "asked_user"
                break
            else:
                error = ("parse_error", parsed["parse_error"])

            # 5. 回饋錯誤  6. 有限修正
            step["runtime_error"] = {"type": error[0], "message": error[1]}
            steps.append(step)
            if corrections < max_corr:
                corrections += 1
                history.append({"role": "feedback", "content": error[1] + "。請修正後重新輸出。"})
            elif not forced_final:
                forced_final = True
                history.append({"role": "feedback",
                                "content": error[1] + "。已達修正次數上限，請不要再呼叫工具，直接用 <answer> 回覆使用者目前的情況。"})
            else:
                stop_reason = "max_corrections_reached"
                break
        else:
            stop_reason = "max_steps"

        outcome = {"tool_calls": tool_calls, "answer": final["answer"], "citations": final["citations"],
                   "ask": final["ask"], "executed": executed}
        verdict = grader.grade(task, outcome, self.toolbox, self.chunk_ids)
        if stop_reason == "budget_exceeded":
            verdict = {"success": False, "error_type": "budget_exceeded"}
        # 每一步的局部正確性（只供分析，從未給模型看）
        attempt_ok = [grader.grade_attempt(task, s["parsed"], self.toolbox, self.chunk_ids) for s in steps]

        return {
            "run_id": run_id, "task_id": task["id"], "task_family": task["task_family"], "group_id": task.get("group_id"), "tags": task.get("tags", []),
            "group": group, "model_label": self.model.label, "is_barbet": self.model.is_barbet,
            "versions": self.versions, "decoding": dec,
            "retrieved": [{"id": c["id"], "score": c.get("score")} for c in context],
            "skill_id": skill["id"] if skill else None, "tools_offered": tool_names,
            "steps": steps, "stop_reason": stop_reason,
            "corrections_used": corrections,
            "success": verdict["success"], "error_type": verdict["error_type"],
            "system_assisted": group == "C" and any(e["result"]["ok"] for e in executed),
            "system_assisted_compute": group == "C" and any(e["result"]["ok"] and e["computes"] for e in executed),
            "attempt_grades_for_analysis_only": attempt_ok,
            "total_tokens": total_tokens, "latency_s": round(total_latency, 4),
        }

    # ---------- 執行全部 ----------
    def run_all(self, groups=("A", "B", "C"), limit=None, task_ids=None):
        tasks = read_jsonl(self.paths["tasks"])
        if task_ids:
            tasks = [t for t in tasks if t["id"] in task_ids]
        if limit:
            tasks = tasks[:limit]
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        out_dir = os.path.join(self.paths["runs"], run_id)
        os.makedirs(out_dir, exist_ok=True)
        write_json(os.path.join(out_dir, "manifest.json"),
                   {"run_id": run_id, "experiment": self.cfg["experiment_name"], "groups": list(groups),
                    "n_tasks": len(tasks), "versions": self.versions, "config": self.cfg})
        trace_path = os.path.join(out_dir, "traces.jsonl")
        records = []
        for t in tasks:
            for g in groups:
                rec = self.run_task(t, g, run_id)
                append_jsonl(trace_path, rec)
                records.append(rec)
                mark = "✓" if rec["success"] else f"✗ {rec['error_type']}"
                print(f"[{g}] {t['id']:<10} {mark}")
        return out_dir, records
