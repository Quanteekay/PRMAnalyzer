"""
Email helpers. In development we log emails to console (MAIL_SUPPRESS_SEND=True),
in production set the SMTP env vars and they'll be sent for real.
"""

from __future__ import annotations

import logging
from typing import Iterable

from flask import current_app, url_for
from flask_mail import Message

from extensions import mail

log = logging.getLogger(__name__)


def _send(subject: str, recipients: Iterable[str], body: str, html: str | None = None) -> None:
    msg = Message(
        subject=subject,
        recipients=list(recipients),
        body=body,
        html=html,
        sender=current_app.config.get("MAIL_DEFAULT_SENDER"),
    )
    if current_app.config.get("MAIL_SUPPRESS_SEND"):
        log.info(
            "[email/suppressed] to=%s subject=%s\n%s",
            ", ".join(recipients), subject, body,
        )
        return
    try:
        mail.send(msg)
    except Exception as exc:  # pragma: no cover
        log.warning("Mail send failed: %s — body logged instead\n%s", exc, body)


def send_password_reset_email(user, token: str) -> None:
    reset_url = url_for("auth.password_reset_confirm", token=token, _external=True)
    body = (
        f"Cześć {user.username},\n\n"
        f"Aby zresetować hasło w PRMAnalyzer, kliknij w link:\n{reset_url}\n\n"
        f"Link wygasa za 60 minut. Jeśli to nie Ty prosiłeś o reset, zignoruj tę wiadomość.\n"
    )
    html = (
        f"<p>Cześć <strong>{user.username}</strong>,</p>"
        f"<p>Aby zresetować hasło w PRMAnalyzer, kliknij w link poniżej:</p>"
        f"<p><a href='{reset_url}'>{reset_url}</a></p>"
        f"<p style='color:#888;font-size:.85rem'>Link wygasa za 60 minut. "
        f"Jeśli to nie Ty prosiłeś o reset — zignoruj tę wiadomość.</p>"
    )
    _send("PRMAnalyzer — reset hasła", [user.email], body, html)
