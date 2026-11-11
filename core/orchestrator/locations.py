"""Shell-accurate known folders (Phase 6).

"My Desktop" means the SHELL Desktop (what Explorer shows), which is
NOT necessarily %USERPROFILE%\\Desktop (OneDrive redirection moves it).
Every consumer (router location resolution, test namespaces) shares
this module so DUDE and its tests always mean the same place.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# KNOWNFOLDERID GUIDs (FOLDERID_Desktop, Documents, Downloads, ...).
_KNOWN_IDS = {
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C6}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "music": "{4BD8D571-6D19-48D3-BE97-42247B585E6C}",
    "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}

_FALLBACK_SUBDIR = {
    "desktop": "Desktop",
    "documents": "Documents",
    "downloads": "Downloads",
    "pictures": "Pictures",
    "music": "Music",
    "videos": "Videos",
}


def shell_known_folder(name: str) -> str:
    """Absolute path of a shell known folder (Desktop, Documents, ...).

    Queries the shell first (OneDrive redirection aware); falls back
    to %USERPROFILE%\\<Name> when the API is unavailable. Never raises:
    worst case returns the fallback path.
    """
    key = (name or "").strip().lower()
    subdir = _FALLBACK_SUBDIR.get(key, key or "Desktop")
    fallback = os.path.join(os.path.expanduser("~"), subdir)
    guid = _KNOWN_IDS.get(key)
    if not guid or os.name != "nt":
        return fallback if os.name == "nt" else os.path.join(
            os.path.expanduser("~"), subdir)
    try:
        import ctypes
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD),
                        ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD),
                        ("Data4", wintypes.BYTE * 8)]

        ole32 = ctypes.windll.ole32
        shell32 = ctypes.windll.shell32
        _GUID_from_string = getattr(ole32, "CLSIDFromString", None)
        path_ptr = wintypes.LPWSTR()
        rfid = _GUID()
        if _GUID_from_string is not None:
            if _GUID_from_string(guid, ctypes.byref(rfid)) != 0:
                return fallback
        else:
            return fallback
        hr = shell32.SHGetKnownFolderPath(
            ctypes.byref(rfid), 0, None, ctypes.byref(path_ptr))
        if hr != 0 or not path_ptr.value:
            return fallback
        try:
            return path_ptr.value
        finally:
            try:
                ole32.CoTaskMemFree(path_ptr)
            except Exception:
                pass
    except Exception as e:
        log.warning(f"shell folder lookup failed for {name!r}: {e}")
        return fallback
