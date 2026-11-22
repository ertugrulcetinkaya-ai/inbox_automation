"""Explicit, interactive one-time Gmail OAuth bootstrap."""

from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from ...config import GMAIL_SCOPES, gmail_credentials_file, gmail_token_file, target_email
from .account import GmailAccountMismatch, validate_gmail_profile
from .auth import GmailAuthError, write_private_text


def authorize_interactively(
    credentials_path: Path | None = None,
    token_path: Path | None = None,
) -> Path:
    credentials_path = Path(credentials_path or gmail_credentials_file()).expanduser()
    token_path = Path(token_path or gmail_token_file()).expanduser()
    expected_email = target_email()
    if not credentials_path.is_file():
        raise FileNotFoundError(f"Desktop OAuth credentials not found: {credentials_path}")
    credentials_path.chmod(0o600)
    flow = InstalledAppFlow.from_client_secrets_file(
        str(credentials_path), list(GMAIL_SCOPES)
    )
    credentials = flow.run_local_server(port=0)
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    try:
        validate_gmail_profile(profile, expected_email)
    except GmailAccountMismatch as exc:
        raise GmailAuthError("Gmail account mismatch") from exc
    write_private_text(token_path, credentials.to_json())
    return token_path
