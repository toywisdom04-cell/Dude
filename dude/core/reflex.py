"""DUDE ReflexRouter: deterministic pre-LLM dispatcher (local-first).

One place for every non-model turn: greetings, acknowledgements, time/date,
calculator, screen/app state reads, and action-intent classification. No
cloud call, no reasoning loop, no history — milliseconds.

Contract: route(text, screen_fn=None, scene_fn=None) returns a dict:
  {"kind": "reply", "reply": "..."}    speak this, no Brain needed
  {"kind": "action", "action": {...}}  hand to TaskEngine/legacy executor
  {"kind": "none"}                     fall through to the Brain

Action intents are CLASSIFIED here but EXECUTED by the existing owners
(TaskEngine for app/GUI work, legacy fast-path for volume) — this module
never duplicates executors.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional

_GREETINGS = {"hey", "hi", "hello", "hey dude", "hi dude", "hello dude",
              "good morning", "good evening", "good afternoon"}
_FAREWELLS = {"bye", "goodbye", "bye dude", "goodbye dude"}
_ACKS = {"ok", "okay", "got it", "good", "great", "nice"}
_THANKS = {"thank you", "thanks", "thanks dude", "thank you dude"}
_WAKE_STRIP_RE = re.compile(
    r"^(hey|hi|hello|dude|computer|assistant)[,.\s]+", re.IGNORECASE)
_VERB_STRIP_RE = re.compile(
    r"^(please\s+|can you\s+|could you\s+|would you\s+|will you\s+|do\s+)",
    re.IGNORECASE)

_ACTION_VERBS_RE = re.compile(
    r"\b(open|launch|start|close|shut|click|press|type|enter|drag|scroll|"
    r"switch|focus|delete|create|move|copy|rename|save|set volume|mute|"
    r"unmute|stop|pause|cancel)\b", re.IGNORECASE)

_NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def _norm(text: str) -> str:
    return (text or "").strip().lower().rstrip(".!?")


def _strip_wake(low: str) -> str:
    prev = None
    while prev != low:
        prev = low
        low = _WAKE_STRIP_RE.sub("", low).strip()
        low = _VERB_STRIP_RE.sub("", low).strip()
    return low


def arithmetic_reply(text: str) -> Optional[str]:
    """Local mental math in milliseconds. Returns reply or None."""
    low = _strip_wake(_norm(text))
    m = re.match(r"^(?:calculate|compute|work out|what is|whats|what's)\s+(.+)$",
                 low)
    expr = m.group(1) if m else low
    expr = expr.replace("divided by", "/").replace("multiplied by", "*")
    expr = expr.replace("multiply by", "*").replace("times", "*")
    expr = expr.replace("plus", "+").replace("add", "+").replace("and", "+")
    expr = expr.replace("minus", "-").replace("subtract", "-")
    expr = expr.replace("divide by", "/").replace("over", "/")
    expr = expr.replace("x", "*").replace("\u00d7", "*").replace("=", "")
    expr = expr.replace("?", "").strip()
    expr = re.sub(r"(\d)\s*([+\-*/])\s*(\d)", r"\1 \2 \3", expr)
    toks = expr.split()
    vals: list = []
    ops: list = []
    for tok in toks:
        if tok in _NUM_WORDS:
            vals.append(float(_NUM_WORDS[tok]))
        elif re.fullmatch(r"\d+(?:\.\d+)?", tok):
            vals.append(float(tok))
        elif tok in ("+", "-", "*", "/") and vals and len(vals) > len(ops):
            ops.append(tok)
        else:
            return None
    if len(vals) != 2 or len(ops) != 1:
        return None
    a, op, b = vals[0], ops[0], vals[1]
    try:
        r = a + b if op == "+" else (a - b if op == "-" else
                                     (a * b if op == "*" else a / b))
    except ZeroDivisionError:
        return "That's a divide by zero, sir."
    return str(int(r)) if float(r).is_integer() else f"{r:.2f}"


def datetime_reply(text: str) -> Optional[str]:
    import datetime
    low = _norm(text)
    if "what time" in low or "time is it" in low:
        return datetime.datetime.now().strftime("It's %I:%M %p.")
    if "what date" in low or "what day is" in low or "today's date" in low:
        return datetime.datetime.now().strftime("It's %A, %B %d.")
    return None


def screen_reply(text: str, screen_fn=None, scene_fn=None,
                 refresh_fn=None) -> Optional[str]:
    """Answer ONLY genuinely deterministic local primitives:
    - time
    - date
    - trivial arithmetic (single operation)
    Anything else falls through to the Brain WITH the live snapshot
    (never a disclaimer without evidence)."""
    low = _norm(text)
    
    # Time
    if low in ("what time", "what time is it", "whats the time", "what's the time",
               "current time", "time now"):
        import datetime
        return datetime.datetime.now().strftime("It's %I:%M %p.")
    
    # Date
    if low in ("what date", "what day", "what's the date", "what's today",
               "today's date", "what day is it", "what is today"):
        import datetime
        return datetime.datetime.now().strftime("It's %A, %B %d.")
    
    # Trivial arithmetic (single operation)
    # Only handles "X plus Y", "X minus Y", "X times Y", "X divided by Y"
    import re
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(plus|minus|times|divided by)\s*(\d+(?:\.\d+)?)$", low)
    if m:
        a, op, b = float(m.group(1)), m.group(2), float(m.group(3))
        if op == "plus":
            r = a + b
        elif op == "minus":
            r = a - b
        elif op == "times":
            r = a * b
        elif op == "divided by":
            if b == 0:
                return "That's a divide by zero."
            r = a / b
        out = str(int(r)) if float(r).is_integer() else f"{r:.2f}"
        return out
    
    return None


def classify_action(text: str) -> Optional[Dict[str, Any]]:
    """Action-intent classification (no execution)."""
    low = _strip_wake(_norm(text))
    if len(text) > 160:
        return None
    if low.startswith(("what ", "who ", "why ", "when ", "where ",
                        "hey", "hi ", "hello", "thanks", "thank you")):
        return None
    m = _ACTION_VERBS_RE.search(low)
    if not m:
        return None
    return {"verb": m.group(1).lower(), "utterance": text}


def route(text: str, screen_fn: Optional[Callable] = None,
          scene_fn: Optional[Callable] = None,
          refresh_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """Single deterministic entry point. No model, no memory, no I/O
    beyond the optional live screen callbacks."""
    low = _norm(text)
    if low in _GREETINGS:
        return {"kind": "reply", "reply": "Hey. I'm here."}
    if low in _FAREWELLS:
        return {"kind": "reply", "reply": "Bye."}
    if low in _ACKS:
        return {"kind": "reply", "reply": "Got it."}
    if low in _THANKS:
        return {"kind": "reply", "reply": "Anytime."}
    dt = datetime_reply(text)
    if dt is not None:
        return {"kind": "reply", "reply": dt}
    calc = arithmetic_reply(text)
    if calc is not None:
        return {"kind": "reply", "reply": calc}
    scr = screen_reply(text, screen_fn=screen_fn, scene_fn=scene_fn,
                       refresh_fn=refresh_fn)
    if scr is not None:
        return {"kind": "reply", "reply": scr}
    # Leading interruption verbs ("Stop. Instead, ...") belong to the
    # existing fast-brain interruption path, not to action executors.
    if re.match(r"^(stop|pause|wait|hold on)\b", _strip_wake(low)):
        return {"kind": "none"}
    act = classify_action(text)
    if act is not None:
        return {"kind": "action", "action": act}
    return {"kind": "none"}
