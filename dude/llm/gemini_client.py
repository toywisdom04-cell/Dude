"""Client for Google Gemini (user's own student-plan API key)."""
import requests


class GeminiClient:
    def __init__(self, api_key: str, model: str,
                 base_url: str = "https://generativelanguage.googleapis.com/v1beta",
                 timeout: int = 45):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def chat(self, messages: list[dict], temperature: float = 0.7,
             max_tokens: int = 500) -> str:
        # Convert OpenAI-style messages into Gemini's contents format.
        contents = []
        for msg in messages:
            role = "model" if msg.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})

        url = f"{self.base_url}/models/{self.model}:generateContent"
        payload = {
            "contents": contents,
            "generationConfig": {"temperature": temperature,
                                 "maxOutputTokens": max_tokens},
        }
        r = requests.post(
            url,
            params={"key": self.api_key},
            json=payload,
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError):
            raise RuntimeError("Gemini returned an empty or unexpected response") from None

