"""Fast, regex-based intent detection.

Pure function module (no heavy imports) so it can be unit-tested in isolation and
reused by the DUDE command path for instant, model-free routing of common commands.
"""

import re
import shutil

_APP_NAMES = (
    "notepad|chrome|comet|calculator|calc|paint|cmd|terminal|powershell|explorer|"
    "browser|edge|firefox|brave|opera|vivaldi|vscode|code|word|excel|powerpoint|"
    "outlook|onenote|spotify|discord|steam|slack|zoom|vlc|obs|audacity|python|"
    "git\\s+bash|putty|task\\s+manager|command\\s+prompt|control\\s+panel|"
    "snipping\\s+tool|file\\s+explorer|photos|camera|whatsapp|anydesk|teamviewer|"
    "media\\s+player|microsoft\\s+edge|google\\s+chrome|visual\\s+studio\\s+code"
)

_FILE_EXTS = (
    "txt|md|pdf|docx?|xlsx?|pptx?|csv|json|py|js|ts|html|htm|log|rtf|zip|png|"
    "jpe?g|gif|bmp|mp3|wav|mp4|mkv"
)

_RE_CREATE_FILE = re.compile(r"create\s+file")
_RE_OPEN_FILE = re.compile(r"open\s+file")
_RE_OPEN_URL = re.compile(r"open\s+url")
_RE_OPEN_APP = re.compile(rf"\bopen\s+(?:the\s+)?({_APP_NAMES})\b")
_RE_BRING_TO_FRONT = re.compile(rf"\b(bring|show|place)\s+(?:the\s+)?({_APP_NAMES})\b", re.IGNORECASE)
_RE_CLOSE_APP = re.compile(rf"\bclose\s+(?:the\s+)?({_APP_NAMES})\b")
_RE_OPEN_PATH = re.compile(rf"\bopen\s+.+\.({_FILE_EXTS})\b")
_RE_OPEN_PATH_TOKEN = re.compile(r"\bopen\s+(?:[a-z]:[\\/]|\.{1,2}[\\/]|~[\\/])")
_RE_OPEN_BARE = re.compile(r"\bopen\s+(?:the\s+)?([a-z][a-z0-9 +.&'-]{1,24})")
_RE_CLOSE_BARE = re.compile(r"\bclose\s+(?:the\s+)?([a-z][a-z0-9 +.&'-]{1,24})")
_RE_POWERSHELL = re.compile(r"launch\s+powershell")
_RE_CREATE_FOLDER = re.compile(r"create\s+folder")
_RE_UI_ACTION = re.compile(r"(click|press|type|scroll|drag|hover)")
_RE_SCREENSHOT = re.compile(r"(screenshot|capture)")
_RE_MEDIA = re.compile(r"\b(volume|mute|unmute|vol|play|pause|skip|next track|previous track|media key)\b")
_RE_SCHEDULE = re.compile(r"(remind|routine|todo)")
_RE_COMPLEX = re.compile(r"(analyze|extract|process|convert|generate|create.*lead|excel|spreadsheet|report)")
_RE_PLANNING = re.compile(r"(plan|workflow|automate|script|macro)")
_RE_LATENCY_STATUS = re.compile(r"/latency\s*$")
_RE_LATENCY_REPORT = re.compile(r"/latency\s+report")

_WHEN_PATTERNS = (
    re.compile(r"\d{1,2}:\d{2}"),                                     # 14:30
    re.compile(r"\d{1,2}(?::\d{2})?\s*(am|pm)"),                      # 2:30pm, 9am
    re.compile(r"in \d+\s*(minute|min|hour|hr|second|sec)s?"),        # in 20 minutes
    re.compile(r"in (an?|half an?)\s*(hour|hr|minute|min|second|sec)s?"),  # in an hour / half an hour
    re.compile(r"tomorrow \d{1,2}:\d{2}"),                            # tomorrow 14:30
    re.compile(r"tomorrow (?:at )?\d{1,2}(?::\d{2})?\s*(am|pm)?"),    # tomorrow at 9am
)


def is_parseable_when(when):
    """True if the reminder 'when' string is one the local parser understands.

    Mirrors core.tools.parse_when's accepted shapes so the reminder fast-path
    only sets a real time and never silently defaults to 'now'.
    """
    w = (when or "").strip().lower()
    if not w:
        return False
    return any(p.fullmatch(w) for p in _WHEN_PATTERNS)


def _resolves_as_exe(name):
    """True if the app name resolves to a runnable executable on PATH.

    Used as the last fast-path app fallback so bare "open telegram" style
    commands that aren't in the curated list still route to open_app instead of
    burning a slow model round-trip. Cheap (PATH lookup only).
    """
    low = name.strip().strip("\"'`")
    if not low or len(low) > 24:
        return False
    for cand in (low, low + ".exe", low + ".com", low + ".cmd"):
        if shutil.which(cand):
            return True
    return False


def _bare_open_app(text, low):
    """Return 'open_app'/'close_app' for a bare 'open/close <word>' command that
    resolves to a real executable, else None (keep falling through)."""
    m = _RE_OPEN_BARE.search(low)
    if not m:
        return None
    target = m.group(1).strip()
    # Don't hijack non-app phrasings.
    if not target or re.search(r"\b(up|down|level|url|file|folder|link|page|tab)\b", target):
        return None
    verb = "open" if re.search(r"\bopen\s", low) else "close"
    if _resolves_as_exe(target):
        return "open_app" if verb == "open" else "close_app"
    return None


def parse_intent(text):
    """Return the best-matching fast-path intent label for a command string.

    Returns 'unknown' when no fast-path rule matches (falls through to the model).
    """
    low = text.strip().lower()
    if _RE_CREATE_FILE.search(low):
        return "create_file"
    if _RE_OPEN_FILE.search(low):
        return "open_file"
    if _RE_OPEN_URL.search(low):
        return "open_url"
    # File extension / path tokens win over app names: "open chrome.pdf" is a
    # file, not the Chrome browser. App-name matching must not swallow filenames.
    if _RE_OPEN_PATH.search(low) or _RE_OPEN_PATH_TOKEN.search(low):
        return "open_file"
    if _RE_OPEN_APP.search(low):
        return "open_app"
    if _RE_BRING_TO_FRONT.search(low):
        return "bring_to_front"
    if _RE_OPEN_FILE.search(low):
        return "open_file"
    if _RE_CLOSE_APP.search(low):
        return "close_app"
    bare = _bare_open_app(text, low)
    if bare:
        return bare
    if _RE_POWERSHELL.search(low):
        return "powershell"
    if _RE_CREATE_FOLDER.search(low):
        return "create_folder"
    if _RE_UI_ACTION.search(low):
        return "ui_action"
    if _RE_SCREENSHOT.search(low):
        return "screenshot"
    if _RE_MEDIA.search(low):
        return "media_control"
    if _RE_SCHEDULE.search(low):
        return "schedule"
    if _RE_COMPLEX.search(low):
        return "complex_reasoning"
    if _RE_PLANNING.search(low):
        return "planning"
    if _RE_LATENCY_STATUS.search(low):
        return "latency_status"
    if _RE_LATENCY_REPORT.search(low):
        return "latency_report"
    return "unknown"