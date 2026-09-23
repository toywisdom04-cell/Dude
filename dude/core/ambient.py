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

# Bare imperatives ("scroll down", "open calculator"): self-contained
# evidence of addressing — background chatter is almost never a bare
# command at DUDE. Question-words (what/when/...) still need the wake
# word or topic continuity, since videos ask questions constantly.
_IMPERATIVE_VERB = re.compile(
    r"^\s*(open|close|launch|start|stop|play|pause|set|remind|schedule|"
    r"tell|search|find|write|send|check|kill|run|make|create|show|hide|"
    r"minimi[sz]e|maximi[sz]e|shutdown|restart|reboot|type|click|scroll|"
    r"volume|mute|screenshot|remember|forget|summari[sz]e|analyze|code)\b",
    re.I)

# Short control/continuation replies carry no topic of their own
# ("yes", "stop", "continue") — always let the dialogue layer decide.
_CONTROL_RE = re.compile(
    r"\b(stop(\sthat)?|cancel|never mind|forget it|continue|go on|"
    r"resume|keep going|proceed|yes|yeah|yep|no|nope|okay|ok|actually|"
    r"instead|rather|explain|why|what happened|what went wrong|"
    r"shut down|shutdown|restart|wake up|wake|go to sleep|sleep|"
    r"good ?night|bye|goodbye)\b", re.I)

# Filler for topic matching: only distinctive content words count.
_TOPIC_STOP = frozenset(
    "the a an and or but so just like really very gonna wanna got get "
    "getting going go come know think that this these those they them "
    "their there here what when where which who whom how why can could "
    "would should will shall may might must have has had having does did "
    "doing done are were was been being your youre mine ours him her his "
    "hers its our with from about into over after before between through "
    "during under again further then once all any both each more most "
    "other some such only own same than too until while dont didnt isnt "
    "arent wasnt werent wont wouldnt cant cannot couldnt shouldnt im ive "
    "id ill hes shes weve theyre thats theres whats hows whys whens "
    "wheres lets oh ah uh um well now today sir hey hi hello back much "
    "many little bit".split())

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
        low = (text or "").strip().lower()
        if len(low.split()) <= 2 or _CONTROL_RE.search(low):
            # Too short to judge, or a control/continuation turn the
            # dialogue layer owns ("stop", "continue", "yes") — pass
            # through; the wake gate + continuation matcher decide.
            return ("directed", text)
        if not self.judge_local(text):
            self.learn(text)
            return ("ambient", "not directed at you")
        # judge_local passed on shape alone (short/question/command-like).
        # In a room with other people talking and videos playing, that is
        # not enough: without the wake word, the speech must also be a
        # bare command or continue the live conversation's topic.
        if DIRECTED_CUES.search(text):
            return ("directed", text)
        core = _FILLER_PREFIX.sub("", low) or low
        if _IMPERATIVE_VERB.match(text) or _IMPERATIVE_VERB.match(core):
            return ("directed", text)
        if self._topic_continues(text):
            return ("directed", text)
        self.learn(text)
        return ("ambient", "off-topic background speech")

    @staticmethod
    def _content_words(s):
        out = set()
        for w in re.findall(r"[a-z]{4,}", (s or "").lower()):
            if w in _TOPIC_STOP:
                continue
            # Light stemming so "prices" meets "price" (applied both sides).
            if len(w) > 5 and w.endswith("s") and not w.endswith("ss"):
                w = w[:-1]
            out.add(w)
        return out

    def _topic_continues(self, text):
        """True when the utterance shares distinctive words with the recent
        conversation. Fails open (True) on any error or empty history so a
        fresh session never mutes the user."""
        try:
            recent = self.memory.recent_messages(limit=10) or []
        except Exception:
            return True
        if not recent:
            return True
        ctx_words = set()
        for m in recent[-10:]:
            try:
                ctx_words |= self._content_words(m.get("content"))
            except Exception:
                continue
        if not ctx_words:
            return True
        heard = self._content_words(text)
        if not heard:
            return True
        overlap = len(heard & ctx_words)
        need = 2 if len(heard) > 4 else 1
        return overlap >= need

    def learn(self, text):
        # Local-first policy: ambient/background speech is NEVER stored as
        # memory. It is counted for diagnostics and dropped. Only
        # user-addressed speech may enter memory/conversation.
        text = text.strip()
        if not text:
            return
        try:
            self.memory.audit("ambient_dropped", text[:80])
        except Exception:
            pass
