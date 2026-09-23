"""Platform adapter selection based on the running OS."""
import platform

from .base import PlatformAdapter


def detect_os() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    return "linux"


def get_platform() -> PlatformAdapter:
    os_name = detect_os()
    if os_name == "windows":
        from .windows import WindowsAdapter
        return WindowsAdapter()
    if os_name == "macos":
        from .macos import MacOSAdapter
        return MacOSAdapter()
    from .linux import LinuxAdapter
    return LinuxAdapter()


def supported_os() -> list[str]:
    return ["windows", "macos", "linux"]

