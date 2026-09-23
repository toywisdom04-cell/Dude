"""Autostart registration - thin convenience wrappers over the platform
adapter's install/uninstall methods."""
from ..platform.registry import get_platform


def enable() -> bool:
    return get_platform().install_autostart()


def disable() -> bool:
    return get_platform().uninstall_autostart()


def status() -> bool:
    """Best-effort: report whether autostart is configured.

    This is advisory only; exact detection differs per OS.
    """
    return False  # advisory only; treat as unknown

