import datetime
import re

from core.config import get_config

DIRECTED_CUES = re.compile(r"\b(dude|hey dude|ok computer|assistant)\b[,!. ]?", re.I)
SECOND_PERSON = re.compile(r"\b(you|your|yours|for me|my)\b", re.I)
COMMAND_VERB = re.compile(
    r"^\s*(open|close|launch|start|stop|play|pause|set|remind|schedule|tell|search|find|"
    r"write|send|check|kill|run|make|create|show|hide|minimi[sz]e|maximi[sz]e|shutdown|"
    r"restart|reboot|type|click|scroll|volume|mute|screenshot|what|when|where|who|how|why|"
    r"remember|forget|summari[sz]e|analyze|code)\b", re.I)
SENSITIVE = re.compile(
    r"(password|passwd|otp|pin code|\bpin\b|credit card|\d{14,16}|api[_ -]?key|secret|"
    r"\d{3}-?\d{2}-?\d{4})", re.I)

CONTROL_TERMS = {
    "turn off", "power off", "shut down", "shutdown", "power down", "go offline", "bye dude",
    "sleep", "go to sleep", "good night", "goodnight",
    "wake up", "wake", "come back", "talk to me", "are you there",
}

# Words people say before actually giving the command ("so minimize...",
# "just open...", "can you close...") — strip them before matching verbs.
_FILLER_PREFIX = re.compile(
    r"^(?:(?:so|and|but|just|please|ok|okay|hey|hi|now|sir|mate)\b[,!\s]+)*"
    r"(?:(?:can|will|would|could|do|did|are|were)\s+you\b[,!\s]+)*",
    re.I)


def _norm_key(text):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", (text or "").lower())).strip()


class Ambient:
    """Decides by itself whether speech is directed at DUDE or is nearby
    conversation. Directed speech is returned for execution; everything else
    is quietly learned from (never acted upon, never spoken about unprompted).
    """

    def __init__(self, memory, brain):
        self.memory = memory
        self.brain = brain
        cfg = get_config()
        self.enabled = bool(cfg.get("ambient", "enabled", default=True))
        self.wake_word_only = bool(cfg.get("ambient", "wake_word_only", default=False))

    def judge_local(self, text):
        if self.wake_word_only:
            return bool(DIRECTED_CUES.search(text))
        low = text.lower().strip()
        if _norm_key(low) in CONTROL_TERMS:
            return True
        if DIRECTED_CUES.search(text):
            return True
        if SENSITIVE.search(text):
            return False
        words = len(text.split())
        core = _FILLER_PREFIX.sub("", low) or low
        verb = COMMAND_VERB.match(text) or COMMAND_VERB.match(core)
        second = SECOND_PERSON.search(text) or SECOND_PERSON.search(core)
        question = core.strip().endswith("?")
        if not verb and words <= 2:
            return False
        if verb and (words >= 2 or second):
            return True
        if question:
            return True
        if second:
            return True
        if words <= 20:
            return True
        return False

    def judge_llm(self, text):
        prompt = (
            "You are the router of a voice assistant named Dude living in the user's PC. "
            "Decide whether this overheard speech is addressed TO the assistant "
            "(a command or question meant for it) or is just people talking / TV / phone call. "
            "Reply with exactly one word: DIRECTED or AMBIENT.\n\n"
            f'Speech: "{text[:400]}"'
        )
        try:
            out = (self.brain.think(prompt) or "").strip().upper()
        except Exception:
            return False
        return out.startswith("DIRECTED")

    def route(self, text):
        """Returns ('directed', text) or ('ambient', reason)."""
        if not self.enabled:
            return ("directed", text)
        if self.judge_local(text):
            return ("directed", text)
        self.learn(text)
        return ("ambient", "not directed at you")

    def learn(self, text):
        text = text.strip()
        if not text or len(text) < 8 or len(text) > 500:
            return
        if SENSITIVE.search(text):
            self.memory.audit("ambient_skipped", "sensitive content suppressed")
            return
        stamp = datetime.datetime.now().strftime("%m-%d %H:%M")
        fact = f"[overheard {stamp}] {text}"
        existing = self.memory.recall_facts(text[:50], limit=3)
        for e in existing:
            if text[:80].lower() in e.lower():
                return
        self.memory.remember_fact(fact, category="ambient")
