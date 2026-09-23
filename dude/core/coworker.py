"""DUDE coworker Goal loop: the persistent cognitive/job loop.

Thin orchestration over EXISTING pieces only — no new engines:

  UNDERSTAND  reflex intent classification (+ Brain when supplied)
  OBSERVE     observer.current_screen() when supplied
  REMEMBER    job_memory cards + Memory recall
  THINK/PLAN  known procedure / adapted procedure / explore plan
  EXECUTE     only through a caller-supplied execute_fn (default: dry run)
  VERIFY      only through a caller-supplied verify_fn (default: unverified)
  RECOVER     recovery engine when supplied, else diagnosed failure
  RESULT      structured outcome + short spoken summary
  LEARN       record_success() on verified outcomes only

Production routing is NOT swapped to this loop yet: it runs dry-run safe
by default. Wiring TaskEngine/ActionExecutor/Verification in comes after a
proven cutover, not before.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core import job_memory as jobs


def _safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception:
        return None


def run_goal(goal: str, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run one user goal through the canonical loop. Never raises."""
    ctx = ctx or {}
    memory = ctx.get("memory")
    out: Dict[str, Any] = {"goal": goal, "stages": {}}
    try:
        # ---- UNDERSTAND: actual goal, purpose, constraints ----
        from core import reflex as _reflex
        low = (goal or "").strip()
        action = _reflex.classify_action(low)
        out["stages"]["understand"] = {
            "goal": low,
            "is_action": bool(action),
            "verb": (action or {}).get("verb", ""),
        }

        # ---- CONTEXT: what is the user doing / what is on screen ----
        screen = None
        observer = ctx.get("observer")
        if observer is not None:
            snap = _safe(observer.current_screen) or {}
            if isinstance(snap, dict):
                screen = {"app": snap.get("app", "?"),
                          "title": str(snap.get("title", ""))[:100]}
        recent = []
        if memory is not None:
            for m in (_safe(memory.recent_messages, limit=6) or []):
                if isinstance(m, dict) and m.get("role") == "user":
                    recent.append(str(m.get("content", ""))[:120])
        out["stages"]["context"] = {"screen": screen,
                                    "recent_user": recent[-3:]}

        # ---- KNOWLEDGE: known / partial / new workflow ----
        cards: List[Dict[str, Any]] = []
        knowledge = "none"
        if memory is not None:
            cards = jobs.find_jobs(memory, low, limit=3)
            if cards:
                best = cards[0]
                proc = best.get("procedure") or []
                knowledge = "known" if len(proc) >= 3 else "partial"
        out["stages"]["knowledge"] = {
            "status": knowledge,
            "jobs": [c.get("name", "") for c in cards],
        }

        # ---- PLAN ----
        plan: List[str] = []
        if knowledge in ("known", "partial") and cards:
            plan = list((cards[0].get("procedure") or [])[:12])
        elif ctx.get("planner") is not None:
            plan = _safe(ctx["planner"], low, out["stages"]) or []
        out["stages"]["plan"] = {"steps": plan, "source": knowledge}

        # ---- EXECUTE (only via supplied executor; default dry run) ----
        exec_fn: Optional[Callable] = ctx.get("execute_fn")
        executed, exec_note = False, "dry-run (no executor wired)"
        if exec_fn is not None and plan:
            res = _safe(exec_fn, plan, out["stages"])
            executed = bool(res and res.get("ok"))
            exec_note = str((res or {}).get("note", ""))[:200]
        out["stages"]["execute"] = {"executed": executed, "note": exec_note}

        # ---- VERIFY ----
        verified = False
        verify_fn: Optional[Callable] = ctx.get("verify_fn")
        if executed and verify_fn is not None:
            verified = bool(_safe(verify_fn, plan, out["stages"]))
        out["stages"]["verify"] = {"verified": verified}

        # ---- RECOVER ----
        recovered = False
        if executed and not verified:
            rec = ctx.get("recovery")
            if rec is not None:
                recovered = bool(_safe(rec, plan, out["stages"]))
        out["stages"]["recover"] = {"recovered": recovered}

        # ---- RESULT ----
        if verified:
            summary = "Done."
        elif not plan:
            summary = "I don't know that workflow yet, sir."
        elif exec_fn is None:
            summary = (f"Planned {len(plan)} steps from "
                       f"{out['stages']['plan']['source']} knowledge. "
                       f"Execution needs a wired executor.")
        else:
            summary = "That didn't verify, sir."
        out["summary"] = summary

        # ---- LEARN (verified outcomes only) ----
        if verified and memory is not None and cards:
            jobs.record_success(memory, cards[0]["name"])
            out["stages"]["learn"] = {"recorded": cards[0]["name"]}
        else:
            out["stages"]["learn"] = {"recorded": ""}
        out["ok"] = True
    except Exception as e:  # never break a caller
        out["ok"] = False
        out["error"] = f"{type(e).__name__}: {e}"
        out["summary"] = "That hit a problem on my side."
    return out


# ---------------- active goal state (production kernel) ----------------

import re as _re

_STOP_RE = _re.compile(r"\b(stop|stop that|cancel|never mind|forget it)\b",
                       _re.IGNORECASE)
