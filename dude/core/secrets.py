import threading


class SecretsBroker:
    RULES = (
        "1. Never read, store, or use any password/API key/secret without asking the user first.\n"
        "2. When the user grants access, use the secret only for the requested action.\n"
        "3. Wipe it from memory immediately after use. Never write secrets to memory.db,\n"
        "   logs, audit entries, or conversation history. Ever.\n"
        "4. Secrets may only be sent to their own service (e.g. Gmail SMTP gets a Gmail\n"
        "   app password). They must NEVER be included in LLM prompts or sent anywhere else.\n"
    )

    def __init__(self, ask_user):
        self.ask_user = ask_user
        self._ephemeral = {}
        self._lock = threading.Lock()

    def grant(self, label, value, purpose):
        with self._lock:
            self._ephemeral[label] = {"value": value, "purpose": purpose}

    def consume(self, label):
        with self._lock:
            item = self._ephemeral.pop(label, None)
        return item["value"] if item else None

    def wipe(self):
        with self._lock:
            for k in list(self._ephemeral):
                self._ephemeral[k] = {"value": "", "purpose": "wiped"}
            self._ephemeral.clear()

    def request_use(self, memory, label, purpose):
        """Ask the user for permission to use a secret; returns True if granted."""
        allowed = memory.audit_recent_secret_denials(label)
        del allowed
        ok = self.ask_user(
            f"I need permission to use {label} for: {purpose}. Allow it this once?")
        memory.audit("secret_request", f"label={label} granted={ok}")
        return ok
