"""Offline skill catalog: fast intent -> tool dispatch, no internet needed.

This replaces the old hardcoded if/elif command chain with a generalized,
data-driven matching system. Each skill declares keywords and an optional
parser; the router picks the best match.
"""
import re
from dataclasses import dataclass, field
from typing import Callable

from ..tools import ToolResult, get_registry  # noqa: F401


@dataclass
class Skill:
    name: str
    keywords: list[str]
    tool: str
    params: dict = field(default_factory=dict)  # static params
    parse: Callable[[str, "Skill"], dict] | None = None

    def matches(self, text: str) -> bool:
        return any(kw in text for kw in self.keywords)


# ---------------------------------------------------------------------------
# parse helpers
# ---------------------------------------------------------------------------
def _take_after(text: str, patterns: list[str]) -> str:
    """Extract the text after the first matched pattern."""
    for pat in patterns:
        idx = text.find(pat)
        if idx != -1:
            return text[idx + len(pat):].strip()
    return ""


def _default_parse(text: str, skill: Skill) -> dict:
    return dict(skill.params)


def _parse_open_app(text: str, skill: Skill) -> dict:
    app = _take_after(text, ["open ", "launch ", "start "])
    return {"app_name": app.strip() or "default"}


def _parse_search(text: str, skill: Skill) -> dict:
    query = _take_after(text, ["search for ", "search ", "look up ", "find "])
    return {"query": query}


_MEDIA_KEYS = [
    ("play music", "play_pause"), ("play song", "play_pause"), ("pause music", "play_pause"),
    ("next song", "next"), ("next track", "next"), ("previous song", "prev"),
    ("previous track", "prev"), ("mute", "mute"),
]


def _media_key(text: str) -> str:
    for kw, key in _MEDIA_KEYS:
        if kw in text:
            return key
    return "play_pause"


def _extract_volume(text: str) -> int:
    m = re.search(r"(\d{1,3})\s*(%|percent)", text)
    return int(m.group(1)) if m else 50


def _parse_focus(text: str, skill: Skill) -> dict:
    title = _take_after(text, ["focus ", "switch to ", "bring up "])
    return {"title": title}


def _site_url(text: str) -> str:
    sites = {
        "youtube": "https://www.youtube.com",
        "google": "https://www.google.com",
        "facebook": "https://www.facebook.com",
        "instagram": "https://www.instagram.com",
        "stack overflow": "https://stackoverflow.com",
        "stackoverflow": "https://stackoverflow.com",
        "github": "https://github.com",
    }
    for name, url in sites.items():
        if name in text:
            return url
    return "https://www.google.com"


def _parse_reminder(text: str, skill: Skill) -> dict:
    body = _take_after(text, ["remind me to ", "remind me that ", "set a reminder to ",
                              "set a reminder ", "set reminder to ", "add reminder "])
    # split "title at time" heuristically
    m = re.search(r"\s+(?:at|for)\s+", body)
    if m:
        title = body[:m.start()].strip()
        when = body[m.end():].strip()
        return {"title": title or "Reminder", "at": when, "message": ""}
    return {"title": body or "Reminder", "at": body, "message": ""}


# ---------------------------------------------------------------------------
# the skill catalog
# ---------------------------------------------------------------------------
SKILLS: list[Skill] = [
    # ---- system / time ----
    Skill("time", ["time", "what's the time", "whats the time"], "tell_time"),
    Skill("joke", ["joke"], "tell_joke"),
    Skill("battery", ["battery", "charge"], "battery"),
    Skill("system", ["system info", "cpu", "ram", "memory usage", "usage"], "system_info"),
    Skill("ip", ["ip address", "my ip"], "my_ip"),
    Skill("online", ["are you online", "internet", "connection"], "is_online"),
    Skill("weather", ["weather", "temperature"], "weather",
          parse=lambda t, s: {"city": _take_after(t, ["weather in ", "weather ", "temperature in "])}),
    Skill("screenshot", ["screenshot", "capture screen"], "screenshot"),
    Skill("mirror", ["mirror", "camera"], "open_mirror"),
    Skill("close_camera", ["close camera", "close the camera"], "close_camera"),
    Skill("minimize", ["minimize all", "show desktop", "minimise all"], "minimize_all"),
    Skill("volume_set", ["volume"], "set_volume",
          parse=lambda t, s: {"level": _extract_volume(t)}),
    Skill("media_play", ["play music", "play song", "pause music", "next song",
                         "previous song", "mute"], "media_control",
          parse=lambda t, s: {"key": _media_key(t)}),
    Skill("focus", ["focus ", "switch to ", "bring up "], "focus_window",
          parse=_parse_focus),
    Skill("list_windows", ["what windows", "open windows"], "list_windows"),
    Skill("list_apps", ["installed apps", "list apps", "what apps"], "list_apps"),

    # ---- web / apps ----
    Skill("open_app", ["open ", "launch ", "start "], "open_app",
          parse=_parse_open_app),
    Skill("close_app", ["close app", "close "], "close_app",
          parse=lambda t, s: {"app_name": _take_after(t, ["close app ", "close "])}),
    Skill("open_site", ["open youtube", "open google", "open facebook",
                        "open instagram", "open stack overflow"], "open_website",
          parse=lambda t, s: {"url": _site_url(t)}),

    # ---- files ----
    Skill("search_files", ["find file", "search file", "where is the file", "find the file"],
          "search_files",
          parse=lambda t, s: {"pattern": _take_after(t, ["find file ", "search file ", "where is the file ", "find the file "])}),
    Skill("list_dir", ["what's in", "whats in", "list folder", "list directory"], "list_directory",
          parse=lambda t, s: {"path": _take_after(t, ["what's in ", "whats in ", "list folder ", "list directory "]) or "."}),
    Skill("read_file", ["read file", "read the file"], "read_file",
          parse=lambda t, s: {"path": _take_after(t, ["read file ", "read the file "])}),
    Skill("open_folder", ["open folder", "open the folder"], "open_folder",
          parse=lambda t, s: {"path": _take_after(t, ["open folder ", "open the folder "])}),

    # ---- schedule ----
    Skill("reminder", ["remind me", "set a reminder", "set reminder", "add reminder"], "set_reminder",
          parse=_parse_reminder),
    Skill("agenda", ["agenda", "what's my day", "whats my day", "what is my day",
                     "my day look", "today's schedule", "today schedule",
                     "meetings today", "what do i have today",
                     "what do i have on today"], "daily_agenda"),
    Skill("list_reminders", ["show reminders", "my reminders", "pending reminders"], "list_reminders"),
]


class OfflineSkillRouter:
    """Match an utterance against the offline skill catalog."""

    def __init__(self, registry=None):
        self.registry = registry or get_registry()

    def dispatch(self, text: str):
        """Return a ToolResult if an offline skill matches, else None."""
        text = text.lower().strip()
        best = None
        best_score = 0
        for skill in SKILLS:
            if skill.matches(text):
                # score by total keyword length covered (rough specificity)
                score = sum(len(kw) for kw in skill.keywords if kw in text)
                if score > best_score:
                    best, best_score = skill, score
        if best is None:
            return None
        parser = best.parse or _default_parse
        params = parser(text, best)
        return self.registry.execute(best.tool, **params)

    def can_handle(self, text: str) -> bool:
        return any(skill.matches(text.lower()) for skill in SKILLS)

