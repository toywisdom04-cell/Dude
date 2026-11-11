import os
import platform as pf
import shutil
import subprocess
import time


class Platform:
    name = "unknown"

    def open_path(self, path):
        raise NotImplementedError

    def open_url(self, url):
        raise NotImplementedError

    def resolve_app(self, name):
        raise NotImplementedError

    def launch_app(self, name):
        raise NotImplementedError

    def foreground_window(self):
        return {"app": "unknown", "title": "unknown"}

    def set_volume(self, level):
        raise NotImplementedError

    def notify(self, title, message):
        pass

    def autostart_install(self, command):
        raise NotImplementedError

    def autostart_remove(self):
        raise NotImplementedError


class Windows(Platform):
    name = "windows"
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
        "mail": "outlook.exe", "calendar": "outlook.exe", "calculator app": "calc.exe",
        "anydesk": "AnyDesk.exe", "teamviewer": "TeamViewer.exe", "obs": "obs64.exe",
        "obs studio": "obs64.exe", "audacity": "audacity.exe", "python": "python.exe",
        "git bash": "git-bash.exe", "putty": "putty.exe", "recycle bin": "RecycleBin",
        "comet": r"C:\Program Files\Perplexity\Comet\Application\comet.exe",
        "comet browser": r"C:\Program Files\Perplexity\Comet\Application\comet.exe",
    }

    def open_path(self, path):
        os.startfile(path)

    def open_url(self, url):
        os.startfile(url)

    def launch_app(self, name):
        path = self.resolve_app(name)
        if path is None or not path:
            return True
        low = str(path)
        if low.startswith("ms-settings:"):
            subprocess.Popen(["cmd", "/c", "start", "", "ms-settings:"], shell=False)
        elif low.startswith("ms-windows-store:"):
            subprocess.Popen(["cmd", "/c", "start", "", "ms-windows-store:"], shell=False)
        elif low.startswith("appid:"):
            appid = low[len("appid:"):]
            subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + appid], shell=False)
        elif low == "RecycleBin":
            subprocess.Popen(["explorer.exe", "shell:RecycleBinFolder"], shell=False)
        elif low == "shell:AppsFolder":
            subprocess.Popen(["explorer.exe", low], shell=False)
        elif os.path.exists(low):
            os.startfile(low)
        else:
            subprocess.Popen(["cmd", "/c", "start", "", low], shell=False)
        return True

    _start_apps_cache = None
    _start_apps_ts = 0.0

    def _start_apps(self):
        """Return list of (display_name, app_id) from Get-StartApps, cached ~60s.
        Uses ConvertTo-Json so long AppIDs are never truncation-mangled."""
        now = time.time()
        if self._start_apps_cache and now - self._start_apps_ts < 60:
            return self._start_apps_cache
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-StartApps | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=30)
            out = []
            if r.stdout.strip():
                import json as _json

                data = _json.loads(r.stdout)
                for item in data:
                    name = item.get("Name") or item.get("name")
                    appid = item.get("AppID") or item.get("appid")
                    if name and appid:
                        out.append((str(name).strip(), str(appid).strip()))
            self._start_apps_cache = out
            self._start_apps_ts = now
        except Exception:
            out = self._start_apps_cache or []
        return out

    def _app_paths_registry(self, name):
        """Look up a bare name in the App Paths registry to get a real exe path."""
        import winreg
        exe_name = name if name.lower().endswith((".exe", ".bat", ".cmd")) else name + ".exe"
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + exe_name)
                val, _ = winreg.QueryValueEx(key, None)
                winreg.CloseKey(key)
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
        for disp, appid in self._start_apps():
            if low in disp.lower() or disp.lower().split()[0] == low:
                if appid.lower().endswith(".exe") and os.path.exists(appid):
                    return appid
                return "appid:" + appid
        return low

    def foreground_window(self):
        try:
            import win32gui
            import win32process
            import psutil

            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            try:
                app = psutil.Process(pid).name()
            except Exception:
                app = "unknown"
            return {"app": app, "title": title}
        except Exception:
            return {"app": "unknown", "title": "unknown"}

    def set_volume(self, level):
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = interface.QueryInterface(IAudioEndpointVolume)
        vol.SetMasterVolumeLevelScalar(max(0, min(100, level)) / 100.0, None)

    def notify(self, title, message):
        ps = (f"[Windows.Forms.MessageBox]::Show('{message}', '{title}', "
              "'OK', 'Information')")
        try:
            subprocess.Popen(["powershell", "-NoProfile", "-Command",
                              "Add-Type -AssemblyName System.Windows.Forms; " + ps],
                             creationflags=0x08000000)
        except Exception:
            pass

    def autostart_install(self, command):
        exe, args = command[0], command[1:]
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        quoted = f'"{exe}"' + "".join(f' "{a}"' for a in args)
        winreg.SetValueEx(key, "DUDE Assistant", 0, winreg.REG_SZ, quoted)
        winreg.CloseKey(key)
        return True

    def autostart_remove(self):
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, "DUDE Assistant")
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            return False


