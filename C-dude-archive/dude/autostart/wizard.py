"""First-run setup wizard - natural, voice-guided, not robotic.

Walks the user through: what to call them, whether to auto-start, which apps
they use daily. Calibrates mic/speaker and detects OS. All conversational.
"""
import logging

from ..output.tts import TextToSpeech
from ..perception.stt import SpeechRecognizer

logger = logging.getLogger(__name__)


class FirstRunWizard:
    def __init__(self, config):
        self.config = config
        self.tts = TextToSpeech(rate=175)
        self.stt = SpeechRecognizer()

    def say(self, text: str) -> None:
        self.tts.speak(text)

    def ask(self, question: str, timeout: float = 12) -> str | None:
        self.say(question)
        return self.stt.listen(timeout=timeout)

    def run(self) -> None:
        self.say("Hi. I'm DUDE, your personal assistant. I'm still getting "
                 "to know you, and this will only take a minute.")
        answer = self.ask("What should I call you?")
        if answer:
            self.config.set("user.name", answer.strip())
            self.say(f"Nice to meet you, {answer.strip()}. I'll call you that "
                     "from now on. Or I can just call you sir.")
            a2 = self.ask("Should I call you by that name, or would you prefer sir?")
            if a2 and any(w in a2.lower() for w in ("sir", "lord", "master")):
                self.config.set("user.honorific", "sir")
            else:
                self.config.set("user.honorific", answer.strip())

        a3 = self.ask("Would you like me to start automatically whenever your "
                      "computer turns on? Say yes or no.")
        auto = bool(a3 and "yes" in a3.lower())
        self.config.set("user.auto_start", auto)
        if auto:
            try:
                from ..autostart.manager import enable
                ok = enable()
                self.say("Done. I'll be here every time you start your computer."
                         if ok else "I could not register autostart, but I'll "
                         "remember your choice for later.")
            except Exception as exc:
                logger.warning("Autostart failed: %s", exc)

        apps = self.ask("Which apps do you use most? You can name a few, "
                        "like Chrome, WhatsApp, or your editor.")
        if apps:
            self.config.set("preferred_apps", [a.strip() for a in apps.split(",") if a.strip()])

        self.config.save()
        self.say("All set. Whenever you need me, just say the word, and I'll "
                 "help you with your work. Have a great day.")

