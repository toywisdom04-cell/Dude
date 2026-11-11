import datetime
import logging
import threading
import time

from core.config import get_config

log = logging.getLogger("dude")


class NightlyLearner:
    def __init__(self, memory, brain, voice=None):
        self.memory = memory
        self.brain = brain
        self.voice = voice
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="du-learn")
        self._thread.start()

    def _seconds_until(self, hour=3, minute=5):
        now = datetime.datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += datetime.timedelta(days=1)
        return (target - now).total_seconds()

    def _loop(self):
        while not self._stop.is_set():
            wait = self._seconds_until()
            if self._stop.wait(timeout=min(wait, 3600)):
                return
            if abs((datetime.datetime.now() -
                    datetime.datetime.now().replace(hour=3, minute=5)).total_seconds()) > 600:
                continue
            try:
                self.run_pass()
            except Exception as e:
                print(f"[learning] pass failed: {e}")

    def run_pass(self):
        yest = datetime.date.today() - datetime.timedelta(days=1)
        rows = self.memory.day_summary(1)
        last = self.memory.last_work_before(
            datetime.datetime.combine(datetime.date.today(), datetime.time.min))
        msgs = self.memory.search_messages("", limit=0) or []
        del msgs
        if not rows and not last:
            return "nothing to learn"
        app_txt = ", ".join(f"{a} ({s // 60} min)" for a, s in rows[:6]) or "no activity"
        last_txt = f'last file/context: "{last[1][:80]}" in {last[0]}' if last else ""
        prompt = (
            f"The user's PC activity yesterday ({yest}): {app_txt}. {last_txt}. "
            "Write 1-3 short durable observations about the user's work patterns, "
            "projects or focus areas. Plain sentences only, no preamble."
        )
        try:
            insights = self.brain.think(prompt)
        except Exception:
            insights = ""
        stamp = yest.strftime("%Y-%m-%d")
        fact = f"[daily {stamp}] Activity: {app_txt}. {last_txt}"
        self.memory.remember_fact(fact, category="daily_summary")
        if insights.strip():
            for line in [l.strip() for l in insights.splitlines() if l.strip()][:3]:
                self.memory.remember_fact(f"[insight {stamp}] {line[:200]}",
                                          category="insight")
            try:
                from core.knowledge import get_knowledge
                kb = get_knowledge()
                kb.save_note(
                    f"Daily insight digest {stamp}",
                    "Activity: " + app_txt + ("\n" + last_txt if last_txt else "") +
                    "\n\nInsights:\n- " + "\n- ".join(
                        [l.strip().lstrip("-*0123456789. ")[:200]
                         for l in insights.splitlines() if l.strip()][:3]),
                    filename=f"daily-digest-{stamp}.md",
                )
            except Exception:
                pass
        self.memory.set_state("last_learning_pass", datetime.datetime.now().isoformat())
        return "learned"

    def stop(self):
        self._stop.set()


class DayLearner:
    """Daytime self-learning engine. While the user works, periodically reads
    the live session (activity, learned facts, recent user messages) and quietly
    distils durable insights into memory + the knowledge base, so DUDE learns
    during the day, not only at night. Never speaks on its own."""

    def __init__(self, memory, brain, enabled=None, interval=None, max_per_day=None):
        self.memory = memory
        self.brain = brain
        cfg = get_config()
        self.enabled = bool(enabled if enabled is not None
                            else cfg.get("live_learner", "enabled", default=True))
        self.interval = int(interval or cfg.get("live_learner", "interval_minutes",
                                                default=35)) * 60
        self.max_per_day = int(max_per_day or cfg.get("live_learner", "max_per_day",
                                                      default=8))
        self._busy = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        if self.enabled:
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="du-daylearn")
            self._thread.start()

    def _count_today(self):
        day = datetime.date.today().isoformat()
        if self.memory.get_state("live_learner_day", "") != day:
            self.memory.set_state("live_learner_day", day)
            self.memory.set_state("live_learner_count", "0")
            return 0
        try:
            return int(self.memory.get_state("live_learner_count", "0") or 0)
        except (TypeError, ValueError):
            return 0

    def _loop(self):
        first = True
        while not self._stop.wait(120 if first else self.interval):
            first = False
            if not self.enabled or self._busy.is_set():
                continue
            try:
                from core.brain import live_turn_active
                if live_turn_active():
                    continue
            except Exception:
                pass
            if self._count_today() >= self.max_per_day:
                continue
            self._busy.set()
            try:
                self.run_pass()
            except Exception:
                log.exception("daylearner: pass failed")
            finally:
                self._busy.clear()

    def run_pass(self):
        rows = self.memory.today_summary() or []
        activity = ", ".join(f"{app} ({sec // 60} min)" for app, sec in rows[:8]) \
            or "minimal activity yet"
        cats = ["workflow_candidate", "screen_learning", "work", "preferences",
                "personal", "workflow"]
        facts = []
        for c in cats:
            for r in self.memory.facts_by_category(c, limit=4):
                facts.append(r["fact"])
        facts = facts[:14] or ["(nothing learned yet)"]
        msgs = [m for m in self.memory.recent_messages(limit=60)
                if m["role"] == "user"]
        recent = " | ".join(m["content"][:150] for m in msgs[-8:]) or "(none)"
        prompt = (
            "You are DUDE's daytime learning engine. The user works at this "
            "computer and you are quietly building a lasting model of their work.\n\n"
            f"TODAY'S ACTIVITY: {activity}\n\n"
            "RECENT THINGS DUDE LEARNED OR SAW:\n" + "\n".join(facts) + "\n\n"
            "RECENT THINGS THE USER SAID:\n" + recent + "\n\n"
            "Reply with 1-3 short, durable observations that are true and useful "
            "for future work: patterns, projects, methods or preferences. Plain "
            "sentences only, no preamble, no bullet labels."
        )
        try:
            raw = self.brain.think(prompt)
        except Exception as e:
            log.warning("daylearner: think failed: %s: %s", type(e).__name__, e)
            return "think failed"
        if not raw or len(raw.strip()) < 15:
            return "nothing to digest"
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [l.strip().lstrip("-*0123456789. ")[:220]
                 for l in raw.splitlines() if l.strip()][:3]
        lines = [l for l in lines if len(l) >= 10]
        for line in lines:
            self.memory.remember_fact(f"[insight {stamp}] {line}",
                                      category="insight")
        try:
            from core.knowledge import get_knowledge
            get_knowledge().save_note(
                f"Learning digest {stamp}",
                f"Session evidence: {activity}\n\nObservations:\n- " +
                "\n- ".join(lines),
                filename=f"learning-digest-{stamp.replace(':', '').replace(' ', '-')}.md",
            )
        except Exception:
            pass
        self.memory.set_state("live_learner_count",
                              str(int(self._count_today()) + 1))
        log.info("daylearner: digested %d observations", len(lines))
        return "ok"

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)


def run_catchup_pass(memory):
    """If we missed yesterday's pass (PC was off at night), run it on boot."""
    import json

    raw = memory.get_state("last_learning_pass", "")
    today = datetime.date.today().isoformat()
    try:
        last_day = json.loads(raw or '""')
    except (ValueError, TypeError):
        last_day = str(raw or "")
    if isinstance(last_day, dict):
        last_day = last_day.get("day", "")
    if last_day == today:
        return False
    memory.set_state("last_learning_pass", today)
    return True
