import datetime
import json
import logging
import re
import threading

from core.config import get_config

log = logging.getLogger("dude")

# Tools the Thinker may run itself when it decides an improvement should be
# implemented, not just explained. Everything else stays a TEACH/SUGGEST.
ACT_TOOLS = {"add_routine", "todo_add", "run_powershell", "remember_about_user"}
# Danger patterns never allowed in autonomous commands.
_BLOCKED_CMD = re.compile(
    r"\b(remove-item|del\s+/s|rmdir\s+/s|format\s+\w:?|shutdown|restart-computer|"
    r"stop-process|taskkill|net\s+user|reg\s+(delete|add)|clear-content|"
    r"Set-ItemProperty.*(delete|remove)|Remove-ChildItem)\b", re.I)

_THINK_EXTRA = (
    "You are DUDE's internal strategic thinker. You are NOT talking to the user "
    "directly right now; you are reasoning about their work to make their life "
    "easier and more profitable. Only use evidence you are given. Be concrete "
    "and specific, never generic advice."
)

_PROMPT = """You study this user's own machine. Evidence about their work today and the
durable knowledge you have already learned about them and their work:

TODAY'S ACTIVITY:
{activity}

RECENT KNOWLEDGE / FACTS YOU LEARNED:
{facts}

RECENT THINGS THE USER SAID:
{recent}

YOUR EXISTING INSIGHTS (do not repeat these):
{insights}

Think like a brilliant, trusted advisor who lives inside their PC. Find the ONE
most valuable, concrete improvement that today's evidence supports. Great candidates:
- a repeated manual sequence you saw that a routine/hotkey/script would remove,
- an app they keep opening and switching between that could be combined or
  simplified,
- a mistake you saw them make twice (that we can prevent),
- a time-wasting step in a project you have learned about,
- something that clearly earns or saves them money if the evidence supports it.

Reply with EXACTLY this JSON (no preamble, no markdown):
{{"topic": "one short label",
 "insight": "2-3 plain sentences: the problem/waste you saw and the exact better way",
 "action": "TEACH or ACT",
 "act_payload": {{"tool": "add_routine or todo_add or run_powershell or none",
                  "text": "payload/command for that tool; empty when action is TEACH"}}}}

Rules:
- ACTION TEACH: a lesson worth telling the user now ("do X this way because...").
- ACTION ACT: you are allowed to implement it yourself right now. Only for
  SAFE, reversible improvements: schedule a routine, add a to-do, or (only via
  run_powershell) write a small workflow script under E:\\Dude\\dude\\workflows.
  NEVER delete anything, NEVER pay or send anything, NEVER change system
  settings. If automation is risky, choose TEACH instead."""


