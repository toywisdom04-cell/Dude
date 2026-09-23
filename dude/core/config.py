import json
import os
import re
import sys


def _app_root():
    if getattr(sys, "_MEIPASS", None):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ROOT = _app_root()
CONFIG_PATH = os.path.join(ROOT, "config.json")
LOCAL_CONFIG_PATH = os.path.join(ROOT, "config.local.json")

DEFAULTS = {
    "assistant_name": "Dude",
    "user_title": "sir",
    "vault_path": "",
    "voice": {"tts_voice": "en-US-ChristopherNeural", "rate": "+8%", "volume": "+0%"},
    "ear": {
        "whisper_model": "small.en",
        "language": "en",
        "sample_rate": 16000,
        "frame_ms": 32,
        "silence_end_ms": 750,
        "min_speech_ms": 250,
        "interrupt_min_speech_ms": 350,
        "vad": "auto",
        "mic_index": None,
    },
    "brain": {
        "temperature": 0.6,
        "max_tool_hops": 6,
        "history_turns": 24,
        "providers": [],
    },
    "tracker": {"poll_seconds": 10, "enabled": True},
    "scheduler": {"poll_seconds": 15, "enabled": True},
    "presence": {"enabled": True, "idle_away_s": 180},
    "autopilot": {"enabled": True, "interval_s": 240},
    "orchestrator": {
        "enabled": False,
        "use_new_task_engine": False,
        "use_new_perception": False,
        "use_new_action_executor": False,
        "use_offline_skills": False,
        "use_background_coordinator": False,
        "min_skill_confidence": 0.8,
        "min_procedure_confidence": 0.7,
        "min_local_confidence": 0.7,
        "enable_procedure_learning": True,
        "min_procedure_successes": 2,
        "min_procedure_confidence": 0.7,
        "enable_procedure_adaptation": True,
        "enable_procedure_composition": True,
        "min_adaptation_confidence": 0.6,
        "max_composition_depth": 3
    },
}


def _deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


VAULT_PATTERNS = {
    "omni": re.compile(r"OmniRoute[^\n]*?\s((?:sk-)?[0-9a-f]{12}-[0-9a-f]{4,8}-[0-9a-f]{6,12})"),
    "openrouter": re.compile(r"(sk-or-v1-[0-9a-f]{32,64})"),
    "openai": re.compile(r"(sk-proj-[A-Za-z0-9_\-]{20,})"),
    "nvidia": re.compile(r"(nvapi-[A-Za-z0-9_\-]{20,})"),
    "codestral": re.compile(r"Codestral Mistral API Key\s*-\s*([A-Za-z0-9]{28,44})", re.M),
}

ENV_KEYS = {
    "omni": "DUDE_OMNI_KEY",
    "openrouter": "DUDE_OPENROUTER_KEY",
    "openai": "DUDE_OPENAI_KEY",
    "nvidia": "DUDE_NVIDIA_KEY",
    "codestral": "DUDE_CODESTRAL_KEY",
}


def _merge_provider_lists(base_list, local_list):
    merged = {}
    order = []
    for p in base_list or []:
        if isinstance(p, dict) and p.get("name"):
            merged[p["name"]] = dict(p)
            order.append(p["name"])
    for p in local_list or []:
        if not isinstance(p, dict):
            continue
        n = p.get("name")
        if n and n in merged:
            for k, v in p.items():
                if v not in ("", None) or k == "enabled":
                    merged[n][k] = v
        elif n:
            merged[n] = dict(p)
            order.append(n)
    return [merged[n] for n in order]


class Config:
    def __init__(self):
        if not os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULTS, f, indent=2)
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if os.path.exists(LOCAL_CONFIG_PATH):
            with open(LOCAL_CONFIG_PATH, "r", encoding="utf-8") as f:
                local = json.load(f)
            local_provs = (local.get("brain") or {}).pop("providers", None)
            data = _deep_merge(data, local)
            if local_provs is not None:
                data.setdefault("brain", {})["providers"] = _merge_provider_lists(
                    data.get("brain", {}).get("providers"), local_provs)
        self._data = _deep_merge(DEFAULTS, data)
        self.root = ROOT
        override = (data.get("data_dir") or "").strip() if isinstance(data, dict) else ""
        if override:
            try:
                os.makedirs(override, exist_ok=True)
                self.data_dir = os.path.abspath(override)
            except Exception:
                self.data_dir = os.path.join(ROOT, "data")
        else:
            self.data_dir = os.path.join(ROOT, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(os.path.join(self.data_dir, "screenshots"), exist_ok=True)
        os.makedirs(os.path.join(self.data_dir, "tts_cache"), exist_ok=True)
        os.makedirs(os.path.join(self.data_dir, "models"), exist_ok=True)
        os.makedirs(os.path.join(self.data_dir, "logs"), exist_ok=True)
        self._vault_cache = None
        self._vault_loaded = False

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if not isinstance(node, dict) or p not in node:
                return default
            node = node[p]
        return node

    def section(self, name):
        return self._data.get(name, {})

    def _read_vault(self):
        if self._vault_loaded:
            return self._vault_cache or ""
        self._vault_loaded = True
        path = self.get("vault_path", default="")
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    self._vault_cache = f.read()
            except OSError:
                self._vault_cache = ""
        return self._vault_cache or ""

    def api_key_for(self, provider_name):
        keys = self.api_keys_for(provider_name)
        return keys[0] if keys else ""

    def api_keys_for(self, provider_name):
        env_name = ENV_KEYS.get(provider_name)
        if env_name and os.environ.get(env_name):
            return [os.environ[env_name]]
        vault = self._read_vault()
        if vault:
            pat = VAULT_PATTERNS.get(provider_name)
            if pat:
                return pat.findall(vault)
        return []

    def db_path(self):
        return os.path.join(self.data_dir, "memory.db")


_config = None


def get_config():
    global _config
    if _config is None:
        _config = Config()
    return _config
