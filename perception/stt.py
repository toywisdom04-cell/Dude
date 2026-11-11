"""Speech recognition for DUDE using SpeechRecognition + Google STT."""

import logging

logger = logging.getLogger(__name__)


class SpeechRecognizer:
    def __init__(
        self,
        language: str = "en-IN",
        energy_threshold: int = 300,
        pause_threshold: float = 0.8,
    ):
        self.language = language
        self.energy_threshold = energy_threshold
        self.pause_threshold = pause_threshold
        self._recognizer = None
        self._warned_missing = False

    @property
    def recognizer(self):
        if self._recognizer is None:
            import speech_recognition as sr

            self._recognizer = sr.Recognizer()

            # Let the recognizer adapt automatically to your microphone.
            self._recognizer.dynamic_energy_threshold = True

            self._recognizer.energy_threshold = self.energy_threshold
            self._recognizer.pause_threshold = self.pause_threshold

            # Prevent DUDE from waiting forever for speech.
            self._recognizer.non_speaking_duration = 0.5

        return self._recognizer

    def listen(self, timeout: float | None = None) -> str | None:
        """Listen to the microphone and return recognized speech."""

        try:
            import speech_recognition as sr

            r = self.recognizer

            print("[STT] Opening microphone...")

            with sr.Microphone() as source:

                print("[STT] Calibrating microphone...")
                r.adjust_for_ambient_noise(source, duration=1)

                print("[STT] Listening... Speak now.")

                audio = r.listen(
                    source,
                    timeout=timeout,
                    phrase_time_limit=15,
                )

            print("[STT] Processing speech...")

            text = r.recognize_google(
                audio,
                language=self.language,
            )

            print(f"[STT] Heard: {text}")

            return text

        except ImportError:
            if not self._warned_missing:
                print(
                    "[STT ERROR] SpeechRecognition or PyAudio "
                    "is not installed."
                )
                logger.warning(
                    "SpeechRecognition/PyAudio not installed; "
                    "cannot listen."
                )
                self._warned_missing = True

            return None

        except sr.WaitTimeoutError:
            print("[STT] No speech detected before timeout.")
            return None

        except sr.UnknownValueError:
            print("[STT] I heard audio, but could not understand it.")
            return None

        except sr.RequestError as exc:
            print(f"[STT ERROR] Google Speech Recognition failed: {exc}")
            logger.warning("STT request failed: %s", exc)
            return None

        except Exception as exc:
            print(f"[STT ERROR] Microphone/STT failed: {type(exc).__name__}: {exc}")
            logger.warning("STT failed: %s", exc)
            return None

    def transcribe_audio(self, audio) -> str | None:
        """Transcribe an already-captured audio object."""

        try:
            text = self.recognizer.recognize_google(
                audio,
                language=self.language,
            )

            print(f"[STT] Transcribed: {text}")

            return text

        except Exception as exc:
            print(
                f"[STT ERROR] Audio transcription failed: "
                f"{type(exc).__name__}: {exc}"
            )
            return None

    def whisper_transcribe(self, audio_file: str) -> str | None:
        """Optional offline Whisper transcription."""

        try:
            import whisper

            print("[STT] Loading Whisper model...")

            model = whisper.load_model("base")

            result = model.transcribe(audio_file)

            text = result["text"].strip()

            print(f"[STT] Whisper heard: {text}")

            return text

        except Exception as exc:
            print(
                f"[STT ERROR] Whisper transcription failed: "
                f"{type(exc).__name__}: {exc}"
            )
            return None