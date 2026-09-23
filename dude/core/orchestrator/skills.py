"""Deterministic Skills Registry for DUDE Orchestrator.

Provides a provider-independent skill layer that maps task intents
to concrete tool executions without requiring an LLM.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .state import (
    Action,
    TargetSpec,
    GroundingMethod,
    RiskLevel,
    VerificationMethod,
    ExpectedResult,
    PerceptionSnapshot,
)
from core.tools import execute_tool, REGISTRY as TOOL_REGISTRY
from core.memory import Memory

log = logging.getLogger(__name__)


@dataclass
class Skill:
    """A deterministic local skill."""
    name: str
    purpose: str
    capabilities: list[str]
    required_perception: int = 1  # PerceptionLevel
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.LOW
    executor: Callable = None
    verification_methods: list[VerificationMethod] = field(default_factory=list)


class SkillExecutor(ABC):
    """Abstract skill executor."""
    
    @abstractmethod
    def execute(self, action: Action, perception: PerceptionSnapshot, memory: Optional[Memory] = None) -> Any:
        pass


# --- Built-in Skill Executors ---

class OpenAppExecutor(SkillExecutor):
    """Execute open_app tool."""
    
    def execute(self, action: Action, perception: PerceptionSnapshot, memory: Optional[Memory] = None):
        app_name = action.target.text_match or action.target.control_name or ""
        return execute_tool("open_app", {"app_name": app_name}, memory, lambda *a: True)


class CloseAppExecutor(SkillExecutor):
    """Execute close_app tool."""
    
    def execute(self, action: Action, perception: PerceptionSnapshot, memory: Optional[Memory] = None):
        app_name = action.target.text_match or action.target.control_name or ""
        return execute_tool("close_app", {"app_name": app_name}, memory, lambda *a: True)


class InspectWindowExecutor(SkillExecutor):
    """Get active window info."""
    
    def execute(self, action: Action, perception: PerceptionSnapshot, memory: Optional[Memory] = None):
        return execute_tool("active_window_info", {}, memory, lambda *a: True)


class FindControlExecutor(SkillExecutor):
    """Find a UI control via UIA/OCR."""
    
    def execute(self, action: Action, perception: PerceptionSnapshot, memory: Optional[Memory] = None):
        # This is a perception query, not an action
        return f"Control search: {action.target_description}"


class ClickControlExecutor(SkillExecutor):
    """Click a grounded control."""
    
    def execute(self, action: Action, perception: PerceptionSnapshot, memory: Optional[Memory] = None):
        if not perception.window_bounds:
            return "ERROR: No window bounds available"
        
        # Grounding handled by ActionExecutor - just execute click
        x, y = 0, 0
        if action.target.coordinates:
            x, y = action.target.coordinates
        elif perception.controls and action.target.control_name:
            for ctrl in perception.controls:
                if action.target.control_name.lower() in ctrl.name.lower():
                    x, y = ctrl.rect.center()
                    break
        
        return execute_tool("ui_click", {"x": x, "y": y}, memory, lambda *a: True)


class TypeTextExecutor(SkillExecutor):
    """Type text into focused or targeted control."""
    
    def execute(self, action: Action, perception: PerceptionSnapshot, memory: Optional[Memory] = None):
        text = action.target.text_match or ""
        args = {"text": text}
        if action.target.coordinates:
            args["x"], args["y"] = action.target.coordinates
        return execute_tool("type_text", args, memory, lambda *a: True)


class FilesystemExecutor(SkillExecutor):
    """Execute filesystem operations."""
    
    def execute(self, action: Action, perception: PerceptionSnapshot, memory: Optional[Memory] = None):
        action_type = action.action_type.lower()
        if action_type == "create_folder":
            return execute_tool("create_folder", {"path": action.target.text_match}, memory, lambda *a: True)
        elif action_type == "read_file":
            return execute_tool("read_file", {"path": action.target.text_match}, memory, lambda *a: True)
        elif action_type == "write_file":
            # Expect text_match to be "path|content"
            parts = (action.target.text_match or "").split("|", 1)
            if len(parts) == 2:
                return execute_tool("write_file", {"path": parts[0], "content": parts[1]}, memory, lambda *a: True)
        elif action_type == "list_directory":
            return execute_tool("list_directory", {"path": action.target.text_match or "."}, memory, lambda *a: True)
        elif action_type == "move_path":
            parts = (action.target.text_match or "").split("|", 1)
            if len(parts) == 2:
                return execute_tool("move_path", {"src": parts[0], "dst": parts[1]}, memory, lambda *a: True)
        elif action_type == "copy_path":
            parts = (action.target.text_match or "").split("|", 1)
            if len(parts) == 2:
                return execute_tool("copy_path", {"src": parts[0], "dst": parts[1]}, memory, lambda *a: True)
        elif action_type == "delete_path":
            return execute_tool("delete_path", {"path": action.target.text_match}, memory, lambda *a: True)
        return "Unknown filesystem action"


class ClipboardExecutor(SkillExecutor):
    """Execute clipboard operations."""
    
    def execute(self, action: Action, perception: PerceptionSnapshot, memory: Optional[Memory] = None):
        if action.action_type.lower() == "clipboard_read":
            return execute_tool("clipboard_read", {}, memory, lambda *a: True)
        elif action.action_type.lower() == "clipboard_write":
            return execute_tool("clipboard_write", {"text": action.target.text_match or ""}, memory, lambda *a: True)
        return "Unknown clipboard action"


class ScreenshotExecutor(SkillExecutor):
    """Take a screenshot."""
    
    def execute(self, action: Action, perception: PerceptionSnapshot, memory: Optional[Memory] = None):
        return execute_tool("screenshot", {"path": ""}, memory, lambda *a: True)


class HotkeyExecutor(SkillExecutor):
    """Press hotkey combination."""
    
    def execute(self, action: Action, perception: PerceptionSnapshot, memory: Optional[Memory] = None):
        return execute_tool("press_hotkey", {"keys": action.target.text_match or ""}, memory, lambda *a: True)


class ScrollExecutor(SkillExecutor):
    """Scroll screen."""
    
    def execute(self, action: Action, perception: PerceptionSnapshot, memory: Optional[Memory] = None):
        return execute_tool("scroll_screen", {"direction": "down", "amount": 3}, memory, lambda *a: True)


class KnowledgeExecutor(SkillExecutor):
    """Query/search knowledge base."""
    
    def execute(self, action: Action, perception: PerceptionSnapshot, memory: Optional[Memory] = None):
        if action.action_type.lower() == "search_knowledge":
            return execute_tool("search_knowledge", {"query": action.target.text_match or ""}, memory, lambda *a: True)
        elif action.action_type.lower() == "learn_knowledge":
            parts = (action.target.text_match or "").split("|", 1)
            if len(parts) == 2:
                return execute_tool("learn_knowledge", {"topic": parts[0], "content": parts[1]}, memory, lambda *a: True)
        return "Unknown knowledge action"


class MemoryExecutor(SkillExecutor):
    """Query conversation/user memory."""
    
    def execute(self, action: Action, perception: PerceptionSnapshot, memory: Optional[Memory] = None):
        action_type = action.action_type.lower()
        if action_type == "recall_about_user":
            return execute_tool("recall_about_user", {"query": action.target.text_match or ""}, memory, lambda *a: True)
        elif action_type == "remember_about_user":
            return execute_tool("remember_about_user", {"fact": action.target.text_match or ""}, memory, lambda *a: True)
        elif action_type == "search_conversation_history":
            return execute_tool("search_conversation_history", {"query": action.target.text_match or ""}, memory, lambda *a: True)
        return "Unknown memory action"


class ReminderExecutor(SkillExecutor):
    """Manage reminders."""
    
    def execute(self, action: Action, perception: PerceptionSnapshot, memory: Optional[Memory] = None):
        action_type = action.action_type.lower()
        if action_type == "add_reminder":
            return execute_tool("add_reminder", {"text": action.target.text_match or ""}, memory, lambda *a: True)
        elif action_type == "list_reminders":
            return execute_tool("list_reminders", {}, memory, lambda *a: True)
        return "Unknown reminder action"


# --- Skill Registry ---

class SkillRegistry:
    """Registry of deterministic skills."""
    
    def __init__(self):
        self._skills: dict[str, Skill] = {}
        self._register_builtins()
    
    def _register_builtins(self):
        """Register all built-in deterministic skills."""
        
        # Application skills
        self.register(Skill(
            name="open_app",
            purpose="Launch an application by name",
            capabilities=["launch", "start", "open"],
            required_perception=1,
            risk_level=RiskLevel.LOW,
            executor=OpenAppExecutor(),
            verification_methods=[VerificationMethod.WINDOW_APPEARED, VerificationMethod.PROCESS_STATE],
        ))
        
        self.register(Skill(
            name="close_app",
            purpose="Close an application by name",
            capabilities=["close", "exit", "quit"],
            required_perception=1,
            risk_level=RiskLevel.MEDIUM,
            executor=CloseAppExecutor(),
            verification_methods=[VerificationMethod.WINDOW_DISAPPEARED, VerificationMethod.PROCESS_STATE],
        ))
        
        self.register(Skill(
            name="inspect_window",
            purpose="Get information about the active window",
            capabilities=["inspect", "window_info", "active_window"],
            required_perception=1,
            risk_level=RiskLevel.LOW,
            executor=InspectWindowExecutor(),
            verification_methods=[VerificationMethod.CUSTOM],
        ))
        
        # UI Control skills
        self.register(Skill(
            name="find_control",
            purpose="Find a UI control by name, role, or text",
            capabilities=["find", "locate", "search_control"],
            required_perception=2,  # Needs UIA
            risk_level=RiskLevel.LOW,
            executor=FindControlExecutor(),
            verification_methods=[VerificationMethod.CUSTOM],
        ))
        
        self.register(Skill(
            name="click_control",
            purpose="Click a UI control (button, link, menu item)",
            capabilities=["click", "press", "select"],
            required_perception=2,
            risk_level=RiskLevel.MEDIUM,
            executor=ClickControlExecutor(),
            verification_methods=[VerificationMethod.UIA_STATE_CHANGE, VerificationMethod.SCREEN_DELTA],
        ))
        
        self.register(Skill(
            name="type_text",
            purpose="Type text into a focused or targeted control",
            capabilities=["type", "input", "enter_text"],
            required_perception=2,
            risk_level=RiskLevel.MEDIUM,
            executor=TypeTextExecutor(),
            verification_methods=[VerificationMethod.OCR_TEXT_APPEARED, VerificationMethod.UIA_STATE_CHANGE],
        ))
        
        # Filesystem skills
        self.register(Skill(
            name="create_folder",
            purpose="Create a directory",
            capabilities=["mkdir", "create_directory", "make_folder", "create_folder", "create folder", "create"],
            required_perception=1,
            risk_level=RiskLevel.LOW,
            executor=FilesystemExecutor(),
            verification_methods=[VerificationMethod.FILE_EXISTS],
        ))
        
        self.register(Skill(
            name="read_file",
            purpose="Read a file's contents",
            capabilities=["read", "cat", "view_file"],
            required_perception=1,
            risk_level=RiskLevel.LOW,
            executor=FilesystemExecutor(),
            verification_methods=[VerificationMethod.CUSTOM],
        ))
        
        self.register(Skill(
            name="write_file",
            purpose="Write content to a file",
            capabilities=["write", "save", "create_file"],
            required_perception=1,
            risk_level=RiskLevel.MEDIUM,
            executor=FilesystemExecutor(),
            verification_methods=[VerificationMethod.FILE_EXISTS],
        ))
        
        self.register(Skill(
            name="list_directory",
            purpose="List directory contents",
            capabilities=["ls", "dir", "list_files"],
            required_perception=1,
            risk_level=RiskLevel.LOW,
            executor=FilesystemExecutor(),
            verification_methods=[VerificationMethod.CUSTOM],
        ))
        
        self.register(Skill(
            name="move_path",
            purpose="Move/rename a file or folder",
            capabilities=["mv", "move", "rename"],
            required_perception=1,
            risk_level=RiskLevel.MEDIUM,
            executor=FilesystemExecutor(),
            verification_methods=[VerificationMethod.FILE_EXISTS],
        ))
        
        self.register(Skill(
            name="copy_path",
            purpose="Copy a file or folder",
            capabilities=["cp", "copy"],
            required_perception=1,
            risk_level=RiskLevel.LOW,
            executor=FilesystemExecutor(),
            verification_methods=[VerificationMethod.FILE_EXISTS],
        ))
        
        self.register(Skill(
            name="delete_path",
            purpose="Delete a file or folder",
            capabilities=["rm", "delete", "remove"],
            required_perception=1,
            risk_level=RiskLevel.HIGH,
            executor=FilesystemExecutor(),
            verification_methods=[VerificationMethod.FILE_EXISTS],
        ))
        
        # Clipboard
        self.register(Skill(
            name="clipboard_read",
            purpose="Read clipboard contents",
            capabilities=["clipboard", "paste"],
            required_perception=1,
            risk_level=RiskLevel.LOW,
            executor=ClipboardExecutor(),
            verification_methods=[VerificationMethod.CLIPBOARD_CONTENT],
        ))
        
        self.register(Skill(
            name="clipboard_write",
            purpose="Write to clipboard",
            capabilities=["copy_to_clipboard"],
            required_perception=1,
            risk_level=RiskLevel.LOW,
            executor=ClipboardExecutor(),
            verification_methods=[VerificationMethod.CLIPBOARD_CONTENT],
        ))
        
        # Screen/Input
        self.register(Skill(
            name="screenshot",
            purpose="Capture current screen",
            capabilities=["capture", "screenshot", "screen_grab"],
            required_perception=4,
            risk_level=RiskLevel.LOW,
            executor=ScreenshotExecutor(),
            verification_methods=[VerificationMethod.CUSTOM],
        ))
        
        self.register(Skill(
            name="hotkey",
            purpose="Press a keyboard shortcut",
            capabilities=["hotkey", "shortcut", "key_combo"],
            required_perception=1,
            risk_level=RiskLevel.LOW,
            executor=HotkeyExecutor(),
            verification_methods=[VerificationMethod.CUSTOM],
        ))
        
        self.register(Skill(
            name="scroll",
            purpose="Scroll the active window",
            capabilities=["scroll", "page_down", "page_up"],
            required_perception=1,
            risk_level=RiskLevel.LOW,
            executor=ScrollExecutor(),
            verification_methods=[VerificationMethod.SCREEN_DELTA],
        ))
        
        # Knowledge
        self.register(Skill(
            name="search_knowledge",
            purpose="Search stored knowledge base",
            capabilities=["search", "query", "lookup"],
            required_perception=1,
            risk_level=RiskLevel.LOW,
            executor=KnowledgeExecutor(),
            verification_methods=[VerificationMethod.CUSTOM],
        ))
        
        self.register(Skill(
            name="learn_knowledge",
            purpose="Store knowledge for future retrieval",
            capabilities=["learn", "store", "remember"],
            required_perception=1,
            risk_level=RiskLevel.LOW,
            executor=KnowledgeExecutor(),
            verification_methods=[VerificationMethod.CUSTOM],
        ))
        
        # Memory
        self.register(Skill(
            name="recall_about_user",
            purpose="Recall a fact about the user",
            capabilities=["recall", "remember", "user_fact"],
            required_perception=1,
            risk_level=RiskLevel.LOW,
            executor=MemoryExecutor(),
            verification_methods=[VerificationMethod.CUSTOM],
        ))
        
        self.register(Skill(
            name="remember_about_user",
            purpose="Store a fact about the user",
            capabilities=["store_fact", "user_memory"],
            required_perception=1,
            risk_level=RiskLevel.LOW,
            executor=MemoryExecutor(),
            verification_methods=[VerificationMethod.CUSTOM],
        ))
        
        self.register(Skill(
            name="search_conversation_history",
            purpose="Search past conversation messages",
            capabilities=["history", "conversation", "past_messages"],
            required_perception=1,
            risk_level=RiskLevel.LOW,
            executor=MemoryExecutor(),
            verification_methods=[VerificationMethod.CUSTOM],
        ))
        
        # Reminders
        self.register(Skill(
            name="add_reminder",
            purpose="Create a reminder",
            capabilities=["remind", "reminder", "schedule"],
            required_perception=1,
            risk_level=RiskLevel.LOW,
            executor=ReminderExecutor(),
            verification_methods=[VerificationMethod.CUSTOM],
        ))
        
        self.register(Skill(
            name="list_reminders",
            purpose="List active reminders",
            capabilities=["reminders", "show_reminders"],
            required_perception=1,
            risk_level=RiskLevel.LOW,
            executor=ReminderExecutor(),
            verification_methods=[VerificationMethod.CUSTOM],
        ))
    
    def register(self, skill: Skill) -> None:
        """Register a skill."""
        self._skills[skill.name] = skill
    
    def get(self, name: str) -> Optional[Skill]:
        """Get a skill by name."""
        return self._skills.get(name)
    
    def find_by_capability(self, capability: str) -> list[Skill]:
        """Find skills that provide a capability."""
        cap = capability.lower()
        return [s for s in self._skills.values() if cap in [c.lower() for c in s.capabilities]]
    
    def find_by_intent(self, intent: str) -> list[Skill]:
        """Find skills matching an intent (simple keyword matching)."""
        intent_lower = intent.lower()
        matches = []
        for skill in self._skills.values():
            if any(cap in intent_lower for cap in skill.capabilities):
                matches.append(skill)
            elif skill.purpose.lower() in intent_lower:
                matches.append(skill)
        return matches
    
    def all(self) -> list[Skill]:
        """Get all skills."""
        return list(self._skills.values())


# Global registry instance
_registry = SkillRegistry()


def get_skill_registry() -> SkillRegistry:
    """Get the global skill registry."""
    return _registry


def register_skill(skill: Skill) -> None:
    """Register a custom skill."""
    _registry.register(skill)