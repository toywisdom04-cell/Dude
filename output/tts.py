"""Text-to-speech output using pyttsx3."""

import logging
import threading

logger = logging.getLogger(__name__)


class TextToSpeech:
    def __init__(self, rate: int = 185, voice_index: int = 0):
        self.rate = rate
        self.voice_index = voice_index
        self._lock = threading.Lock()
        self._engine = None

        print("[TTS] Initializing speech engine...")

        try:
            import pyttsx3

            self._engine = pyttsx3.init()
            self._configure()

            print("[TTS] Speech engine initialized successfully.")

        except Exception as exc:
            logger.exception("Could not initialize TTS")
            print(f"[TTS ERROR] Could not initialize speech engine: {exc}")

    def _configure(self) -> None:
        """Configure voice and speech rate."""
        if self._engine is None:
            return

        try:
            voices = self._engine.getProperty("voices")

            print(f"[TTS] Available voices: {len(voices)}")

            if voices:
                index = self.voice_index

                if index < 0 or index >= len(voices):
                    index = 0

                selected_voice = voices[index]

                print(
                    f"[TTS] Using voice {index}: "
                    f"{getattr(selected_voice, 'name', 'Unknown')}"
                )

                self._engine.setProperty(
                    "voice",
                    selected_voice.id
                )

            self._engine.setProperty("rate", self.rate)

            print(f"[TTS] Speech rate: {self.rate}")

        except Exception as exc:
            logger.exception("TTS configuration failed")
            print(f"[TTS ERROR] Configuration failed: {exc}")

    @property
    def engine(self):
        return self._engine

    def set_voice(self, index: int) -> None:
        self.voice_index = index

        if self._engine is not None:
            self._configure()

    def set_rate(self, rate: int) -> None:
        self.rate = rate

        try:
            if self._engine is not None:
                self._engine.setProperty("rate", rate)
        except Exception as exc:
            print(f"[TTS ERROR] Could not change rate: {exc}")

    def speak(self, text: str, interruptible: bool = True) -> None:
        """Speak text synchronously."""

        if not text:
            return

        print(f"[TTS] Requested speech: {text}")

        if self._engine is None:
            print("[TTS ERROR] Speech engine is not available.")
            return

        try:
            with self._lock:
                print("[TTS] Stopping any previous speech...")

                try:
                    self._engine.stop()
                except Exception:
                    pass

                print("[TTS] Calling engine.say()...")

                self._engine.say(str(text))

                print("[TTS] Calling engine.runAndWait()...")

                self._engine.runAndWait()

                print("[TTS] Speech finished.")

        except Exception as exc:
            logger.exception("TTS speak failed")
            print(f"[TTS ERROR] Speech failed: {exc}")

    def stop(self) -> None:
        """Stop current speech."""

        try:
            if self._engine is not None:
                self._engine.stop()

        except Exception as exc:
            print(f"[TTS ERROR] Stop failed: {exc}")

    def available(self) -> bool:
        return self._engine is not None