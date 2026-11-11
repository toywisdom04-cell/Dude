import datetime
import os
import re
import sqlite3
import threading
import time

from core.config import get_config


class PatternRecognizer:
    """Pattern recognition: turn a screen/app state into a compact pattern
    token. Learns a lightweight classifier over what (app, window) patterns the
    user spends time in, so the action tracker and neural model can group
    'the same kind of work' together."""

    _BROWSER = re.compile(r"(chrome|comet|edge|brave|opera|firefox|msedge)", re.I)
    _TERMINAL = re.compile(r"(terminal|powershell|cmd|console|windows command)", re.I)
    _CODE = re.compile(r"(code|visual studio|pycharm|notebook|jupyter|cursor|windsurf)", re.I)
    _DOC = re.compile(r"(word|document|doc |write|pdf|notepad|text)", re.I)
    _TALK = re.compile(r"(zoom|meet|teams|discord|slack|voice|call)", re.I)

    def classify(self, app, window):
        """Return a stable pattern label for an (app, window) state."""
        text = f"{app} {window}"
        if self._TALK.search(text):
            return "talk"
        if self._CODE.search(text):
            return "code"
        if self._BROWSER.search(text):
            return "browse"
        if self._TERMINAL.search(text):
            return "terminal"
        if self._DOC.search(text):
            return "document"
        return "other"


class ActionTracker:
    """Action tracker: continuously observe what the user IS DOING on screen
    (app/window movements + tool usage) and write it to a sequence store that
    the neural action model trains on. Also drives periodic fine-tuning.

    This is the 'learn by seeing my movements' circuit — it records the real
    behaviour stream so the model learns the user's actual work patterns."""

    def __init__(self, data_dir=None, observer=None, action_learner=None):
        if data_dir is None:
            data_dir = get_config().data_dir
        self.data_dir = data_dir
        self.observer = observer
        self.learner = action_learner
        self.db = os.path.join(data_dir, "action_track.db")
        self.pattern = PatternRecognizer()
        self._conn = sqlite3.connect(self.db, check_same_thread=False)
        self._conn.execute("""CREATE TABLE IF NOT EXISTS user_moves(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, app TEXT, window TEXT, pattern TEXT)""")
        self._conn.commit()
        self._on = False
        self._last_state = None
        self._thread = None
        self._lock = threading.Lock()

    # ---------- continuous tracking ----------
    def start(self):
        if self._on:
            return
        self._on = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="du-actiontrack")
        self._thread.start()

    def _loop(self):
        while self._on:
            try:
                if self.observer is not None:
                    cur = self.observer.current_screen()
                    app = cur.get("app", "")
                    window = cur.get("title", "")
                    state = (app, window)
                    if state != self._last_state:
                        pat = self.pattern.classify(app, window)
                        with self._lock:
                            self._conn.execute(
                                "INSERT INTO user_moves(ts, app, window, pattern) "
                                "VALUES (?,?,?,?)",
                                (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                 app[:60], window[:120], pat))
                            self._conn.commit()
                        self._last_state = state
            except Exception:
                pass
            time.sleep(3)

    def stop(self):
        self._on = False

    # ---------- data access / datasets ----------
    def moves(self, limit=1000):
        c = self._conn.cursor()
        return c.execute(
            "SELECT ts, app, window, pattern FROM user_moves "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def pattern_distribution(self):
        c = self._conn.cursor()
        return c.execute(
            "SELECT pattern, COUNT(*) FROM user_moves GROUP BY pattern "
            "ORDER BY 2 DESC").fetchall()

    def datasets_report(self):
        """List-of-datasets overview: what data feeds the neural model, with counts."""
        c = self._conn.cursor()
        n_moves = c.execute("SELECT COUNT(*) FROM user_moves").fetchone()[0]
        try:
            exp = sqlite3.connect(os.path.join(self.data_dir, "experience.db"))
            n_exp = exp.execute("SELECT COUNT(*) FROM experiences").fetchone()[0]
            n_obs = exp.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            exp.close()
        except Exception:
            n_exp, n_obs = 0, 0
        return {
            "model_weights": os.path.join(self.data_dir, "action_model.pt"),
            "datasets": [
                {"name": "action_track.db/user_moves",
                 "rows": n_moves,
                 "use": "user's observed screen movements (app/window/pattern)"},
                {"name": "experience.db/experiences",
                 "rows": n_exp,
                 "use": "DUDE's actions + outcome per context (trains next-action net)"},
                {"name": "experience.db/observations",
                 "rows": n_obs,
                 "use": "screen/app observations (pattern context)"},
            ],
            "model": "PyTorch GRU sequence net (actionbrain.py)",
        }

    # ---------- fine-tuning ----------
    def fine_tune(self, epochs=25):
        """Retrain (fine-tune) the neural action model on all accumulated data."""
        if self.learner is not None:
            return self.learner.train(epochs=epochs)
        return "no action learner attached"

    def migrate_observations(self):
        """Seed user_moves from existing observations for immediate training data."""
        try:
            exp = sqlite3.connect(os.path.join(self.data_dir, "experience.db"))
            rows = exp.execute(
                "SELECT ts, app, window FROM observations ORDER BY id ASC").fetchall()
            exp.close()
            with self._lock:
                for ts, app, window in rows:
                    pat = self.pattern.classify(app or "", window or "")
                    self._conn.execute(
                        "INSERT OR IGNORE INTO user_moves(ts, app, window, pattern) "
                        "VALUES (?,?,?,?)",
                        (ts, app or "", window or "", pat))
                self._conn.commit()
            return len(rows)
        except Exception as e:
            return f"migrate failed: {e}"
