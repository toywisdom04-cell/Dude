"""Brain Router: decides per-utterance whether to use an offline skill or the LLM.

Offline-first priority:
1. Quick skill match (instant, offline)
2. Local LLM (offline conversation) when the cloud is unreachable
3. Cloud LLM (Gemini) when online
Fallback: polite rephrase request.
"""
import datetime
import logging

from ..platform.registry import get_platform
from .gemini_client import GeminiClient
from .local_client import LocalLLMClient
from .skills import OfflineSkillRouter

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are DUDE, a personal AI assistant in the style of JARVIS. You are "
    "warm, concise, and helpful. Address the user as {honorific}. Keep spoken "
    "responses short and natural - like a real assistant talking, not reading "
    "an essay. When asked to perform an action you are not certain about, say "
    "what you intend to do. You have access to tools through the calling code. "
    "If you cannot do something offline, say so plainly.\n\n"
    "Current context:\n{context}"
)


class BrainRouter:
    def __init__(self, config, memory=None, registry=None):
        self.config = config
        self.memory = memory
        self.skills = OfflineSkillRouter(registry=registry)
        self.registry = registry
        self._local = LocalLLMClient(
            base_url=config.get("llm.local.base_url", "http://localhost:11434/v1"),
            model=config.get("llm.local.model", "llama3.2:3b"),
            timeout=config.get("llm.local.timeout", 60),
        )
        self._gemini = GeminiClient(
            api_key=config.get("llm.gemini.api_key", ""),
            model=config.get("llm.gemini.model", "gemini-3.6-flash"),
            base_url=config.get("llm.gemini.base_url",
                                "https://generativelanguage.googleapis.com/v1beta"),
        )
        self._platform = get_platform()

    # ---- public API ----
    def respond(self, text: str, directed: bool = True) -> str:
        """Produce a reply string for the user's utterance."""
        if directed:
            result = self.skills.dispatch(text)
            if result is not None:
                return self._skill_to_speech(result)

        if self._gemini.available and self._platform.is_online():
            try:
                return self._gemini.chat(self._build_messages(text))
            except Exception as exc:
                logger.warning("Gemini failed: %s", exc)

        if self._local.available:
            try:
                return self._local.chat(self._build_messages(text))
            except Exception as exc:
                logger.warning("Local LLM failed: %s", exc)

        # no LLM available - fall back to a web search / polite response
        fallback = self.skills.dispatch("search for " + text)
        if fallback is not None and fallback.ok:
            return self._skill_to_speech(fallback)
        return ("I couldn't do that just now, sir. I'm offline and my local "
                "model isn't running. Could you rephrase or check my model?")

    def can_speak_offline(self) -> bool:
        return self._local.available

    # ---- internals ----
    def _build_messages(self, text: str) -> list[dict]:
        context_blocks = []
        if self.memory is not None:
            facts = self.memory.facts()
            if facts:
                context_blocks.append("Known facts: " + "; ".join(f["fact"] for f in facts))
            recent = self.memory.recent_messages(limit=10)
            if recent:
                context_blocks.append("Recent conversation:\n" +
                                      "\n".join(f"{m['role']}: {m['text']}" for m in recent))
        context_blocks.append(f"Now: {datetime.datetime.now():%A %I:%M %p}")
        info = self._platform.system_info()
        if "battery_percent" in info:
            context_blocks.append(f"Battery: {info['battery_percent']}%")

        honorific = self.config.get("user.honorific", "sir") or "sir"
        system = SYSTEM_PROMPT.format(
            honorific=honorific,
            context="\n".join(context_blocks),
        )
        return [{"role": "system", "content": system},
                {"role": "user", "content": text}]

    @staticmethod
    def _skill_to_speech(result) -> str:
        if not result.ok:
            return result.message
        # prefer data-bearing human message
        return result.message

