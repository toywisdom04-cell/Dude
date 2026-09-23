# Import tool modules so their @register_tool decorators execute.
# This must happen before any dispatch.
from . import (
    communication,  # noqa: F401
    desktop,  # noqa: F401
    filesystem,  # noqa: F401
    schedule,  # noqa: F401
    shell,  # noqa: F401
    system_info,  # noqa: F401
)
from .registry import ToolRegistry, ToolResult, get_registry, register_tool

__all__ = ["ToolRegistry", "ToolResult", "register_tool", "get_registry"]

