import datetime
import json
import logging
import sqlite3
import threading

from core.config import get_config

log = logging.getLogger(__name__)

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
        if limit is None:
            limit = 24
        limit = int(limit)
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
    def _ensure_fact_semantic_columns(self):
        """Phase 18: additive, idempotent migration of the EXISTING facts
        table (no new store). Older rows keep working with defaults."""
        cols = ("subject", "confidence", "source", "status", "updated_at")
        for col in cols:
            try:
                self.conn.execute(
                    f"ALTER TABLE facts ADD COLUMN {col} TEXT")
                self.conn.commit()
            except sqlite3.OperationalError:
                pass  # already present
        # confidence/status are typed; TEXT columns are fine for SQLite but
        # seed sensible defaults for pre-existing rows.
        try:
            self.conn.execute(
                "UPDATE facts SET status='active' WHERE status IS NULL "
                "OR status=''")
            self.conn.execute(
                "UPDATE facts SET confidence=0.5 WHERE confidence IS NULL "
                "OR confidence=''")
            self.conn.execute(
                "UPDATE facts SET source='legacy' WHERE source IS NULL "
                "OR source=''")
            self.conn.commit()
        except sqlite3.Error:
            pass
        try:
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_subject "
                "ON facts(subject, status)")
            self.conn.commit()
        except sqlite3.Error:
            pass

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

    def remember_semantic(self, fact, category="general", subject="",
                          confidence=0.6, source="", replaces=True):
        """Phase 18: remember a fact about the human/work WITH provenance.

        subject is an optional stable key for one fact slot (e.g.
        "home_city"). When supplied, any prior active fact on the same
        subject is marked superseded rather than deleted, so the human's
        current belief is single and history stays auditable.

        No inference, no rewriting, no guessing: exactly what is given
        is stored. Confidence is a bounded numeric 0..1.
        """
        try:
            fact = (fact or "").strip()
            if not fact:
                return False
            subject = (subject or "").strip()[:120]
            source = (source or "")[:80]
            try:
                confidence = float(confidence)
            except Exception:
                confidence = 0.5
            confidence = max(0.0, min(1.0, confidence))
            ts = _iso(_now())
            with self._lock:
                self._ensure_fact_semantic_columns()
                if subject:
                    row = self.conn.execute(
                        "SELECT fact FROM facts WHERE subject=? AND status="
                        "'active' LIMIT 1", (subject,)).fetchone()
                    if row and str(row[0]) == fact:
                        # Same statement restated: reinforce, don't dup.
                        self.conn.execute(
                            "UPDATE facts SET confidence=MIN(1.0,"
                            "confidence+0.05), updated_at=? WHERE subject=? "
                            "AND status='active'", (ts, subject))
                        self.conn.commit()
                        return True
                    if row and replaces:
                        self.conn.execute(
                            "UPDATE facts SET status='superseded', "
                            "updated_at=? WHERE subject=? AND status='active'",
                            (ts, subject))
                try:
                    self.conn.execute(
                        "INSERT INTO facts(ts, category, fact, subject, "
                        "confidence, source, status, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (ts, category, fact, subject, str(confidence),
                         source, "active", ts))
                except sqlite3.IntegrityError:
                    # The exact statement already exists in history.
                    # Re-asserting it makes it the CURRENT belief again
                    # (the human restated it), bounded and provenance-
                    # preserved; otherwise just reinforce it.
                    self.conn.execute(
                        "UPDATE facts SET status='active', subject=?, "
                        "confidence=MIN(1.0, ?), source=?, updated_at=? "
                        "WHERE fact=?", (subject, max(0.05, confidence - 0.05),
                                         source, ts, fact))
                self.conn.commit()
            return True
        except Exception as e:
            log.warning(f"remember_semantic failed: {e}")
            return False

    def recall_semantic(self, query="", category="", limit=8,
                        include_superseded=False):
        """Phase 18: current (active) facts first, bounded and typed.

        Returns dicts with fact/category/subject/confidence/source/
        ts/status so callers can reason about how much to trust each
        item. Superseded rows are excluded unless asked for.
        """
        try:
            self._ensure_fact_semantic_columns()
            where = []
            args = []
            if not include_superseded:
                where.append("COALESCE(status,'active')='active'")
            if query.strip():
                where.append("fact LIKE ?")
                args.append(f"%{query.strip()}%")
            if category:
                where.append("category=?")
                args.append(category)
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            try:
                conf = "COALESCE(CAST(confidence AS REAL), 0.5)"
            except Exception:
                conf = "0.5"
            sql = (f"SELECT fact, category, COALESCE(subject,''), {conf}, "
                   f"COALESCE(source,''), ts, COALESCE(status,'active') "
                   f"FROM facts{clause} "
                   f"ORDER BY {conf} DESC, id DESC LIMIT ?")
            args.append(max(1, int(limit or 8)))
            with self._lock:
                rows = self.conn.execute(sql, args).fetchall()
            out = []
            for r in rows:
                try:
                    out.append({
                        "fact": str(r[0])[:300],
                        "category": str(r[1] or "general")[:60],
                        "subject": str(r[2] or "")[:120],
                        "confidence": round(float(r[3]), 3),
                        "source": str(r[4] or "")[:80],
                        "ts": str(r[5] or ""),
                        "status": str(r[6] or "active")[:20],
                    })
                except Exception:
                    continue
            return out
        except Exception as e:
            log.warning(f"recall_semantic failed: {e}")
            return []

    def fact_history(self, subject, limit=5):
        """Phase 18: what the human used to say about one subject."""
        try:
            self._ensure_fact_semantic_columns()
            with self._lock:
                rows = self.conn.execute(
                    "SELECT fact, COALESCE(status,'active'), ts "
                    "FROM facts WHERE subject=? ORDER BY id DESC LIMIT ?",
                    (str(subject or "")[:120], max(1, int(limit or 5))),
                ).fetchall()
            return [{"fact": str(r[0])[:200], "status": str(r[1]),
                     "ts": str(r[2])} for r in rows]
        except Exception as e:
            log.warning(f"fact_history failed: {e}")
            return []

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
