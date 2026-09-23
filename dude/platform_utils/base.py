"""Base PlatformAdapter interface.

All OS-specific behavior lives behind this interface. The Agent Core and
tools only ever talk to PlatformAdapter - they never see OS-specific paths.
"""
from abc import ABC, abstractmethod


class PlatformAdapter(ABC):
    """Cross-platform abstraction over OS capabilities."""

    name = "base"

    # ---- apps ----
    @abstractmethod
    def open_app(self, app_name: str) -> bool:
        """Open an application by name. Returns True on success."""

    @abstractmethod
    def close_app(self, app_name: str) -> bool:
        """Close an application by name. Returns True on success."""

    @abstractmethod
    def find_installed_apps(self) -> list[str]:
        """Return a list of installed application names on this system."""

    # ---- windows ----
    @abstractmethod
    def list_windows(self) -> list[str]:
        """List open window titles."""

    @abstractmethod
    def focus_window(self, title_part: str) -> bool:
        """Bring a window matching title_part to the foreground."""

    @abstractmethod
    def minimize_all(self) -> None:
        """Minimize all windows / show desktop."""

    # ---- filesystem ----
    @abstractmethod
    def open_in_file_manager(self, path: str) -> bool:
        """Reveal path in the OS file manager."""

    # ---- media / volume ----
    @abstractmethod
    def volume(self, level: int) -> bool:
        """Set system volume to 0-100."""

    @abstractmethod
    def media_key(self, key: str) -> None:
        """Send a media key: play/pause, next, prev, mute, volup, voldown."""

    # ---- system ----
    @abstractmethod
    def system_info(self) -> dict:
        """Return basic system info dict (os, hostname, cpu, ram, disk, battery)."""

    @abstractmethod
    def is_online(self) -> bool:
        """True if an internet connection is available."""

    @abstractmethod
    def shutdown(self) -> None:
        """Shutdown the system."""

    @abstractmethod
    def restart(self) -> None:
        """Restart the system."""

    # ---- camera ----
    @abstractmethod
    def camera_stream(self) -> bool:
        """Open a camera/mirror stream. Returns True if a camera is available."""

    @abstractmethod
    def close_camera(self) -> None:
        """Stop any camera stream opened by camera_stream."""

    # ---- autostart ----
    @abstractmethod
    def install_autostart(self) -> bool:
        """Register DUDE to start with the system."""

    @abstractmethod
    def uninstall_autostart(self) -> bool:
        """Remove autostart registration."""

