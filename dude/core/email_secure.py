import datetime
import os
import smtplib
from email.mime.text import MIMEText

from core.config import get_config


def send_email_via_gmail(memory, broker, to_addr, subject, body):
    cfg = get_config()
    vault = cfg.get("vault_path", default="")
    if not vault or not os.path.exists(vault):
        return "ERROR: no credential vault configured on this machine."
    raw = ""
    try:
        with open(vault, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
    except OSError as e:
        return f"ERROR: cannot read credentials ({e})"
    import re

    m = re.search(r"Google app password[^\n]*?:\s*([a-z ]{15,25})", raw)
    if not m:
        return "ERROR: no Google app password found in your local vault."
    password = m.group(1).replace(" ", "")

    from_user = re.search(r"([A-Za-z0-9._%+-]+@gmail\.com)", raw)
    sender = from_user.group(1) if from_user else ""

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_addr
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
        server.ehlo()
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, [to_addr], msg.as_string())
        server.close()
        result = f"Email sent to {to_addr}."
    except Exception as e:
        result = f"ERROR sending email: {e}"
    finally:
        password = ""
        raw = ""
        broker.wipe()
    return result
