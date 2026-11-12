"""文件與 skill 的載入與檢索。

文件：data/docs/*.md，以空行切段，每段的來源 ID 為「檔名#p段號」，例如 doc_shipping#p2。
（以 # 開頭的標題行不算段落。）
檢索：純 Python 的 BM25，中文用「單字＋相鄰兩字」切詞，CPU 即可執行，不需要額外套件。
"""
import json
import math
import os
import re
from collections import Counter


def tokenize(text):
    text = text.lower()
    tokens = re.findall(r"[a-z0-9]+", text)
    for run in re.findall(r"[一-鿿]+", text):
        tokens.extend(run)  # 單字
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))  # 相鄰兩字
    return tokens


def load_docs(doc_dir):
    """回傳段落清單：[{id, doc, para, text}]"""
    chunks = []
    for name in sorted(os.listdir(doc_dir)):
        if not name.endswith(".md"):
            continue
        doc = name[:-3]
        with open(os.path.join(doc_dir, name), encoding="utf-8") as f:
            raw = f.read()
        paras = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
        n = 0
        for p in paras:
            if p.startswith("#") or p.startswith("<!--"):
                continue
            n += 1
            chunks.append({"id": f"{doc}#p{n}", "doc": doc, "para": n, "text": p})
    return chunks


def load_skills(skill_dir):
    """回傳 {skill_id: {id, text, output_regex, checks, path}}（包含子資料夾，例如 skills/tulu/）"""
    skills = {}
    for root, _, files in sorted(os.walk(skill_dir)):
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as f:
                raw = f.read()
            sid = re.search(r"skill_id:\s*(\S+)", raw)
            rgx = re.search(r"output_regex:\s*(.+?)\s*-->", raw)
            chk = re.search(r"<!--\s*checks:\s*(.*?)\s*-->", raw, re.S)
            skill_id = sid.group(1) if sid else name[:-3]
            if skill_id in skills:
                raise ValueError(f"skill_id 重複：{skill_id}")
            # 給模型看的內容不含 HTML 註解（那是給驗證器用的中繼資料）
            visible = re.sub(r"<!--.*?-->\n?", "", raw).strip()
            skills[skill_id] = {
                "id": skill_id,
                "text": visible,
                "output_regex": rgx.group(1) if rgx else None,
                "checks": json.loads(chk.group(1)) if chk else None,
                "path": os.path.relpath(path, skill_dir),
            }
    return skills


class BM25:
    def __init__(self, chunks, k1=1.5, b=0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.docs_tokens = [tokenize(c["text"]) for c in chunks]
        self.avgdl = sum(len(t) for t in self.docs_tokens) / max(len(self.docs_tokens), 1)
        df = Counter()
        for toks in self.docs_tokens:
            df.update(set(toks))
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query, top_k=3):
        q = tokenize(query)
        scored = []
        for chunk, toks in zip(self.chunks, self.docs_tokens):
            tf = Counter(toks)
            dl = len(toks)
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                f = tf[t]
                s += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            scored.append((s, chunk))
        scored.sort(key=lambda x: -x[0])
        return [dict(c, score=round(s, 3)) for s, c in scored[:top_k] if s > 0]
