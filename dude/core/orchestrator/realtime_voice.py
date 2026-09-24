"""Phase 10: realtime full-duplex voice interaction controller.
ONE shared controller, no competing conversation engine. It reuses the
existing pieces instead of replacing them:

- VAD: WebRTCVAD from core.ear (continuous, cheap; STT only on speech).
- STT: faster-whisper tiny.en, ONE resident model (production backend);
  the stt_worker.py subprocess protocol stays available for the legacy
  Ear path untouched.
- TTS: local Windows SAPI via pyttsx3 (production backend). edge_tts is
  NEVER used here — offline/local path only.
- Conversation memory: the existing Memory message store (same as
  dude.py handle_text) — one authoritative context.
- Secrets: SecretSanitizer before anything persists.
- Screen: Phase 9 PerceptionService cache first, OCR only on demand.
- Tasks: a deep_handler callback (production: TaskEngine). Voice never
  executes tasks itself and never duplicates TaskEngine.

Audio is EPHEMERAL: utterance buffers live only for the active turn
unless diagnostics explicitly keep them. No permanent recordings.
"""
from __future__ import annotations

import collections
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .perception_service import LatencyMetrics
from .goal_executor import CognitiveFrontDoor, CognitiveMode

log = logging.getLogger(__name__)

try:
    from core.ear import WebRTCVAD, _is_garbage_transcript
except Exception:  # pragma: no cover - import-time fallback
    WebRTCVAD = None  # type: ignore

    def _is_garbage_transcript(text):  # type: ignore
        return not (text or "").strip()


# ---------------------------------------------------------------- states

class VoiceState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    EXECUTING_TASK = "executing_task"


_ALLOWED = {
    VoiceState.IDLE: (VoiceState.LISTENING,),
    VoiceState.LISTENING: (VoiceState.THINKING, VoiceState.IDLE,
                           VoiceState.INTERRUPTED),
    VoiceState.THINKING: (VoiceState.SPEAKING, VoiceState.EXECUTING_TASK,
                          VoiceState.IDLE, VoiceState.INTERRUPTED),
    VoiceState.SPEAKING: (VoiceState.LISTENING, VoiceState.INTERRUPTED,
                          VoiceState.IDLE, VoiceState.EXECUTING_TASK),
    VoiceState.INTERRUPTED: (VoiceState.THINKING, VoiceState.LISTENING,
                             VoiceState.IDLE, VoiceState.EXECUTING_TASK),
    VoiceState.EXECUTING_TASK: (VoiceState.INTERRUPTED, VoiceState.SPEAKING,
                                VoiceState.LISTENING, VoiceState.IDLE),
}


@dataclass
class ConversationTurn:
    turn_id: int = 0
    partial_text: str = ""
    final_text: str = ""
    last_user_utterance: str = ""
    prev_user_utterance: str = ""
    prev_interrupted: bool = False
    stream_spoken: str = ""
    speech_start: float = 0.0
    speech_end: float = 0.0
    current_dude_utterance: str = ""
    utterance_interrupted: bool = False
    active_task_id: str = ""
    interruption_epoch: int = 0
    task_context: Dict[str, Any] = field(default_factory=dict)
    screen_context: str = ""
    speaking_state: str = VoiceState.IDLE.value
    pending_response: str = ""


@dataclass
class DeepResult:
    """What the deep path (TaskEngine) returns for one utterance."""
    speech_reply: str = ""
    task_id: str = ""
    long_running: bool = False


# ---------------------------------------------------------------- TTS

def split_chunks(text: str, max_chars: int = 90) -> List[str]:
    """Short natural chunks; never queue long paragraphs."""
    bits = [b.strip() for b in re.split(r"(?<=[.!?])\s+|\n+", text or "")
            if b.strip()]
    out: List[str] = []
    for b in bits:
        while len(b) > max_chars:
            cut = b.rfind(" ", 0, max_chars)
            cut = cut if cut > 20 else max_chars
            out.append(b[:cut].strip())
            b = b[cut:].strip()
        if b:
            out.append(b)
    return out or ([text.strip()] if (text or "").strip() else [])


# Removed: _FAST_CMD_RE, _ACTION_WORD_RE, greeting_patterns, question_without_action
# All production routing now goes through CognitiveFrontDoor (semantic classification)
# No regex/keyword-based routing in production path.

# High-level action DSL (§5): the model may emit exactly one line
#   ACTION: VERB optional-args
# with VERB in {OPEN_APP, READ_SCREEN, AUDIO_CONTROL, CANCEL_TASK}.
# DUDE validates + executes via existing verified tools; the reply is
# replaced by a short confirmation (never the ACTION line itself).
_DSL_RE = re.compile(
    r"^ACTION:\s*(OPEN_APP|READ_SCREEN|AUDIO_CONTROL|CANCEL_TASK)\b\s*(.*)$",
    re.IGNORECASE)


def _parse_dsl_action(reply):
    for line in (reply or "").splitlines():
        m = _DSL_RE.match(line.strip())
        if m:
            return (m.group(1).upper(), m.group(2).strip()[:80])
    return None


_TAG_RE = re.compile(r"^\[(RELATED|UNRELATED|CORRECTION|CONTINUATION)\]\s*",
                     re.IGNORECASE)

# Stall-word heads: spoken by NOBODY. The streaming path consumes these
# instead of voicing them (the "answering" third-voice incident).
_STALL_HEAD_RE = re.compile(
    r"^(answering|processing|thinking|working|one moment|just a (moment|"
    r"second|sec)|let me (see|check|think|look)|hold on|hang on|give me a "
    r"(moment|second|sec)|i('m| am) (on it|looking|checking))\b",
    re.IGNORECASE)

_NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

# Removed: _fast_arithmetic function
# Local arithmetic now handled by reflex router (truly instant) or CognitiveFrontDoor

def _looks_like_tool_json(s: str) -> bool:
    """True if text is serialized tool machinery, never speakable aloud."""
    t = (s or "").strip()
    if not t:
        return True
    head = t[:300]
    if t.startswith("{") and '"name"' in head:
        return True
    if head.startswith('"name"') or head.startswith('"arguments"'):
        return True
    if "tool_call_id" in head or '"tool_calls"' in head:
        return True
    return False


# Non-English output (CJK scripts) must never reach the user's ears:
# the STT side is English-only, so any of this comes from the chat
# model slipping languages. Detected replies are re-rendered in
# English by the model itself before speaking — never hardcoded.
_CJK_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]")


def _contains_cjk(s: str) -> bool:
    return bool(s and _CJK_RE.search(s))


class TTSBackend:
    def speak(self, text: str, on_chunk_start: Optional[Callable] = None,
              should_stop: Optional[Callable[[], bool]] = None) -> bool:
        raise NotImplementedError

    def stop(self) -> float:
        """Halt speech now. Returns stop latency in ms."""
        raise NotImplementedError

    def is_speaking(self) -> bool:
        raise NotImplementedError

    @property
    def current_utterance(self) -> str:
        return ""

    @property
    def interrupted(self) -> bool:
        return False


class SapiTTSBackend(TTSBackend):
    """Local Windows SAPI speech. ONE engine, own thread, interruptible."""

    def __init__(self, rate: int = 185):
        self._rate = rate
        self._engine = None
        self._lock = threading.Lock()
        self._speak_lock = threading.Lock()
        self._speaking = threading.Event()
        self._stop_ms = 0.0
        self._current = ""
        self._was_interrupted = False
        # Chunks whose runAndWait returns implausibly fast almost certainly
        # produced no audible sound (silent-driver failure, seen live in
        # Phase 10 rounds 2 and 4). Counted so silence is evidence, not
        # mystery.
        self.suspicious_completions = 0

    def _ensure(self):
        # Live finding (Phase 10 round 2): after engine.stop() the SAPI
        # driver goes silent or deadlocks on next runAndWait. An engine
        # that has been stopped is therefore NEVER reused — stop()
        # detaches it and the next chunk builds a fresh one.
        with self._lock:
            if self._engine is None:
                import pyttsx3
                eng = pyttsx3.init("sapi5")
                try:
                    eng.setProperty("rate", self._rate)
                except Exception:
                    pass
                self._engine = eng
            return self._engine

    def _new_engine(self):
        # Live finding (round 4, proven by diag): a SAPI engine speaks
        # exactly ONCE — every subsequent say+runAndWait on the same
        # engine returns in ~0.18s producing silence. Speech therefore
        # builds one fresh engine per speak() call. synth_to_file keeps
        # its own path (file render is unaffected by this driver bug).
        import pyttsx3
        eng = pyttsx3.init("sapi5")
        try:
            eng.setProperty("rate", self._rate)
        except Exception:
            pass
        return eng

    def speak(self, text, on_chunk_start=None, should_stop=None) -> bool:
        chunks = split_chunks(text)
        if not chunks:
            return True
        # Speech is serialized: a new utterance preempts a stale one via
        # stop() (same outcome the old per-chunk should_stop polling gave,
        # but safe with single-pump playback).
        if not self._speak_lock.acquire(blocking=False):
            try:
                self.stop()
            except Exception:
                pass
            self._speak_lock.acquire()
        try:
            return self._speak_pumped(chunks, on_chunk_start, should_stop)
        finally:
            self._speak_lock.release()

    def _speak_pumped(self, chunks, on_chunk_start, should_stop) -> bool:
        # ONE fresh engine per utterance (SAPI engines speak exactly once;
        # reuse goes silent — proven live), queue ALL chunks, pump ONCE
        # (the documented pyttsx3 pattern; per-chunk pump cycling drops
        # audio — proven live). Barge-in aborts mid-pump via stop().
        # Total-time watchdog replaces the per-chunk timing check.
        def aborted() -> bool:
            # External stop() OR superseded epoch: either way the
            # utterance must end quietly — never "retry" a deliberate
            # stop, never count it as suspicious.
            return bool(self._was_interrupted
                        or (should_stop and should_stop()))

        total_chars = sum(len(c) for c in chunks)
        expected_min = total_chars / 15.0 * 0.6  # ~15 chars/s at rate 185
        self._was_interrupted = False
        for attempt in (1, 2):
            if aborted():
                self._was_interrupted = True
                return False
            eng = self._new_engine()
            with self._lock:
                self._engine = eng
            self._speaking.set()
            if on_chunk_start:
                for ch in chunks:
                    try:
                        on_chunk_start(ch)
                    except Exception:
                        pass
            t0 = time.perf_counter()
            try:
                with self._lock:
                    self._current = " ".join(chunks)[:120]
                # ONE queue item: the SAPI driver drains only (part of) the
                # first queued item then returns, so multi-say queueing
                # silently drops the tail (proven live). Join and pump once.
                eng.say(" ".join(chunks))
                eng.runAndWait()
                dt = time.perf_counter() - t0
            except Exception as e:
                log.warning(f"SAPI pump failed: {e}")
                dt = 0.0
            finally:
                with self._lock:
                    self._engine = None
                    self._current = ""
                self._speaking.clear()
            if aborted():
                self._was_interrupted = True
                return False
            if total_chars > 20 and dt < expected_min:
                log.warning(
                    f"SAPI pump suspiciously fast ({dt:.2f}s for "
                    f"{total_chars} chars, try {attempt}): retrying once")
                continue
            return True
        self.suspicious_completions += 1
        log.warning("SAPI pump silent after retry; utterance "
                    "FAILED, not completed")
        return False

    def stop(self) -> float:
        t0 = time.perf_counter()
        with self._lock:
            eng, self._engine = self._engine, None
        try:
            if eng is not None:
                eng.stop()
        except Exception:
            pass
        self._was_interrupted = True
        self._speaking.clear()
        self._stop_ms = (time.perf_counter() - t0) * 1000.0
        return self._stop_ms

    def is_speaking(self) -> bool:
        return self._speaking.is_set()

    @property
    def current_utterance(self) -> str:
        with self._lock:
            return self._current

    @property
    def interrupted(self) -> bool:
        return self._was_interrupted

    def synth_to_file(self, text: str, path: str) -> bool:
        """Render speech to WAV without playback (fixtures/diagnostics)."""
        try:
            eng = self._ensure()
            eng.save_to_file(text, path)
            eng.runAndWait()
            return True
        except Exception as e:
            log.warning(f"SAPI synth_to_file failed: {e}")
            return False


