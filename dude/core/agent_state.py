"""DUDE authoritative agent state (single facade).

One canonical runtime truth for: current turn, full goal, active task,
step, progress, application/window/focus, screen facts, OCR facts, visual
evidence metadata, verification truth, recovery state, corrections, last
result, confidence, provenance, continuation context.

Subsystems keep their internal objects where necessary, but AgentState is
the synchronization point: nobody guesses which task state is real.

Thread-safe. Dependency-free (stdlib only) so any layer can import it.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional


class AgentState:
    def __init__(self):
        self._lock = threading.Lock()
        self._turn_id = 0
        self._turn_text = ""
        self._goal = ""
        self._purpose = ""
        self._task = ""
        self._step = ""
        self._step_index = 0
        self._step_total = 0
        self._progress = ""
        self._app = ""
        self._window = ""
        self._focus = ""
        self._screen = ""
        self._ocr = ""
        self._ocr_at = 0.0
        self._visual = ""
        self._verified = None
        self._verify_note = ""
        self._recovery = ""
        self._retries = 0
        self._correction = ""
        self._result = ""
        self._confidence = 0.0
        self._provenance = ""
        self._updated = time.time()

    # ---------------- turn / goal ----------------

    def set_goal(self, goal: str, purpose: str = "") -> None:
        with self._lock:
            self._turn_id += 1
            self._turn_text = goal or ""
            self._goal = goal or ""
            self._purpose = purpose or ""
            self._updated = time.time()

    def get_goal(self) -> str:
        with self._lock:
            return self._goal

    # ---------------- task / step ----------------

    def update_task(self, task: str = "", progress: str = "") -> None:
        with self._lock:
            if task:
                self._task = task
            if progress:
                self._progress = progress
            self._updated = time.time()

    def set_step(self, desc: str, index: int = 0, total: int = 0) -> None:
        with self._lock:
            self._step = desc or ""
            self._step_index = index
            self._step_total = total
            self._updated = time.time()

    # ---------------- screen / perception ----------------

    def set_screen_context(self, app: str = "", window: str = "",
                           focus: str = "", screen: str = "",
                           ocr: str = "", visual: str = "") -> None:
        with self._lock:
            if app:
                self._app = app
            if window:
                self._window = window
            if focus:
                self._focus = focus
            if screen:
                self._screen = screen
            if ocr:
                self._ocr = ocr
                self._ocr_at = time.time()
            if visual:
                self._visual = visual
            self._updated = time.time()

    # ---------------- verification / recovery ----------------

    def set_verification(self, ok: Optional[bool], note: str = "") -> None:
        with self._lock:
            self._verified = ok
            self._verify_note = note or ""
            self._updated = time.time()

    def set_recovery(self, note: str, retries: int = 0) -> None:
        with self._lock:
            self._recovery = note or ""
            self._retries = retries
            self._updated = time.time()

    # ---------------- correction / result ----------------

    def set_correction(self, text: str) -> None:
        with self._lock:
            self._correction = text or ""
            self._updated = time.time()

    def set_result(self, text: str, confidence: float = 0.0,
                   provenance: str = "") -> None:
        with self._lock:
            self._result = text or ""
            self._confidence = confidence
            self._provenance = provenance or ""
            self._updated = time.time()

    def clear_active_goal(self) -> None:
        with self._lock:
            self._goal = ""
            self._purpose = ""
            self._task = ""
            self._step = ""
            self._step_index = 0
            self._step_total = 0
            self._progress = ""
            self._verified = None
            self._verify_note = ""
            self._recovery = ""
            self._retries = 0
            self._correction = ""
            self._updated = time.time()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "turn_id": self._turn_id,
                "turn_text": self._turn_text,
                "goal": self._goal,
                "purpose": self._purpose,
                "task": self._task,
                "step": self._step,
                "step_index": self._step_index,
                "step_total": self._step_total,
                "progress": self._progress,
                "app": self._app,
                "window": self._window,
                "focus": self._focus,
                "screen": self._screen,
                "ocr": self._ocr,
                "ocr_at": self._ocr_at,
                "visual": self._visual,
                "verified": self._verified,
                "verify_note": self._verify_note,
                "recovery": self._recovery,
                "retries": self._retries,
                "correction": self._correction,
                "result": self._result,
                "confidence": self._confidence,
                "provenance": self._provenance,
                "updated": self._updated,
            }
