"""Conversation Manager: turn-taking, barge-in, and natural wait-and-talk.

Design goals (from the spec):
- Continuous listening (streaming mic), never a push-to-talk gate.
- VAD decides sentence start/end; waits silence_wait_seconds after the
  user stops before treating the turn as finished.
- Barge-in: while DUDE speaks the mic stays live; if the user starts
  talking, DUDE pauses, listens, and re-plans.
- The conversation never blocks: perception runs on a worker thread while
  the event loop stays responsive.
"""
import datetime
import threading


class Utterance:
    def __init__(self, text: str, timestamp: datetime.datetime = None,
                 directed_at_dude: bool = True):
        self.text = text
        self.timestamp = timestamp or datetime.datetime.now()
        self.directed_at_dude = directed_at_dude

    def __repr__(self):
        return f"Utterance({self.text!r}, directed={self.directed_at_dude})"


class ConversationManager:
    """State machine over the listening/speaking lifecycle."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    PAUSED = "paused"

    def __init__(self, silence_wait: float = 1.2, max_context_turns: int = 20):
        self.state = self.IDLE
        self.silence_wait = silence_wait
        self.max_context_turns = max_context_turns
        self._history: list[Utterance] = []
        self._lock = threading.Lock()
        self._interrupt_event = threading.Event()
        self._listener = None

    # ---- history / context ----
    def record_user(self, text: str, directed_at_dude: bool = True) -> None:
        with self._lock:
            self._history.append(Utterance(text, directed_at_dude=directed_at_dude))

    def record_dude(self, text: str) -> None:
        with self._lock:
            self._history.append(Utterance(text, directed_at_dude=False))

    def recent_context(self, max_turns: int | None = None) -> list[Utterance]:
        with self._lock:
            n = max_turns or self.max_context_turns
            return self._history[-n:]

    def clear(self) -> None:
        with self._lock:
            self._history.clear()

    # ---- lifecycle ----
    def set_state(self, new_state: str) -> None:
        self.state = new_state

    def listening(self) -> bool:
        return self.state in (self.LISTENING,)

    def is_busy(self) -> bool:
        return self.state in (self.THINKING, self.SPEAKING)

    def on_speech_started(self) -> None:
        """The user started talking. Used for barge-in."""
        self._interrupt_event.set()
        if self.state == self.SPEAKING:
            self.set_state(self.LISTENING)

    def on_speech_ended(self) -> None:
        self._interrupt_event.clear()

    def wait_for_turn(self, source, timeout: float | None = None) -> str | None:
        """Listen until the user finishes a turn.

        source must expose:
            - next_utterance(timeout) -> Utterance or None
            - on_interrupt(callback) / stop() optional
        """
        self.set_state(self.LISTENING)
        try:
            utter = source.next_utterance(timeout=timeout)
            if utter is None:
                return None
            self.record_user(utter.text, directed_at_dude=utter.directed_at_dude)
            return utter.text
        finally:
            self.set_state(self.IDLE)

    def barge_in_requested(self) -> bool:
        return self._interrupt_event.is_set()

