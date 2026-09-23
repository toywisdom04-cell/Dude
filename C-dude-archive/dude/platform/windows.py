"""Windows platform adapter using win32 + PowerShell."""
import os
import subprocess
import sys

from .base import PlatformAdapter


class WindowsAdapter(PlatformAdapter):
    name = "windows"

    def _powershell(self, script: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=30,
        )

    # ---- apps ----
    def open_app(self, app_name: str) -> bool:
        try:
            os.startfile(app_name)  # noqa: S606 - app name may be a path/url
            return True
        except OSError:
            pass
        # Try a windows path resolution via powershell Start-Process
        result = self._powershell(
            f"Start-Process -FilePath '{app_name}' -ErrorAction Stop"
        )
        return result.returncode == 0

    def close_app(self, app_name: str) -> bool:
        result = subprocess.run(
            ["taskkill", "/IM", app_name, "/F"],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0

    def find_installed_apps(self) -> list[str]:
        result = self._powershell(
            "Get-StartApps | Select-Object -ExpandProperty Name"
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    # ---- windows ----
    def list_windows(self) -> list[str]:
        result = self._powershell(
            "(Get-Process | Where-Object {$_.MainWindowTitle}) "
            "| Select-Object -ExpandProperty MainWindowTitle"
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def focus_window(self, title_part: str) -> bool:
        script = (
            "$sig = @'"
            "[DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr hWnd);"
            "'@;"
            "$type = Add-Type -MemberDefinition $sig -Name Win32 -Namespace Native -PassThru;"
            f"$proc = Get-Process | Where-Object {{ $_.MainWindowTitle -like '*{title_part}*' }} "
            "| Select-Object -First 1;"
            "if ($proc) { $type::SetForegroundWindow($proc.MainWindowHandle); exit 0 } else { exit 1 }"
        )
        result = self._powershell(script)
        return result.returncode == 0

    def minimize_all(self) -> None:
        try:
            import pyautogui
            pyautogui.hotkey("win", "d")
        except Exception:
            self._powershell(
                "(New-Object -ComObject Shell.Application).MinimizeAll()"
            )

    # ---- filesystem ----
    def open_in_file_manager(self, path: str) -> bool:
        try:
            os.startfile(os.path.normpath(path))  # noqa: S606
            return True
        except OSError:
            return False

    # ---- media / volume ----
    def volume(self, level: int) -> bool:
        level = max(0, min(100, int(level)))
        script = (
            "$ErrorActionPreference='SilentlyContinue';"
            "$wshShell = New-Object -ComObject WScript.Shell;"
            f"for ($i=0; $i -lt {level}; $i++){{$wshShell.SendKeys([char]175)}};"
            f"for ($i=0; $i -lt {100 - level}; $i++){{$wshShell.SendKeys([char]174)}}"
        )
        result = self._powershell(script)
        return result.returncode == 0

    def media_key(self, key: str) -> None:
        try:
            import pyautogui
            mapping = {
                "play_pause": "playpause",
                "next": "nexttrack",
                "prev": "prevtrack",
                "mute": "volumemute",
                "volup": "volumeup",
                "voldown": "volumedown",
            }
            key_name = mapping.get(key)
            if key_name:
                pyautogui.press(key_name)
        except Exception:
            pass

    # ---- system ----
    def system_info(self) -> dict:
        import platform

        import psutil
        info = {
            "os": f"Windows {platform.release()}",
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
        os.system("shutdown /s /t 10")  # noqa: S605

    def restart(self) -> None:
        os.system("shutdown /r /t 10")  # noqa: S605

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
        subprocess.run(
            ["taskkill", "/IM", "WindowsCamera.exe", "/F"],
            capture_output=True, timeout=10,
        )

    # ---- autostart ----
    def install_autostart(self) -> bool:
        startup = os.path.join(
            os.environ.get("APPDATA", ""), "Microsoft", "Windows",
            "Start Menu", "Programs", "Startup",
        )
        if not os.path.isdir(startup):
            return False
        exe = os.path.abspath(sys.argv[0])
        link = os.path.join(startup, "DUDE.lnk")
        script = (
            "$ws = New-Object -ComObject WScript.Shell;"
            f"$s = $ws.CreateShortcut('{link}');"
            f"$s.TargetPath = '{exe}';"
            f"$s.WorkingDirectory = '{os.path.dirname(exe)}';"
            "$s.Save()"
        )
        result = self._powershell(script)
        return result.returncode == 0

    def uninstall_autostart(self) -> bool:
        startup = os.path.join(
            os.environ.get("APPDATA", ""), "Microsoft", "Windows",
            "Start Menu", "Programs", "Startup",
        )
        link = os.path.join(startup, "DUDE.lnk")
        if os.path.exists(link):
            os.remove(link)
            return True
        return False

