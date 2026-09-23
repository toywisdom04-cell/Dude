import os
import re
import json
import math
import datetime
import threading
import hashlib
from core.config import get_config

STOP = set(
    "a an the and or of to in on for is are was were be been being it its this that "
    "with as at by from your you i we they he she but if then so do does did have has "
    "had will would can could should may might not no yes".split()
)

_lock = threading.Lock()
_KB = None


class KnowledgeBase:
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "knowledge")
        os.makedirs(self.dir, exist_ok=True)
        self.index_path = os.path.join(self.dir, "index.json")
        self.docs = {}
        self.df = {}
        self.load()

    def load(self):
        try:
            with open(self.index_path, encoding="utf-8") as f:
                d = json.load(f)
            self.docs = d.get("docs", {})
            self.df = d.get("df", {})
        except Exception:
            self.docs, self.df = {}, {}

    def save(self):
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"docs": self.docs, "df": self.df}, f)
        os.replace(tmp, self.index_path)

    def _tok(self, text):
        return [t for t in re.findall(r"[a-z0-9_]+", text.lower())
                if t not in STOP and len(t) > 1]

    def _chunks(self, text, size=500, overlap=60):
        w = text.split()
        out, i = [], 0
        while i < len(w):
            c = " ".join(w[i:i + size])
            if c.strip():
                out.append(c)
            i += size - overlap
        return out

    def index_file(self, path):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception:
            return 0
        doc_id = hashlib.md5(path.encode("utf-8")).hexdigest()
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        existing = self.docs.get(doc_id)
        if existing and existing.get("mtime") == mtime:
            return len(existing.get("chunks", []))
        chunks = self._chunks(text)
        objs, local_df = [], {}
        for ci, c in enumerate(chunks):
            tf = {}
            for t in self._tok(c):
                tf[t] = tf.get(t, 0) + 1
            objs.append({"id": ci, "text": c, "tf": tf})
            for t in set(tf):
                local_df[t] = local_df.get(t, 0) + 1
        self.docs[doc_id] = {"path": path, "mtime": mtime, "chunks": objs}
        for t, c in local_df.items():
            self.df[t] = self.df.get(t, 0) + c
        return len(objs)

    def index_folder(self, folder,
                     exts=(".txt", ".md", ".py", ".js", ".ts", ".json", ".csv",
                           ".log", ".ini", ".yaml", ".yml", ".html", ".css",
                           ".bat", ".ps1", ".cfg")):
        n = 0
        for root, _, files in os.walk(folder):
            for fn in files:
                if fn.lower().endswith(exts):
                    n += self.index_file(os.path.join(root, fn))
        self.save()
        return n

    def query(self, q, k=5):
        qt = self._tok(q)
        if not qt:
            return []
        N = max(1, len(self.docs))
        qset = set(qt)
        scores = []
        for doc_id, doc in self.docs.items():
            for ch in doc["chunks"]:
                score = 0.0
                for t in qset:
                    tf = ch["tf"].get(t, 0)
                    if tf:
                        idf = math.log((N + 1) / (self.df.get(t, 0) + 1)) + 1
                        score += (tf * (1.2 + tf)) / (tf + 1.2) * idf
                if score > 0:
                    scores.append((score, doc["path"], ch["text"]))
        scores.sort(reverse=True)
        seen, out = set(), []
        for s, p, t in scores:
            if p in seen:
                continue
            seen.add(p)
            out.append({"path": p, "text": t})
            if len(out) >= k:
                break
        return out

    def notes_dir(self):
        d = os.path.join(self.dir, "notes")
        os.makedirs(d, exist_ok=True)
        return d

    def save_note(self, title, body, filename=None):
        """Write a durable, searchable markdown note into the notes vault and
        re-index it so search_knowledge can find it immediately."""
        d = self.notes_dir()
        if not filename:
            slug = re.sub(r"[^a-z0-9_]+", "-", title.lower()).strip("-")[:60]
            stamp = datetime.datetime.now().strftime("%Y-%m-%d")
            filename = f"{stamp}_{slug}.md"
        path = os.path.join(d, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n{body}\n")
        self.index_folder(d)
        return path

    def append_daily_note(self, stamp_prefix, lines):
        """Append new unique lines to a daily note (e.g. 'screen-learning-2026-08-31'),
        skipping anything already recorded, then re-index."""
        d = self.notes_dir()
        path = os.path.join(d, f"{stamp_prefix}.md")
        existing = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                existing = f.read()
        added = 0
        chunks = []
        for ln in lines:
            ln = ln.strip()
            if not ln or ln in existing:
                continue
            chunks.append(f"- {ln}\n")
            existing += f"- {ln}\n"
            added += 1
        if chunks:
            with open(path, "a", encoding="utf-8") as f:
                f.writelines(chunks)
            self.index_folder(d)
        return added


def get_knowledge():
    global _KB
    if _KB is None:
        with _lock:
            if _KB is None:
                cfg = get_config()
                _KB = KnowledgeBase(cfg.data_dir)
                notes = _KB.notes_dir()
                paths = cfg.get("knowledge_paths", default=[]) or []
                if isinstance(paths, str):
                    paths = [paths]
                for p in [p for p in paths if p] + [notes]:
                    try:
                        _KB.index_folder(p)
                    except Exception:
                        pass
    return _KB
