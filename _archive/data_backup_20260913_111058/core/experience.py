import datetime
import json
import logging
import os
import re
import threading
import time

from core.config import get_config
from platform_utils import get_platform

log = logging.getLogger("dude")

_WATCH_BRAIN = None


def set_watch_brain(brain):
    global _WATCH_BRAIN
    _WATCH_BRAIN = brain

_OK_TOOLS = {"screenshot", "analyze_recent_screens", "ui_scan", "scroll_screen",
             "active_window_info", "list_windows", "list_running_apps", "search_knowledge",
             "recall_about_user", "search_conversation_history"}
_DIRTY = re.compile(r"\b(delete|remove|taskkill|shutdown|restart|format|rmdir|del\s+/s|"
                    r"clear-content)\b", re.I)


class ExperienceLearner:
    """The learning circuit: see -> act -> outcome -> lesson -> next decision.

    Every action DUDE takes on the user's machine is recorded as an experience
    together with what was on screen before/after and whether that action
    helped. From the accumulated record it derives behaviour lessons (what
    works, what keeps failing) that get fed back into the next turn's context,
    so DUDE gets better by doing — observing, clicking, making mistakes and
    learning through them.
    """

    def __init__(self, memory, data_dir=None):
        self.memory = memory
        cfg = get_config()
        if data_dir is None:
            data_dir = cfg.data_dir
        import sqlite3 as sqlite3

        os.makedirs(data_dir, exist_ok=True)
        self.conn = sqlite3.connect(os.path.join(data_dir, "experience.db"),
                                    check_same_thread=False)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS experiences(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, app TEXT, window TEXT, tool TEXT, args TEXT, outcome TEXT,
            detail TEXT)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS observations(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, app TEXT, window TEXT, detail TEXT, img TEXT)""")
        self.conn.commit()
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(observations)")}
        if "img" not in cols:
            try:
                self.conn.execute("ALTER TABLE observations ADD COLUMN img TEXT")
                self.conn.commit()
            except Exception:
                pass
        self._lock = threading.Lock()
        self._watch = {"on": False, "last": None, "stop_req": False,
                       "started": None, "thread": None}
        # --- continuous observe tuning ---
        self.capture_seconds = float(cfg.get("observe", "capture_seconds", default=4))
        self.analyze_seconds = float(cfg.get("observe", "analyze_seconds", default=25))
        self.motion_threshold = float(cfg.get("observe", "motion_threshold", default=2.0))
        self.max_batch_screens = int(cfg.get("observe", "max_batch_screens", default=6))
        self.ocr_on = bool(cfg.get("observe", "ocr", default=True))
        self.watch_dir = os.path.join(data_dir, "observation", "watch")
        os.makedirs(self.watch_dir, exist_ok=True)
        self._last_gray = None
        self._pending = []

    # ---------- recording ----------
    def record(self, tool, args, outcome="MISS", detail="", app="", window=""):
        try:
            with self._lock:
                self.conn.execute(
                    "INSERT INTO experiences(ts, app, window, tool, args, outcome, detail) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     app[:60], window[:120], tool[:40], json.dumps(args)[:300],
                     outcome[:12], str(detail)[:300]))
                self.conn.commit()
        except Exception:
            pass

    def classify(self, result, detail=""):
        r = (result or "").strip()
        low = r[:400].lower()
        if "denied" in low or "declined" in low:
            return "FAIL"
        if r.startswith("ERROR") or r.startswith("could not") or " failed" in low \
                or "does not exist" in low or "no matching" in low \
                or "not found" in low:
            if "no matching knowledge" in low or "not found" in low:
                return "MISS"
            return "FAIL"
        if r.startswith("SKIPPED"):
            return "SKIP"
        return "OK"

    def screen_signal(self, observer):
        try:
            cur = observer.current_screen()
            return cur.get("app", "")[:60], cur.get("title", "")[:120]
        except Exception:
            return "", ""

    # ---------- derived lessons ----------
    def lessons(self, app="", tool="", limit=6, fail_only_recent=6):
        """Runs over the experience record and produces a short behaviour
        summary a model can act on: proven successes and repeated failures
        in roughly this context."""
        try:
            rows = self.conn.execute(
                "SELECT tool, outcome, args, ts FROM experiences "
                "WHERE (?='' OR app=?) AND (?='' OR tool=?) "
                "ORDER BY id DESC LIMIT 400",
                (app, app, tool, tool)).fetchall()
        except Exception:
            return []
        if not rows:
            return []
        ok, miss, fail = 0, 0, 0
        ok_examples, fail_recent = [], []
        by_tool = {}
        for tool_n, outcome, args, ts in rows:
            by_tool.setdefault(tool_n, [0, 0, 0])
            if outcome == "OK":
                ok += 1
                by_tool[tool_n][0] += 1
                if len(ok_examples) < 3 and tool_n in _OK_TOOLS:
                    ok_examples.append(tool_n)
            elif outcome == "MISS":
                miss += 1
                by_tool[tool_n][1] += 1
            elif outcome == "FAIL":
                fail += 1
                by_tool[tool_n][2] += 1
                fail_recent.append(tool_n)
        out = []
        if fail_recent:
            worst = {}
            for t in fail_recent:
                worst[t] = worst.get(t, 0) + 1
            for t, n in sorted(worst.items(), key=lambda kv: -kv[1])[:3]:
                out.append(f"tool {t} has failed you {n}x recently — avoid and take another route")
        total = ok + miss + fail
        if total >= 3:
            out.append(f"you have {ok} successful, {miss} empty, {fail} failed actions in "
                       f"{total} experience entries")
        for t, (o, m, f) in sorted(by_tool.items(), key=lambda kv: -kv[1][0])[:3]:
            if o >= 2:
                out.append(f"{t} is reliable for you ({o} wins, {f} fails)")
        return out[:limit]

    # ---------- watch & learn (observe me, continuous) ----------
    def _session_dir(self):
        started = self._watch.get("started")
        stamp = started.strftime("%Y%m%d_%H%M%S") if started else \
            datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        d = os.path.join(self.watch_dir, f"session_{stamp}")
        os.makedirs(d, exist_ok=True)
        return d

    def _motion_score(self, img):
        """OpenCV change score vs the previous frame (0 = identical). Returns a
        float; big values mean the user actually did something new on screen."""
        try:
            import cv2
            import numpy as np

            arr = cv2.resize(np.array(img.convert("L")), (256, 144),
                             interpolation=cv2.INTER_AREA).astype(np.float32)
            if self._last_gray is None:
                self._last_gray = arr
                return 999.0
            diff = float(np.mean(np.abs(arr - self._last_gray)))
            self._last_gray = arr
            return diff
        except Exception:
            return 999.0

    def _ocr_text(self, img):
        """Read visible text with tesseract (graceful no-op if unavailable)."""
        if not self.ocr_on:
            return ""
        try:
            from core.ocr import ocr_image

            return ocr_image(img)
        except Exception:
            return ""

    def _ui_view(self):
        try:
            from core.tools import _SCREENTREE
            if _SCREENTREE is not None:
                return _SCREENTREE.current_view(limit=20)
        except Exception:
            pass
        return ""

    def _flush_batch(self, brain):
        """Send pending changed screenshots (+ OCR + UI map) to the vision brain,
        then store every durable line immediately: observations table, knowledge
        note and memory facts. Called on a cadence so nothing waits for 'stop'."""
        with self._lock:
            batch = list(self._pending)
            self._pending = []
        if not batch:
            return 0
        from PIL import Image, ImageFile

        ImageFile.MAX_IMAGE_PIXELS = None
        imgs = [b["img"] for b in batch[-self.max_batch_screens:]]
        w = max(im.width for im in imgs)
        h = max(im.height for im in imgs)
        cols = 2 if len(imgs) > 1 else 1
        rows = (len(imgs) + cols - 1) // cols
        grid = Image.new("RGB", (w * cols, h * rows), "black")
        for i, im in enumerate(imgs):
            grid.paste(im, ((i % cols) * w, (i // cols) * h))
        import io
        import base64

        buf = io.BytesIO()
        grid.save(buf, "JPEG", quality=60)
        b64 = base64.b64encode(buf.getvalue()).decode()

        ocr_parts = [f"[{b['ts']}] {b['app']} — {b['window']}" +
                     (f"\nSCREEN TEXT:\n{b['ocr']}" if b.get("ocr") else "")
                     for b in batch]
        ui_hint = ""
        try:
            ui_hint = batch[-1].get("ui", "") or ""
            if ui_hint:
                ui_hint = ("\nLIVE UI CONTROLS (names + positions from Windows UI "
                           "Automation):\n" + ui_hint)
        except Exception:
            pass
        own_actions = ""
        try:
            st = self._watch.get("started")
            if st is not None and self.conn is not None:
                rows = self.conn.execute(
                    "SELECT ts, app, tool, args, outcome FROM experiences "
                    "WHERE ts >= ? ORDER BY id DESC LIMIT 10",
                    (st.strftime("%Y-%m-%d %H:%M:%S"),),
                ).fetchall()
                if rows:
                    rep = {"\n": " ", "\t": " "}
                    def clean(s):
                        s = str(s or "")[:120]
                        for a, b in rep.items():
                            s = s.replace(a, b)
                        return s
                    own_actions = ("\nDUDE'S OWN RECENT ACTIONS (what DUDE did on this "
                                   "machine, with outcomes — learn from the wins and "
                                   "mistakes):\n" +
                                   "\n".join(
                                       f"- {clean(ts)} {clean(app)} tool=({clean(tool)}) "
                                       f"args=({clean(args)}) -> {clean(outcome)}"
                                       for ts, app, tool, args, outcome in rows))
        except Exception:
            own_actions = ""
        prompt = (
            "These are recent screenshots from ONE person's live screen while they "
            "work. The person asked you to watch continuously and remember everything.\n\n"
            "CAPTURE LOG (timestamps, app, window, visible text):\n" +
            "\n".join(ocr_parts) + ui_hint + own_actions + "\n\n"
            "Describe — in short plain lines — exactly what the person is doing in "
            "each step: which app, what they are working on, any button/dialog/field "
            "they are using, and any value or document visible. Also include one line "
            "about DUDE'S OWN actions shown above: what DUDE did well or repeated, and "
            "one line for any mistake to avoid, each beginning 'LEARN:' . End with any "
            "obvious repeatable sequence as one line beginning WORKFLOW: followed by a "
            "short name and the observed steps. Never guess the next step, never "
            "recommend sending/publishing/deleting/paying anything automatically. "
            "Maximum 8 short plain lines; no preamble."
        )
        out = ""
        if brain is not None and brain.has_vision():
            try:
                out = (brain.vision_analyze(b64, prompt) or "").strip()
            except Exception as e:
                print(f"[watch] vision analyze failed: {e}")
        elif brain is None:
            out = ""
        added = 0
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        kb_lines, fact_lines = [], []
        with self._lock:
            for b in batch:
                self.conn.execute(
                    "INSERT INTO observations(ts, app, window, detail, img) "
                    "VALUES (?,?,?,?,?)",
                    (b["ts"], (b["app"] or "")[:60], (b["window"] or "")[:120],
                     (b.get("ocr") or "")[:400], b.get("imgname") or ""))
            self.conn.commit()
        for line in (out.splitlines() or []):
            line = line.strip().lstrip("-*0123456789. ")
            if len(line) < 12 or len(line) > 250:
                continue
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            fact = f"[watch {stamp}] {line}"
            dupe = False
            try:
                for e in self.memory.recall_facts(line[:50], limit=4):
                    if line[:70].lower() in e.lower():
                        dupe = True
                        break
            except Exception:
                pass
            if not dupe:
                try:
                    category = ("workflow_candidate" if line.upper().startswith("WORKFLOW:")
                                else "screen_learning")
                    self.memory.remember_fact(fact, category=category)
                    added += 1
                except Exception:
                    pass
            kb_lines.append(f"({stamp}) {line}")
        if kb_lines or batch:
            try:
                from core.knowledge import get_knowledge
                today = datetime.date.today().strftime("%Y-%m-%d")
                kb = get_knowledge()
                kb.append_daily_note(
                    f"watch-session-{today}",
                    kb_lines if kb_lines else
                    [f"({now}) {b['app']} — {b['window']}" for b in batch])
            except Exception:
                pass
        with self._lock:
            self._watch["summary_lines"] = (self._watch.get("summary_lines") or [])
            for kb_ln in kb_lines[:8]:
                if kb_ln not in self._watch["summary_lines"]:
                    self._watch["summary_lines"].append(kb_ln)
        return added

    def watch_start(self, observer, brain=None, capture_seconds=None,
                    analyze_seconds=None):
        if self._watch["on"]:
            return False
        cad = (int(capture_seconds or self.capture_seconds),
               int(analyze_seconds or self.analyze_seconds))
        self.capture_seconds, self.analyze_seconds = cad
        self._watch = {"on": True, "last": None, "stop_req": False,
                       "started": datetime.datetime.now(), "thread": None,
                       "summary_lines": [], "frames": 0, "last_analysis": time.time(),
                       "restarts": 0, "cadence": cad}
        sdir = self._session_dir()
        self._watch["dir"] = sdir

        def run_loop():
            last_capture = 0.0
            while self._watch["on"] and not self._watch["stop_req"]:
                try:
                    now = time.time()
                    if now - last_capture >= self.capture_seconds:
                        last_capture = now
                        self._tick(brain, sdir)
                except Exception as e:
                    print(f"[watch] tick error: {e}")
                time.sleep(0.7)

        def loop():
            try:
                run_loop()
            except Exception as e:  # anything fatal kills ONE attempt, never the mode
                print(f"[watch] loop crashed ({e}) — self-healing will restart it")

        def keeper():
            # SELF-HEAL: if the watch loop thread ever dies while observe mode is
            # still on, restart it automatically (max 3 restarts per session).
            while self._watch["on"]:
                t = self._watch.get("thread")
                if t is not None and not t.is_alive():
                    if self._watch.get("restarts", 0) >= 3:
                        print("[watch] self-heal: giving up after 3 restarts")
                        break
                    self._watch["restarts"] = self._watch.get("restarts", 0) + 1
                    print(f"[watch] self-heal: watch loop died, restarting "
                          f"({self._watch['restarts']}/3)")
                    thr = threading.Thread(target=loop, daemon=True, name="du-watch")
                    self._watch["thread"] = thr
                    thr.start()
                time.sleep(5)

        self._watch["thread"] = threading.Thread(target=loop, daemon=True,
                                                 name="du-watch")
        self._watch["thread"].start()
        threading.Thread(target=keeper, daemon=True,
                         name="du-watch-keeper").start()
        print(f"[watch] continuously observing -> {sdir} "
              f"(capture {self.capture_seconds}s, analyze {self.analyze_seconds}s)")
        return True

    def watch_status(self):
        """Full truth about whether observation is REALLY happening. Used to keep
        replies honest and to drive the self-heal/diagnose tools."""
        w = self._watch
        if not w.get("on"):
            return {"on": False, "frames": w.get("frames", 0),
                    "insights": len(w.get("summary_lines") or []),
                    "started": "", "dir": "", "restarts": w.get("restarts", 0)}
        thr = w.get("thread")
        obs = self.conn.execute(
            "SELECT COUNT(*) FROM observations WHERE ts >= ?",
            (w.get("started", datetime.datetime.now())
             .strftime("%Y-%m-%d %H:%M:%S"),),
        ).fetchone()[0]
        return {
            "on": True,
            "frames": w.get("frames", 0),
            "observations_row_count": obs,
            "insights": len(w.get("summary_lines") or []),
            "loop_alive": thr is not None and thr.is_alive(),
            "restarts": w.get("restarts", 0),
            "started": w.get("started", datetime.datetime.now())
            .strftime("%Y-%m-%d %H:%M:%S"),
            "dir": w.get("dir", ""),
        }

    def _tick(self, brain, sdir):
        from core.tools import _capture_screen_composite
        from PIL import Image

        img, _ = _capture_screen_composite()
        if img is None:
            return
        img.thumbnail((1280, 1280))
        motion = self._motion_score(img)
        app = ""
        title = ""
        try:
            w = get_platform().foreground_window()
            app = w.get("app", "")
            title = w.get("title", "")
        except Exception:
            pass
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if motion < self.motion_threshold:
            return  # screen did not actually move — no reason to burn analysis
        with self._lock:
            self._watch["frames"] = self._watch.get("frames", 0) + 1
        imgname = f"step_{self._watch['frames']:05d}.jpg"
        img.convert("RGB").save(os.path.join(sdir, imgname), "JPEG", quality=55)
        ocr = self._ocr_text(img) or ""
        ui = self._ui_view()
        with self._lock:
            self._pending.append({"ts": ts, "app": app[:60], "window": title[:120],
                                  "ocr": ocr, "ui": ui, "img": img.copy(),
                                  "imgname": imgname})
        flush_at = self._watch.get("last_analysis") + self.analyze_seconds
        if len(self._pending) >= self.max_batch_screens or \
                time.time() >= flush_at:
            self._watch["last_analysis"] = time.time()
            added = self._flush_batch(brain)
            if added:
                print(f"[watch] stored {added} new learning line(s) "
                      f"({self._watch['frames']} captures so far)")

    def watch_stop(self):
        if not self._watch["on"]:
            return None
        self._watch["stop_req"] = True
        self._watch["on"] = False
        self._flush_batch(_WATCH_BRAIN)
        try:
            if self._watch.get("thread"):
                self._watch["thread"].join(timeout=4)
        except Exception:
            pass
        obs = []
        try:
            obs = self.conn.execute(
                "SELECT app, window, detail, img FROM observations "
                "ORDER BY id DESC LIMIT 80").fetchall()
        except Exception:
            pass
        lines, seen = [], set()
        for app, window, detail, img in obs[:80]:
            key = (app or "") + "|" + (window or "") + "|" + (detail or "")
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"{app} — {window}" + (f": {detail[:200]}" if detail else ""))
        started = self._watch.get("started")
        sdir = self._watch.get("dir", "")
        session_lines = (self._watch.get("summary_lines") or [])[:40]
        own_lessons = []
        try:
            own_lessons = self.lessons(limit=8)
        except Exception:
            pass
        try:
            from core.knowledge import get_knowledge
            body_parts = []
            if session_lines:
                body_parts.append("Steps observed:\n- " +
                                  "\n- ".join(session_lines))
            if own_lessons:
                body_parts.append("What DUDE itself learned from its own work "
                                  "(wins + mistakes to avoid):\n- " +
                                  "\n- ".join(own_lessons))
            distinct = "\n".join(f"- {p}" for p in lines[:20])
            if distinct:
                body_parts.append("Places/apps used:\n" + distinct)
            if sdir:
                body_parts.append(f"Raw captures (every changed frame) saved to:\n`{sdir}`")
            if body_parts:
                get_knowledge().save_note(
                    f"What you taught me {started.strftime('%Y-%m-%d %H:%M') if started else 'this session'}",
                    "\n\n".join(body_parts),
                    filename=f"watch-session-{(started.strftime('%Y%m%d_%H%M') if started else 'now')}.md")
        except Exception:
            pass
        summary = {
            "n": len(lines) or len(session_lines),
            "from": started.strftime("%Y-%m-%d %H:%M") if started else "",
            "places": lines[:30],
            "frames": self._watch.get("frames", 0),
            "archive": sdir,
            "stored": session_lines,
        }
        self._pending = []
        print(f"[watch] session over: {summary['frames']} captures, "
              f"{len(session_lines)} insights stored")
        return summary

    def stop(self):
        self._watch["on"] = False
        self._watch["stop_req"] = True