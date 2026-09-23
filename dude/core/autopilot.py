import logging
import threading
import traceback

from core.config import get_config

log = logging.getLogger("dude")


_AUTONOMY_EXTRA = (
    "You are DUDE running in AUTONOMOUS EXECUTOR MODE. The user may be away or "
    "busy, so you operate WITHOUT asking for permission and WITHOUT waiting for "
    "any answer. Your job is to move the user's work forward: finish requests "
    "they left, work the task list, verify completed runs or analyses, and "
    "handle anything obvious that needs doing. You choose the single most "
    "useful action right now and call tools to do it (chain several if needed). "
    "Never repeat an action that already succeeded. If everything is already "
    "handled or nothing needs doing, reply with exactly IDLE.\n"
    "You have the whole PC in your hands while working: open/close/focus apps, "
    "drive the cursor (click_fraction 0-1 of the screen), type text, press "
    "hotkeys, run PowerShell, open the browser, read/write files, use the "
    "to-do list. Prefer actions that unprompt users, even when nothing was "
    "asked this exact moment.\n"
    "When a tool, shell command, or backend step errors, DO NOT stop there: "
    "read the error, then try an alternative route yourself (shell instead of "
    "UI, a hotkey, the app's own menus via cursor clicks, open_url, or a "
    "different command), then VERIFY the outcome (active_window_info / "
    "list_running_apps / reading the result file) before moving on. Only give "
    "up and report after you tried real workarounds.\n"
    "Use the CURRENT SCREEN block and analyze_recent_screens to see what is on "
    "screen before clicking; never click blindly. Don't repeat a step that "
    "already succeeded. Keep your report, if any, to one short spoken line."
)


class Autopilot:
    def __init__(self, memory, voice, make_brain, enabled=None, interval=None,
                 report=None):
        cfg = get_config()
        if enabled is None:
            enabled = bool(cfg.get("autopilot", "enabled", default=True))
        self.enabled = bool(enabled)
        self.interval = int(interval or cfg.get("autopilot", "interval_s", default=300))
        self.memory = memory
        self.voice = voice
        self.make_brain = make_brain
        self.report = report
        self._brain = None
        self._busy = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._force_until = 0.0
        if self.enabled:
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="du-autopilot")
            self._thread.start()

    def force_now(self, seconds=600):
        import time

        self._force_until = time.time() + seconds

    def _user_present(self):
        try:
            state = self.memory.get_state("presence_state", "present")
            return state == "present"
        except Exception:
            return True

    def _context_text(self):
        parts = []
        user_msgs = [m for m in self.memory.recent_messages(limit=50)
                     if m["role"] == "user"]
        if user_msgs:
            recent = " | ".join(m["content"][:140] for m in user_msgs[-5:])
            parts.append(f"Last things the user said or asked: {recent}")
        cur = self.memory.current_work()
        if cur:
            parts.append(f"App currently focused: {cur[0]} - \"{cur[1][:90]}\" "
                         f"open since {cur[2]}")
        pending = [t for t in self.memory.todo_list() if not t["done"]]
        if pending:
            parts.append("Open tasks: " + "; ".join(
                f"#{t['id']} {t['text']}" for t in pending[:10]))
        else:
            parts.append("Task list is empty.")
        return "\n".join(parts)

    def _loop(self):
        import time

        while not self._stop.wait(self.interval):
            if not self.enabled:
                continue
            if self._busy.is_set():
                continue
            # Quiet discipline: while the user is present and hasn't asked us
            # to work autonomously this moment, do NOT act or narrate on our
            # own - honour "stop doing that, just observe me".
            present = self._user_present()
            if present and time.time() > self._force_until:
                continue
            self._busy.set()
            try:
                self._tick()
            except Exception:
                traceback.print_exc()
            finally:
                self._busy.clear()

    def _tick(self):
        if self.voice is not None and self.voice.speaking:
            return
        try:
            from core.brain import live_turn_active
            if live_turn_active():
                return
        except Exception:
            pass
        if self._brain is None:
            self._brain = self.make_brain()
        ctx = self._context_text()
        prompt = ("AUTONOMOUS TICK. Current situation:\n" + ctx +
                  "\n\nAct now. When everything is handled or there is nothing "
                  "to do, answer exactly IDLE.")
        result = self._brain.chat(
            prompt,
            on_delta=lambda sentence: None,
            on_tool=self._on_tool,
            system_extra=_AUTONOMY_EXTRA,
        )
        summary = (result or "").strip()
        if summary and summary.upper() != "IDLE" \
                and not summary.upper().startswith("IDLE "):
            self.memory.add_notice(f"Autonomous: {summary[:200]}")
            if self.report:
                try:
                    self.report(summary)
                except Exception:
                    pass
        else:
            log.info("autopilot: idle (nothing to do)")

    def _on_tool(self, name):
        try:
            self.memory.audit("autopilot_tool", name)
        except Exception:
            pass

    def set_enabled(self, on):
        self.enabled = bool(on)
        try:
            self.memory.set_state("autopilot_enabled",
                                  "1" if self.enabled else "0")
        except Exception:
            pass

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)