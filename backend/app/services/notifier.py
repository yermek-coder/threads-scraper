"""Batched WhatsApp and SMTP notification delivery with an audit log."""

import asyncio
import html
import logging
from email.message import EmailMessage
from dataclasses import dataclass
from typing import Sequence

import aiosmtplib
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import config
from app.models import NotificationLog, Post
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SMTPConfig:
    """SMTP connection settings loaded once per notification operation."""

    host: str
    port: int
    username: str
    password: str
    sender: str
    start_tls: bool
    use_tls: bool


def _post_line(post: Post) -> str:
    """Format one post for plain-text notification channels."""
    author = f"@{post.author_username}" if post.author_username else "Unknown author"
    return f"{author}: {post.text.strip()}\n{post.permalink}".strip()


def _batch_text(posts: Sequence[Post], title: str) -> str:
    """Render a compact batch message suitable for WhatsApp and email."""
    sections = [title, ""]
    sections.extend(_post_line(post) for post in posts)
    return "\n\n".join(sections)


def _record(
    db: AsyncSession,
    channel: str,
    recipient: str,
    success: bool,
    error: str | None,
    post_id: int | None,
) -> None:
    db.add(
        NotificationLog(
            channel=channel,
            recipient=recipient,
            status="sent" if success else "failed",
            error=error,
            post_id=post_id,
        )
    )


async def _send_whatsapp(recipient: str, message: str) -> None:
    """Send a message through the private WhatsApp bridge."""
    url = f"{config.whatsapp_bridge_url.rstrip('/')}/api/send"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        response = await client.post(url, json={"phone": recipient, "message": message})
        response.raise_for_status()


async def _load_smtp_config(db: AsyncSession) -> SMTPConfig:
    """Load SMTP settings once using the caller's database session."""
    port = int(await get_setting(db, "smtp_port"))
    tls_enabled = bool(await get_setting(db, "smtp_use_tls"))
    return SMTPConfig(
        host=await get_setting(db, "smtp_host"),
        port=port,
        username=await get_setting(db, "smtp_user"),
        password=await get_setting(db, "smtp_pass"),
        sender=await get_setting(db, "smtp_from"),
        # Port 465 expects TLS from the start of the connection; port 587 uses
        # STARTTLS after the initial SMTP handshake.
        start_tls=tls_enabled and port != 465,
        use_tls=tls_enabled and port == 465,
    )


async def _send_email(
    recipient: str,
    subject: str,
    text_body: str,
    html_body: str,
    smtp: SMTPConfig,
) -> None:
    """Send a multipart email through SMTP with the port-appropriate TLS mode."""
    if not smtp.host or not smtp.sender:
        raise RuntimeError("SMTP host and sender address must be configured")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp.sender
    message["To"] = recipient
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    await aiosmtplib.send(
        message,
        hostname=smtp.host,
        port=smtp.port,
        username=smtp.username or None,
        password=smtp.password or None,
        start_tls=smtp.start_tls,
        use_tls=smtp.use_tls,
        timeout=30,
    )


async def send_posts(db: AsyncSession, posts: Sequence[Post], immediate: bool = False) -> int:
    """Deliver one batch to every configured recipient and persist each attempt."""
    if not posts:
        return 0
    whatsapp_recipients = await get_setting(db, "whatsapp_recipients")
    email_recipients = await get_setting(db, "email_recipients")
    smtp = await _load_smtp_config(db) if email_recipients else None
    await db.commit()
    title = "New Threads mention" if immediate else f"{len(posts)} new Threads post(s)"
    text_body = _batch_text(posts, title)
    escaped_posts = "".join(
        f'<li><p><strong>@{html.escape(post.author_username or "unknown")}</strong>: '
        f'{html.escape(post.text)}</p><p><a href="{html.escape(post.permalink, quote=True)}">Open post</a></p></li>'
        for post in posts
    )
    html_body = (
        "<html><body><h2>" + html.escape(title) + "</h2><ol>" + escaped_posts + "</ol></body></html>"
    )
    async def deliver(channel: str, recipient: str) -> tuple[str, str, str | None]:
        try:
            if channel == "whatsapp":
                await _send_whatsapp(recipient, text_body)
            else:
                assert smtp is not None
                await _send_email(recipient, "Threads monitoring alert", text_body, html_body, smtp)
            return channel, recipient, None
        except Exception as exc:  # A failed destination must not block other destinations.
            logger.warning("Notification delivery failed (%s): %s", channel, exc)
            return channel, recipient, str(exc)[:2000]

    outcomes = await asyncio.gather(
        *(deliver("whatsapp", str(recipient)) for recipient in whatsapp_recipients),
        *(deliver("email", str(recipient)) for recipient in email_recipients),
    )
    sent = 0
    for channel, recipient, error in outcomes:
        success = error is None
        _record(db, channel, recipient, success, error, posts[0].id)
        sent += int(success)
    await db.commit()
    return sent


async def send_test_notification(db: AsyncSession, channel: str) -> int:
    """Send a configuration check to all recipients in the chosen channel."""
    recipients_key = "email_recipients" if channel == "email" else "whatsapp_recipients"
    recipients = await get_setting(db, recipients_key)
    if not recipients:
        raise ValueError(f"No {channel} recipients are configured")
    smtp = await _load_smtp_config(db) if channel == "email" else None
    await db.commit()
    text_body = "Threads Brand Monitoring test notification. Delivery is configured correctly."
    count = 0
    for recipient in recipients:
        try:
            if channel == "whatsapp":
                await _send_whatsapp(str(recipient), text_body)
            else:
                assert smtp is not None
                await _send_email(
                    str(recipient),
                    "Threads monitoring test",
                    text_body,
                    "<html><body><p>Threads Brand Monitoring test notification. Delivery is configured correctly.</p></body></html>",
                    smtp,
                )
            _record(db, channel, str(recipient), True, None, None)
            count += 1
        except Exception as exc:
            _record(db, channel, str(recipient), False, str(exc)[:2000], None)
            logger.warning("Test notification failed (%s): %s", channel, exc)
    await db.commit()
    if count == 0:
        raise RuntimeError(f"Test notification failed for every {channel} recipient")
    return count
