"""
gmail_send.py
─────────────
Light wrapper around the Gmail API for one-off transactional mail.

Usage
-----
from gmail_send import send_gmail

send_gmail(
    to_list=["patient@example.com", "samrobinson290225@gmail.com"],
    subject="We’ve received your appointment request – UPFH",
    body="Plain-text body here …",
    attachment=("appointment_request.csv", csv_bytes)   # optional
)
"""

import os
import base64
import email.message
import pathlib
from typing import List, Tuple, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


# Location of the refresh-token file (set as a secret file in production)
TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH", "gmail_token.json")
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def _gmail_service():
    """Lazy-load a Gmail API service instance."""
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def send_gmail(
    to_list:   List[str],
    subject:   str,
    body:      str,
    attachment: Optional[Tuple[str, bytes]] = None,
    sender:    str = "Utah Partners for Health <samrobinson290225@gmail.com>",
) -> None:
    """
    Send a plain-text e-mail (optionally with **one** attachment).

    Parameters
    ----------
    to_list      : list[str]        Recipients
    subject      : str             Email subject
    body         : str             Plain-text body
    attachment   : (filename, bytes) or None
    sender       : str             "From" header (default: UPFH helper)
    """
    # ── 1. Build MIME message ─────────────────────────────────────────────
    msg = email.message.EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    msg.set_content(body)

    if attachment:
        fname, data_bytes = attachment
        msg.add_attachment(
            data_bytes,
            maintype="application", subtype="octet-stream",
            filename=fname,
        )

    # ── 2. Push via Gmail API ─────────────────────────────────────────────
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    _gmail_service().users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()
