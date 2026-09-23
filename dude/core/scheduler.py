import datetime
import threading
import time


class Scheduler:
    def __init__(self, memory, voice):
        self.memory = memory
        self.voice = voice
        poll, enabled = 20, True
        try:
            from core.config import get_config

            poll = int(get_config().get("scheduler", "poll_seconds", default=20))
            enabled = get_config().get("scheduler", "enabled", default=True)
        except Exception:
            pass
        self._thread = None
        if not enabled:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="du-sched", args=(poll,))
        self._thread.start()

    def _loop(self, poll):
        while self._running:
            time.sleep(poll)
            try:
                # Incident guard: a scheduled reminder must never stomp a
                # live turn as a third voice. If the user is mid-turn, leave
                # it pending for the next poll instead of preempting audio.
                try:
                    from core.brain import live_turn_active
                    if live_turn_active() or self.voice.speaking:
                        continue
                except Exception:
                    pass
                now = datetime.datetime.now()
                for r in self.memory.pending_reminders(now):
                    self.memory.complete_reminder(r["id"])
                    title = self._user_title()
                    self.voice.say(f"Reminder {title}: {r['text']}", priority=True)
                for routine in self.memory.due_routines(now):
                    self.memory.mark_routine_fired(routine["id"])
                    self.voice.say(f"{routine['text']}", priority=True)
            except Exception as e:
                print(f"[scheduler] {e}")

    def _user_title(self):
        try:
            from core.config import get_config

            return get_config().get("user_title", default="sir")
        except Exception:
            return "sir"

    def stop(self):
        self._running = False