class MacOS(Platform):
    name = "macos"

    def open_path(self, path):
        subprocess.Popen(["open", path])

    def open_url(self, url):
        subprocess.Popen(["open", url])

    def launch_app(self, name):
        r = subprocess.run(["open", "-Ra", name], capture_output=True)
        if r.returncode == 0:
            subprocess.Popen(["open", "-a", name])
            return True
        return False

    def resolve_app(self, name):
        return name

    def foreground_window(self):
        script = ('tell application "System Events" to get name of '
                  'first application process whose frontmost is true')
        try:
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=3)
            return {"app": r.stdout.strip() or "unknown", "title": ""}
        except Exception:
            return {"app": "unknown", "title": "unknown"}

    def set_volume(self, level):
        subprocess.Popen(["osascript", "-e", f"set volume output volume {int(level)}"])

    def autostart_install(self, command):
        plist_dir = os.path.expanduser("~/Library/LaunchAgents")
        os.makedirs(plist_dir, exist_ok=True)
        plist = os.path.join(plist_dir, "com.dude.assistant.plist")
        prog_args = "".join(f"<string>{c}</string>" for c in command)
        xml = (f"<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
               f"<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" "
               f"'http://www.apple.com/DTDs/PropertyList-1.0.dtd'>"
               f"<plist version=\"1.0\"><dict>"
               f"<key>Label</key><string>com.dude.assistant</string>"
               f"<key>ProgramArguments</key><array>{prog_args}</array>"
               f"<key>RunAtLoad</key><true/></dict></plist>")
        with open(plist, "w") as f:
            f.write(xml)
        return True

    def autostart_remove(self):
        plist = os.path.expanduser("~/Library/LaunchAgents/com.dude.assistant.plist")
        if os.path.exists(plist):
            os.remove(plist)
            return True
        return False


class Linux(Platform):
    name = "linux"

    def open_path(self, path):
        subprocess.Popen(["xdg-open", path])

    def open_url(self, url):
        subprocess.Popen(["xdg-open", url])

    def launch_app(self, name):
        exe = self.resolve_app(name)
        if exe:
            subprocess.Popen([exe])
            return True
        return False

    def resolve_app(self, name):
        found = shutil.which(name)
        if found:
            return found
        for d in ("/usr/share/applications", os.path.expanduser("~/.local/share/applications")):
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    if fn.startswith(name.lower()) and fn.endswith(".desktop"):
                        with open(os.path.join(d, fn), errors="ignore") as f:
                            for line in f:
                                if line.startswith("Exec="):
                                    return line[5:].split()[0]
        return None

    def foreground_window(self):
        try:
            r = subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                               capture_output=True, text=True, timeout=2)
            return {"app": "X11", "title": r.stdout.strip()}
        except Exception:
            return {"app": "unknown", "title": "unknown"}

    def set_volume(self, level):
        subprocess.Popen(["amixer", "-D", "pulse", "sset", "Master", f"{int(level)}%"])

    def autostart_install(self, command):
        d = os.path.expanduser("~/.config/autostart")
        os.makedirs(d, exist_ok=True)
        entry = os.path.join(d, "dude-assistant.desktop")
        exec_str = " ".join(command)
        with open(entry, "w") as f:
            f.write(f"[Desktop Entry]\nType=Application\nName=DUDE Assistant\nExec={exec_str}\n"
                    f"X-GNOME-Autostart-enabled=true\n")
        return True

    def autostart_remove(self):
        p = os.path.expanduser("~/.config/autostart/dude-assistant.desktop")
        if os.path.exists(p):
            os.remove(p)
            return True
        return False


def get_platform():
    s = pf.system().lower()
    if s == "windows":
        return Windows()
    if s == "darwin":
        return MacOS()
    if s == "linux":
        return Linux()
    raise RuntimeError(f"Unsupported OS: {s}")
