"""Client for a local LLM exposed via an OpenAI-compatible HTTP endpoint.

Supports the user's local models (Omnirouter, Nararouter, 9Router) as well as
standard local servers such as Ollama or llama.cpp server.
"""
import requests


class LocalLLMClient:
    def __init__(self, base_url: str, model: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def available(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/models", timeout=3)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def chat(self, messages: list[dict], temperature: float = 0.7,
             max_tokens: int = 500) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        r = requests.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()

