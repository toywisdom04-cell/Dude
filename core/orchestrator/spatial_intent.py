"""Phase 15: semantic spatial-want classification (local model only).

Answers ONE question about a turn: does the user want something on the
current screen pointed out, and if so WHAT? The judgment is made by a
small local language model reading the transcript plus a one-line
screen summary — never by matching the user's wording against a phrase
list (there is no phrase list in this file, on purpose).

Fail-closed: model unreachable, timeout, or unparseable reply means
"no spatial" (today's behavior). No cloud, no keys, no new dependency
beyond the loopback Ollama DUDE already ships with.
"""
from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_TIMEOUT = 15.0
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"

_PROMPT = (
    "Decide if the user wants something on their computer screen "
    "pointed out. Reply with exactly one line: a short element name, "
    "or FOCUS, or NONE.\n"
    "Examples:\n"
    "\"tell me a joke\" -> NONE\n"
    "\"what time is it\" -> NONE\n"
    "\"show me the save button\" -> Save\n"
    "\"where is the file menu\" -> File\n"
    "\"click close\" -> Close\n"
    "\"show me what you are looking at\" -> FOCUS\n"
    "\"what am I looking at\" -> FOCUS\n"
    "Screen: {screen}\n"
    "\"{transcript}\" ->"
)


def classify_spatial_want(transcript: str, screen_summary: str = "",
                          model: str = DEFAULT_MODEL,
                          timeout: float = DEFAULT_TIMEOUT,
                          url: str = OLLAMA_URL) -> str | None:
    """Return an element expression, 'FOCUS', or None (no spatial).

    None also covers every failure mode: unreachable model, timeout,
    empty reply. Callers treat None as "answer verbally".
    """
    try:
        text = (transcript or "").strip()
        if not text:
            return None
        body = json.dumps({
            "model": model,
            "prompt": _PROMPT.format(
                screen=(screen_summary or "unknown")[:200],
                transcript=text[:300]),
            "stream": False,
            # Qwen3 thinks by default (long 'thinking' trace, empty
            # reply): disable thinking for a one-line judgment.
            "think": False,
            "stop": ["\n"],
            "options": {"num_predict": 30, "temperature": 0.0},
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        reply = str(data.get("response", "") or "").strip().splitlines()
        line = (reply[0].strip() if reply else "").strip("\"' ")
        if not line or line.upper() == "NONE":
            return None
        if line.upper() == "FOCUS":
            return "FOCUS"
        return line[:80] or None
    except Exception as e:
        log.warning(f"spatial classification unavailable: {e}")
        return None
