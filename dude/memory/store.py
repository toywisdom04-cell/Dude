"""SQLite-backed memory store.

Tables:
  conversations - every exchange (role, text, timestamp)
  session_state - last app/file/task per day, for greeting resume
  profile_facts - learned facts about the user (confirmed by user)
  reminders     - one-off and recurring reminders/meetings
  actions_log   - audit log of actions DUDE took
"""
import datetime
import sqlite3
from pathlib import Path


class MemoryStore:
    def __init__(self, data_dir: str = "./dude_data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.data_dir / "dude.db"))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                directed INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS session_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                last_app TEXT DEFAULT '',
                last_file TEXT DEFAULT '',
                last_task TEXT DEFAULT '',
                last_seen TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS profile_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                confirmed INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                message TEXT DEFAULT '',
                remind_at TEXT NOT NULL,
                recurring TEXT DEFAULT '',
                completed INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS actions_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                details TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    # ---- conversations ----
    def add_message(self, role: str, text: str, directed: bool = True) -> None:
        self._conn.execute(
            "INSERT INTO conversations (role, text, directed, created_at) VALUES (?,?,?,?)",
            (role, text, int(directed), datetime.datetime.now().isoformat()),
        )
        self._conn.commit()

    def recent_messages(self, limit: int = 40) -> list[dict]:
        rows = self._conn.execute(
            "SELECT role, text FROM conversations ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ---- session state ----
    def update_session(self, last_app: str = "", last_file: str = "",
                       last_task: str = "") -> None:
        day = datetime.date.today().isoformat()
        row = self._conn.execute(
            "SELECT id FROM session_state WHERE day = ?", (day,),
        ).fetchone()
        now = datetime.datetime.now().isoformat()
        if row:
            self._conn.execute(
                "UPDATE session_state SET last_app=?, last_file=?, last_task=?, last_seen=? WHERE id=?",
                (last_app, last_file, last_task, now, row["id"]),
            )
        else:
            self._conn.execute(
                "INSERT INTO session_state (day, last_app, last_file, last_task, last_seen) VALUES (?,?,?,?,?)",
                (day, last_app, last_file, last_task, now),
            )
        self._conn.commit()

    def latest_session(self) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM session_state ORDER BY id DESC LIMIT 1",
        ).fetchone()
        return dict(row) if row else None

    # ---- profile facts ----
    def add_fact(self, fact: str, category: str = "general", confirmed: bool = True) -> None:
        self._conn.execute(
            "INSERT INTO profile_facts (fact, category, confirmed, created_at) VALUES (?,?,?,?)",
            (fact, category, int(confirmed), datetime.datetime.now().isoformat()),
        )
        self._conn.commit()

    def facts(self, category: str | None = None) -> list[dict]:
        if category:
            rows = self._conn.execute(
                "SELECT fact, category FROM profile_facts WHERE category = ? ORDER BY id",
                (category,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT fact, category FROM profile_facts ORDER BY id",
            ).fetchall()
        return [dict(r) for r in rows]

    def forget(self, fact_fragment: str) -> int:
        cur = self._conn.execute(
            "DELETE FROM profile_facts WHERE fact LIKE ?", (f"%{fact_fragment}%",),
        )
        self._conn.commit()
        return cur.rowcount

    # ---- reminders ----
    def add_reminder(self, title: str, message: str, remind_at: str,
                     recurring: str = "") -> int:
        cur = self._conn.execute(
            "INSERT INTO reminders (title, message, remind_at, recurring, created_at) VALUES (?,?,?,?,?)",
            (title, message, remind_at, recurring, datetime.datetime.now().isoformat()),
        )
        self._conn.commit()
        return cur.lastrowid

    def due_reminders(self, now: datetime.datetime | None = None) -> list[dict]:
        now = now or datetime.datetime.now()
        rows = self._conn.execute(
            "SELECT * FROM reminders WHERE completed = 0 AND remind_at <= ?",
            (now.isoformat(),),
        ).fetchall()
        return [dict(r) for r in rows]

    def upcoming_reminders(self, days: int = 1) -> list[dict]:
        now = datetime.datetime.now()
        horizon = now + datetime.timedelta(days=days)
        rows = self._conn.execute(
            "SELECT * FROM reminders WHERE completed = 0 AND remind_at BETWEEN ? AND ? ORDER BY remind_at",
            (now.isoformat(), horizon.isoformat()),
        ).fetchall()
        return [dict(r) for r in rows]

    def complete_reminder(self, reminder_id: int) -> None:
        self._conn.execute(
            "UPDATE reminders SET completed = 1 WHERE id = ?", (reminder_id,),
        )
        self._conn.commit()

    # ---- actions log ----
    def log_action(self, action: str, details: str = "") -> None:
        self._conn.execute(
            "INSERT INTO actions_log (action, details, created_at) VALUES (?,?,?)",
            (action, details, datetime.datetime.now().isoformat()),
        )
        self._conn.commit()

    def recent_actions(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM actions_log ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()