class Thinker:
    """Autonomous strategic-thinking engine: regularly reviews what DUDE has
    seen and learned, then (a) TEACHES the user a better way, and (b) implements
    safe improvements itself — turning knowledge into action instead of storage."""

    def __init__(self, memory, brain, voice=None, report=None, enabled=None):
        cfg = get_config()
        if enabled is None:
            enabled = bool(cfg.get("thinker", "enabled", default=True))
        self.enabled = bool(enabled)
        self.interval = int(cfg.get("thinker", "interval_minutes", default=40)) * 60
        self.max_per_day = int(cfg.get("thinker", "max_per_day", default=6))
        self.memory = memory
        self.brain = brain
        self.voice = voice
        self.report = report
        self._busy = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        if self.enabled:
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="du-thinker")
            self._thread.start()

    # ---------------- timer ----------------
    def _loop(self):
        waited = 0
        while not self._stop.wait(min(self.interval, 60 if not waited else self.interval)):
            if self._stop.is_set():
                return
            if self.enabled:
                try:
                    res = self.run_pass()
                    log.info("thinker: pass -> %s", str(res)[:200])
                except Exception:
                    log.exception("thinker: pass failed")
            waited += 1

    def run_pass(self, present=True):
        if self._busy.is_set():
            return "already running"
        self._busy.set()
        try:
            return self._pass(present)
        finally:
            self._busy.clear()

    # ---------------- the reasoning pass ----------------
    def _gather(self):
        import datetime as _dt

        rows = self.memory.today_summary() or []
        activity = ", ".join(f"{app} ({sec // 60} min)" for app, sec in rows[:8]) or \
            "minimal activity yet today"
        cats = ["screen_learning", "workflow_candidate", "personal",
                "preferences", "insight", "knowledge"]
        facts = []
        for c in cats:
            for r in self.memory.facts_by_category(c, limit=6):
                facts.append(r["fact"])
        facts = facts[:18] or ["(nothing learned yet)"]
        msgs = [m for m in self.memory.recent_messages(limit=50) if m["role"] == "user"]
        recent = " | ".join(m["content"][:160] for m in msgs[-6:]) or "(none)"
        raw = self.memory.get_state("thinker_last", "[]") or "[]"
        try:
            last = json.loads(raw)
        except (ValueError, TypeError):
            last = []
        insights = "\n".join(" - " + str(x)[:160] for x in last[-5:]) or "(none)"
        return activity, "\n".join(facts), recent, insights, last

    def _speak(self, text):
        try:
            if self.report:
                self.report(text)
                return
            if self.voice is not None:
                self.voice.say(str(text)[:280])
        except Exception:
            log.warning("thinker: could not speak", exc_info=True)

    def _act(self, payload):
        from core.tools import execute_tool
        tool = (payload or {}).get("tool") or "none"
        text = ((payload or {}).get("text") or "").strip()
        if tool not in ACT_TOOLS or not text:
            return "no action"
        if tool == "run_powershell":
            if len(text) > 600:
                return "declined: command too long"
            if _BLOCKED_CMD.search(text):
                return "declined: command not allowed autonomously"
            if "E:\\Dude\\dude\\workflows" not in text.replace("/", "\\"):
                return "declined: may only write under the DUDE workflows folder"
        try:
            result = execute_tool(tool, json.dumps(
                {"command": text} if tool == "run_powershell" else
                ({"text": text} if tool == "add_routine" else
                 ({"fact": text} if tool == "remember_about_user" else {"text": text}))),
                self.memory, lambda _q: True)
        except Exception as e:
            return f"failed: {type(e).__name__}: {e}"
        try:
            self.memory.audit("thinker_act", f"{tool}: {text[:120]}")
        except Exception:
            pass
        return result

    def _pass(self, present):
        activity, facts, recent, insights, last = self._gather()
        prompt = _PROMPT.format(activity=activity, facts=facts,
                                recent=recent, insights=insights)
        try:
            raw = self.brain.think(prompt, system_extra=_THINK_EXTRA)
        except Exception as e:
            return f"think failed: {type(e).__name__}: {e}"
        if not raw or "{" not in raw:
            return "no insight"
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            plan = json.loads(raw[start:end])
        except (ValueError, IndexError):
            return "unparsable insight"

        topic = str(plan.get("topic") or "improvement")[:80]
        insight = str(plan.get("insight") or "")[:500]
        action = str(plan.get("action") or "TEACH").upper()
        if not insight or len(insight) < 25:
            return "empty insight"
        if any(topic[:50].lower() in str(x).lower() for x in last[-5:]):
            return "duplicate insight"
        if action.startswith("ACT"):
            act = self._act(plan.get("act_payload") or {})
        else:
            act = ""

        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        self.memory.remember_fact(
            f"[insight {stamp}] {insight}" + (f" | action: {act[:120]}" if act else ""),
            category="insight")
        last.append(f"{stamp} {topic}: {insight[:120]}")
        self.memory.set_state("thinker_last", json.dumps(last[-8:]))
        try:
            from core.knowledge import get_knowledge
            get_knowledge().save_note(
                f"Advice {stamp} — {topic}",
                insight + (f"\n\nImplemented: {act}" if act else ""),
                filename=f"advice-{stamp.replace(':', '').replace(' ', '-')}.md")
        except Exception:
            pass
        if present:
            self._speak(insight + (f" I went ahead and set that up." if act else ""))

        log.info("thinker: %s (%s) -> %s", topic, action, (act or "voiced"))
        shown = insight + (f" Also done: {act}" if act else "")
        return shown

    def set_enabled(self, on):
        self.enabled = bool(on)
        self.memory.set_state("thinker_enabled", "1" if self.enabled else "0")

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)