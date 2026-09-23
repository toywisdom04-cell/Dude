"""Communication tools: email, WhatsApp, web browsing.

Email credentials are stored locally in config (never in git) and are used
ephemerally - never persisted to memory and never sent online beyond the
intended message.
"""
import smtplib
from email.mime.text import MIMEText

import requests

from .registry import ToolResult, register_tool


@register_tool("send_email", "Send an email via Gmail", permission="ask")
def send_email(to: str, subject: str, body: str,
               from_email: str = "", app_password: str = "") -> ToolResult:
    if not from_email or not app_password:
        return ToolResult(False, "Email credentials not configured. Set them in config.json.")
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.ehlo()
        server.starttls()
        server.login(from_email, app_password)
        server.sendmail(from_email, [to], msg.as_string())
        server.quit()
        return ToolResult(True, f"Email sent to {to}")
    except Exception as exc:
        return ToolResult(False, f"Email failed: {exc}")


@register_tool("open_website", "Open a website in the default browser", permission="allow")
def open_website(url: str) -> ToolResult:
    import webbrowser
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return ToolResult(True, f"Opened {url}")


@register_tool("web_search", "Search the web and return top results", permission="allow")
def web_search(query: str, num_results: int = 5) -> ToolResult:
    try:
        r = requests.get("https://www.google.com/search", params={"q": query},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        # Extremely lightweight extraction; full results handled by the LLM.
        import re
        titles = re.findall(r'<h3[^>]*>(.*?)</h3>', r.text)
        titles = [re.sub(r"<[^>]+>", "", t) for t in titles[:num_results]]
        return ToolResult(True, f"{len(titles)} results found", data=titles)
    except requests.RequestException as exc:
        return ToolResult(False, f"Search failed: {exc}")

