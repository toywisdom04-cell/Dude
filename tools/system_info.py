"""System info tools: battery, CPU/RAM/disk, network, IP, location, time, jokes."""
import datetime
import random

from ..platform.registry import get_platform
from .registry import ToolResult, register_tool

_platform = get_platform()


@register_tool("tell_time", "Tell the current date and time", permission="allow")
def tell_time() -> ToolResult:
    now = datetime.datetime.now()
    return ToolResult(True, now.strftime("It is %I:%M %p on %A, %B %d, %Y"))


@register_tool("system_info", "Get CPU, RAM, disk, battery and OS info", permission="allow")
def system_info() -> ToolResult:
    info = _platform.system_info()
    return ToolResult(True, "System info retrieved", data=info)


@register_tool("battery", "Get battery charge percentage", permission="allow")
def battery() -> ToolResult:
    info = _platform.system_info()
    if "battery_percent" not in info:
        return ToolResult(True, "This system does not report battery status.")
    pct = info["battery_percent"]
    if pct >= 75:
        tip = "we have enough power to keep going."
    elif pct >= 50:
        tip = "consider plugging in soon."
    elif pct >= 20:
        tip = "power is draining, please connect to charging."
    else:
        tip = "please charge now, the system may shut down soon."
    return ToolResult(True, f"Battery is at {pct}%. {tip}", data=info)


@register_tool("is_online", "Check internet connectivity", permission="allow")
def is_online() -> ToolResult:
    online = _platform.is_online()
    return ToolResult(True, "Connected" if online else "Offline", data=online)


@register_tool("my_ip", "Get the public IP address", permission="allow")
def my_ip() -> ToolResult:
    import requests
    try:
        ip = requests.get("https://api.ipify.org", timeout=10).text
        return ToolResult(True, f"Your IP address is {ip}", data=ip)
    except Exception as exc:
        return ToolResult(False, f"Could not determine IP: {exc}")


@register_tool("tell_joke", "Tell a random joke", permission="allow")
def tell_joke() -> ToolResult:
    jokes = [
        "Why do programmers prefer dark mode? Because light attracts bugs.",
        "Why did the developer go broke? Because he used up all his cache.",
        "There are only 10 types of people: those who understand binary and those who don't.",
        "Why was the computer cold? Because it left its Windows open.",
        "I told my computer I needed a break, now it won't stop sending me KitKat ads.",
    ]
    return ToolResult(True, random.choice(jokes))


@register_tool("weather", "Get current weather for a city", permission="allow")
def weather(city: str = "") -> ToolResult:
    import requests
    from bs4 import BeautifulSoup
    query = city or "current location"
    try:
        r = requests.get(f"https://www.google.com/search?q=temperature+in+{query}",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        temp = soup.find("div", class_="BNeawe").text
        return ToolResult(True, f"Current temperature in {query}: {temp}")
    except Exception:
        return ToolResult(False, "Could not fetch weather right now.")