class FakeTTSBackend(TTSBackend):
    """Deterministic test double: per-word timed 'playback', interruptible."""

    def __init__(self, word_ms: float = 60.0):
        self.word_ms = word_ms
        self._speaking = threading.Event()
        self._stop_evt = threading.Event()
        self._current = ""
        self._was_interrupted = False
        self.spoken: List[str] = []
        self.chunk_starts: List[str] = []
        self._stop_ms = 0.0

    def speak(self, text, on_chunk_start=None, should_stop=None) -> bool:
        self._stop_evt.clear()
        self._was_interrupted = False
        self._speaking.set()
        try:
            for ch in split_chunks(text):
                self._current = ch
                if on_chunk_start:
                    try:
                        on_chunk_start(ch)
                    except Exception:
                        pass
                self.chunk_starts.append(ch)
                for _ in ch.split():
                    if self._stop_evt.is_set() or (should_stop
                                                   and should_stop()):
                        self._was_interrupted = True
                        return False
                    time.sleep(self.word_ms / 1000.0)
                self.spoken.append(ch)
            return True
        finally:
            self._current = ""
            self._speaking.clear()

    def stop(self) -> float:
        t0 = time.perf_counter()
        self._stop_evt.set()
        self._was_interrupted = True
        self._speaking.clear()
        self._stop_ms = (time.perf_counter() - t0) * 1000.0
        return self._stop_ms

    def is_speaking(self) -> bool:
        return self._speaking.is_set()

    @property
    def current_utterance(self) -> str:
        return self._current

    @property
    def interrupted(self) -> bool:
        return self._was_interrupted


# ---------------------------------------------------------------- STT

class STTBackend:
    def transcribe(self, pcm_f32, sample_rate: int):
        """Returns (text, latency_ms)."""
        raise NotImplementedError

    def close(self) -> None:
        pass


class TinyWhisperBackend(STTBackend):
    """ONE resident faster-whisper tiny.en model, CPU int8."""

    def __init__(self, model_name: str = "tiny.en"):
        self._model_name = model_name
        self._model = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._model is None:
            import os
            os.environ.setdefault("HF_HOME", r"D:\whisper\hf")
            os.environ.setdefault("HF_HUB_CACHE", r"D:\whisper\hf\hub")
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self._model_name, device="cpu",
                                       compute_type="int8")
        return self._model

    def transcribe(self, pcm_f32, sample_rate: int):
        import numpy as np
        t0 = time.perf_counter()
        model = self._ensure()
        audio = np.asarray(pcm_f32, dtype=np.float32).ravel()
        if sample_rate != 16000 and len(audio) > 0:
            # naive decimation to 16k for common 22050/44100/48000 sources
            import math
            factor = max(1, int(round(sample_rate / 16000)))
            if factor > 1:
                audio = audio[::factor]
        with self._lock:
            segments, _ = model.transcribe(
                audio, language="en", task="transcribe", beam_size=1,
                best_of=1, temperature=0, vad_filter=True,
                condition_on_previous_text=False)
            text = " ".join(s.text for s in segments).strip()
        return text, (time.perf_counter() - t0) * 1000.0

    def partial(self, pcm_f32, sample_rate: int):
        """Streaming partial: transcribe the buffer so far.

        Returns (text, latency_ms, stable=False). Partials are UNSTABLE
        hints for responsiveness only — never routed as intent. Callers
        must wait for the final segmentation before acting.
        """
        text, ms = self.transcribe(pcm_f32, sample_rate)
        return text, ms, False

    def close(self) -> None:
        with self._lock:
            self._model = None


