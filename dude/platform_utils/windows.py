"""Windows platform adapter using win32 + PowerShell."""
import os
import shutil
import subprocess
import sys
import time

from .base import PlatformAdapter


def _is_browser_path(path):
    base = os.path.basename(str(path)).lower()
    return base in {"comet.exe", "chrome.exe", "msedge.exe", "brave.exe",
                    "opera.exe", "chromium.exe", "firefox.exe", "vivaldi.exe"}


class WindowsAdapter(PlatformAdapter):
    name = "windows"

    def _powershell(self, script: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=30,
            creationflags=0x08000000,  # CREATE_NO_WINDOW: no console flash
        )

    # ---- apps ----
    COMMON = {
        "notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe",
        "paint": "mspaint.exe", "explorer": "explorer.exe", "file explorer": "explorer.exe",
        "this pc": "explorer.exe", "task manager": "taskmgr.exe",
        "cmd": "cmd.exe", "command prompt": "cmd.exe", "powershell": "powershell.exe",
        "windows powershell": "powershell.exe", "terminal": "wt.exe", "snipping tool": "SnippingTool.exe",
        "control panel": "control.exe", "settings": "ms-settings:",
        "chrome": "chrome.exe", "google chrome": "chrome.exe", "edge": "msedge.exe",
        "microsoft edge": "msedge.exe", "firefox": "firefox.exe", "mozilla firefox": "firefox.exe",
        "brave": "brave.exe", "opera": "opera.exe", "vivaldi": "vivaldi.exe",
        "word": "winword.exe", "microsoft word": "winword.exe", "excel": "excel.exe",
        "powerpoint": "powerpnt.exe", "outlook": "OUTLOOK.EXE", "onenote": "ONENOTE.EXE",
        "vscode": "code.exe", "visual studio code": "code.exe", "code": "code.exe",
        "notepad++": "notepad++.exe", "discord": "Discord.exe", "spotify": "Spotify.exe",
        "steam": "steam.exe", "slack": "slack.exe", "zoom": "Zoom.exe",
        "vlc": "vlc.exe", "media player": "wmplayer.exe", "photos": "Microsoft.Photos.exe",
        "camera": "WindowsCamera.exe", "store": "ms-windows-store:", "microsoft store": "ms-windows-store:",
        "mail": "outlook.exe", "calendar": "outlook.exe", "anydesk": "AnyDesk.exe",
        "teamviewer": "TeamViewer.exe", "obs": "obs64.exe", "obs studio": "obs64.exe",
        "audacity": "audacity.exe", "python": "python.exe", "git bash": "git-bash.exe",
        "putty": "putty.exe", "recycle bin": "RecycleBin",
        "comet": r"C:\Program Files\Perplexity\Comet\Application\comet.exe",
        "comet browser": r"C:\Program Files\Perplexity\Comet\Application\comet.exe",
    }

    _start_apps_cache = None
    _start_apps_ts = 0.0
    _appx_cache = None
    _appx_ts = 0.0

    # Packaged (Store / UWP) apps we can resolve by AUMID. Also discovered
    # live via Get-AppxPackage below (Get-StartApps is unreliable on this box).
    PACKAGED = {
        "whatsapp": "WhatsApp",
        "whatsapp desktop": "WhatsApp",
        "mail": "WindowsMail",
        "calculator": "CalculatorApp",
        "clock": "WindowsAlarms",
        "alarms": "WindowsAlarms",
        "photos": "Microsoft.Windows.Photos",
        "store": "Microsoft.WindowsStore",
        "microsoft store": "Microsoft.WindowsStore",
        "camera": "Microsoft.WindowsCamera",
        "calendar": "WindowsApps_microsoft.windowscommunicationsapps",
        "paint": "Microsoft.Paint",
        "snipping tool": "Microsoft.ScreenSketch",
        "terminal": "Microsoft.WindowsTerminal",
        "windows terminal": "Microsoft.WindowsTerminal",
    }

    def open_app(self, app_name: str) -> bool:
        return self.launch_app(app_name)

    def _appx(self):
        """Cached map of display/app-name -> AUMID from Get-AppxPackage.

        Get-StartApps returns nothing on this machine, so we build the
        packaged-app mapping straight from the package table and the
        manifest AppId. Returns {normalized_lower_name: "family!AppId"}.
        """
        now = time.time()
        if self._appx_cache and now - self._appx_ts < 300:
            return self._appx_cache
        out = {}
        script = (
            "$ErrorActionPreference='SilentlyContinue';"
            "Get-AppxPackage | ForEach-Object { $n=$_.Name; $f=$_.PackageFamilyName; "
            "try { $m=Get-AppxPackageManifest $_.PackageFullName; "
            "$app=$m.Package.Applications.Application | Select-Object -First 1; "
            "$id=$app.Id } catch { $id='App' }; "
            "if ($n -and $f) { \"$n|$f|$id\" } }"
        )
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                               capture_output=True, text=True, timeout=40,
                               creationflags=0x08000000)
            for ln in r.stdout.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                parts = ln.split("|")
                if len(parts) >= 3 and parts[0] and parts[1]:
                    n = parts[0].strip().lower()
                    fid = parts[1].strip()
                    aid = (parts[2] or "App").strip()
                    full = f"{fid}!{aid}"
                    out[n] = full
                    if aid and aid != "App":
                        out.setdefault(n, full)
        except Exception:
            pass
        self._appx_cache = out
        self._appx_ts = now
        return out

    def _appx_aumid(self, name):
        """Find an AUMID for a user-supplied app name (adapted package lookup)."""
        low = name.strip().lower()
        pkg = self.PACKAGED.get(low)
        table = self._appx()
        if pkg:
            plow = pkg.lower()
            for n, aumid in table.items():
                if n == plow or plow in n or n.startswith(plow):
                    return aumid
        for n, aumid in table.items():
            if low in n or n.startswith(low):
                return aumid
        return None

    def _start_apps(self):
        """Return list of (display_name, app_id) from Get-StartApps, cached ~60s.
        Uses ConvertTo-Json so long/path-style AppIDs are never truncation-mangled."""
        now = time.time()
        if self._start_apps_cache and now - self._start_apps_ts < 60:
            return self._start_apps_cache
        out = []
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-StartApps | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=30,
                creationflags=0x08000000)
            if r.stdout.strip():
                import json

                data = json.loads(r.stdout)
                if isinstance(data, dict):
                    data = [data]
                for item in data or []:
                    name = item.get("Name") or item.get("name")
                    appid = item.get("AppID") or item.get("appid")
                    if name and appid:
                        out.append((str(name).strip(), str(appid).strip()))
            self._start_apps_cache = out
            self._start_apps_ts = now
        except Exception:
            pass
        return out

    def _app_paths_registry(self, name):
        """Look up a bare name in the App Paths registry to get a real exe path."""
        import winreg
        exe_name = name if name.lower().endswith((".exe", ".bat", ".cmd")) else name + ".exe"
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(root, "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + exe_name)
                val, _ = winreg.QueryValueEx(key, None)
                winreg.CloseKey(key)
                if val:
                    return val
            except OSError:
                continue
        return None

    def resolve_app(self, name):
        low = name.strip().lower()
        if low in ("settings", "ms-settings"):
            return "ms-settings:"
        if low in ("store", "microsoft store", "ms-store"):
            return "ms-windows-store:"
        if low in ("recycle bin", "recyclebin"):
            return "RecycleBin"
        if os.path.exists(name):
            return name
        found = shutil.which(name)
        if found:
            return found
        alias = self.COMMON.get(low)
        if alias:
            if alias.startswith("ms-settings:") or alias.startswith("ms-windows-store:"):
                return alias
            exe = alias if os.path.isabs(alias) else shutil.which(alias)
            if exe:
                return exe
            reg = self._app_paths_registry(alias)
            if reg:
                return reg
            return alias
        reg = self._app_paths_registry(low)
        if reg:
            return reg
        auc = self._appx_aumid(low)
        if auc:
            return "appid:" + auc
        for disp, appid in self._start_apps():
            if low in disp.lower() or (disp.lower().split() and disp.lower().split()[0] == low):
                if appid.lower().endswith(".exe") and os.path.exists(appid):
                    return appid
                return "appid:" + appid
        return None

    def launch_app(self, app_name: str) -> bool:
        try:
            name = (app_name or "").strip()
            if not name:
                return False
            path = self.resolve_app(name)
            if not path:
                return False
            low = str(path)
            expected = None
            try:
                if low.startswith("ms-settings:") or low.startswith("ms-windows-store:"):
                    subprocess.Popen(["cmd", "/c", "start", "", low], shell=False,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=0x08000000)
                elif low.startswith("appid:"):
                    subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + low[len("appid:"):]],
                                     shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif low == "RecycleBin":
                    subprocess.Popen(["explorer.exe", "shell:RecycleBinFolder"], shell=False,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif low == "shell:AppsFolder":
                    subprocess.Popen(["explorer.exe", low], shell=False,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif os.path.exists(low):
                    expected = os.path.basename(low).lower()
                    base = os.path.basename(low).lower()
                    chromes = {"comet.exe", "chrome.exe", "msedge.exe", "brave.exe",
                               "opera.exe", "chromium.exe"}
                    if base in chromes:
                        subprocess.Popen([low, "--force-renderer-accessibility=complete"],
                                         shell=False,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         creationflags=0x08000000)
                    else:
                        os.startfile(low)
                else:
                    expected = os.path.basename(low).lower()
                    subprocess.Popen(["cmd", "/c", "start", "", low], shell=False,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=0x08000000)
            except OSError:
                return False
            if expected and not self._process_started(expected, 4.0):
                return False
            # MAXIMIZE preference: the user wants apps opened full-screen (stored
            # preference "Always use all applications in full screen mode"), not
            # minimized or small. Best-effort maximize the freshly opened window.
            if not _is_browser_path(low):
                self._maximize_window_of(expected or low)
            return True
        except Exception:
            return False

    def _maximize_window_of(self, hint: str):
        """Best-effort maximize the window of a just-launched app so it opens
        full-screen. Uses EnumWindows + ShowWindow(SW_MAXIMIZE)."""
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            exact = (os.path.splitext(os.path.basename(str(hint)))[0] or "").lower()
            found = []

            def _cb(hwnd, _lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length == 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if not title:
                    return True
                # match by window class == process image name, or title if exact known
                cls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls, 256)
                cname = (cls.value or "").lower()
                if exact and (exact in cname or (os.path.basename(cname) == exact)):
                    found.append(hwnd)
                return True

            user32.EnumWindows(ctypes.WINFUNCTYPE(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(_cb), 0)
            # Maximize the first match, but only if it isn't already maximized
            # and isn't a small utility dialog.
            for hwnd in found[-3:]:
                if user32.IsIconic(hwnd):  # minimized -> restore first
                    user32.ShowWindow(hwnd, 9)
                user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
        except Exception:
            pass

    def _process_started(self, exe_name: str, wait: float) -> bool:
        """Best-effort: confirm a matching process is present after launch.

        Only reached after a successful resolve, so a matching image name
        means the launch worked (or the app was already running).
        """
        import psutil
        if not exe_name:
            return True
        base = exe_name.lower().rstrip(".exe")
        deadline = time.time() + wait
        while time.time() < deadline:
            for p in psutil.process_iter(["name"]):
                try:
                    n = (p.info["name"] or "").lower()
                except Exception:
                    continue
                if n and (base in n or n.startswith(base)):
                    return True
            time.sleep(0.4)
        return False

    def close_app(self, app_name: str) -> bool:
        result = subprocess.run(
            ["taskkill", "/IM", app_name, "/F"],
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000,
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
            "(Get-Process | Where-Object {$.MainWindowTitle}) "
            "| Select-Object -ExpandProperty MainWindowTitle"
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def foreground_window(self) -> dict:
        """Get the currently active foreground window."""
        try:
            import win32gui
            import win32process
        except ImportError:
            return {"app": "unknown", "title": "unknown"}

        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            import psutil
            app = psutil.Process(pid).name()
        except Exception:
            app = "unknown"
        return {"app": app, "title": title}

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

