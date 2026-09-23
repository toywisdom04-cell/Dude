import datetime
import json
import sqlite3
import threading

from core.config import get_config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_content ON messages(content);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    fact TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS work_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app TEXT NOT NULL,
    title TEXT NOT NULL,
    started TEXT NOT NULL,
    ended TEXT
);
CREATE INDEX IF NOT EXISTS idx_ws_started ON work_sessions(started);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts TEXT NOT NULL,
    due_ts TEXT NOT NULL,
    text TEXT NOT NULL,
    done INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS routines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time_of_day TEXT NOT NULL,
    days TEXT NOT NULL DEFAULT 'daily',
    text TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    last_fired TEXT
);

CREATE TABLE IF NOT EXISTS session_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT
);
"""


def _now():
    return datetime.datetime.now()


def _iso(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse(s):
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


class Memory:
    def __init__(self):
        self.cfg = get_config()
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.cfg.db_path(), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        self.session_id = _now().strftime("s%Y%m%d-%H%M%S")

    def close(self):
        with self._lock:
            self.conn.close()

    # ---------- conversation ----------
    def add_message(self, role, content):
        with self._lock:
            self.conn.execute(
                "INSERT INTO messages(session_id, ts, role, content) VALUES (?,?,?,?)",
                (self.session_id, _iso(_now()), role, content),
            )
            self.conn.commit()

    def recent_messages(self, limit=24):
        rows = self.conn.execute(
            "SELECT role, content FROM (SELECT id, role, content FROM messages ORDER BY id DESC LIMIT ?) "
            "ORDER BY id ASC",
            (limit,),
        ).fetchall()
        return [{"role": r, "content": c} for r, c in rows]

    def search_messages(self, query, limit=8):
        like = f"%{query}%"
        rows = self.conn.execute(
            "SELECT ts, role, content FROM messages WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
            (like, limit),
        ).fetchall()
        return [{"ts": r[0], "role": r[1], "content": r[2]} for r in rows]

    # ---------- facts ----------
    def remember_fact(self, fact, category="general"):
        fact = fact.strip()
        if not fact:
            return False
        try:
            with self._lock:
                self.conn.execute(
                    "INSERT INTO facts(ts, category, fact) VALUES (?,?,?)",
                    (_iso(_now()), category, fact),
                )
                self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def install_doctrine(self, text):
        """Persist an immutable operating rule (CORE_IDENTITY) so it is part of
        DUDE's permanent memory and is ALWAYS recalled in every context."""
        text = text.strip()
        exists = self.conn.execute(
            "SELECT 1 FROM facts WHERE category='CORE_IDENTITY' AND fact=? LIMIT 1",
            (text,),
        ).fetchone()
        if not exists:
            self.remember_fact(text, category="CORE_IDENTITY")
            return True
        return False

    def recall_facts(self, query="", limit=8):
        if query.strip():
            like = f"%{query}%"
            rows = self.conn.execute(
                "SELECT fact FROM facts WHERE fact LIKE ? ORDER BY id DESC LIMIT ?", (like, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT fact FROM facts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [r[0] for r in rows]

    # Categories that describe the USER durably (job/company, preferences,
    # identity) and forever-pinned facts. These are far more important than the
    # auto-captured "ambient"/"screen_learning" notes, which change constantly
    # and bury the real personal profile. build_context uses this so the model
    # always sees the user's true identity instead of the latest background noise.
    _KEY_CATEGORIES = (
        "CORE_IDENTITY", "personal", "preferences", "work",
        "identity", "work_preferences", "self", "user",
    )
    _KEY_RANK = {
        "CORE_IDENTITY": 1, "personal": 2, "identity": 3,
        "preferences": 4, "work": 5, "work_preferences": 6,
        "self": 7, "user": 8,
    }

    def recall_key_facts(self, limit=14):
        # Balanced: take 4 most recent from each important category, then
        # interleave by category priority so the model ALWAYS sees who you are,
        # where you work, and your preferences — not just the newest block of
        # one category eating all slots. Dedup by normalized text.
        pool = []
        seen = set()
        rank = self._KEY_RANK
        for cat in ("CORE_IDENTITY", "personal", "preferences", "work"):
            rows = self.conn.execute(
                "SELECT fact FROM facts WHERE category=? ORDER BY id DESC LIMIT 4",
                (cat,),
            ).fetchall()
            for (fact,) in rows:
                key = fact.strip().lower()
                if key not in seen:
                    seen.add(key)
                    pool.append((fact, rank.get(cat, 9)))
        pool.sort(key=lambda x: x[1])
        return [f for f, _ in pool[:limit]]

    def facts_by_category(self, category, limit=50):
        rows = self.conn.execute(
            "SELECT ts, fact FROM facts WHERE category=? ORDER BY id DESC LIMIT ?",
            (category, limit),
        ).fetchall()
        return [{"ts": r[0], "fact": r[1]} for r in rows]

    def add_notice(self, text):
        stamp = _now().strftime("%Y-%m-%d %H:%M:%S")
        return self.remember_fact(f"[notice {stamp}] {text}", category="notice")

    def since_notices(self, since_iso, limit=20):
        rows = self.conn.execute(
            "SELECT fact FROM facts WHERE category='notice' AND fact LIKE '[notice %' "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for (fact,) in rows:
            try:
                ts = fact[len("[notice "):fact.index("]")]
            except ValueError:
                continue
            if ts >= since_iso:
                out.append(fact)
        out.reverse()
        return out

    def _todo_load(self):
        try:
            return json.loads(self.get_state("todo_queue", "[]") or "[]")
        except Exception:
            return []

    def _todo_save(self, q):
        self.set_state("todo_queue", json.dumps(q))
        return q

    def todo_add(self, text):
        q = self._todo_load()
        nid = (max([t["id"] for t in q], default=0) + 1)
        q.append({"id": nid, "text": text, "created": _now().strftime("%Y-%m-%d %H:%M"),
                  "done": False})
        self._todo_save(q)
        return nid

    def todo_list(self):
        return self._todo_load()

    def todo_done(self, tid):
        q = self._todo_load()
        for t in q:
            if t["id"] == tid:
                t["done"] = True
                t["completed"] = _now().strftime("%Y-%m-%d %H:%M")
        return self._todo_save(q)

    def todo_remove(self, tid):
        q = self._todo_load()
        q = [t for t in q if t["id"] != tid]
        return self._todo_save(q)

    # ---------- work sessions ----------
    def open_work_session(self, app, title):
        with self._lock:
            self.conn.execute(
                "UPDATE work_sessions SET ended=? WHERE ended IS NULL",
                (_iso(_now()),),
            )
            self.conn.execute(
                "INSERT INTO work_sessions(app, title, started) VALUES (?,?,?)",
                (app, title[:300], _iso(_now())),
            )
            self.conn.commit()

    def close_open_sessions(self):
        with self._lock:
            self.conn.execute(
                "UPDATE work_sessions SET ended=? WHERE ended IS NULL", (_iso(_now()),)
            )
            self.conn.commit()

    def last_work_before(self, before_dt=None):
        before_dt = before_dt or (_now() - datetime.timedelta(minutes=5))
        row = self.conn.execute(
            "SELECT app, title, started, ended FROM work_sessions "
            "WHERE ended IS NOT NULL AND started < ? AND ended < ? "
            "ORDER BY ended DESC LIMIT 1",
            (_iso(before_dt), _iso(before_dt)),
        ).fetchone()
        return row

    def last_real_work_before(self, before_dt=None):
        """Most recent ended session that actually looks like work (skips media/
        entertainment/blank titles), so a greeting doesn't recite a YouTube tab."""
        before_dt = before_dt or (_now() - datetime.timedelta(minutes=5))
        rows = self.conn.execute(
            "SELECT app, title, started, ended FROM work_sessions "
            "WHERE ended IS NOT NULL AND started < ? AND ended < ? "
            "AND title IS NOT NULL AND trim(title) <> '' "
            "ORDER BY ended DESC LIMIT 300",
            (_iso(before_dt), _iso(before_dt)),
        ).fetchall()
        junk = ("youtube", "shorts", " #", "comet", "marvel", "netflix",
                "primevideo", "smartconnect", "spring", "settings", "lock")
        for app, t, started, ended in rows:
            low = (str(app) + " " + str(t)).lower()
            if any(k in low for k in junk):
                continue
            if str(app).lower().strip() in ("python.exe", "cmd.exe", "conhost.exe"):
                continue
            return (app, t, started, ended)
        return self.last_work_before(before_dt)

    def today_summary(self):
        day = _now().strftime("%Y-%m-%d")
        rows = self.conn.execute(
            "SELECT app, SUM(julianday(ended)-julianday(started)) * 86400 AS secs "
            "FROM work_sessions WHERE date(started)=? AND ended IS NOT NULL "
            "GROUP BY app ORDER BY secs DESC",
            (day,),
        ).fetchall()
        return [(r[0], int(r[1] or 0)) for r in rows]

    def day_summary(self, days_ago=1):
        day = (_now() - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%d")
        rows = self.conn.execute(
            "SELECT app, SUM(julianday(ended)-julianday(started)) * 86400 AS secs "
            "FROM work_sessions WHERE date(started)=? AND ended IS NOT NULL "
            "GROUP BY app ORDER BY secs DESC",
            (day,),
        ).fetchall()
        return [(r[0], int(r[1] or 0)) for r in rows]

    def current_work(self):
        row = self.conn.execute(
            "SELECT app, title, started FROM work_sessions WHERE ended IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row

    # ---------- reminders ----------
    def add_reminder(self, text, when_iso):
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO reminders(created_ts, due_ts, text) VALUES (?,?,?)",
                (_iso(_now()), when_iso, text),
            )
            self.conn.commit()
            return cur.lastrowid

    def pending_reminders(self, now=None, limit=20):
        now = now or _now()
        rows = self.conn.execute(
            "SELECT id, due_ts, text FROM reminders WHERE done=0 AND due_ts <= ? ORDER BY due_ts LIMIT ?",
            (_iso(now), limit),
        ).fetchall()
        return [{"id": r[0], "due_ts": r[1], "text": r[2]} for r in rows]

    def upcoming_reminders(self, limit=10):
        rows = self.conn.execute(
            "SELECT id, due_ts, text FROM reminders WHERE done=0 AND due_ts > ? ORDER BY due_ts LIMIT ?",
            (_iso(_now()), limit),
        ).fetchall()
        return [{"id": r[0], "due_ts": r[1], "text": r[2]} for r in rows]

    def complete_reminder(self, rid):
        with self._lock:
            self.conn.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,))
            self.conn.commit()

    def all_reminders(self, limit=30):
        rows = self.conn.execute(
            "SELECT id, due_ts, text, done FROM reminders ORDER BY done ASC, due_ts ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"id": r[0], "due_ts": r[1], "text": r[2], "done": bool(r[3])} for r in rows
        ]

    # ---------- routines (recurring) ----------
    def add_routine(self, text, time_of_day, days="daily"):
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO routines(time_of_day, days, text) VALUES (?,?,?)",
                (time_of_day, days, text),
            )
            self.conn.commit()
            return cur.lastrowid

    def list_routines(self):
        rows = self.conn.execute(
            "SELECT id, time_of_day, days, text, active FROM routines ORDER BY time_of_day"
        ).fetchall()
        return [{"id": r[0], "time_of_day": r[1], "days": r[2], "text": r[3],
                 "active": bool(r[4])} for r in rows]

    def due_routines(self, now=None):
        now = now or _now()
        day_name = now.strftime("%A").lower()
        out = []
        for rid, tod, days, text, active, last in self.conn.execute(
            "SELECT id, time_of_day, days, text, active, last_fired FROM routines "
            "WHERE active=1"
        ).fetchall():
            try:
                hh, mm = map(int, tod.split(":"))
            except ValueError:
                continue
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if now < target:
                continue
            if last and last >= target.strftime("%Y-%m-%d %H:%M"):
                continue
            if days.strip().lower() not in ("daily", "everyday", ""):
                day_list = [d.strip().lower() for d in days.split(",")]
                if day_name not in day_list:
                    continue
            out.append({"id": rid, "time_of_day": tod, "days": days, "text": text})
        return out

    def mark_routine_fired(self, rid):
        with self._lock:
            self.conn.execute(
                "UPDATE routines SET last_fired=? WHERE id=?", (_iso(_now()), rid))
            self.conn.commit()

    def remove_routine(self, rid):
        with self._lock:
            self.conn.execute("UPDATE routines SET active=0 WHERE id=?", (rid,))
            self.conn.commit()

    # ---------- session state ----------
    def set_state(self, key, value):
        with self._lock:
            self.conn.execute(
                "INSERT INTO session_state(key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )
            self.conn.commit()

    def get_state(self, key, default=None):
        row = self.conn.execute(
            "SELECT value FROM session_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    # ---------- audit ----------
    def audit(self, action, detail=""):
        try:
            with self._lock:
                self.conn.execute(
                    "INSERT INTO audit_log(ts, action, detail) VALUES (?,?,?)",
                    (_iso(_now()), action, detail[:500]),
                )
                self.conn.commit()
        except Exception:
            pass

    def recent_audit(self, limit=20):
        rows = self.conn.execute(
            "SELECT ts, action, detail FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{"ts": r[0], "action": r[1], "detail": r[2]} for r in rows]

    def audit_recent_secret_denials(self, label):
        row = self.conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='secret_request' "
            "AND detail LIKE ? AND detail LIKE '%granted=False'",
            (f"%label={label}%",),
        ).fetchone()
        return row[0] if row else 0
