"""Tool registry: a catalog of actions the agent can perform.

Each tool is a callable with metadata. The Brain Router and offline skills
both dispatch through the registry, and every invocation goes through the
PermissionGate before executing.
"""
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolResult:
    ok: bool
    message: str
    data: Any = None

    def __bool__(self):
        return self.ok


ToolFn = Callable[..., ToolResult]


@dataclass
class Tool:
    name: str
    description: str
    fn: ToolFn
    permission: str = "allow"  # allow | ask
    parameters: dict = field(default_factory=dict)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def execute(self, name: str, gate=None, **kwargs) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            return ToolResult(False, f"Unknown tool: {name}")
        if gate is not None and not gate.request(
            tool.name, tool.description, force=(tool.permission == "ask")
        ):
            return ToolResult(False, "Permission denied by user.")
        try:
            return tool.fn(**kwargs)
        except Exception as exc:  # noqa: BLE001 - surface cleanly to user
            return ToolResult(False, f"Tool '{name}' failed: {exc}")


_registry = ToolRegistry()


def register_tool(name, description, permission="allow", parameters=None):
    """Decorator to register a tool function into the global registry."""
    def decorator(fn: ToolFn) -> ToolFn:
        _registry.register(Tool(
            name=name,
            description=description,
            fn=fn,
            permission=permission,
            parameters=parameters or {},
        ))
        return fn
    return decorator


def get_registry() -> ToolRegistry:
    return _registry

