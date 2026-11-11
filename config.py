"""Configuration management for DUDE.

Loads config from a JSON file (default: config.json next to the app, or
DUDE_CONFIG env var). Auto-creates from config.example.json on first run.
"""
import copy
import json
import os
from pathlib import Path

DEFAULT_CONFIG_FILE = "config.json"
EXAMPLE_CONFIG_FILE = "config.example.json"
ENV_CONFIG_KEY = "DUDE_CONFIG"


def _find_config_path() -> Path:
    env_path = os.environ.get(ENV_CONFIG_KEY)
    if env_path:
        return Path(env_path)
    return Path(DEFAULT_CONFIG_FILE)


def _load_example() -> dict:
    example = Path(EXAMPLE_CONFIG_FILE)
    if example.exists():
        return json.loads(example.read_text(encoding="utf-8"))
    return {}


def _defaults() -> dict:
    return {
        "user": {"name": "", "honorific": "sir", "auto_start": True},
        "llm": {
            "online_backend": "gemini",
            "offline_backend": "local",
            "gemini": {"api_key": "AIzaSyDKQHFQCVxAwK2Lr0OjQUwfYMpMXEH3_U0", "model": "gemini-3.6-flash",
                       "base_url": "https://generativelanguage.googleapis.com/v1beta"},
            "local": {"base_url": "http://localhost:11434/v1",
                      "model": "llama3.2:3b", "timeout": 60},
        },
        "voice": {"tts_rate": 185, "tts_voice_index": 0, "wake_word": "dude",
                  "silence_wait_seconds": 1.2, "vad_aggressiveness": 2},
        "permissions": {
            "always_allow": ["open_app", "tell_time", "search_files", "weather",
                             "joke", "screenshot"],
            "always_ask": ["run_command", "delete", "send_email", "send_message",
                           "write_file", "change_settings", "shutdown"],
        },
        "memory": {"data_dir": "./dude_data", "learn_from_screen": False,
                   "nightly_learning": True, "max_context_turns": 20},
        "schedule": {"check_interval_seconds": 30, "reminder_lead_minutes": 5},
    }


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    """Thin wrapper around the configuration dict."""

    def __init__(self, data: dict | None = None):
        self._data = data or _defaults()

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        target = path or _find_config_path()
        if not target.exists():
            data = _load_example() or _defaults()
            Config._write(target, data)
        else:
            data = json.loads(target.read_text(encoding="utf-8"))
            # merge with defaults so new keys never crash old configs
            data = _deep_merge(_defaults(), data)
        return cls(data)

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def save(self, path: Path | None = None) -> None:
        target = path or _find_config_path()
        self._write(target, self._data)

    @property
    def data(self) -> dict:
        return self._data

    def get(self, dotted_key: str, default=None):
        """Get a nested value using dot notation, e.g. llm.gemini.model."""
        node: dict = self._data
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted_key: str, value) -> None:
        parts = dotted_key.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def update(self, dotted_key: str, value) -> None:
        self.set(dotted_key, value)

