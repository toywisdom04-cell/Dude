"""DUDEAgent: the central event loop.

Runs a non-blocking lifecycle:
- Wakes and greets.
- Continuously listens for speech.
- Sends speech to the brain.
- Speaks responses using TTS.
- Logs conversation to memory.
- Checks reminders periodically.
"""

import datetime
import logging
import threading
import time
from pathlib import Path

from ..config import Config
from ..core.conversation import ConversationManager
from ..core.permissions import PermissionGate
from ..llm.router import BrainRouter
from ..memory.session import SessionState
from ..memory.store import MemoryStore
from ..output.tts import TextToSpeech
from ..perception.screen import ScreenObserver
from ..perception.stt import SpeechRecognizer
from ..platform.registry import get_platform

logger = logging.getLogger(__name__)


class DUDEAgent:
    def __init__(
        self,
        config: Config | None = None,
        data_dir: str | None = None,
    ):
        self.config = config or Config.load()

        if data_dir:
            self.config.set("memory.data_dir", data_dir)

        self.data_dir = Path(
            self.config.get("memory.data_dir", "./dude_data")
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Memory
        self.memory = MemoryStore(str(self.data_dir))

        from ..tools.schedule import set_store as _set_schedule_store

        _set_schedule_store(self.memory)

        self.session = SessionState(self.memory)

        # Conversation
        self.conversation = ConversationManager(
            silence_wait=self.config.get(
                "voice.silence_wait_seconds",
                1.2,
            ),
            max_context_turns=self.config.get(
                "memory.max_context_turns",
                20,
            ),
        )

        # Permissions
        self.gate = PermissionGate(
            always_allow=self.config.get(
                "permissions.always_allow",
                [],
            ),
            always_ask=self.config.get(
                "permissions.always_ask",
                [],
            ),
        )

        # Text to speech
        self.tts = TextToSpeech(
            rate=self.config.get(
                "voice.tts_rate",
                185,
            ),
            voice_index=self.config.get(
                "voice.tts_voice_index",
                0,
            ),
        )

        # Speech recognition
        self.stt = SpeechRecognizer()

        # Brain
        self.brain = BrainRouter(
            self.config,
            memory=self.memory,
        )

        # Platform
        self.platform = get_platform()

        # Screen observer
        self.screen = ScreenObserver(
            data_dir=str(self.data_dir),
            enabled=self.config.get(
                "memory.learn_from_screen",
                False,
            ),
        )

        # Runtime state
        self.running = False
        self._lock = threading.Lock()

        self._wake_word = self.config.get(
            "voice.wake_word",
            "dude",
        ).lower()

        self._honorific = (
            self.config.get(
                "user.honorific",
                "sir",
            )
            or "sir"
        )

        self._last_capture = None
        self._on_event = None

    # --------------------------------------------------
    # Lifecycle
    # --------------------------------------------------

    def start(self, greet: bool = True) -> None:
        self.running = True

        self.gate.set_confirmer(
            self._confirm
        )

        logger.info(
            "DUDE starting (os=%s)",
            self.platform.name,
        )

        if greet:
            self.greet()

        self._run_loop()

    def stop(self) -> None:
        print("[DUDE] Stopping...")
        self.running = False

    def set_event_callback(self, callback) -> None:
        self._on_event = callback

    # --------------------------------------------------
    # Events
    # --------------------------------------------------

    def _emit(self, event: str, **data) -> None:
        if self._on_event:
            try:
                self._on_event(
                    event,
                    data,
                )
            except Exception:
                pass

    # --------------------------------------------------
    # Greeting
    # --------------------------------------------------

    def greet(self) -> None:
        greeting = self.session.greeting_message()

        if not greeting:
            hour = datetime.datetime.now().hour

            if hour < 12:
                greeting = "Good morning"
            elif hour < 18:
                greeting = "Good afternoon"
            else:
                greeting = "Good evening"

            greeting += f", {self._honorific}."

        try:
            agenda = self.brain.skills.dispatch(
                "today's schedule"
            )

            if agenda is not None and agenda.ok:
                greeting += f" {agenda.message}"

        except Exception as exc:
            logger.warning(
                "Could not get agenda: %s",
                exc,
            )

        print(f"[DUDE] Greeting: {greeting}")

        self.say(greeting)

        self._emit(
            "greeted",
            message=greeting,
        )

    # --------------------------------------------------
    # Speaking
    # --------------------------------------------------

    def say(self, text: str) -> None:
        if not text:
            return

        print(f"[AGENT TTS] About to speak: {text}")

        self._emit(
            "speaking",
            text=text,
        )

        try:
            print("[AGENT TTS] Calling self.tts.speak()...")

            self.tts.speak(text)

            print(
                "[AGENT TTS] self.tts.speak() finished."
            )

        except Exception as exc:
            print(
                f"[AGENT TTS ERROR] "
                f"{type(exc).__name__}: {exc}"
            )

            logger.exception(
                "TTS failed"
            )

        self._emit("idle")

        try:
            self.memory.add_message(
                "assistant",
                text,
                directed=False,
            )
        except Exception as exc:
            logger.warning(
                "Could not save assistant message: %s",
                exc,
            )

    # --------------------------------------------------
    # Main voice loop
    # --------------------------------------------------

    def _run_loop(self) -> None:
        check_interval = self.config.get(
            "schedule.check_interval_seconds",
            30,
        )

        last_check = time.monotonic()

        print("[DUDE] Voice loop started.")

        while self.running:
            try:
                print("[DUDE] Waiting for speech...")

                self._emit("listening")

                query = self.stt.listen(
                    timeout=5
                )

                if not query:
                    print(
                        "[DUDE] No usable speech received."
                    )

                    self._maybe_screen_capture()

                    if (
                        time.monotonic()
                        - last_check
                        >= check_interval
                    ):
                        last_check = time.monotonic()

                        self._check_schedule()

                    continue

                print(
                    f"[DUDE] Received speech: {query}"
                )

                self.conversation.record_user(
                    query
                )

                self._emit(
                    "heard",
                    text=query,
                )

                self._handle(query)

                if (
                    time.monotonic()
                    - last_check
                    >= check_interval
                ):
                    last_check = time.monotonic()

                    self._check_schedule()

            except Exception as exc:
                print(
                    f"[DUDE ERROR] "
                    f"Voice loop error: "
                    f"{type(exc).__name__}: {exc}"
                )

                logger.exception(
                    "Voice loop crashed internally"
                )

                time.sleep(1)

    # --------------------------------------------------
    # Process speech
    # --------------------------------------------------

    def _handle(self, query: str) -> None:
        print(
            f"[AGENT] Processing: {query}"
        )

        directed = self._is_directed(query)
        dude_asked = self._is_dude_asked(query)

        print(
            f"[AGENT] Directed: {directed} | "
            f"DUDE mentioned: {dude_asked}"
        )

        if not directed and not dude_asked:
            print(
                "[AGENT] Speech ignored because "
                "it was not directed at DUDE."
            )

            return

        try:
            print(
                "[AGENT] Sending request to brain..."
            )

            self._emit("thinking")

            response = self.brain.respond(
                query,
                directed=directed,
            )

            print(
                f"[AGENT] Brain response: {response}"
            )

            self._emit(
                "responding",
                text=response,
            )

            try:
                self.memory.add_message(
                    "user",
                    query,
                    directed=directed,
                )
            except Exception as exc:
                logger.warning(
                    "Could not save user message: %s",
                    exc,
                )

            print(
                "[AGENT] Speaking response..."
            )

            self.say(response)

        except Exception as exc:
            print(
                f"[AGENT ERROR] "
                f"Failed while processing: "
                f"{type(exc).__name__}: {exc}"
            )

            logger.exception(
                "Failed to process user query"
            )

            self.say(
                f"Sorry {self._honorific}, "
                "something went wrong while processing that."
            )

    # --------------------------------------------------
    # Determine whether speech is for DUDE
    # --------------------------------------------------

    def _is_directed(self, query: str) -> bool:
        """Treat normal speech in voice mode as directed at DUDE."""

        q = query.strip().lower()

        if not q:
            return False

        return True

    def _is_dude_asked(self, query: str) -> bool:
        q = query.lower()

        return (
            "dude" in q
            or self._honorific.lower() in q
        )

    # --------------------------------------------------
    # Permission confirmation
    # --------------------------------------------------

    def _confirm(
        self,
        action: str,
        description: str,
    ) -> bool:
        if not self.config.get("user.name"):
            prompt = (
                f"Do you allow me to "
                f"{description or action}, "
                f"{self._honorific}? "
                "Please say yes or no."
            )
        else:
            prompt = (
                f"Sir, may I "
                f"{description or action}? "
                "Please say yes or no."
            )

        self.say(prompt)

        answer = self.stt.listen(
            timeout=8
        )

        if answer and "yes" in answer.lower():
            self.say(
                f"Done, {self._honorific}."
            )

            return True

        self.say(
            f"Very well, {self._honorific}. "
            "I'll skip that."
        )

        return False

    # --------------------------------------------------
    # Schedule
    # --------------------------------------------------

    def _check_schedule(self) -> None:
        try:
            due = self.memory.due_reminders()

            for reminder in due:
                self.say(
                    f"Reminder, {self._honorific}: "
                    f"{reminder['title']}."
                )

                self.memory.complete_reminder(
                    reminder["id"]
                )

                self.memory.log_action(
                    "reminder_fired",
                    reminder["title"],
                )

        except Exception as exc:
            logger.warning(
                "Schedule check failed: %s",
                exc,
            )

    # --------------------------------------------------
    # Screen observation
    # --------------------------------------------------

    def _maybe_screen_capture(self) -> None:
        if self.screen.should_capture(
            self._last_capture
        ):
            self._last_capture = (
                datetime.datetime.now()
            )

            self.screen.capture()