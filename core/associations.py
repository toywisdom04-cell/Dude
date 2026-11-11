"""Associative memory / neural-link engine.

DUDE's local "thinking on a linked graph": observed screen content gets
understood (analytics), recurring things become entities, and entities get
connected into a weighted co-occurrence + action-pattern link graph that
bridges PAST and PRESENT knowledge. Reasoning/suggestions are surfaced ONLY
on request (think_deep). Everything here is local and additive — it never
touches or rewrites the existing observer/experience/voice code paths.
"""
import datetime
import hashlib
import os
import re
import sqlite3
import threading

from core.config import get_config

_LOCK = threading.Lock()
_BRAIN = None

_SCH = """
CREATE TABLE IF NOT EXISTS analytics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    app TEXT, window TEXT, topic TEXT, summary TEXT,
    tags TEXT, goal TEXT, source TEXT, content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_analytics_ts ON analytics(ts);
CREATE INDEX IF NOT EXISTS idx_analytics_app ON analytics(app);

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT DEFAULT 'concept',
    first_seen TEXT, last_seen TEXT,
    strength REAL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    rel TEXT DEFAULT 'co-occur',
    weight REAL DEFAULT 1.0,
    evidence INTEGER DEFAULT 1,
    last_seen TEXT,
    UNIQUE(src, dst, rel)
);

CREATE TABLE IF NOT EXISTS insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT DEFAULT 'link',
    body TEXT NOT NULL,
    basis TEXT
);
"""

_STOP = set(
    "a an the and or of to in on for is are was were be been being it its this that "
    "with as at by from your you i we they he she but if then so do does did have has "
    "had will would can could should may might not no yes dude ok okay sir please "
    "want like just one get got see look make need making them these those".split())

_TASK_NOISE = set(
    "dude sir okay alright yeah going using use now today yesterday day work let'll "
    "let will would could continue continuously forever remember easy okayplease thanks".split())

# Meta-words that are almost never a real topic by themselves.
_NOISE = _STOP | _TASK_NOISE

# Window-title prefixes/suffixes that carry no meaning (browser/OS chrome).
_WIN_GENERIC = {"whatsapp", "chat", "chrome", "microsoft edge", "edge", "comet",
                "desktop", "messaging", "browser", "file explorer", "youtube",
                "duolingo", "settings", "windows"}
_WIN_TRAIL = re.compile(r"(?i)\s*[|\-–—]\s*(google chrome|chrome|microsoft edge|"
                        r"edge|firefox|youtube|file explorer)\s*$")
_WIN_COUNT = re.compile(r"^\(?\d+\s*\)?\s+|^\d+\s*[-–—:]\s+|\s*\(\d+\)\s*$")

# Per-app / text-cue goal inference (used when vision is quota-paused).
_APP_GOALS = [
    ("whatsapp", "messaging / follow-ups"),
    ("chat", "messaging / follow-ups"),
    ("comet", "coding / work session"),
    ("pycharm", "coding / work session"),
    ("code.exe", "coding / work session"),
    ("notepad", "editing text"),
    ("excel", "spreadsheet work"),
    ("word", "document work"),
    ("outlook", "reading / answering email"),
    ("file explorer", "finding / managing files"),
    ("youtube", "watching videos"),
    ("powerpoint", "slides work"),
]
_GOAL_CUES = [
    (("follow-up", "follow up", "reply", "message", "send", "respond", "dm"),
     "messaging / follow-ups"),
    (("search", "research", "look up", "find"), "research / finding info"),
    (("debug", "error", "function", "compile", "exception", "traceback", "syntax"),
     "coding / debugging"),
    (("payment", "invoice", "payment received", "order", "transaction"),
     "business / payments"),
    (("tutorial", "lesson", "learn", "course", "exercise"), "learning / study"),
]


def get_associations():
    """Process-wide singleton for the associative memory (like get_knowledge)."""
    global _BRAIN
    with _LOCK:
        if _BRAIN is None:
            _BRAIN = AssociationBrain(get_config().data_dir)
        return _BRAIN


