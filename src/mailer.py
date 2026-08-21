"""Delivery over Gmail SMTP, plus the failure alert.

App Password auth, not OAuth: one secret, no refresh token to expire out from
under a cron job at 3am. Requires 2-Step Verification on the Google account —
see the README.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any, Sequence

log = logging.getLogger(__name__)


class MailError(RuntimeError):
    """Raised when the message can't be sent."""


BODY = """\
Good morning,

Today's edition of News in 60 Seconds is attached.

{headline}

— Sent Automatically
"""


def _credentials() -> tuple[str, str]:
    address = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not address:
        raise MailError("GMAIL_ADDRESS is not set")
    if not password:
        raise MailError(
            "GMAIL_APP_PASSWORD is not set. Generate one at "
            "https://myaccount.google.com/apppasswords (needs 2-Step Verification)."
        )
    # Google displays app passwords in groups of four; the spaces aren't part of it.
    return address, password.replace(" ", "")


def _send(msg: EmailMessage, config: dict[str, Any]) -> None:
    address, password = _credentials()
    host = config["email"]["smtp_host"]
    port = int(config["email"]["smtp_port"])

    try:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=60) as smtp:
            smtp.login(address, password)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(
            "Gmail rejected the login. Check GMAIL_ADDRESS and that "
            "GMAIL_APP_PASSWORD is a current 16-character app password "
            f"(not the account password). {exc}"
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"SMTP send failed: {exc}") from exc


def resolve_recipients(root: Path) -> dict[str, list[str]]:
    """Recipients from the RECIPIENTS env var if set, else recipients.yaml."""
    env = os.environ.get("RECIPIENTS", "").strip()
    if env:
        to = [a.strip() for a in env.split(",") if a.strip()]
        log.info("  %d recipient(s) from RECIPIENTS env var", len(to))
        return {"to": to, "cc": [], "bcc": []}

    import yaml

    path = root / "recipients.yaml"
    if not path.exists():
        raise MailError(f"no RECIPIENTS env var and {path} does not exist")

    data = yaml.safe_load(path.read_text("utf-8")) or {}
    out = {k: [a for a in (data.get(k) or []) if a] for k in ("to", "cc", "bcc")}
    if not out["to"]:
        raise MailError(f"{path} has an empty `to:` list")
    log.info("  %d recipient(s) from recipients.yaml", len(out["to"]))
    return out


def send_newsletter(config: dict[str, Any], pdf_path: Path, date_long: str,
                    iso_date: str, headline: str, recipients: dict[str, list[str]]) -> None:
    address, _ = _credentials()

    msg = EmailMessage()
    msg["From"] = address
    msg["To"] = ", ".join(recipients["to"])
    if recipients.get("cc"):
        msg["Cc"] = ", ".join(recipients["cc"])
    msg["Subject"] = config["email"]["subject"].format(date=date_long)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="news-in-60-seconds")
    msg.set_content(BODY.format(headline=headline))

    msg.add_attachment(
        pdf_path.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=config["email"]["attachment_name"].format(iso_date=iso_date),
    )

    _send(msg, config)
    everyone = recipients["to"] + recipients.get("cc", []) + recipients.get("bcc", [])
    log.info("  sent to %d address(es)", len(everyone))


def send_failure_alert(config: dict[str, Any], stage: str, error: str,
                       traceback_text: str, iso_date: str) -> None:
    """Tell the operator the run died. Never raises — this is the last resort."""
    try:
        address, _ = _credentials()
        msg = EmailMessage()
        msg["From"] = address
        msg["To"] = address  # always the operator, never the distribution list
        msg["Subject"] = f"News in 60 Seconds FAILED — {iso_date} ({stage})"
        msg["Date"] = formatdate(localtime=True)
        msg.set_content(
            f"The {iso_date} newsletter was not sent.\n\n"
            f"Stage:  {stage}\n"
            f"Error:  {error}\n\n"
            f"{traceback_text}\n"
        )
        _send(msg, config)
        log.info("failure alert sent to %s", address)
    except Exception as exc:  # noqa: BLE001 - alerting must never mask the real error
        log.error("could not send failure alert: %s", exc)
