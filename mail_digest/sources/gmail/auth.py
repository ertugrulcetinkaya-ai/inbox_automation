"""Non-interactive Gmail OAuth credential loading and refresh."""

from __future__ import annotations

import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ...config import GMAIL_SCOPES, gmail_token_file


class GmailAuthError(RuntimeError):
    """Credentials are unavailable or cannot be refreshed unattended."""


def _ensure_private_parent(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass


def write_private_text(path: Path, text: str) -> None:
    """Create or replace a secret file without a world-readable window."""
    _ensure_private_parent(path)
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
    finally:
        try:
            path.chmod(0o600)
        except OSError:
            pass


def load_credentials(token_path: Path = None) -> Credentials:
    """Load/refresh a token without ever starting an interactive flow."""
    token_path = Path(token_path or gmail_token_file()).expanduser()
    if not token_path.is_file():
        raise GmailAuthError(
            "Gmail token is missing; run scripts/gmail_auth.py once before using MAIL_SOURCE=gmail"
        )
    try:
        token_path.chmod(0o600)
        credentials = Credentials.from_authorized_user_file(str(token_path), list(GMAIL_SCOPES))
        if credentials.expired:
            if not credentials.refresh_token:
                raise GmailAuthError(
                    "Gmail token is expired and has no refresh token; rerun scripts/gmail_auth.py"
                )
            credentials.refresh(Request())
            write_private_text(token_path, credentials.to_json())
        if not credentials.valid:
            raise GmailAuthError(
                "Gmail token is invalid; rerun scripts/gmail_auth.py"
            )
        token_path.chmod(0o600)
        return credentials
    except GmailAuthError:
        raise
    except Exception as exc:
        raise GmailAuthError(
            "Gmail authorization could not be refreshed; rerun scripts/gmail_auth.py"
        ) from exc


def build_service(token_path: Path = None):
    credentials = load_credentials(token_path)
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)
