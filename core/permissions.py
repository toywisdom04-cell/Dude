"""Permission gating for tool actions.

Policy:
- auto_allow: read-only / harmless actions run without asking.
- always_ask: anything modifying or executing requires confirmation.
- User can promote categories into always_allow by voice.
"""
from dataclasses import dataclass, field


class PermissionDenied(Exception):
    """Raised when the user refuses permission for an action."""


@dataclass
class PermissionGate:
    always_allow: set = field(default_factory=set)
    always_ask: set = field(default_factory=set)
    _confirmer=None

    def __post_init__(self):
        self.always_allow = set(self.always_allow)
        self.always_ask = set(self.always_ask)

    def set_confirmer(self, confirmer):
        """confirmer(action) -> bool. Injected by the agent (voice/text UI)."""
        self._confirmer = confirmer

    def requires_permission(self, action: str) -> bool:
        if action in self.always_allow:
            return False
        if action in self.always_ask:
            return True
        # default: modifying/remote actions ask; unknown read-only actions allow
        sensitive = {"write", "delete", "run", "send", "execute", "shutdown",
                     "restart", "install", "change", "call", "set"}
        return any(token in action for token in sensitive)

    def request(self, action: str, description: str = "", force: bool = False) -> bool:
        """Ask the user for permission. Returns True if granted.

        force=True bypasses the allow-list (used by tools whose policy is ask).
        """
        if force:
            pass
        elif not self.requires_permission(action):
            return True
        if self._confirmer is None:
            return False
        return bool(self._confirmer(action, description))

    def grant_always(self, action: str) -> None:
        self.always_allow.add(action)
        self.always_ask.discard(action)

    def revoke(self, action: str) -> None:
        self.always_allow.discard(action)

