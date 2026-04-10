from __future__ import annotations

import asyncio
import datetime as dt
from email.message import EmailMessage
from email.utils import formataddr
import smtplib
import ssl
from urllib.parse import quote_plus

from app.config_loader import SmtpConfig


def _build_reset_link(config: SmtpConfig, code: str, email: str) -> str | None:
    template = (config.reset_url_template or "").strip()
    if not template:
        return None
    link = template
    has_email_placeholder = "{email}" in link
    if has_email_placeholder:
        link = link.replace("{email}", quote_plus(email))
    has_code_placeholder = "{code}" in link
    if has_code_placeholder:
        link = link.replace("{code}", quote_plus(code))
    if "{token}" in template:
        # Backward compatibility for older template key.
        link = link.replace("{token}", quote_plus(code))
        has_code_placeholder = True

    if not has_code_placeholder:
        separator = "&" if "?" in link else "?"
        link = f"{link}{separator}code={quote_plus(code)}"

    if not has_email_placeholder:
        separator = "&" if "?" in link else "?"
        link = f"{link}{separator}email={quote_plus(email)}"

    return link


def _build_reset_message(
    *,
    config: SmtpConfig,
    code: str,
    email: str,
    expires_at: dt.datetime,
) -> str:
    expires_text = expires_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    reset_link = _build_reset_link(config, code, email)
    lines = [
        "Your TeamClaw password reset request was received.",
        "",
        f"Verification code: {code}",
        f"Expires at: {expires_text}",
    ]
    if reset_link:
        lines.extend(["", f"Reset link: {reset_link}"])
    lines.extend(["", "If you did not request this, you can ignore this email."])
    return "\n".join(lines)


def _send_email_sync(
    *,
    config: SmtpConfig,
    to_email: str,
    subject: str,
    body: str,
) -> None:
    if not config.can_send or not config.host or not config.from_email:
        raise RuntimeError("SMTP is not fully configured")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((config.from_name, config.from_email))
    message["To"] = to_email
    if config.reply_to:
        message["Reply-To"] = config.reply_to
    message.set_content(body)

    if config.use_ssl:
        with smtplib.SMTP_SSL(config.host, config.port, timeout=config.timeout) as client:
            if config.username:
                client.login(config.username, config.password or "")
            client.send_message(message)
        return

    with smtplib.SMTP(config.host, config.port, timeout=config.timeout) as client:
        client.ehlo()
        if config.use_tls:
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if config.username:
            client.login(config.username, config.password or "")
        client.send_message(message)


async def send_password_reset_email(
    *,
    config: SmtpConfig,
    to_email: str,
    code: str,
    expires_at: dt.datetime,
) -> None:
    subject = config.reset_subject or "TeamClaw Password Reset"
    body = _build_reset_message(config=config, code=code, email=to_email, expires_at=expires_at)
    await asyncio.to_thread(
        _send_email_sync,
        config=config,
        to_email=to_email,
        subject=subject,
        body=body,
    )
