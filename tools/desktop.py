"""Desktop control tools: apps, windows, media, volume, screenshots,
clipboard, keyboard/mouse automation, camera/mirror."""
import datetime
import os

from ..platform.registry import get_platform
from .registry import ToolResult, register_tool

_platform = get_platform()


@register_tool("open_app", "Open an application by name", permission="allow")
def open_app(app_name: str) -> ToolResult:
    ok = _platform.open_app(app_name)
    return ToolResult(ok, f"Opened {app_name}" if ok else f"Could not open {app_name}")


@register_tool("close_app", "Close an application by name", permission="allow")
def close_app(app_name: str) -> ToolResult:
    ok = _platform.close_app(app_name)
    return ToolResult(ok, f"Closed {app_name}" if ok else f"Could not close {app_name}")


@register_tool("list_apps", "List installed applications", permission="allow")
def list_apps() -> ToolResult:
    apps = _platform.find_installed_apps()
    return ToolResult(True, f"Found {len(apps)} installed apps", data=apps)


@register_tool("list_windows", "List currently open windows", permission="allow")
def list_windows() -> ToolResult:
    windows = _platform.list_windows()
    return ToolResult(True, f"{len(windows)} open windows", data=windows)


@register_tool("focus_window", "Bring a window to the foreground", permission="allow")
def focus_window(title: str) -> ToolResult:
    ok = _platform.focus_window(title)
    return ToolResult(ok, f"Focused window '{title}'" if ok else f"Window '{title}' not found")


@register_tool("minimize_all", "Minimize all windows and show the desktop", permission="allow")
def minimize_all() -> ToolResult:
    _platform.minimize_all()
    return ToolResult(True, "All windows minimized")


@register_tool("set_volume", "Set system volume 0-100", permission="allow")
def set_volume(level: int) -> ToolResult:
    ok = _platform.volume(int(level))
    return ToolResult(ok, f"Volume set to {level}%" if ok else "Could not set volume")


@register_tool("media_control", "Control media playback (play_pause/next/prev/mute)", permission="allow")
def media_control(key: str) -> ToolResult:
    _platform.media_key(key)
    return ToolResult(True, f"Sent media key {key}")


@register_tool("screenshot", "Take a screenshot and save it to a path", permission="allow")
def screenshot(path: str = "") -> ToolResult:
    try:
        import pyautogui
        if not path:
            name = f"screenshot_{datetime.datetime.now():%Y%m%d_%H%M%S}.png"
            path = os.path.join(os.getcwd(), name)
        img = pyautogui.screenshot()
        img.save(path)
        return ToolResult(True, f"Screenshot saved to {path}", data=path)
    except Exception as exc:
        return ToolResult(False, f"Screenshot failed: {exc}")


@register_tool("clipboard", "Read or write the system clipboard", permission="ask")
def clipboard(action: str = "read", content: str = "") -> ToolResult:
    try:
        import pyperclip
        if action == "write":
            pyperclip.copy(content)
            return ToolResult(True, "Clipboard updated")
        return ToolResult(True, "Clipboard read", data=pyperclip.paste())
    except Exception as exc:
        return ToolResult(False, f"Clipboard failed: {exc}")


@register_tool("type_text", "Type text into the focused window", permission="allow")
def type_text(text: str) -> ToolResult:
    try:
        import pyautogui
        pyautogui.write(text)
        return ToolResult(True, "Text typed")
    except Exception as exc:
        return ToolResult(False, f"Typing failed: {exc}")


@register_tool("open_mirror", "Open the camera mirror", permission="allow")
def open_mirror() -> ToolResult:
    from ..ui.camera import open_mirror as _open_mirror
    ok = _open_mirror()
    return ToolResult(ok, "Camera opened" if ok else "No camera available")


@register_tool("close_camera", "Close the camera", permission="allow")
def close_camera() -> ToolResult:
    from ..ui.camera import close_mirror as _close_mirror
    _close_mirror()
    return ToolResult(True, "Camera closed")

