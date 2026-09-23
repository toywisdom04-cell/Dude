"""Linux platform adapter using xdg-open, desktop files, and dbus."""
import os
import shutil
import subprocess
from pathlib import Path

from .base import PlatformAdapter


def _run(args, timeout=20) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


class LinuxAdapter(PlatformAdapter):
    name = "linux"

    def _desktop_files(self) -> list[Path]:
        dirs = [
            Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")) / "applications",
            Path("/usr/share/applications"),
            Path("/usr/local/share/applications"),
        ]
        files = []
        for d in dirs:
            if d.exists():
                files.extend(d.glob("*.desktop"))
        return files

    # ---- apps ----
    def open_app(self, app_name: str) -> bool:
        # exact match on desktop file Name= or Exec=
        for df in self._desktop_files():
            try:
                name, exec_line = self._parse_desktop(df)
            except Exception:
                continue
            if app_name.lower() in (name.lower(), Path(exec_line.split()[0]).name.lower()):
                cmd = exec_line.replace("%u", "").replace("%U", "").replace("%f", "").replace("%F", "").strip()
                result = _run(["bash", "-c", f"{cmd} &"])
                if result.returncode == 0:
                    return True
        # fall back to xdg-open (works for paths and some app names)
        result = _run(["xdg-open", app_name])
        return result.returncode == 0

    @staticmethod
    def _parse_desktop(path: Path) -> tuple[str, str]:
        name, exec_line = "", ""
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("Name=") and not name:
                name = line.split("=", 1)[1]
            elif line.startswith("Exec=") and not exec_line:
                exec_line = line.split("=", 1)[1]
        return name, exec_line

    def close_app(self, app_name: str) -> bool:
        result = _run(["pkill", "-f", app_name])
        return result.returncode == 0

    def find_installed_apps(self) -> list[str]:
        names = []
        for df in self._desktop_files():
            try:
                name, _ = self._parse_desktop(df)
            except Exception:
                continue
            if name:
                names.append(name)
        return names

    # ---- windows ----
    def list_windows(self) -> list[str]:
        result = _run(["wmctrl", "-l"])
        if result.returncode != 0:
            return []
        return [line.split(None, 3)[-1] for line in result.stdout.splitlines() if len(line.split()) >= 4]

    def focus_window(self, title_part: str) -> bool:
        result = _run(["wmctrl", "-a", title_part])
        return result.returncode == 0

    def minimize_all(self) -> None:
        _run(["wmctrl", "-k", "on"])

    # ---- filesystem ----
    def open_in_file_manager(self, path: str) -> bool:
        result = _run(["xdg-open", os.path.dirname(os.path.abspath(path)) if os.path.isfile(path) else os.path.abspath(path)])
        return result.returncode == 0

    # ---- media / volume ----
    def volume(self, level: int) -> bool:
        level = max(0, min(100, int(level)))
        sink = _run(["pactl", "get-default-sink"]).stdout.strip()
        if not sink:
            return False
        result = _run(["pactl", "set-sink-volume", sink, f"{level}%"])
        return result.returncode == 0

    def media_key(self, key: str) -> None:
        key_map = {
            "play_pause": "XF86AudioPlay",
            "next": "XF86AudioNext",
            "prev": "XF86AudioPrev",
            "mute": "XF86AudioMute",
            "volup": "XF86AudioRaiseVolume",
            "voldown": "XF86AudioLowerVolume",
        }
        sym = key_map.get(key)
        if not sym:
            return
        for tool in ("ydotool key", "xdotool key"):
            cmd = tool.split()
            if shutil.which(cmd[0]):
                _run(cmd + [sym])

    # ---- system ----
    def system_info(self) -> dict:
        import platform

        import psutil
        info = {
            "os": f"Linux {platform.release()}",
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
        _run(["systemctl", "poweroff"])

    def restart(self) -> None:
        _run(["systemctl", "reboot"])

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
        autostart = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "autostart"
        autostart.mkdir(parents=True, exist_ok=True)
        desktop = autostart / "dude.desktop"
        try:
            desktop.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=DUDE\n"
                f"Exec={sys_executable()} -m dude\n"
                "X-GNOME-Autostart-enabled=true\n",
                encoding="utf-8",
            )
            return True
        except OSError:
            return False

    def uninstall_autostart(self) -> bool:
        autostart = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "autostart"
        desktop = autostart / "dude.desktop"
        if desktop.exists():
            desktop.unlink()
            return True
        return False


def sys_executable() -> str:
    import sys
    return sys.executable

