"""Schedule & reminders tools backed by the memory store."""
import datetime
import re

from ..memory.store import MemoryStore
from .registry import ToolResult, register_tool

_store: MemoryStore | None = None


def _init_store() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store


def set_store(store: MemoryStore) -> None:
    """Inject the agent's memory store so schedule tools share it."""
    global _store
    _store = store


def _parse_time(text: str) -> str | None:
    """Parse a human time expression into ISO datetime, or None.

    Accepts: '3pm', '15:30', '3:15 pm', 'in 5 minutes', 'tomorrow 9am',
    'thursday 10 am'.
    """
    text = text.strip().lower()
    now = datetime.datetime.now()

    # relative: "in 5 minutes / 2 hours"
    m = re.search(r"in\s+(\d+)\s+(minute|hour|second)s?", text)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("min"):
            return (now + datetime.timedelta(minutes=n)).isoformat()
        if unit.startswith("hour"):
            return (now + datetime.timedelta(hours=n)).isoformat()
        return (now + datetime.timedelta(seconds=n)).isoformat()

    # "tomorrow 9am" / "thursday 10 am"
    weekday_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                   "friday": 4, "saturday": 5, "sunday": 6}
    day = None
    if "tomorrow" in text:
        day = (now + datetime.timedelta(days=1)).date()
    else:
        for name, idx in weekday_map.items():
            if name in text:
                days_ahead = (idx - now.weekday()) % 7
                day = (now + datetime.timedelta(days=days_ahead)).date()
                break
    if day is None:
        day = now.date()

    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        meridiem = m.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        when = datetime.datetime(day.year, day.month, day.day, hour, minute)
        # a clock time already passed today rolls forward to the next
        # occurrence so reminders are always set in the future
        if when <= now and day == now.date():
            when += datetime.timedelta(days=1)
        return when.isoformat()
    return None


@register_tool("set_reminder", "Set a reminder. Args: title, at (natural time), message",
               permission="allow")
def set_reminder(title: str, at: str, message: str = "") -> ToolResult:
    parsed = _parse_time(at)
    if not parsed:
        return ToolResult(False, f"Could not understand the time '{at}'. "
                                 "Try '3pm', '15:30', or 'in 5 minutes'.")
    rid = _init_store().add_reminder(title, message, parsed)
    return ToolResult(True, f"Reminder set for {at} (id {rid})", data={"id": rid})


@register_tool("list_reminders", "List upcoming reminders", permission="allow")
def list_reminders(days: int = 1) -> ToolResult:
    rows = _init_store().upcoming_reminders(days=days)
    if not rows:
        return ToolResult(True, "No upcoming reminders")
    lines = [f"{r['remind_at']} - {r['title']}" for r in rows]
    return ToolResult(True, "\n".join(lines), data=rows)


@register_tool("complete_reminder", "Mark a reminder as done", permission="allow")
def complete_reminder(reminder_id: int) -> ToolResult:
    _init_store().complete_reminder(reminder_id)
    return ToolResult(True, f"Reminder {reminder_id} completed")


@register_tool("daily_agenda", "Summarize today's agenda", permission="allow")
def daily_agenda() -> ToolResult:
    rows = _init_store().upcoming_reminders(days=1)
    if not rows:
        return ToolResult(True, "Your calendar is clear for today.")
    lines = [f"{r['remind_at'][11:16]} - {r['title']}" for r in rows]
    return ToolResult(True, "Today's agenda:\n" + "\n".join(lines), data=rows)