class FakeSTTBackend(STTBackend):
    def __init__(self, transcript: str = "", latency_ms: float = 5.0):
        self.transcript = transcript
        self.latency_ms = latency_ms
        self.calls = 0

    def transcribe(self, pcm_f32, sample_rate: int):
        self.calls += 1
        return self.transcript, self.latency_ms

    def partial(self, pcm_f32, sample_rate: int):
        self.calls += 1
        words = self.transcript.split()
        return " ".join(words[:max(1, len(words) // 2)]), self.latency_ms, False


# ------------------------------------------------- internal narration filter

_INTERNAL_SPEECH = (
    "state ", "ui_click", "invoke", "invoking", "verification",
    "activate tool", "calling tool", "executing action", "grounding",
    "perception", "replanning", "epoch", "traceback", "round-trip",
)


def is_internal_narration(text: str) -> bool:
    low = (text or "").lower()
    return any(tok in low for tok in _INTERNAL_SPEECH)


# ---------------------------------------------------------------- controller

class RealtimeInteractionController:
    """One authoritative realtime voice loop around existing DUDE systems."""

    def __init__(
        self,
        tts: Optional[TTSBackend] = None,
        stt: Optional[STTBackend] = None,
        memory=None,
        screen_provider: Optional[Callable[[], str]] = None,
        ocr_provider: Optional[Callable[[], str]] = None,
        scene_provider: Optional[Callable[[], Any]] = None,
        deep_handler: Optional[Callable[[str, Dict[str, Any]], DeepResult]] = None,
        task_control: Optional[Callable[..., str]] = None,
        permission_gate: Optional[Callable[[str], bool]] = None,
        sample_rate: int = 16000,
        keep_audio_diagnostics: bool = False,
    ):
        self.tts: TTSBackend = tts or SapiTTSBackend()
        self.stt: STTBackend = stt or TinyWhisperBackend()
        self.memory = memory
        self.screen_provider = screen_provider or (lambda: "")
        self.ocr_provider = ocr_provider
        # Shared live scene (Phase 12 slice): semantic truth with
        # freshness. Falls back to screen_provider text when absent.
        self.scene_provider = scene_provider
        # Targeted observation hook: called for screen questions when the
        # cached snapshot is thin. Set by wire_realtime_voice.
        self.refresh_fn = None
        self.deep_handler = deep_handler or self._default_deep
        self.task_control = task_control or (lambda *a, **k: "no task running")
        self.permission_gate = permission_gate or (lambda _risk: True)
        self.sample_rate = sample_rate
        self.keep_audio_diagnostics = keep_audio_diagnostics
        self.metrics = LatencyMetrics()
        self._state = VoiceState.IDLE
        self._lock = threading.Lock()
        self._turn = ConversationTurn()
        # Bounded working context (Part 13): recent verbatim turns plus
        # already-compact session facts (goals, corrections, outcomes).
        # Old verbatim is compacted away; facts survive compaction.
        self._history: "collections.deque" = collections.deque(maxlen=200)
        self._session_facts: "collections.deque" = collections.deque(
            maxlen=100)
        self._turn_id = 0
        self._epoch = 0
        self._stop = threading.Event()
        self._speak_thread: Optional[threading.Thread] = None
        self._task_thread: Optional[threading.Thread] = None
        self._vad = WebRTCVAD(sample_rate=sample_rate) if WebRTCVAD else None
        self._utter_q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=16)
        self._listen_thread: Optional[threading.Thread] = None
        self._last_audio = None
        try:
            from core.orchestrator.procedure_learner import SecretSanitizer
            self._sanitizer = SecretSanitizer()
        except Exception:
            self._sanitizer = None

    # ---------------- state machine ----------------

    @property
    def state(self) -> VoiceState:
        with self._lock:
            return self._state

    def _transition(self, nxt: VoiceState) -> None:
        with self._lock:
            cur = self._state
            if nxt == cur:
                return
            if nxt not in _ALLOWED.get(cur, ()):
                raise ValueError(f"illegal voice transition {cur} -> {nxt}")
            self._state = nxt
            self._turn.speaking_state = nxt.value

    def _force(self, nxt: VoiceState) -> None:
        with self._lock:
            self._state = nxt
            self._turn.speaking_state = nxt.value

    @property
    def turn(self) -> ConversationTurn:
        with self._lock:
            import copy
            return copy.deepcopy(self._turn)

    # ---------------- lifecycle ----------------

    def start_listening(self) -> None:
        """Enter continuous-listening readiness (VAD armed, no STT yet)."""
        if self.state is VoiceState.IDLE:
            self._transition(VoiceState.LISTENING)

    def stop(self) -> None:
        self._stop.set()
        try:
            self.tts.stop()
        except Exception:
            pass
        for t in (self._listen_thread, self._speak_thread, self._task_thread):
            try:
                if t and t.is_alive():
                    t.join(timeout=2.0)
            except Exception:
                pass
        try:
            self.stt.close()
        except Exception:
            pass
        self._force(VoiceState.IDLE)

    # ---------------- audio input (VAD-gated) ----------------

    def vad_is_speech(self, frame_f32) -> bool:
        """Continuous VAD probe on one frame. Returns True on speech."""
        t0 = time.perf_counter()
        try:
            import numpy as np
            frame = np.asarray(frame_f32, dtype=np.float32).ravel()
            prob = self._vad.prob(frame) if self._vad else 0.0
        except Exception:
            prob = 0.0
        self.metrics.record("vad_detection_ms",
                            (time.perf_counter() - t0) * 1000.0)
        return prob >= 0.5

    def segment_utterance(self, frames, is_speech_flags,
                          silence_end_frames: int = 24):
        """Group VAD-gated frames into one speech segment.

        Returns (pcm_or_None, speech_start_idx, speech_end_idx). Silence /
        noise-only input yields None: STT never runs on it.
        """
        voiced = [i for i, f in enumerate(is_speech_flags) if f]
        if len(voiced) < 8:  # <~250ms of voicing: noise blip, discard
            return None, -1, -1
        import numpy as np
        start = max(0, voiced[0] - 4)  # pre-roll
        end = voiced[-1] + 1
        # trailing silence past the last voiced frame ends the segment
        tail = 0
        for i in range(voiced[-1] + 1, len(is_speech_flags)):
            if is_speech_flags[i]:
                break
            tail += 1
            if tail >= silence_end_frames:
                end = i + 1
                break
        else:
            end = len(frames)
        pcm = np.concatenate([np.asarray(f, dtype=np.float32).ravel()
                              for f in frames[start:end]])
        return pcm, start, end

    def ingest_pcm_utterance(self, pcm_f32, sample_rate: int,
                             source: str = "fixture") -> Optional[str]:
        """Full input path for one VAD-approved segment: STT -> route.

        Returns the final text, or None when discarded as garbage.
        """
        t0 = time.perf_counter()
        text, stt_ms = self.stt.transcribe(pcm_f32, sample_rate)
        self.metrics.record("stt_final_ms", stt_ms)
        if not text or _is_garbage_transcript(text):
            return None
        if self.keep_audio_diagnostics:
            import numpy as np
            self._last_audio = np.asarray(pcm_f32, dtype=np.float32)
        return self.on_final_text(text, source=source,
                                  speech_start=t0, speech_end=time.time())

    # ---------------- routing: fast vs deep ----------------

    def _live_scene_answer(self) -> Optional[str]:
        """Answer from the shared live scene (never a stale cache).

        Returns None when no scene provider is wired (caller falls back
        to the text screen_provider). Reports staleness honestly instead
        of reciting an old scene as current.
        """
        if self.scene_provider is None:
            return None
        t0 = time.perf_counter()
        try:
            s = self.scene_provider()
        except Exception:
            return None
        if s is None:
            return None
        if isinstance(s, dict):
            get = s.get
            stale = bool(s.get("stale", False))
            fresh_ms = float(s.get("freshness_ms", 0.0))
        else:
            get = lambda k, d="": getattr(s, k, d)
            try:
                stale = bool(s.is_stale())
            except Exception:
                stale = False
            try:
                fresh_ms = float(s.freshness_ms())
            except Exception:
                fresh_ms = 0.0
        self.metrics.record("routing_ms",
                            (time.perf_counter() - t0) * 1000.0)
        if stale:
            return ("My live view is stale right now — give me a second "
                    "to take a fresh look.")
        app = get("active_app", "unknown") or "unknown"
        title = (get("window_title", "") or "")[:80]
        focus = (get("focused_name", "") or "")[:60]
        dialog = get("dialog_kind", "none") or "none"
        delta = (get("delta_summary", "") or "")[:120]
        bits = [f"You are in {app}"]
        if title:
            bits.append(f"window {title!r}")
        if focus:
            bits.append(f"focus on {focus!r}")
        if dialog not in ("none", ""):
            bits.append(f"a {dialog} dialog is open")
        if delta and delta != "no-change":
            bits.append(f"latest change: {delta}")
        reply = ". ".join(bits) + "."
        self.metrics.record("response_decision_ms",
                            (time.perf_counter() - t0) * 1000.0)
        return reply

    def _screen_now(self) -> str:
        try:
            return self.screen_provider() or ""
        except Exception:
            return ""

    def _fast_reply(self, text: str) -> Optional[str]:
        """Local instant answers for genuinely deterministic primitives only:
        - time
        - date
        - trivial arithmetic
        Returns None for everything else to go through CognitiveFrontDoor.
        """
        from core.reflex import route as reflex_route
        t0 = time.perf_counter()
        try:
            res = reflex_route(text, screen_fn=self._screen_now,
                               scene_fn=self._live_scene_answer,
                               refresh_fn=getattr(self, "refresh_fn", None))
        except Exception:
            res = {"kind": "none"}
        if res.get("kind") == "reply":
            reply = res["reply"]
            self.metrics.record("response_decision_ms",
                                (time.perf_counter() - t0) * 1000.0)
            return reply
        if res.get("kind") == "action":
            return None  # existing action executors own it, not the Brain
        return None  # All other routing goes through CognitiveFrontDoor

    def _default_deep(self, text: str, ctx: Dict[str, Any]) -> DeepResult:
        return DeepResult(
            speech_reply="I heard you. Deep task execution gets wired here.",
            task_id="", long_running=False)

    # ---------------- main entry: one finished utterance ----------------

    def on_final_text(self, text: str, source: str = "voice",
                      speech_start: float = 0.0,
                      speech_end: float = 0.0) -> str:
        """Route one final user utterance. Returns the spoken reply."""
        entry_state = self.state
        with self._lock:
            self._turn_id += 1
            self._turn.turn_id = self._turn_id
            # One utterance = one authoritative generation: a new turn
            # always preempts older audio. Context is preserved; only
            # old audio is invalidated (I).
            self._epoch += 1
            # Snapshot pre-turn interruption evidence BEFORE reset: VAD barge
            # (or overlap) proves this utterance cut into live DUDE speech,
            # so the new turn inherits the unfinished-topic context below.
            self._turn.prev_interrupted = (
                self._turn.utterance_interrupted
                or entry_state in (VoiceState.SPEAKING,
                                   VoiceState.EXECUTING_TASK,
                                   VoiceState.INTERRUPTED))
            self._turn.prev_user_utterance = self._turn.last_user_utterance
            self._turn.final_text = text
            self._turn.last_user_utterance = text
            self._turn.speech_start = speech_start
            self._turn.speech_end = speech_end
            self._turn.utterance_interrupted = False
            self._turn.stream_spoken = ""
            entry_epoch = self._epoch
        # Interruption imperatives always win, in any state.
        low = text.strip().lower().rstrip(".!?")
        if low in ("stop", "wait", "wait stop", "hold on", "stop stop",
                   "cancel", "never mind", "nevermind"):
            return self._handle_stop(text)
        cur = self.state
        if cur in (VoiceState.SPEAKING, VoiceState.EXECUTING_TASK):
            # User talked over DUDE outside the audio barge path (e.g. text
            # injection): same semantic interruption event.
            self._begin_interruption(text, source="overlap")
        elif cur is VoiceState.IDLE:
            self._transition(VoiceState.LISTENING)
            cur = VoiceState.LISTENING
        if cur in (VoiceState.LISTENING, VoiceState.INTERRUPTED):
            try:
                self._transition(VoiceState.THINKING)
            except ValueError:
                self._force(VoiceState.THINKING)
        else:
            self._force(VoiceState.THINKING)
        with self._lock:
            # Post-bump epoch: the overlap path above may have raised the
            # generation via _begin_interruption; staleness is judged from
            # here, when the reply work actually starts.
            entry_epoch = self._epoch
        log.info("STT_RESULT %r", text[:120])
        with self._lock:
            self._lat = {"t_recv": float(
                getattr(self, "last_input_ts", 0.0) or time.time())}
        reply = self._fast_reply(text)
        if reply is None:
            log.info("BRAIN_REQUEST %r", text[:120])
            reply = self._handle_deep(text)
            log.info("BRAIN_RESPONSE %.120r", reply)
        self._persist_turn(text, reply)
        with self._lock:
            same = (self._epoch == entry_epoch)
            spoken = self._turn.stream_spoken or ""
        if not same:
            # A newer generation took over while this turn was thinking
            # (barge won mid-reasoning): never speak the stale result.
            log.info("STALE_TTS_DROPPED generation=%d (turn superseded "
                     "mid-think)", entry_epoch)
            return reply
        if spoken and reply.startswith(spoken):
            rest = reply[len(spoken):].strip()
        else:
            rest = reply
        if rest:
            log.info("NEW_TTS_START generation=%d", self._epoch)
            self._speak(rest)
        with self._lock:
            lat = dict(self._lat)
        if lat.get("t_recv"):
            now = time.time()
            t_b = lat.get("t_brain_start", now)
            t_f = lat.get("t_first_out", t_b)
            log.info("LATENCY in_brain=%.2fs brain_first=%.2fs "
                     "first_ttsenq=%.2fs",
                     t_b - lat["t_recv"], t_f - t_b,
                     lat.get("t_tts_enq", now) - t_f)
            log.info("LIVE_TURN_TOTAL_FIRST_AUDIO ms=%.0f",
                     (lat.get("t_tts_enq", now) - lat["t_recv"]) * 1000.0)
        return reply

    def _handle_stop(self, text: str) -> str:
        self._begin_interruption(text, source="command")
        try:
            status = self.task_control("pause")
        except Exception as e:
            status = f"pause request failed: {e}"
        reply = "Stopped." if "no task" in str(status).lower() else \
            "Stopping. Your task is paused, not lost."
        self._persist_turn(text, reply)
        self._speak(reply)
        return reply

    def _handle_deep(self, text: str) -> str:
        t0 = time.perf_counter()
        with self._lock:
            ctx = {"goal": text,
                   "task_context": dict(self._turn.task_context),
                   "screen": self._turn.screen_context,
                   "epoch": self._epoch}
        low = (text or "").strip().lower()
        low = re.sub(r"^(hey|hi|hello|dude|computer|assistant)[,.\s]+", "",
                     low)
        # Fast path for simple arithmetic/time/date - handled by reflex route
        # All other routing goes through CognitiveFrontDoor
        fast = False
        ctx["fast"] = fast
        log.info("ROUTE_SELECTED COGNITIVE %r", text[:80])
        with self._lock:
            was_cut = self._turn.prev_interrupted
            prev_q = self._turn.prev_user_utterance or ""
            unfinished = self._turn.current_dude_utterance or ""
        if was_cut:
            ctx["interrupt_brief"] = (
                "\n\nINTERRUPTION CONTEXT (applies to the CURRENT REQUEST "
                "above): the user cut into your previous reply. "
                f'Your unfinished reply was: "{unfinished[:400]}". '
                + (f'The interrupted question was: "{prev_q[:200]}". '
                   if prev_q else "")
                + 'Decide the interruption\'s relationship to the interrupted '
                  'topic: RELATED (follow-up on the same topic — answer it '
                  'with that context, then naturally continue the unfinished '
                  'part), UNRELATED (answer normally, then return with '
                  '"Coming back to..." only if the old topic still matters), '
                  'CORRECTION (user redirected you — acknowledge and adjust), '
                  'CONTINUATION (user carries on the same task). Begin your '
                  'reply with exactly one tag [RELATED], [UNRELATED], '
                  '[CORRECTION] or [CONTINUATION], then a space, then the '
                  'answer. Never speak the tag explanation.')
        try:
            res = self.deep_handler(text, ctx)
        except Exception as e:
            log.warning(f"deep handler failed: {e}")
            res = DeepResult(speech_reply="That hit a problem on my side.")
        self.metrics.record("response_decision_ms",
                            (time.perf_counter() - t0) * 1000.0)
        if res.long_running or res.task_id:
            with self._lock:
                self._turn.active_task_id = res.task_id or self._turn.active_task_id
                # An interruption correction must NEVER overwrite the
                # original goal: it merges via task_context["correction"].
                if not self._turn.task_context.get("goal"):
                    self._turn.task_context["goal"] = text
                if res.task_id:
                    self._turn.task_context["task_id"] = res.task_id
            self._record_fact(f"task goal: {text[:120]}")
            self._force(VoiceState.EXECUTING_TASK)
            self.metrics.record("deep_task_start_ms",
                                (time.perf_counter() - t0) * 1000.0)
        reply = res.speech_reply or "I'm working on it."
        if is_internal_narration(reply):
            reply = "I'm working on it."
        dsl = _parse_dsl_action(reply)
        if dsl is not None:
            done = self._execute_dsl(dsl, text)
            if done:
                reply = done
        return reply

    def _execute_dsl(self, dsl, text):
        """Execute one structured model action via existing verified tools.
        The LLM proposes; DUDE validates, executes, verifies. Only the
        four safe verbs are honored; anything else stays spoken words."""
        import json as _json
        verb, arg = dsl
        try:
            if verb == "OPEN_APP" and arg:
                from core.tools import execute_tool
                res = execute_tool("open_app",
                                   _json.dumps({"name": arg}),
                                   self.memory, lambda *a, **k: True)
                ok = not str(res).startswith("ERR")
                log.info("DSL_EXECUTED verb=%s arg=%r ok=%s", verb, arg, ok)
                return f"Opening {arg}." if ok else \
                    f"I couldn't open {arg}, sir."
            if verb == "READ_SCREEN":
                from core.tools import execute_tool
                res = execute_tool("read_screen_text", "{}", self.memory,
                                   lambda *a, **k: True)
                log.info("DSL_EXECUTED verb=%s ok=%s", verb,
                         not str(res).startswith("ERR"))
                return str(res)[:220] or "I can't see the screen clearly."
            if verb == "AUDIO_CONTROL":
                a = (arg or "").lower()
                key = "volumemute" if "mute" in a else (
                    "volumeup" if "up" in a else (
                        "volumedown" if "down" in a else ""))
                if key:
                    from core.tools import execute_tool
                    execute_tool("media_key", _json.dumps({"key": key}),
                                 self.memory, lambda *a, **k: True)
                    log.info("DSL_EXECUTED verb=%s arg=%r", verb, arg)
                    return "Done, sir."
                return None
            if verb == "CANCEL_TASK":
                try:
                    status = self.task_control("pause")
                except Exception as e:
                    status = f"pause request failed: {e}"
                log.info("DSL_EXECUTED verb=%s status=%s", verb, status)
                return "Stopping."
        except Exception as e:
            log.warning("DSL_EXECUTED verb=%s failed: %s", verb, e)
            return "That hit a problem on my side."
        return None

    def _record_fact(self, fact: str) -> None:
        fact = (fact or "").strip()[:220]
        if not fact:
            return
        if self._sanitizer is not None:
            try:
                fact = self._sanitizer.sanitize(fact)
            except Exception:
                pass
        with self._lock:
            if not self._session_facts or self._session_facts[-1] != fact:
                self._session_facts.append(fact)

    def _log_turn(self, role: str, text: str) -> None:
        with self._lock:
            self._history.append((role, (text or "")[:500]))

    def recent_context(self, n: int = 10) -> Dict[str, Any]:
        """Bounded working context: recent verbatim + compact facts."""
        with self._lock:
            return {"turns": list(self._history)[-n:],
                    "facts": list(self._session_facts),
                    "active_task": self._turn.active_task_id,
                    "task_context": dict(self._turn.task_context)}

    def compact_history(self, keep_recent: int = 10) -> Dict[str, Any]:
        """Compact old verbatim into already-kept structured facts.

        Active task parameters, corrections, and outcomes live in
        task_context/session_facts and are NEVER dropped — only old
        verbatim turns are discarded. A sanitized one-line summary of
        the compaction is persisted to Memory (semantic, not audio).
        """
        with self._lock:
            total = len(self._history)
            dropped = max(0, total - keep_recent)
            if dropped:
                del_count = dropped
                hist = list(self._history)
                self._history = collections.deque(hist[-keep_recent:],
                                                  maxlen=200)
            else:
                del_count = 0
            facts = list(self._session_facts)
            with_ctx = dict(self._turn.task_context)
        summary = (f"compacted {del_count} old turns; kept "
                   f"{len(self._history)} recent + {len(facts)} facts; "
                   f"active_task={self._turn.active_task_id or 'none'}; "
                   f"facts: {' | '.join(facts[-8:])}")
        if self.memory is not None and del_count:
            try:
                s = summary
                if self._sanitizer is not None:
                    s = self._sanitizer.sanitize(s)
                self.memory.add_message("system", s)
            except Exception:
                pass
        return {"dropped_turns": del_count,
                "kept_turns": len(self._history),
                "facts": facts, "task_context": with_ctx,
                "active_task": self._turn.active_task_id}

    def ingest_partial(self, pcm_f32, sample_rate: int) -> str:
        """Streaming partial transcript (unstable hint only).

        Records stt_first_partial_ms and stashes partial_text. NEVER
        routed as intent — callers must wait for the final segmentation.
        """
        try:
            text, ms, _stable = self.stt.partial(pcm_f32, sample_rate)
        except AttributeError:
            text, ms = self.stt.transcribe(pcm_f32, sample_rate)
        except Exception:
            return ""
        self.metrics.record("stt_first_partial_ms", ms)
        with self._lock:
            self._turn.partial_text = text or ""
        return text or ""

    def _persist_turn(self, user_text: str, reply: str) -> None:
        self._log_turn("user", user_text)
        self._log_turn("assistant", reply)
        if self.memory is None:
            return
        try:
            u = user_text
            r = reply
            if self._sanitizer is not None:
                u = self._sanitizer.sanitize(user_text)
                r = self._sanitizer.sanitize(reply)
            self.memory.add_message("user", u)
            self.memory.add_message("assistant", r)
        except Exception as e:
            log.warning(f"voice turn persist failed: {e}")

    # ---------------- speech out ----------------

    def _speak(self, text: str) -> None:
        cur = self.state
        resume = (VoiceState.EXECUTING_TASK if cur is VoiceState.EXECUTING_TASK
                  else VoiceState.LISTENING)
        self._force(VoiceState.SPEAKING)
        with self._lock:
            self._turn.current_dude_utterance = text
            epoch = self._epoch
        t0 = time.perf_counter()
        first = {"fired": False}

        def on_chunk(ch):
            if not first["fired"]:
                first["fired"] = True
                self.metrics.record("tts_start_ms",
                                    (time.perf_counter() - t0) * 1000.0)
                log.info("TTS_PLAYBACK_START generation=%d chars=%d",
                         epoch, len(text))

        def should_stop():
            with self._lock:
                return self._epoch != epoch

        log.info("TTS_START generation=%d chars=%d device=edge-tts", epoch, len(text))
        log.info("generation_created generation=%d", epoch)

        # Exactly-once response ownership: retire the previous speak thread
        # before the new generation owns the single Voice queue. stop()
        # bumps the adapter generation so the old loop exits on its own;
        # the join (bounded) keeps two generations from saying() at once.
        prev = self._speak_thread
        if prev is not None and prev.is_alive() \
                and prev is not threading.current_thread():
            try:
                self.tts.stop()
            except Exception:
                pass
            prev.join(timeout=2.0)

        def run():
            tts_ok = False
            try:
                tts_ok = self.tts.speak(text, on_chunk_start=on_chunk,
                                       should_stop=should_stop)
                log.info("TTS_COMPLETED generation=%d ok=%s duration_ms=%.1f",
                         epoch, tts_ok, (time.perf_counter() - t0) * 1000.0)
            except Exception as e:
                log.error("TTS_ERROR generation=%d error=%s", epoch, e)
            finally:
                with self._lock:
                    same_epoch = (self._epoch == epoch)
                if same_epoch and self.state is VoiceState.SPEAKING:
                    try:
                        self._transition(resume)
                    except ValueError:
                        self._force(resume)
                log.info("FINAL_STATE %s generation=%d tts_ok=%s",
                         self.state.value, epoch, tts_ok)

        th = threading.Thread(target=run, daemon=True, name="dude-voice-out")
        self._speak_thread = th
        th.start()

    def wait_spoken(self, timeout: float = 30.0) -> None:
        t0 = time.time()
        th = self._speak_thread
        while th and th.is_alive() and time.time() - t0 < timeout:
            time.sleep(0.05)

    # ---------------- barge-in ----------------

    def signal_user_speech(self) -> bool:
        """VAD reports user speech while DUDE speaks/works. Returns True if
        treated as a barge-in."""
        t0 = time.perf_counter()
        cur = self.state
        if cur not in (VoiceState.SPEAKING, VoiceState.EXECUTING_TASK):
            return False
        stop_ms = self.tts.stop()
        self.metrics.record("tts_stop_ms", stop_ms)
        self.metrics.record("interruption_detection_ms",
                            (time.perf_counter() - t0) * 1000.0)
        with self._lock:
            self._turn.utterance_interrupted = True
        self._force(VoiceState.INTERRUPTED)
        with self._lock:
            self._epoch += 1
            self._turn.interruption_epoch = self._epoch
            gen = self._epoch
        log.info("BARGE_DETECTED generation_cancelled=%d TTS_STOP generation=%d "
                 "stop_ms=%.1f", gen - 1, gen - 1, stop_ms)
        return True

    def _begin_interruption(self, text: str, source: str) -> None:
        try:
            self.tts.stop()
        except Exception:
            pass
        self._force(VoiceState.INTERRUPTED)
        with self._lock:
            self._epoch += 1
            self._turn.interruption_epoch = self._epoch
            gen = self._epoch
            self._turn.utterance_interrupted = True
            # The correction becomes the newest conversational event; the
            # task context (goal, progress, task_id) is PRESERVED.
            self._turn.task_context["correction"] = text
            self._turn.task_context["correction_source"] = source
            self._turn.last_user_utterance = text
        self._record_fact(f"correction: {text[:120]}")
        log.info("BARGE_DETECTED generation_cancelled=%d TTS_STOP generation=%d "
                 "source=%s", gen - 1, gen - 1, source)

    def merge_correction_and_replan(self) -> str:
        """Fold the interruption correction into the live task and replan."""
        with self._lock:
            ctx = dict(self._turn.task_context)
            tid = self._turn.active_task_id
        if not tid and "goal" not in ctx:
            return "nothing to replan"
        try:
            return self.task_control("replan", ctx)
        except Exception as e:
            return f"replan failed: {e}"

    # ---------------- task progress (concise, never internal) ----------------

    def report_progress(self, text: str) -> None:
        """Speak a short progress note. Internal narration is suppressed."""
        if not text or is_internal_narration(text):
            return
        with self._lock:
            self._turn.task_context["progress"] = text[:200]
        cur = self.state
        if cur is VoiceState.EXECUTING_TASK:
            self._speak(text)

    def mark_task_done(self, summary: str = "Done.") -> None:
        with self._lock:
            self._turn.task_context.pop("progress", None)
            self._turn.task_context.pop("correction", None)
            self._turn.task_context.pop("correction_source", None)
            self._turn.task_context["last_task_outcome"] = summary[:220]
            self._turn.active_task_id = ""
        self._record_fact(f"outcome: {summary[:120]}")
        # Leave EXECUTING_TASK before speaking so the post-speech resume
        # lands on LISTENING, not back on the finished task.
        self._force(VoiceState.THINKING)
        self._speak(summary)

    def note_task_finished(self, success: bool, note: str) -> None:
        """Record an externally-run task's outcome WITHOUT speaking.
        Callers decide whether to speak it. Clears the active task so
        later 'what are you doing' answers report truth, not stale goals.
        """
        with self._lock:
            self._turn.task_context.pop("progress", None)
            self._turn.task_context.pop("correction", None)
            self._turn.task_context.pop("correction_source", None)
            outcome = ("Done. " if success else "Did not finish. ") + \
                (note or "")
            self._turn.task_context["last_task_outcome"] = outcome[:220]
            self._turn.active_task_id = ""
        self._record_fact(f"outcome: {outcome[:120]}")


class DeepTaskRunner:
    """Concurrent deep work: voice stays responsive while a task runs.

    The runner executes the controller's deep_handler in ONE worker
    thread (never a second engine, never a second conversation loop).
    Progress flows through report_progress (concise only, internals
    filtered); completion lands via mark_task_done/note_task_finished.
    Cooperative cancellation: ctx["should_stop"] is set on pause and
    honored by handlers that check it. Handlers that ignore it (e.g. a
    monolithic TaskEngine.run) run to verification — reported honestly
    via status(), never claimed as preempted.
    """

    def __init__(self, controller: RealtimeInteractionController):
        self.c = controller
        self._thread: Optional[threading.Thread] = None
        self._task_id = ""
        self._should_stop = threading.Event()
        self._done = threading.Event()
        self._result: Optional[DeepResult] = None
        self._error = ""

    @property
    def task_id(self) -> str:
        return self._task_id

    def alive(self) -> bool:
        t = self._thread
        return bool(t and t.is_alive())

    def start(self, text: str, preamble: str = "On it.") -> str:
        """Launch deep work in background; voice returns immediately."""
        if self.alive():
            return self._task_id
        self._should_stop.clear()
        self._done.clear()
        self._result = None
        self._error = ""
        self._task_id = f"deep-{int(time.time() * 1000) % 1000000}"

        def should_stop() -> bool:
            return self._should_stop.is_set()

        def worker():
            t0 = time.perf_counter()
            try:
                with self.c._lock:
                    ctx = {"goal": text,
                           "task_context": dict(self.c._turn.task_context),
                           "epoch": self.c._epoch,
                           "should_stop": should_stop}
                res = self.c.deep_handler(text, ctx)
                self._result = res
                self.c.metrics.record(
                    "deep_task_start_ms",
                    (time.perf_counter() - t0) * 1000.0)
                if res.long_running or res.task_id:
                    with self.c._lock:
                        self.c._turn.active_task_id = (
                            res.task_id or self._task_id)
                        if not self.c._turn.task_context.get("goal"):
                            self.c._turn.task_context["goal"] = text
                    self.c._record_fact(f"task goal: {text[:120]}")
            except Exception as e:
                self._error = f"{type(e).__name__}: {e}"
                log.warning(f"deep runner failed: {self._error}")
            finally:
                self._done.set()

        self._thread = threading.Thread(target=worker, daemon=True,
                                        name="dude-deep-task")
        self._thread.start()
        # Voice path: ack now, work silently after.
        self.c._force(VoiceState.EXECUTING_TASK)
        with self.c._lock:
            self.c._turn.active_task_id = self._task_id
            self.c._turn.task_context["goal"] = text
        self.c._record_fact(f"task goal: {text[:120]}")
        self.c._speak(preamble)
        return self._task_id

    def status(self) -> str:
        with self.c._lock:
            prog = self.c._turn.task_context.get("progress", "")
            goal = self.c._turn.task_context.get("goal", "")
        if self._error:
            return f"task {self._task_id} failed: {self._error}"
        if self.alive():
            return (f"task {self._task_id} running: {goal[:120]}"
                    + (f" — {prog[:120]}" if prog else ""))
        if self._result is not None:
            return f"task {self._task_id} finished: " \
                   f"{(self._result.speech_reply or 'done')[:120]}"
        return f"task {self._task_id or 'none'}: no task running"

    def request_pause(self) -> str:
        """Cooperative pause: honored by handlers checking should_stop.
        Monolithic handlers run to verification; status() says which."""
        if not self.alive():
            return "no task running"
        self._should_stop.set()
        return ("pause requested; cooperative handlers stop at the next "
                "checkpoint, monolithic ones run to verification")

    def wait(self, timeout: float = 120.0) -> bool:
        return self._done.wait(timeout=timeout)


# ------------------------------------------------- production bridge
# Phase 11: drive the ONE existing Voice engine (core/voice.py) and the
# ONE existing Ear pipeline (core/ear.py). No second STT/TTS engine is
# ever constructed here.

class VoiceEngineAdapter(TTSBackend):
    """Drives the existing Voice engine with chunk-level stop granularity.

    Synthesis/playback stay inside Voice's worker; this adapter adds
    realtime takeover (drop stale queue), per-chunk stop checks, honest
    completion tracking, and measured stop latency on top.
    """

    def __init__(self, voice):
        self._voice = voice
        self._lock = threading.Lock()
        self._current = ""
        self._was_interrupted = False
        self._stop_ms = 0.0
        # Realtime generation: bumped on every speak()/stop() so a stale
        # generation's loop can never say() (clearing a newer generation's
        # stop flag) after a newer generation took over the single queue.
        self._gen = 0

    def _idle(self) -> bool:
        try:
            return not self._voice.speaking and self._voice.q.empty()
        except Exception:
            return True

    def speak(self, text, on_chunk_start=None, should_stop=None) -> bool:
        chunks = split_chunks(text)
        if not chunks:
            return True
        with self._lock:
            self._gen += 1
            mine = self._gen

        def _dead():
            with self._lock:
                if mine != self._gen:
                    return True
            return bool(should_stop and should_stop())

        try:
            self._voice.abort()  # realtime takeover: drop stale queue
        except Exception:
            pass
        self._was_interrupted = False
        log.info("generation_created adapter_gen=%d chunks=%d text_len=%d", mine, len(chunks), len(text))
        for i, ch in enumerate(chunks):
            if _dead():
                try:
                    self._voice.interrupt()
                except Exception:
                    pass
                self._was_interrupted = True
                log.info("STALE_TTS_DROPPED adapter_gen=%d (pre-say)", mine)
                return False
            with self._lock:
                self._current = ch
            if on_chunk_start:
                try:
                    on_chunk_start(ch)
                except Exception:
                    pass
            try:
                self._voice.say(ch)
            except Exception as e:
                log.warning(f"VoiceEngineAdapter say failed: {e}")
                return False
            # Prefetch the next chunk's audio while this one plays. The key
            # is computed with the worker's own pipeline, so a hit is exact
            # and the inter-chunk gap collapses to ~0.
            try:
                if i + 1 < len(chunks):
                    self._voice.prewarm(self._voice._combined(chunks[i + 1]))
            except Exception:
                pass
            if _dead():
                # Lost the race between our pre-say check and say(): our
                # say() just cleared a newer generation's stop flag. Set it
                # back WITHOUT draining — the queue now belongs to the
                # newer generation.
                try:
                    self._voice.interrupt()
                except Exception:
                    pass
                self._was_interrupted = True
                log.info("STALE_TTS_DROPPED adapter_gen=%d (post-say race)", mine)
                return False
            t0 = time.time()
            while not self._idle():
                if _dead():
                    try:
                        self._voice.interrupt()
                    except Exception:
                        pass
                    self._was_interrupted = True
                    log.info("STALE_TTS_DROPPED adapter_gen=%d (mid-chunk)", mine)
                    return False
                if time.time() - t0 > 120:
                    log.warning("VoiceEngineAdapter chunk wait timed out")
                    return False
                time.sleep(0.02)
            log.info("TTS_CHUNK_COMPLETED adapter_gen=%d chunk=%r", mine, ch)
        with self._lock:
            self._current = ""
        return True

    def stop(self) -> float:
        t0 = time.perf_counter()
        with self._lock:
            self._gen += 1
            gen = self._gen
        try:
            self._voice.interrupt()
        except Exception:
            pass
        self._was_interrupted = True
        with self._lock:
            self._current = ""
        self._stop_ms = (time.perf_counter() - t0) * 1000.0
        log.info("generation_cancelled adapter_gen=%d", gen)
        return self._stop_ms

    def is_speaking(self) -> bool:
        try:
            return bool(self._voice.speaking)
        except Exception:
            return False

    @property
    def current_utterance(self) -> str:
        with self._lock:
            return self._current

    @property
    def interrupted(self) -> bool:
        return self._was_interrupted


class UnwiredSTTBackend(STTBackend):
    """Guarantees no second STT engine materializes in production.

    The realtime production path consumes Ear's finished utterances;
    this backend exists only so the controller never lazily loads a
    second Whisper model next to the already-running Ear worker.
    """

    def transcribe(self, pcm_f32, sample_rate: int):
        raise RuntimeError(
            "STT is served by Ear; the controller consumes finished utterances")

    def partial(self, pcm_f32, sample_rate: int):
        raise RuntimeError(
            "STT is served by Ear; the controller consumes finished utterances")


class EarSTTBackend(STTBackend):
    """STT backend that reads finished utterances from the Ear's output queue."""

    def __init__(self, ear):
        self.ear = ear

    def transcribe(self, pcm_f32, sample_rate: int):
        # This should not be called directly - we use the Ear's queue directly
        # But we implement it for the STTBackend interface
        try:
            # Try to get a finished utterance from the Ear's queue
            _, text = self.ear.pop_utterance(block=True, timeout=5.0)
            return text
        except queue.Empty:
            return ""

    def partial(self, pcm_f32, sample_rate: int):
        # Not used - we use the Ear's queue directly
        return ""


def _fresh_perception_block(perception) -> str:
    """Compact render of a fresh engine snapshot for the answering prompt.

    The door already fetches this every turn (force refresh); without this
    the answer only ever sees the observer's cached snapshot, so a newly
    popped app is invisible until the cache catches up.
    """
    if perception is None:
        return ""
    try:
        parts = []
        app = str(getattr(perception, "active_app", "") or "").strip()
        if app and app != "unknown":
            parts.append(f"Foreground app RIGHT NOW: {app}")
        win = getattr(perception, "active_window", {}) or {}
        if isinstance(win, dict):
            title = str(win.get("title", "") or "").strip()
            if title:
                parts.append(f"Foreground window: {title[:120]}")
        foc = getattr(perception, "focused_control", None)
        if foc is not None:
            parts.append("Focused control: %s" % str(
                getattr(foc, "name", foc))[:100])
        ctrls = getattr(perception, "controls", None) or []
        names = []
        for c in ctrls[:12]:
            n = str(getattr(c, "name", "") or "").strip()
            t = str(getattr(c, "control_type", "") or
                    getattr(c, "ctype", "") or "").strip()
            if n:
                names.append(f"{n} ({t})" if t else n)
        if names:
            parts.append("On-screen controls: " + "; ".join(names)[:400])
        ocr = " ".join(str(getattr(perception, "ocr_text", "") or "").split())
        if ocr:
            parts.append("Fresh on-screen text: " + ocr[:300])
        if getattr(perception, "change_detected", False):
            parts.append("(screen changed just now)")
        return "\n".join(parts)
    except Exception:
        return ""


def build_live_context(observer, task_ctx=None, active_task_id=""):
    """ONE authoritative live-context snapshot (shared semantic bus).

    Same observer every caller uses; bounded text, no screenshots.
    Order: screen (app/window/focus/dialog/description/OCR) then task
    (goal/step/progress/correction). Exactly one return. Empty string
    only when nothing at all was observable.
    """
    parts = []
    try:
        snap = observer.current_screen() if observer is not None else None
    except Exception:
        snap = None
    if isinstance(snap, dict):
        app = snap.get("app", "?") or "?"
        title = snap.get("title", "?") or "?"
        parts.append(f"Live perception: app={app} window={title!r}")
        if snap.get("focused_name"):
            parts.append(f"focused={snap['focused_name']!r}")
        if snap.get("dialog_kind") not in (None, "", "none"):
            parts.append(f"dialog={snap['dialog_kind']}")
        if snap.get("description"):
            parts.append("Screen: " + str(snap["description"])[:400])
        if snap.get("ocr"):
            parts.append("Visible text: " +
                         " ".join(str(snap["ocr"]).split())[:300])
    tctx = dict(task_ctx or {})
    tid = active_task_id or ""
    if tid or tctx.get("goal"):
        g = str(tctx.get("goal", ""))[:120]
        parts.append("Active goal: " + (g or tid))
        if tctx.get("current_step"):
            parts.append("Current step: " + str(tctx["current_step"])[:120])
        if tctx.get("progress"):
            parts.append("Progress: " + str(tctx["progress"])[:160])
        if tctx.get("correction"):
            parts.append("Latest user correction: " +
                         str(tctx["correction"])[:160])
    # Last task outcome (incl. failure detail): without this the model can
    # only say "I couldn't finish it" when asked what went wrong. Bounded.
    _outcome = str(tctx.get("last_task_outcome", "") or "")
    _detail = str(tctx.get("last_task_detail", "") or "")
    if _outcome and _outcome.lower() not in ("done", ""):
        parts.append("Last task outcome: " + _outcome[:80] +
                     (": " + _detail[:160] if _detail else ""))
    if not parts:
        return ""
    return "LIVE PERCEPTION (real sensor evidence):\n" + "\n".join(parts)


def wire_realtime_voice(*, voice, ear, memory, brain,
                        autopilot=None, thinker=None, observer=None,
                        task_engine=None,
                        cognitive_front_door=None,
                        agent_state=None,
                        capability_bus=None,
                        enable_barge_in: bool = False):
    """Build the production realtime controller around existing systems.

    - TTS: the existing Voice engine via VoiceEngineAdapter (no new engine).
    - STT/VAD/mic: the existing Ear pipeline (finished utterances fed in
      by the caller; UnwiredSTTBackend blocks any second engine).
    - Brain: existing Brain.chat in collect mode (no speech side effects).
    - Memory: existing Memory (controller persists turns; no new store).
    - TaskEngine: optional autonomous task executor (plans, acts, verifies, recovers).
    """
    from core.brain import BrainUnavailable

    adapter = VoiceEngineAdapter(voice)
    ctl = RealtimeInteractionController(
        tts=adapter, stt=EarSTTBackend(ear), memory=memory)

    try:
        _vcfg = voice.cfg
        _voice_label = "%s at rate %s volume %s via edge-tts" % (
            _vcfg.get("voice", "tts_voice",
                      default="en-US-ChristopherNeural"),
            _vcfg.get("voice", "rate", default="+0%"),
            _vcfg.get("voice", "volume", default="+0%"))
    except Exception:
        _voice_label = "the configured system voice"
    # Runtime invariant: the realtime adapter MUST wrap the one production
    # Voice instance created at startup — never a replacement engine.
    log.info("VOICE_INSTANCE_ID id=%d TTS_ADAPTER_INSTANCE_ID id=%d same=%s",
             id(voice), id(adapter), adapter._voice is voice)

    def _screen() -> str:
        """Thin wrapper: the shared bus with this controller's live state."""
        try:
            with ctl._lock:
                tctx = dict(ctl._turn.task_context)
                tid = ctl._turn.active_task_id
        except Exception:
            tctx, tid = {}, ""
        out = build_live_context(observer, tctx, tid)
        if not out:
            log.info("LIVE_CTX empty (no perception snapshot)")
        return out

    def _deep(text: str, ctx) -> DeepResult:
        fast = bool(ctx.get("fast"))
        # Hop budget by complexity: fast action (3), short conversational
        # chat (6), deep task (configured, default 10).
        low_m = re.sub(r"^(hey|hi|hello|dude|computer|assistant)[,.\s]+",
                       "", (text or "").strip().lower())
        if fast:
            mode = "fast"
        else:
            # All non-fast routing goes through CognitiveFrontDoor
            mode = "deep"
        # Compact live prompt for ordinary conversation (F); deep/action
        # turns keep the full doctrine. Voice identity included so the
        # Brain can answer truthfully about whose voice it uses.
        prompt_mode = "compact" if mode in ("fast", "chat") else "full"
        t_brain = time.perf_counter()
        log.info("BRAIN_START route=%s mode=%s",
                 "FAST" if fast else "DEEP", mode)
        ctl._lat["t_brain_start"] = time.time()
        with ctl._lock:
            my_epoch = ctl._epoch

        def _dead():
            with ctl._lock:
                return ctl._epoch != my_epoch

        sys_extra = _screen()
        # Fresh engine snapshot (taken for THIS turn) outranks everything
        # cached below it — app identity, fresh controls, latest change.
        _fresh = ctx.get("fresh_perception_block") or ""
        if _fresh:
            sys_extra = ("FRESH LIVE PERCEPTION (taken for THIS turn — this "
                         "overrides anything older below):\n" + _fresh +
                         "\n\n" + sys_extra)
        # Context isolation: the CURRENT REQUEST outranks everything older.
        # History/memory stay available for genuinely relevant recall, but no
        # stale task may override what the user just asked.
        sys_extra += ("\n\nCURRENT REQUEST (highest priority — answer THIS, "
                      "not any older task): " + text[:300])
        ibrief = ctx.get("interrupt_brief") or ""
        if ibrief:
            sys_extra += ibrief
        uia_view = ctx.get("uia_view") or ""
        if not uia_view:
            # Cached UIA rows (microseconds, no screenshot): attach the live
            # control map to EVERY conversational turn so answers name real
            # buttons/fields/focus instead of guessing.
            try:
                _sb = (capability_bus.screentree
                       if capability_bus is not None else None)
                if _sb is not None:
                    uia_view = _sb.current_view(limit=12) or ""
            except Exception as e:
                log.warning(f"UI map attach failed: {e}")
                uia_view = ""
        if uia_view:
            sys_extra += ("\n\nLIVE UI MAP (controls actually on screen right "
                          "now — buttons, fields, focused control, cursor "
                          "position; use these exact names for clicks, never "
                          "invent control names):\n" + uia_view[:1500])
        # Query-relevant memory: the generic context block carries key
        # facts, but the model also needs records matching THIS question —
        # otherwise it answers as if it remembers nothing about the user's
        # work. Keyword recall over facts + conversation (no embeddings).
        try:
            if memory is not None:
                import re as _re3
                _stop = frozenset(
                    "what when where which while with have has this that "
                    "from they them then than there here your youre about "
                    "dude hey please tell show give doing work working sir "
                    "just like really very going know think will would can "
                    "could does did are were was been being".split())
                keys = [w for w in _re3.findall(r"[a-z]{4,}",
                                                (text or "").lower())
                        if w not in _STOP][:6]
                mem_bits = []
                for k in keys:
                    try:
                        for f in (memory.recall_facts(query=k, limit=3) or []):
                            f = str(f)[:200]
                            if f and f not in mem_bits:
                                mem_bits.append(f)
                    except Exception:
                        pass
                    try:
                        for m in (memory.search_messages(query=k, limit=3) or []):
                            c = (m.get("content") if isinstance(m, dict) else "")
                            c = " ".join(str(c or "").split())[:200]
                            if c and c not in mem_bits:
                                mem_bits.append(c)
                    except Exception:
                        pass
                if mem_bits:
                    sys_extra += ("\n\nRELEVANT MEMORY (your own records — "
                                  "speak from these first; never claim memory "
                                  "loss when the answer is here):\n" +
                                  "\n".join("- " + b for b in mem_bits[:8]))
                    log.info("MEMORY_INJECT bits=%d keys=%s", len(mem_bits),
                             ",".join(keys))
        except Exception as e:
            log.warning(f"memory recall injection failed: {e}")
        collected: list = []
        buf = {"s": ""}
        first = {"done": False}
        tools_used: list = []
        first_out_logged = {"done": False}

        def on_p(name, n):
            log.info("LIVE_PROVIDER_ATTEMPT provider=%s n=%d", name, n)
            if n == 1:
                log.info("LIVE_PROVIDER_SELECTED provider=%s mode=%s",
                         name, mode)

        def _wait_first_done():
            t0 = time.time()
            while True:
                th = ctl._speak_thread
                if not (th and th.is_alive()):
                    break
                if _dead() or time.time() - t0 > 45:
                    break
                time.sleep(0.05)

        def on_d(s):
            collected.append(s)
            if not first_out_logged["done"] and (s or "").strip():
                first_out_logged["done"] = True
                log.info("LIVE_PROVIDER_FIRST_OUTPUT ms=%.0f",
                         (time.perf_counter() - t_brain) * 1000.0)
            if first["done"] or _dead():
                return
            buf["s"] += s
            for _pass in range(3):
                clean = re.sub(r"<think>.*?</think>", "", buf["s"],
                               flags=re.DOTALL)
                if "<think>" in clean:
                    clean = clean.split("<think>")[0]
                sents = split_chunks(clean)
                if not sents:
                    return
                # Incident fix: stall-word heads ("Answering…", "Let me…")
                # are spoken by NOBODY — consume the filler and fire on the
                # first substantive clause instead.
                if _STALL_HEAD_RE.match(sents[0]) and len(sents[0]) < 60:
                    if len(sents) >= 2:
                        log.info("STREAM_FILLER_SKIPPED %r",
                                 sents[0][:50])
                        buf["s"] = buf["s"][len(sents[0]):]
                        continue
                    return  # filler only so far; wait for substance
                # First CLEAN clause starts audio: one complete sentence, or
                # a long partial clause, is enough — never wait for the reply.
                first_ready = (len(sents) >= 2
                               or (len(sents) == 1 and clean.rstrip().endswith(
                                   (".", "!", "?")))
                               or len(clean) >= 100)
                if not first_ready:
                    return
                head = sents[0]
                if _looks_like_tool_json(head):
                    return  # never speak machinery; wait for real words
                if _contains_cjk(head):
                    log.info("STREAM_NONENGLISH_SKIPPED chars=%d", len(head))
                    return  # wait for the repaired English full reply
                first["done"] = True
                log.info("FIRST_USABLE_OUTPUT chars=%d ms=%.0f", len(head),
                         (time.perf_counter() - t_brain) * 1000.0)
                ctl._lat["t_first_out"] = time.time()
                ctl._speak(head)
                # Prefetch the buffered remainder while the head plays: if it
                # survives verbatim into the follow-up generation, its audio
                # is already synthesized and the gap collapses.
                try:
                    _rest = " ".join(sents[1:]).strip()
                    if _rest:
                        voice.prewarm(voice._combined(_rest))
                except Exception:
                    pass
                log.info("TTS_FIRST_AUDIO generation=%d", my_epoch)
                ctl._lat["t_tts_enq"] = time.time()
                _wait_first_done()
                with ctl._lock:
                    if ctl._epoch == my_epoch:
                        ctl._turn.stream_spoken = head
                return

        def on_t(name):
            tools_used.append(name)
            log.info("TOOL_CALL_START %s", name)

        from core.brain import live_turn_begin, live_turn_end
        live_turn_begin()
        try:
            reply = brain.chat(
                text,
                on_delta=on_d,
                on_tool=on_t,
                on_provider=on_p,
                system_extra=sys_extra,
                fast=fast,
                mode=mode,
                prompt_mode=prompt_mode,
                voice_label=_voice_label)
        except BrainUnavailable:
            reply = "I couldn't complete that just now."
        except Exception:
            log.exception("realtime deep handler failed")
            reply = "Sorry sir, something glitched on my side just then."
        finally:
            live_turn_end()
        full = (reply or "".join(collected)).strip()
        full = re.sub(r"<think>.*?</think>", "", full,
                      flags=re.DOTALL).strip()
        relation = "NA"
        if ibrief:
            m = _TAG_RE.match(full)
            relation = m.group(1).upper() if m else "UNKNOWN"
            full = _TAG_RE.sub("", full, count=1).strip()
            log.info("CONTEXT_RELATION %s", relation)
        if tools_used:
            log.info("TOOL_RESULT tools=%s reply_chars=%d", tools_used,
                     len(full))
        if _looks_like_tool_json(full):
            # CRITICAL: structured tool machinery must NEVER reach TTS.
            log.warning("TOOL_JSON_BLOCKED chars=%d tools=%s", len(full),
                        tools_used)
            full = ("Done, sir." if tools_used else
                    "I hit a glitch on my side just then, sir.")
        if _contains_cjk(full):
            # The chat model slipped languages (STT is English-only, so it
            # can only come from here). Have the model itself re-render it
            # in English — never a canned line, never spoken as-is.
            log.warning("NONENGLISH_REPLY chars=%d, repairing to English",
                        len(full))
            try:
                tr = brain.chat(
                    "Translate the following text to natural spoken English. "
                    "Reply with ONLY the English translation, nothing else:\n\n"
                    + full,
                    fast=True, voice_label=_voice_label)
                tr = re.sub(r"<think>.*?</think>", "", tr or "",
                            flags=re.DOTALL).strip()
                if tr and not _contains_cjk(tr):
                    log.info("NONENGLISH_REPAIRED chars=%d", len(tr))
                    full = tr
                else:
                    full = ("Sorry sir, that came out garbled — "
                            "could you say that again?")
            except Exception:
                log.exception("english repair failed")
                full = ("Sorry sir, that came out garbled — "
                        "could you say that again?")
        return DeepResult(speech_reply=full or "I didn't catch that, sir.")

    def _task_control(action: str, *a, **k) -> str:
        action = (action or "").lower()
        if action == "status":
            bits = []
            try:
                bits.append("autopilot on" if getattr(autopilot, "enabled", False)
                            else "autopilot off")
            except Exception:
                pass
            try:
                bits.append("thinker on" if getattr(thinker, "enabled", False)
                            else "thinker off")
            except Exception:
                pass
            bits.append(f"voice={ctl.state.value}")
            return "; ".join(bits) if bits else "no task running"
        if action == "pause":
            for comp in (autopilot, thinker):
                try:
                    if comp is not None:
                        comp.set_enabled(False)
                except Exception:
                    pass
            return "paused autonomy"
        if action == "replan":
            return ("replan runs through the TaskEngine path; "
                    "say stop, then rephrase the goal")
        return "no task running"

    # Autonomous task deep handler - uses IntelligenceRouter + TaskEngine for full cognitive loop
    async def _run_cognitive_task(text: str, ctx) -> DeepResult:
        """Run the full cognitive loop: understand → observe → plan → act → verify → report."""
        if task_engine is None:
            return DeepResult(
                speech_reply="Task engine not available.",
                task_id="", long_running=False)
        
        try:
            # Check if already running a task
            if getattr(task_engine, '_running', False):
                return DeepResult(
                    speech_reply="I'm already working on something. Say 'stop' if you want me to switch.",
                    task_id="", long_running=False)
            
            # 1. UNDERSTAND: Use IntelligenceRouter to understand goal and create plan
            # The router observes live perception, inspects capabilities, reasons about strategy
            # The engine may hold no state yet (first task since boot): seed it
            # so routing, planning and level selection never touch None.
            if getattr(task_engine, '_task_state', None) is None:
                try:
                    from .state import TaskState
                    task_engine._task_state = TaskState(goal=text)
                except Exception as e:
                    log.warning(f"task state seed failed: {e}")
            if hasattr(task_engine, 'intelligence_router') and task_engine.intelligence_router:
                perception = task_engine.perception.observe(
                    task_engine._required_perception_level(),
                    force_refresh=True
                )
                routing_result = task_engine.intelligence_router.route(
                    task_state=task_engine._task_state,
                    perception=perception,
                    user_intent=text,
                )
                
                # If router produced a plan, use it
                if routing_result.plan and routing_result.plan.steps:
                    task_engine._task_state.subgoals = routing_result.plan.steps
                elif routing_result.decision.value == "model_fallback":
                    # Decompose via model
                    task_engine._task_state.subgoals = await task_engine._decompose_via_model(text, text)
                else:
                    # General planning
                    task_engine._task_state.subgoals = await task_engine._decompose_via_model(text, text)
            
            task_engine._task_state.pending_steps = list(task_engine._task_state.subgoals)
            task_engine._task_state.current_step = 0
            if not task_engine._task_state.subgoals:
                # Nothing actionable in the request (e.g. "i have a task"
                # with no content): hand it back to the conversational
                # brain so IT asks what the task is in its own words.
                # Never a canned line, never an empty execution.
                return _deep(text, ctx)
            
            # 2. EXECUTE: Run the task through TaskEngine (act → observe → verify → recover)
            task_state = await task_engine.resume()
            
            # 3. REPORT: success news only. Failure detail stays in
            # logs/state — never narrated (user's standing order).
            if task_state and task_state.subgoals:
                completed = [s for s in task_state.completed_steps if s.success]
                failed = [s for s in task_state.completed_steps if not s.success]
                if not failed and not task_state.failure_reason:
                    summary = f"Done. Completed {len(completed)} steps"
                else:
                    summary = "I couldn't finish that one, sir."
                    # Leave the reason where the next turn can see it, so a
                    # direct "what went wrong?" gets a real answer instead of
                    # another one-liner. Bounded, best effort.
                    try:
                        with ctl._lock:
                            _tctx = ctl._turn.task_context
                            _tctx["last_task_outcome"] = "not verified"
                            _tctx["last_task_detail"] = str(
                                task_state.failure_reason or
                                f"{len(failed)} steps had issues")[:160]
                    except Exception:
                        pass
            else:
                summary = "Task completed."
            
            task_id = getattr(task_state, 'task_id', '') if task_state else ''
            return DeepResult(
                speech_reply=summary,
                task_id=task_id, long_running=False)
        except Exception as e:
            log.exception(f"Cognitive task execution failed: {e}")
            return DeepResult(
                speech_reply="I couldn't finish that one, sir.",
                task_id="", long_running=False)

    def _deep_task(text: str, ctx) -> DeepResult:
        """Synchronous wrapper for cognitive task execution."""
        import threading
        result_holder = {"result": None, "done": False}
        
        def run_task():
            import asyncio
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result_holder["result"] = loop.run_until_complete(
                    _run_cognitive_task(text, ctx))
            except Exception as e:
                log.exception(f"Cognitive task thread failed: {e}")
                result_holder["result"] = DeepResult(
                    speech_reply="I couldn't finish that one, sir.",
                    task_id="", long_running=False)
            finally:
                result_holder["done"] = True
        
        thread = threading.Thread(target=run_task, daemon=True)
        thread.start()
        # No time budget: join until the work actually completes. The run
        # always resolves (result or exception path), and the user can
        # still stop it by saying "stop".
        thread.join()
        
        if result_holder["result"]:
            return result_holder["result"]
        return DeepResult(
            speech_reply="I couldn't finish that one, sir.",
            task_id="", long_running=False)

    # Unified handler: ALL utterances go through CognitiveFrontDoor
    # CognitiveFrontDoor does semantic classification and routes appropriately
    def _cognitive_deep(text: str, ctx) -> DeepResult:
        if cognitive_front_door is None:
            # Fallback to old behavior if no cognitive front door
            log.warning("No cognitive_front_door, falling back to brain.chat")
            return _deep(text, ctx)
        
        try:
            # Get fresh perception for this turn through CapabilityBus
            perception = None
            if capability_bus is not None and capability_bus.perception:
                try:
                    from .state import PerceptionLevel
                    perception = capability_bus.perception.observe(
                        required_level=PerceptionLevel.LEVEL_2_UIA_TREE,
                        force_refresh=True
                    )
                except Exception as e:
                    log.warning(f"Failed to get perception for cognitive door: {e}")
            
            # Build context for cognitive front door
            context = {
                "interrupt_brief": ctx.get("interrupt_brief", ""),
                "fast": ctx.get("fast", False),
            }
            # App-switch detection: if the fresh engine snapshot names a
            # different foreground app than the observer cache, the cached
            # screen text/description belongs to the PREVIOUS app and any
            # answer from it describes the wrong screen. Refresh
            # synchronously (bounded) so THIS turn sees the new screen.
            # Non-switch turns pay nothing and stay fast.
            try:
                _fresh_app = (str(getattr(perception, "active_app", "") or "")
                              if perception is not None else "")
                _cached_app = ""
                if observer is not None:
                    try:
                        _cs = observer.current_screen() or {}
                        _cached_app = str(_cs.get("app", "") or "")
                    except Exception:
                        pass
                if (_fresh_app and _cached_app
                        and _fresh_app.lower() != _cached_app.lower()):
                    log.info("SCREEN_CHANGED %r -> %r, forcing fresh snapshot",
                             _cached_app, _fresh_app)
                    if observer is not None:
                        _rt2 = threading.Thread(
                            target=observer.refresh_snapshot, daemon=True)
                        _rt2.start()
                        _rt2.join(timeout=4.0)
            except Exception as e:
                log.warning(f"screen-change refresh failed: {e}")
            # Carry the ALREADY-FRESH engine snapshot into the answering
            # turn as well: otherwise a newly popped app is invisible to
            # the answer until the observer cache catches up.
            try:
                _fb = _fresh_perception_block(perception)
                if _fb:
                    ctx["fresh_perception_block"] = _fb
            except Exception:
                pass
            
            # Process through cognitive front door
            interpretation = cognitive_front_door.process_turn(
                transcript=text,
                perception=perception,
                context=context,
            )
            
            log.info("COGNITIVE_ROUTE mode=%s goal=%s action=%s conf=%.2f",
                     interpretation.intent_kind.value,
                     interpretation.user_goal[:60],
                     interpretation.requires_computer_action,
                     interpretation.confidence)
            
            # Route based on SEMANTIC interpretation
            if interpretation.intent_kind == CognitiveMode.CONVERSE:
                # Social/conversational -> brain.chat
                return _deep(text, ctx)
            
            elif interpretation.intent_kind in (CognitiveMode.ANSWER_FROM_KNOWLEDGE, 
                                                 CognitiveMode.ANSWER_FROM_LIVE_ENV):
                # Questions -> use brain.chat with live context
                # Add perception context to the prompt
                enhanced_ctx = dict(ctx)
                enhanced_ctx["perception_focus"] = interpretation.perception_focus
                if interpretation.intent_kind == CognitiveMode.ANSWER_FROM_LIVE_ENV:
                    # Screen evidence must be fresh, but the answer must stay
                    # fast: refresh in the background, wait at most ~2s, then
                    # answer from whatever is cached (capture+OCR usually
                    # takes well under a second; the old 12s wait is gone).
                    try:
                        if observer is not None:
                            _rt = threading.Thread(
                                target=observer.refresh_snapshot, daemon=True)
                            _rt.start()
                            _rt.join(timeout=2.0)
                    except Exception as e:
                        log.warning(f"Live snapshot refresh failed: {e}")
                    # Live control map (buttons, fields, focused control,
                    # cursor) so the answer names real on-screen controls
                    # instead of guessing or listing background apps.
                    try:
                        _st = (capability_bus.screentree
                               if capability_bus is not None else None)
                        if _st is not None:
                            enhanced_ctx["uia_view"] = _st.current_view(limit=14)
                    except Exception as e:
                        log.warning(f"UI map attach failed: {e}")
                return _deep(text, enhanced_ctx)
            
            elif interpretation.intent_kind == CognitiveMode.MEMORY_OPERATION:
                # Memory operations -> brain.chat with memory context
                return _deep(text, ctx)
            
            elif interpretation.intent_kind == CognitiveMode.COMPUTER_WORK:
                # Computer work -> use cognitive task execution
                if interpretation.execution_plan and task_engine is not None:
                    # Use the plan from cognitive front door
                    return _deep_task_with_plan(text, ctx, interpretation.execution_plan)
                else:
                    # Fall back to standard task engine
                    return _deep_task(text, ctx)
            
            elif interpretation.intent_kind == CognitiveMode.NEEDS_CLARIFICATION:
                # Ask for clarification
                return DeepResult(
                    speech_reply="Could you clarify what you'd like me to do?",
                    task_id="", long_running=False
                )
            
            else:
                # Unknown -> try task engine
                return _deep_task(text, ctx)
                
        except Exception as e:
            log.exception(f"Cognitive front door failed: {e}")
            return _deep(text, ctx)

    # Helper for task with pre-built plan
    async def _run_task_with_plan(text: str, ctx, plan) -> DeepResult:
        if task_engine is None:
            return DeepResult(speech_reply="Task engine unavailable.", task_id="", long_running=False)
        try:
            if getattr(task_engine, '_running', False):
                return DeepResult(speech_reply="Already working on something. Say 'stop' to switch.", task_id="", long_running=False)
            # Same seeding as _run_cognitive_task: first task since boot has
            # no state yet; planning must never touch None.
            if getattr(task_engine, '_task_state', None) is None:
                try:
                    from .state import TaskState
                    task_engine._task_state = TaskState(goal=text)
                except Exception as e:
                    log.warning(f"task state seed failed: {e}")
            task_engine._task_state.subgoals = plan.steps if plan else []
            task_engine._task_state.pending_steps = list(task_engine._task_state.subgoals)
            task_engine._task_state.current_step = 0
            if not task_engine._task_state.subgoals:
                # Plan came back empty (vague request like "i have a task"):
                # hand it back to the conversational brain so IT asks what
                # the task is in its own words. Never canned, never empty.
                return _deep(text, ctx)
            task_state = await task_engine.resume()
            if task_state and task_state.subgoals:
                completed = [s for s in task_state.completed_steps if s.success]
                failed = [s for s in task_state.completed_steps if not s.success]
                if not failed and not task_state.failure_reason:
                    summary = f"Done. Completed {len(completed)} steps"
                else:
                    summary = "I couldn't finish that one, sir."
                    # Leave the reason where the next turn can see it, so a
                    # direct "what went wrong?" gets a real answer instead of
                    # another one-liner. Bounded, best effort.
                    try:
                        with ctl._lock:
                            _tctx = ctl._turn.task_context
                            _tctx["last_task_outcome"] = "not verified"
                            _tctx["last_task_detail"] = str(
                                task_state.failure_reason or
                                f"{len(failed)} steps had issues")[:160]
                    except Exception:
                        pass
            else:
                summary = "Task completed."
            task_id = getattr(task_state, 'task_id', '') if task_state else ''
            return DeepResult(speech_reply=summary, task_id=task_id, long_running=False)
        except Exception as e:
            log.exception(f"Task execution failed: {e}")
            return DeepResult(speech_reply="I couldn't finish that one, sir.", task_id="", long_running=False)

    def _deep_task_with_plan(text: str, ctx, plan) -> DeepResult:
        import threading
        result_holder = {"result": None, "done": False}
        def run_task():
            import asyncio
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result_holder["result"] = loop.run_until_complete(_run_task_with_plan(text, ctx, plan))
            except Exception as e:
                log.exception(f"Task thread failed: {e}")
                result_holder["result"] = DeepResult(speech_reply="I couldn't finish that one, sir.", task_id="", long_running=False)
            finally:
                result_holder["done"] = True
        thread = threading.Thread(target=run_task, daemon=True)
        thread.start()
        # No time budget: join until the work actually completes (see above).
        thread.join()
        if result_holder["result"]:
            return result_holder["result"]
        return DeepResult(speech_reply="I couldn't finish that one, sir.", task_id="", long_running=False)

    ctl.deep_handler = _cognitive_deep
    ctl.task_control = _task_control

    def _scene():
        try:
            if observer is None:
                return None
            snap = observer.current_screen() or {}
            return {
                "active_app": snap.get("app", "unknown"),
                "window_title": snap.get("title", ""),
                "focused_name": "",
                "dialog_kind": "none",
                "delta_summary": (snap.get("description", "") or "")[:120],
                "stale": False,
                "freshness_ms": 0.0,
            }
        except Exception:
            return None

    ctl.scene_provider = _scene

    def _refresh():
        try:
            if observer is None:
                return {}
            return observer.refresh_snapshot() or {}
        except Exception:
            return {}

    ctl.refresh_fn = _refresh

    if enable_barge_in:
        try:
            ear.barge_enabled = True
        except Exception:
            pass
    log.info("realtime voice wired (barge_in=%s)", enable_barge_in)
    return ctl
