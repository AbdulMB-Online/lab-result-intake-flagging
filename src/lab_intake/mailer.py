"""Sending a report by email.

The report is always printed first; email is a copy, never the only copy.
Connection details come from environment variables, which can be loaded
from a .env file that is never committed:

    LAB_SMTP_HOST      mail server (required to send)
    LAB_SMTP_PORT      default 587
    LAB_SMTP_USER      login, optional
    LAB_SMTP_PASSWORD  login, optional
    LAB_SMTP_STARTTLS  "1" (default) to upgrade the connection to TLS, "0" for a local test server
    LAB_MAIL_FROM      sender address (required to send)

Named mailer, not email, because Python's standard library already has an
email package and a module called email.py here would shadow it.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Mapping


class MailError(Exception):
    """The report could not be sent. The caller still has the printed copy."""


@dataclass(frozen=True)
class MailSettings:
    host: str
    sender: str
    port: int = 587
    user: str | None = None
    password: str | None = None
    starttls: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "MailSettings":
        """Read the LAB_SMTP_* and LAB_MAIL_FROM variables. Missing host or sender is an error."""
        env = os.environ if env is None else env
        host = env.get("LAB_SMTP_HOST", "").strip()
        sender = env.get("LAB_MAIL_FROM", "").strip()
        if not host or not sender:
            raise MailError("LAB_SMTP_HOST and LAB_MAIL_FROM must be set to send email")
        try:
            port = int(env.get("LAB_SMTP_PORT", "587"))
        except ValueError:
            raise MailError(f"LAB_SMTP_PORT must be a number, got {env['LAB_SMTP_PORT']!r}") from None
        return cls(
            host=host,
            sender=sender,
            port=port,
            user=env.get("LAB_SMTP_USER") or None,
            password=env.get("LAB_SMTP_PASSWORD") or None,
            starttls=env.get("LAB_SMTP_STARTTLS", "1").strip() != "0",
        )


def send_report(to: str, subject: str, body: str, settings: MailSettings) -> None:
    """Email *body* as plain text. Any failure is raised as MailError."""
    message = EmailMessage()
    message["From"] = settings.sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(settings.host, settings.port, timeout=30) as smtp:
            if settings.starttls:
                smtp.starttls(context=ssl.create_default_context())
            if settings.user:
                smtp.login(settings.user, settings.password or "")
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"could not send to {to} via {settings.host}:{settings.port}: {exc}") from exc


def load_env_file(path: str | Path, env: dict[str, str] | None = None) -> None:
    """Put KEY=VALUE lines from a .env file into the environment.

    Blank lines and # comments are skipped. A variable already set in the
    environment wins, so a real deployment can override the file.
    """
    env = os.environ if env is None else env
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("'\"")
        env.setdefault(key, value)
