"""macOS platform adapter using `open`, osascript, and AppleScript."""
import os
import plistlib
import subprocess
from pathlib import Path

from .base import PlatformAdapter


def _run(args, timeout=20) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


class MacOSAdapter(PlatformAdapter):
    name = "macos"

    def _osascript(self, script: str) -> subprocess.CompletedProcess:
        return _run(["osascript", "-e", script])

    # ---- apps ----
    def open_app(self, app_name: str) -> bool:
        result = _run(["open", "-a", app_name])
        if result.returncode == 0:
            return True
        # try a path
        result = _run(["open", app_name])
        return result.returncode == 0

    def close_app(self, app_name: str) -> bool:
        result = self._osascript(
            f'tell application "{app_name}" to quit'
        )
        return result.returncode == 0

    def find_installed_apps(self) -> list[str]:
        apps = []
        for base in ("/Applications", "/System/Applications"):
            try:
                for p in Path(base).iterdir():
                    if p.suffix == ".app":
                        apps.append(p.stem)
            except FileNotFoundError:
                pass
        return apps

    # ---- windows ----
    def list_windows(self) -> list[str]:
        script = (
            'tell application "System Events" to get name of '
            'every window of every process whose background only is false'
        )
        result = self._osascript(script)
        if result.returncode != 0:
            return []
        return [w.strip() for w in result.stdout.split(",") if w.strip()]

    def focus_window(self, title_part: str) -> bool:
        script = (
            'tell application "System Events" to set frontmost of '
            f'(first process whose name contains "{title_part}") to true'
        )
        result = self._osascript(script)
        return result.returncode == 0

    def minimize_all(self) -> None:
        self._osascript('tell application "System Events" to key code 3 using {command down}')

    # ---- filesystem ----
    def open_in_file_manager(self, path: str) -> bool:
        result = _run(["open", "-R" if os.path.isfile(path) else "-a", "Finder", path])
        if result.returncode != 0:
            result = _run(["open", path])
        return result.returncode == 0

    # ---- media / volume ----
    def volume(self, level: int) -> bool:
        level = max(0, min(100, int(level)))
        result = self._osascript(f"set volume output volume {level}")
        return result.returncode == 0

    def media_key(self, key: str) -> None:
        key_code = {"play_pause": 16, "next": 17, "prev": 18, "mute": 0}.get(key)
        if key_code is None:
            return
        self._osascript(
            f'tell application "System Events" to key code {key_code}'
        )

    # ---- system ----
    def system_info(self) -> dict:
        import platform

        import psutil
        info = {
            "os": f"macOS {platform.release()}",
            "hostname": platform.node(),
            "cpu_percent": psutil.cpu_percent(interval=0.2),
            "cpu_count": psutil.cpu_count(),
            "ram_percent": psutil.virtual_memory().percent,
            "ram_total_gb": round(psutil.virtual_memory().total / 1e9, 1),
        }
        try:
            battery = psutil.sensors_battery()
            if battery:
                info["battery_percent"] = int(battery.percent)
                info["battery_plugged"] = battery.power_plugged
        except Exception:
            pass
        try:
            info["disk_percent"] = psutil.disk_usage("/").percent
        except Exception:
            pass
        return info

    def is_online(self) -> bool:
        try:
            import socket
            socket.setdefaulttimeout(3)
            socket.create_connection(("1.1.1.1", 53))
            return True
        except OSError:
            return False

    def shutdown(self) -> None:
        _run(["osascript", "-e", 'tell app "System Events" to shut down'])

    def restart(self) -> None:
        _run(["osascript", "-e", 'tell app "System Events" to restart'])

    # ---- camera ----
    def camera_stream(self) -> bool:
        try:
            import cv2
            cam = cv2.VideoCapture(0)
            ok, _ = cam.read()
            cam.release()
            return ok
        except Exception:
            return False

    def close_camera(self) -> None:
        pass  # camera window handled by UI layer

    # ---- autostart ----
    def install_autostart(self) -> bool:
        launcher = Path.home() / "Library" / "LaunchAgents"
        launcher.mkdir(parents=True, exist_ok=True)
        plist = launcher / "com.dude.agent.plist"
        entry = {
            "Label": "com.dude.agent",
            "ProgramArguments": [sys_executable(), "-m", "dude"],
            "RunAtLoad": True,
            "KeepAlive": True,
        }
        try:
            plist.write_bytes(plistlib.dumps(entry))
            return True
        except OSError:
            return False

    def uninstall_autostart(self) -> bool:
        plist = Path.home() / "Library" / "LaunchAgents" / "com.dude.agent.plist"
        if plist.exists():
            plist.unlink()
            return True
        return False


def sys_executable() -> str:
    import sys
    return sys.executable

