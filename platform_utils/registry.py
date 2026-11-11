"""Platform adapter selection based on the running OS."""
import sys

from .base import PlatformAdapter


def detect_os() -> str:
    # sys.platform is sufficient for adapter selection and does not invoke the
    # Windows `ver` subprocess that platform.system() may use during import.
    # Keeping this probe side-effect free is important for diagnostics and for
    # every module that imports the platform registry.
    system = sys.platform.lower()
    if system.startswith("win"):
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
