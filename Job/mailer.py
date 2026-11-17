#!/usr/bin/env python3
"""
mailer.py - send e-mail applications (Bewerbung.eml) from the applicant's own Gmail.

The browser agent writes Bewerbung.eml into the job folder when an employer
takes applications by e-mail. This module sends that draft unchanged in
content: recipient, subject, text and both PDFs as written there.

Credentials live only in Job/.env (never in code, logs or history.json):

    JOB_APPLIER_SMTP_USER=malekaloui1777@gmail.com
    JOB_APPLIER_SMTP_PASSWORD=<16-character Google app password>

Optional:
    JOB_APPLIER_SMTP_HOST=smtp.gmail.com     (default)
    JOB_APPLIER_SMTP_PORT=465                (default, SSL)
    JOB_APPLIER_BCC_SELF=1                   (default 1: a copy goes to your own inbox)

Gmail stores every message sent this way in the account's "Gesendet / Sent" folder.

Test the connection without sending anything:
    python mailer.py --check
"""

from __future__ import annotations

import argparse
import os
import re
import smtplib
import ssl
import sys
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Any, Dict, Optional

JOB_DIR = Path(__file__).resolve().parent
ENV_PATH = JOB_DIR / ".env"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


def _load_env() -> None:
    """Read Job/.env into os.environ without overriding variables already set."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH, override=False)
        return
    except ImportError:
        pass
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def smtp_config() -> Dict[str, Any]:
    _load_env()
    return {
        "user": os.getenv("JOB_APPLIER_SMTP_USER", "").strip(),
        "password": os.getenv("JOB_APPLIER_SMTP_PASSWORD", "").replace(" ", ""),
        "host": os.getenv("JOB_APPLIER_SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.getenv("JOB_APPLIER_SMTP_PORT", "465")),
        "bcc_self": os.getenv("JOB_APPLIER_BCC_SELF", "1") == "1",
    }


def is_configured() -> bool:
    cfg = smtp_config()
    return bool(EMAIL_RE.match(cfg["user"]) and cfg["password"])


def configured_sender() -> str:
    return smtp_config()["user"] if is_configured() else ""


def build_message(eml_path: Path, sender: str, bcc_self: bool,
                  attachments: Optional[Dict[str, Path]] = None) -> EmailMessage:
    """
    Turn the saved draft into a sendable message from `sender`.

    attachments: {"Anschreiben.pdf": path, "Lebenslauf.pdf": path}. When given,
    the PDFs stored inside the draft are replaced by these files, so a letter
    regenerated after the draft was written is what gets sent.
    """
    msg = BytesParser(policy=policy.default).parsebytes(eml_path.read_bytes())
    if attachments:
        body = msg.get_body(("plain",))
        text = body.get_content() if body is not None else ""
        fresh = EmailMessage()
        for key in ("To", "From", "Subject"):
            if msg[key]:
                fresh[key] = str(msg[key])
        fresh.set_content(text)
        for name, path in attachments.items():
            fresh.add_attachment(Path(path).read_bytes(), maintype="application", subtype="pdf", filename=name)
        msg = fresh
    to_addr = parseaddr(str(msg.get("To", "")))[1]
    if not EMAIL_RE.match(to_addr):
        raise ValueError(f"draft has no valid recipient: {msg.get('To')!r}")
    pdfs = [p for p in msg.iter_attachments() if p.get_content_type() == "application/pdf"]
    if len(pdfs) < 2:
        raise ValueError(f"draft must carry the cover letter and the CV as PDFs, found {len(pdfs)}")
    name = parseaddr(str(msg.get("From", "")))[0]
    for header in ("X-Unsent", "From", "Date", "Message-ID", "Bcc"):
        del msg[header]
    msg["From"] = formataddr((name, sender)) if name else sender
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@", 1)[1])
    if bcc_self:
        msg["Bcc"] = sender
    return msg


def send_application(eml_path: Path, expected_to: Optional[str] = None, smtp_factory: Any = None,
                     attachments: Optional[Dict[str, Path]] = None) -> Dict[str, str]:
    """
    Send one Bewerbung.eml. Returns {"to", "message_id", "sent_from"}.
    Raises on any problem; nothing is sent unless every check passes.
    """
    cfg = smtp_config()
    if not is_configured():
        raise RuntimeError("e-mail sending is not set up: add JOB_APPLIER_SMTP_USER and JOB_APPLIER_SMTP_PASSWORD to Job/.env")
    eml_path = Path(eml_path)
    if not eml_path.is_file():
        raise FileNotFoundError(f"{eml_path.name} not found in the job folder")
    msg = build_message(eml_path, cfg["user"], cfg["bcc_self"], attachments)
    to_addr = parseaddr(str(msg["To"]))[1]
    if expected_to and to_addr.lower() != expected_to.lower():
        raise ValueError(f"draft recipient {to_addr} does not match the recorded address {expected_to}")

    factory = smtp_factory or (lambda: smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30,
                                                         context=ssl.create_default_context()))
    with factory() as server:
        server.login(cfg["user"], cfg["password"])
        server.send_message(msg)
    return {"to": to_addr, "message_id": str(msg["Message-ID"]), "sent_from": cfg["user"]}


def check_connection() -> str:
    """Log in and out without sending anything."""
    cfg = smtp_config()
    if not is_configured():
        return "NOT CONFIGURED: add JOB_APPLIER_SMTP_USER and JOB_APPLIER_SMTP_PASSWORD to Job/.env"
    try:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30, context=ssl.create_default_context()) as server:
            server.login(cfg["user"], cfg["password"])
        return f"OK: logged in to {cfg['host']} as {cfg['user']} (nothing was sent)"
    except smtplib.SMTPAuthenticationError:
        return ("LOGIN FAILED: Gmail rejected the password. Use a Google app password "
                "(myaccount.google.com/apppasswords, needs 2-Step Verification), not the normal account password.")
    except Exception as exc:  # noqa: BLE001
        return f"CONNECTION FAILED: {type(exc).__name__}: {exc}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Job Applier e-mail sender")
    ap.add_argument("--check", action="store_true", help="test the SMTP login without sending")
    args = ap.parse_args()
    if args.check:
        print(check_connection())
        sys.exit(0)
    ap.print_help()
