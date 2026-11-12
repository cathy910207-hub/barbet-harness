"""Tülu 匯入的整合測試：匯入 → verify.py → A/B/C 流程（假模型）。

用仿造的 4 筆資料（tests/fixtures/tulu_sample.jsonl），在暫存的專案複本中執行，不會動到正式資料。
執行：python tests/test_tulu_integration.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sh(cwd, *args):
    r = subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def main():
    tmp = tempfile.mkdtemp()
    work = os.path.join(tmp, "repo")
    shutil.copytree(BASE, work, ignore=shutil.ignore_patterns("runs", "__pycache__", ".git"))
    for f in ("tulu_draft.jsonl", "tulu_rejected.jsonl", "tulu_import_log.json"):
        p = os.path.join(work, "data/raw", f)
        if os.path.exists(p):
            os.remove(p)
    try:
        out = sh(work, "importers/tulu_import.py", "--n", "5", "--input", "tests/fixtures/tulu_sample.jsonl")
        assert "收錄 2" in out and "淘汰 2" in out, out

        sh(work, "verify.py")
        cands = [json.loads(l) for l in open(os.path.join(work, "data/candidates.jsonl"), encoding="utf-8")]
        rej = [json.loads(l) for l in open(os.path.join(work, "data/rejected.jsonl"), encoding="utf-8")]
        tulu = [c for c in cands if c["source"]["type"] == "public_dataset"]
        assert len(tulu) == 2, f"Tülu 題目應通過 2 題，實際 {len(tulu)}"
        assert sum(r.get("stage") == "import" for r in rej) == 2
        manifest = json.load(open(os.path.join(work, "data/manifest.json"), encoding="utf-8"))
        assert any((s.get("revision") or "").startswith("fe0c7d35") for s in manifest["sources"])
        assert manifest["import_logs"]["tulu"]["accepted"] == 2

        chant = next(c for c in tulu if "Hawks" in c["question"])
        assert chant["split"] == "unassigned" and chant["group_id"].startswith("grp_tulu_")
        # 參考回答只當診斷，不會出現在題目資料中
        assert "ROAR Hawks ROAR" not in json.dumps(chant, ensure_ascii=False)

        # A/B/C：C 組第一次回答有逗號 → 收到規則回饋 → 修正後通過
        script = {chant["id"]: {
            "A": ["<answer>Roar, Hawks, roar for victory and spirit!</answer>"],
            "B": ["<answer>Roar, Hawks! VICTORY and SPIRIT are OURS TODAY roar</answer>"],
            "C": ["<answer>Roar, Hawks! VICTORY and SPIRIT are OURS TODAY roar</answer>",
                  "<answer>roar Hawks roar\nVICTORY SPIRIT GO HAWKS</answer>"]}}
        with open(os.path.join(work, "tests/_script.json"), "w", encoding="utf-8") as f:
            json.dump(script, f)
        sys.path.insert(0, work)
        from harness.io_utils import read_json
        from harness.runner import Harness
        cfg = read_json(os.path.join(work, "config.json"))
        cfg["model"]["dummy_script"] = os.path.join(work, "tests/_script.json")
        h = Harness(cfg, base_dir=work)
        _, recs = h.run_all(task_ids=[chant["id"]])
        idx = {r["group"]: r for r in recs}
        assert idx["A"]["error_type"] == "rule_violation:no_comma", idx["A"]["error_type"]
        assert not idx["B"]["success"]
        c = idx["C"]
        assert c["success"] and c["corrections_used"] == 1, (c["success"], c["error_type"], c["corrections_used"])
        fb = c["steps"][0]["runtime_error"]["message"]
        assert "逗號" in fb, fb
        skill_prompt = c["steps"][0]["prompt"]
        assert "Do not use any commas." in skill_prompt and "checks:" not in skill_prompt
        print("Tülu 整合測試通過 ✓")
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    main()
