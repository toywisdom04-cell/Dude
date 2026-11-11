
# -*- coding: utf-8 -*-
"""
Desktop Checklist and Session Intelligence for DUDE

Manages Desktop/DUDE_TODAYS_GOALS.txt as a human-readable projection of DUDE's
authoritative task state and memory. Performs bidirectional sync (user editing
the text file updates memory and task state), startup context restoration, and
shutdown session review.
"""

import datetime
import json
import logging
import os
import re
import threading
import time

from core.config import get_config
from core.task_state import get_task_state

log = logging.getLogger("dude")


class DesktopChecklist:
    def __init__(self, memory, thinker=None, voice=None):
        self.memory = memory
        self.thinker = thinker
        self.voice = voice
        self.cfg = get_config()
        self.desktop_dir = self._get_desktop_path()
        self.file_path = os.path.join(self.desktop_dir, "DUDE_TODAYS_GOALS.txt")
        self._last_mtime = 0.0
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._watcher_thread = None

    def _get_desktop_path(self):
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            desktop = os.path.join(user_profile, "Desktop")
            if os.path.isdir(desktop):
                return desktop
        return os.path.expanduser("~/Desktop")

    # ---------------- Sync & Generation ----------------

    def render_checklist(self):
        """Generate human-readable DUDE_TODAYS_GOALS.txt from Memory & TaskState."""
        now = datetime.datetime.now()
        date_str = now.strftime("%d %B %Y").upper()

        lines = []
        lines.append("================================================================")
        lines.append("                     DUDE - TODAY'S GOALS                       ")
        lines.append("================================================================")
        lines.append(f"DATE: {date_str}\n")

        lines.append("PRIORITY TASKS\n")
        
        task_state = get_task_state()
        todos = self.memory.todo_list() or []
        
        if task_state.has_active_task():
            lines.append(f"[ ] {task_state.original_goal} (ACTIVE GOAL)")
            for st in task_state.completed_subtasks:
                lines.append(f"[x]   +- {st}")
            for st in task_state.subtasks:
                lines.append(f"[ ]   +- {st}")
            lines.append("")

        has_tasks = False
        for t in todos:
            status = "[x]" if t.get("done") else "[ ]"
            lines.append(f"{status} #{t['id']}: {t['text']}")
            has_tasks = True

        if not has_tasks and not task_state.has_active_task():
            lines.append("[ ] No pending tasks. Ask DUDE to start a new task!")
        
        lines.append("\nDUDE SUGGESTIONS\n")
        insights = self.memory.facts_by_category("insight", limit=3)
        if insights:
            for f in insights:
                fact_text = re.sub(r"^\[.*?\]\s*", "", f["fact"])
                lines.append(f"-> {fact_text[:100]}")
        else:
            lines.append("-> Work steadily; DUDE will observe and suggest automation tips.")

        lines.append("\nYESTERDAY / RECENT PROGRESS\n")
        yest_summary = self.memory.day_summary(1)
        if yest_summary:
            for app, sec in yest_summary[:4]:
                lines.append(f"* Worked in {app} ({sec // 60} mins)")
        else:
            lines.append("* System ready for session.")

        lines.append("\n================================================================")
        
        content = "\n".join(lines)
        with self._lock:
            try:
                with open(self.file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                self._last_mtime = os.path.getmtime(self.file_path)
                log.info("checklist: updated %s", self.file_path)
            except Exception as e:
                log.warning("checklist: failed to write: %s", e)
        return content

    def parse_and_sync_file(self):
        """Parse DUDE_TODAYS_GOALS.txt to detect user checkmarks [x] and sync to Memory."""
        if not os.path.exists(self.file_path):
            return

        with self._lock:
            try:
                mtime = os.path.getmtime(self.file_path)
                if mtime <= self._last_mtime:
                    return
                self._last_mtime = mtime
                with open(self.file_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except Exception:
                return

        matches = re.findall(r"\[([xX ])\]\s*#(\d+):", text)
        todos = {t["id"]: t for t in (self.memory.todo_list() or [])}
        
        updated = False
        for mark, tid_str in matches:
            tid = int(tid_str)
            is_checked = mark.lower() == "x"
            if tid in todos:
                cur_done = bool(todos[tid].get("done"))
                if is_checked and not cur_done:
                    self.memory.todo_done(tid)
                    log.info("checklist sync: marked #%d as done", tid)
                    updated = True

        if updated:
            self.render_checklist()

    def start_watcher(self):
        """Start background watcher thread for live file updates."""
        if self._watcher_thread is not None:
            return

        self.render_checklist()

        def _loop():
            while not self._stop_evt.wait(3.0):
                try:
                    self.parse_and_sync_file()
                except Exception:
                    pass

        self._watcher_thread = threading.Thread(target=_loop, daemon=True, name="du-checklist")
        self._watcher_thread.start()

    def stop_watcher(self):
        self._stop_evt.set()

    # ---------------- Startup & Shutdown Intelligence ----------------

    def startup_greeting(self):
        """Generate intelligent morning/startup greeting with session awareness."""
        now = datetime.datetime.now()
        last_end = self.memory.last_session_end()
        last_work = self.memory.last_work_before(now - datetime.timedelta(minutes=10))
        todos = [t for t in (self.memory.todo_list() or []) if not t.get("done")]

        greeting_parts = []
        
        hr = now.hour
        tod = "Good morning" if hr < 12 else ("Good afternoon" if hr < 18 else "Good evening")
        user_title = self.memory.get_state("user_title") or "sir"
        greeting_parts.append(f"{tod} {user_title}.")

        if last_end:
            from core.memory import _age_str
            greeting_parts.append(f"Your last session ended {_age_str(last_end, now)}.")
        
        if last_work:
            greeting_parts.append(f"You were working on {last_work[1][:60]} in {last_work[0]}.")

        if todos:
            greeting_parts.append(f"You have {len(todos)} pending task{'s' if len(todos) != 1 else ''} on today's checklist.")
        else:
            greeting_parts.append("Today's task list is clear.")

        greeting_parts.append("I've updated your desktop goals file and I'm ready to assist.")
        
        full_greeting = " ".join(greeting_parts)
        log.info("startup greeting: %r", full_greeting)
        return full_greeting

    def shutdown_review(self):
        """Perform pre-shutdown review: summarize progress, update context & checklist."""
        log.info("checklist: performing shutdown review...")

        todos = self.memory.todo_list() or []
        done_count = sum(1 for t in todos if t.get("done"))
        pending_count = len(todos) - done_count

        self.memory.mark_session_end()
        self.render_checklist()

        user_title = self.memory.get_state("user_title") or "sir"
        if pending_count == 0 and done_count > 0:
            msg = f"All goals completed for today, {user_title}! I've saved your session and updated your desktop. Have a great rest of your day."
        elif pending_count > 0:
            msg = f"Session saved, {user_title}. You completed {done_count} task{'s' if done_count != 1 else ''}, and {pending_count} remain on your desktop checklist for next time. Going offline."
        else:
            msg = f"Going offline. Have a great day, {user_title}."

        return msg
