"""Shared SMTP helpers (HTML + plain text)."""
from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from services.async_utils import run_blocking

logger = logging.getLogger(__name__)


def smtp_configured() -> bool:
    return bool((os.getenv("SMTP_USERNAME") or "").strip() and (os.getenv("SMTP_PASSWORD") or "").strip())


def send_email_sync(
    to_email: str,
    subject: str,
    text_body: str,
    html_body: Optional[str] = None,
) -> bool:
    """Send one email via SMTP. Returns True on success."""
    username = (os.getenv("SMTP_USERNAME") or "").strip()
    password = (os.getenv("SMTP_PASSWORD") or "").strip()
    if not username or not password:
        logger.info("SMTP not configured — skip email to %s subject=%s", to_email, subject)
        return False

    smtp_server = (os.getenv("SMTP_SERVER") or "smtp.gmail.com").strip()
    smtp_port = int(os.getenv("SMTP_PORT") or "587")
    from_addr = (os.getenv("SMTP_FROM") or username).strip()
    from_name = (os.getenv("SMTP_FROM_NAME") or "Augusta Search").strip()

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    server = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
    try:
        server.starttls()
        server.login(username, password)
        server.sendmail(from_addr, [to_email], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return True


async def send_email(
    to_email: str,
    subject: str,
    text_body: str,
    html_body: Optional[str] = None,
) -> bool:
    return await run_blocking(send_email_sync, to_email, subject, text_body, html_body)