_CONTINUE_RE = _re.compile(r"\b(continue|go on|resume|keep going|proceed)\b",
                           _re.IGNORECASE)
_CORRECT_RE = _re.compile(
    r"\b(actually|instead|rather|use (the other|this)|no,)\b", _re.IGNORECASE)
_EXPLAIN_RE = _re.compile(
    r"\b(why did (that|it|you)|why (are|aren't|did|didn't|have|haven't|won't|"
    r"would|could|can't|can) you|you (didn't|haven't|won't|failed)|"
    r"what happened|what went wrong|how did it fail)\b",
    _re.IGNORECASE)

# Irreversible verbs: goals containing them carry a confirm constraint.
# General safety rule, not a command list.
_RISKY_RE = _re.compile(
    r"\b(delete|remove permanently|format|shutdown|restart|send|pay|purchase|"
    r"buy)\b", _re.IGNORECASE)


def _app_hints(goal):
    """General application mentions: 'open <Name>', 'in <Name>', 'with
    <Name>'. No app-name list: the name is whatever the user said."""
    found = []
    for m in _re.finditer(
            r"\bopen\s+(?:a\s+|the\s+)?([A-Za-z][\w. ]{1,24}?)(?:\s+and\b|\s*,|\s*$)|"
            r"\bin\s+(?:a\s+|the\s+)?([A-Za-z][\w. ]{1,24}?)(?:\s*,|\s*$)|"
            r"\bwith\s+(?:a\s+|the\s+)?([A-Za-z][\w. ]{1,24}?)(?:\s*,|\s*$)",
            goal or "", _re.IGNORECASE):
        for g in m.groups():
            if g and g.strip() not in found:
                found.append(g.strip())
    return found[:4]


def new_goal_state(goal: str, job=None, context=None) -> Dict[str, Any]:
    """First-class state for one user goal (§2). Plain dict, no schema.
    The complete original goal is always preserved untouched."""
    context = context or {}
    job = job or {}
    apps = list(job.get("applications", []) or [])
    for a in _app_hints(goal):
        if a not in apps:
            apps.append(a)
    data = list(job.get("data", []) or [])
    proc = list(job.get("procedure", []) or [])
    missing = "" if proc else \
        "no verified procedure: explore environment, discover capabilities"
    return {
        "goal": goal, "purpose": goal,
        "constraints": ("confirm before irreversible step"
                        if _RISKY_RE.search(goal or "") else ""),
        "context": dict(context), "app": apps[0] if apps else "",
        "apps": apps, "data": data,
        "job": job.get("name", ""),
        "procedure": proc, "missing": missing,
        "verify_target": ("intended outcome verified: " +
                          (job.get("verification", "") or
                           "result present and correct")),
        "step": 0, "plan": [],
        "exec_result": "", "verify_ok": None, "failure": "",
        "recovery": [], "retries": 0, "learned": "",
        "status": "active",
    }


def match_continuation(text: str, active) -> Optional[str]:
    """Classify follow-up against the ACTIVE goal. Returns stop/continue/
    correct/explain or None. Pure patterns, no model call."""
    if not active or active.get("status") not in ("active", "paused",
                                                  "failed"):
        return None
    low = (text or "").strip().lower()
    if not low or len(low) > 120:
        return None
    if _STOP_RE.search(low):
        return "stop"
    if _CONTINUE_RE.search(low):
        return "continue"
    if _EXPLAIN_RE.search(low):
        return "explain"
    if _CORRECT_RE.search(low):
        return "correct"
    return None


def verified_ok(task_state) -> Optional[bool]:
    """Real verification judgment (§7): True only when the engine verified
    the intended outcome. None when nothing ran. Never True on cancel."""
    try:
        if task_state is None:
            return None
        if bool(getattr(task_state, "cancelled", False)):
            return False
        if getattr(task_state, "failure_reason", None):
            return False
        vr = getattr(task_state, "verification_result", None)
        if vr is not None:
            return bool(getattr(vr, "success", False))
        return None  # ran, but nothing verified: UNVERIFIED, not success
    except Exception:
        return None


def _user_safe_failure(reason):
    """Router/engine jargon must never reach TTS. Map known technical
    failures to honest user-safe lines; keep the original in state/logs."""
    r = (reason or "").strip()
    if not r:
        return ""
    low = r.lower()
    if ("no solver" in low or "no solution" in low or "unsupported" in low
            or "cannot be planned" in low or "no plan" in low):
        return "I couldn't figure out how to do that part."
    if "verif" in low and ("fail" in low or "mismatch" in low):
        return "I couldn't verify the result."
    if "cancel" in low:
        return "Stopped."
    return r[:160]


def summarize_goal(state: Dict[str, Any]) -> str:
    """One concise spoken line for a goal outcome. No internals."""
    v = state.get("verify_ok")
    goal = (state.get("goal") or "")[:80]
    if state.get("status") == "cancelled":
        return "Stopped."
    if v is True:
        return f"Done. {goal}" if goal else "Done."
    if v is False:
        # Speech carries one short line only; the failure detail stays in
        # state/logs (user's standing order: success news, never problem dumps).
        return "That didn't verify, sir."
    return "Working on it."