class AssociationBrain:
    def __init__(self, data_dir):
        self.db = os.path.join(data_dir, "experience.db")
        self.conn = sqlite3.connect(self.db, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCH)
        self.conn.commit()
        self._db_lock = threading.Lock()
        self._recent_hashes = []  # (hash, ts) in-memory dedupe window

    # ---------------- helpers ----------------
    def _now(self):
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _add_entity(self, name, kind="concept"):
        name = (name or "").strip().strip("'\".,;:()[]{}!?-").strip()
        if len(name) < 3:
            return
        now = self._now()
        with self._db_lock:
            self.conn.execute(
                "INSERT INTO entities(name, kind, first_seen, last_seen) VALUES (?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen, "
                "strength=MIN(999.0, strength+0.5)",
                (name, kind, now, now))
            self.conn.commit()

    def _link(self, a, b, rel="co-occur", delta=1.0, ts=None):
        a = (a or "").strip()
        b = (b or "").strip()
        if not a or not b or a == b:
            return
        if a > b:
            a, b = b, a
        ts = ts or self._now()
        with self._db_lock:
            self.conn.execute(
                "INSERT INTO links(src, dst, rel, weight, evidence, last_seen) "
                "VALUES (?,?,?,?,1,?) "
                "ON CONFLICT(src, dst, rel) DO UPDATE SET "
                "weight=MIN(999.0, weight+excluded.weight), "
                "evidence=evidence+1, last_seen=excluded.last_seen",
                (a, b, rel, delta, ts))
            self.conn.commit()

    # ---------------- entity extraction (local, no API) ----------------
    def extract_entities(self, text, known=None):
        """Cheap local entity extraction: capitalized multi-word phrases and
        single capitalized terms (len>=3), plus exact hits from known names."""
        text = text or ""
        found = []
        known = known or []
        for name in known:
            n = (name or "").strip()
            if n and len(n) > 2 and n.lower() in text.lower():
                found.append(n)
        for m in re.finditer(r"([A-Z][a-zA-Z0-9&.,+'\-]*(?:\s+[A-Z][a-zA-Z0-9&.,+'\-]+){0,3})",
                             text):
            phrase = " ".join(m.group(0).split())
            words = phrase.split()
            if not words:
                continue
            phr = phrase.strip("'\".,;:()[]{}!?-")
            if (2 <= len(phr.split()) <= 4 and any(len(w) > 2 for w in words)
                    and phr.lower() not in _NOISE):
                found.append(phr)
            elif len(words) == 1 and len(words[0]) >= 3 and words[0].lower() not in _NOISE:
                found.append(words[0])
        out, seen = [], set()
        for f in found:
            key = f.lower()
            if key not in seen:
                seen.add(key)
                out.append(f)
        return out[:24]

    # ---------------- analytics: understand the screen content ----------------
    def record_analytics(self, app="", window="", ocr_text="", viewer_out=None,
                         source="observer", goal=""):
        try:
            ocr_text = ocr_text or ""
            if len(ocr_text.strip()) < 4 and not viewer_out:
                return
            hkey = (app or "") + "|" + (goal or "") + "|" \
                + " ".join((ocr_text or "").strip().split())[:400]
            h = hashlib.md5(hkey.encode("utf-8", "ignore")).hexdigest()
            now_ts = datetime.datetime.now()
            with self._db_lock:
                self._recent_hashes = [(k, t) for k, t in self._recent_hashes
                                       if (now_ts - t).total_seconds() < 1800]
                if any(k == h for k, _ in self._recent_hashes):
                    return
                self._recent_hashes.append((h, now_ts))
                if len(self._recent_hashes) > 60:
                    self._recent_hashes.pop(0)

            topic, summary, tags = self._semantic(viewer_out, ocr_text, app, window)
            if not goal:
                goal = self._infer_goal(app, window, ocr_text or (viewer_out or ""))
            if goal == "" and viewer_out:
                for line in viewer_out.splitlines():
                    m = re.search(r"GOAL:\s*(.+)", line, re.I)
                    if m:
                        goal = m.group(1).strip()
            ts = self._now()
            with self._db_lock:
                self.conn.execute(
                    "INSERT INTO analytics(ts, app, window, topic, summary, tags, "
                    "goal, source, content_hash) VALUES (?,?,?,?,?,?,?,?,?)",
                    (ts, (app or "")[:60], (window or "")[:120], topic[:120],
                     (summary or "")[:300], "|".join(tags)[:400], (goal or "")[:200],
                     source, h))
                self.conn.commit()

            # entities + co-occurrence links (the neural-adjacency edges)
            entities = set(tags)
            if topic:
                entities.add(topic[:60])
            for e in entities:
                self._add_entity(e, kind="topic")
            if app and app.lower() != "unknown":
                self._add_entity(app, kind="app")
            names = [n for n in entities if n]
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    self._link(names[i], names[j], "co-occur", 1.0, ts)
            if app and app.lower() != "unknown" and names:
                for n in names:
                    self._link(app, n, "co-occur", 0.5, ts)
        except Exception as e:
            print(f"[assoc] record_analytics failed: {e}")

    def _clean_window(self, window):
        """Turn '(22) YouTube - Comet' or 'Inbox - duvvu@gmail.com - Outlook'
        into a clean meaningful title."""
        w = " ".join((window or "").split())
        w = _WIN_COUNT.sub("", w).strip()
        w = _WIN_TRAIL.sub("", w).strip()
        w = re.sub(r"^(?:\(?\d+\s*\)?\s*[-–—:.]*\s*|\d+\s*/\s*\d+\s*[-–—]\s*|\d+\s*[-–—:]\s*)", "", w).strip()
        w = w.strip(" -–—|()")
        return w or (" ".join((window or "").split()))[:80]

    def _infer_goal(self, app, window, text):
        t = " ".join(((window or "") + " " + (text or "")).lower().split())
        appk = (app or "").lower()
        for key, g in _APP_GOALS:
            if key in appk:
                return g
        for cues, g in _GOAL_CUES:
            if any(c in t for c in cues):
                return g
        return ""

    def understand_local(self, app="", window="", ocr_text=""):
        """Public local reader used by the observer so SCREEN 'seeing' keeps
        producing real content (topic/summary/tags/goal) with zero Gemini."""
        topic, summary, tags = self._semantic(None, ocr_text, app, window)
        return {"app": (app or "")[:60], "window": self._clean_window(window)[:120],
                "topic": topic[:120], "summary": summary[:300],
                "tags": "|".join(tags)[:400],
                "goal": self._infer_goal(app, window, ocr_text)[:200]}

    def _semantic(self, viewer_out, ocr_text, app, window):
        """Build topic/summary/tags. Uses vision text when available, otherwise
        a LOCAL extractive fallback so understanding never dies under quota."""
        text = (viewer_out or "") + " " + (ocr_text or "") + " " + (window or "")
        tags = self.extract_entities(text)
        if viewer_out:
            vw = viewer_out.strip()
            topic = re.sub(r"^(TOPIC|SUMMARY|GUESS|GUESSED|LIKELY):\s*", "", vw.splitlines()[0]).strip()
            topic = " ".join(topic.split())[:120] if topic else \
                " ".join((window or "").split())[:80]
            summary = re.sub(r"\s+", " ", vw)[:300]
            return topic, summary, tags
        # local extractive fallback
        tokens = re.findall(r"[a-z0-9_+#.-]+", (ocr_text or "").lower())
        freq = {}
        for t in tokens:
            if len(t) >= 4 and t not in _NOISE:
                freq[t] = freq.get(t, 0) + 1
        top = [t for t, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:4]]
        win = self._clean_window(window)
        win0 = win.lower().split()[0] if win.split() else ""
        win_generic = not win or win0 in _WIN_GENERIC
        if win_generic:
            topic = " ".join(top) or win
        else:
            topic = win
        if len(topic) < 3:
            topic = " ".join(top) if top else (win or app or "screen")
        summary = ("Screen: " + (win or app or "unknown") + ". Visible: "
                   + " ".join((ocr_text or "").split())[:220])
        return topic[:120], summary[:300], tags

    # ---------------- pattern edges from the neural/action records ----------------
    def pattern_edges(self, app=None, limit=6):
        """App->next-app transitions learned from experiences (pure local stats)."""
        try:
            with self._db_lock:
                rows = self.conn.execute(
                    "SELECT app, tool FROM experiences WHERE app IS NOT NULL AND "
                    "app NOT IN ('', 'unknown') ORDER BY id DESC LIMIT 2000").fetchall()
            seq = [(r[0] or "unknown", r[1] or "") for r in rows]
            trans = {}
            for i in range(len(seq) - 1):
                a, b = seq[i][0], seq[i + 1][0]
                if a == b or not a or not b:
                    continue
                trans[(a, b)] = trans.get((a, b), 0) + 1
            cand = [(a, b, n) for (a, b), n in trans.items()
                    if (not app or a == app)]
            cand.sort(key=lambda x: -x[2])
            out = []
            for a, b, n in cand[:limit]:
                out.append(f"{a} => {b} (seen {n}x in your past work)")
            return out
        except Exception:
            return []

    # ---------------- the linked-graph read ----------------
    def associations_for(self, app=None, entity_names=(), limit=8):
        """Top weighted associations near the current context (app + entities)."""
        ent = set(entity_names or [])
        if app and app.lower() != "unknown":
            ent.add(app)
        if not ent:
            return []
        rows = []
        try:
            placeholders = ",".join("?" for _ in ent)
            q = (f"SELECT src, dst, weight, evidence FROM links "
                 f"WHERE src IN ({placeholders}) OR dst IN ({placeholders}) "
                 f"ORDER BY weight DESC LIMIT 40")
            with self._db_lock:
                rows = self.conn.execute(
                    q, list(ent) + list(ent)).fetchall() or []
        except Exception:
            rows = []
        out, seen = [], set()
        for src, dst, weight, evidence in rows:
            nm = f"{src} <-> {dst}"
            if nm not in seen:
                seen.add(nm)
                out.append(f"{nm} (strength {weight:.1f})")
            if len(out) >= limit:
                break
        pat = self.pattern_edges(app, limit=4)
        return (out + pat)[:limit + 4]

    def top_insights(self, limit=3):
        try:
            with self._db_lock:
                rows = self.conn.execute(
                    "SELECT body FROM insights ORDER BY id DESC LIMIT ?",
                    (limit,)).fetchall()
            return [r[0] for r in rows]
        except Exception:
            return []

    def record_insight(self, body, kind="link", basis=""):
        with self._db_lock:
            self.conn.execute(
                "INSERT INTO insights(ts, kind, body, basis) VALUES (?,?,?,?)",
                (self._now(), kind, body[:500], (basis or "")[:400]))
            self.conn.commit()

    def _stats(self):
        try:
            with self._db_lock:
                a = self.conn.execute("SELECT COUNT(*) FROM analytics").fetchone()[0]
                e = self.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
                l = self.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
                i = self.conn.execute("SELECT COUNT(*) FROM insights").fetchone()[0]
            return f"analytics={a} entities={e} links={l} insights={i}"
        except Exception:
            return "associations db not ready"