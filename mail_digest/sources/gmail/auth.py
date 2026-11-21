"""Non-interactive Gmail OAuth credential loading and refresh."""

from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google_auth_httplib2 import AuthorizedHttp
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import httplib2

from ...config import GMAIL_HTTP_TIMEOUT_SECONDS, GMAIL_SCOPES, gmail_token_file


class GmailAuthError(RuntimeError):
    """Credentials are unavailable or cannot be refreshed unattended."""


def _ensure_private_parent(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass


def write_private_text(path: Path, text: str) -> None:
    """Atomically replace a secret file without a world-readable window."""
    _ensure_private_parent(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_descriptor = os.open(
            str(path.parent),
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        raise


def load_credentials(token_path: Path | None = None) -> Credentials:
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
            try:
                credentials.refresh(Request())
            except RefreshError as exc:
                raise GmailAuthError(
                    "Gmail token is invalid; rerun scripts/gmail_auth.py"
                ) from exc
            except (TransportError, TimeoutError, socket.error) as exc:
                raise GmailAuthError(
                    "Gmail token refresh temporarily failed; retry later"
                ) from exc
            except Exception as exc:
                raise GmailAuthError(
                    f"Gmail authorization failed ({type(exc).__name__})"
                ) from exc
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
        raise GmailAuthError(f"Gmail authorization failed ({type(exc).__name__})") from exc


def build_service(token_path: Path | None = None):
    credentials = load_credentials(token_path)
    http = httplib2.Http(timeout=GMAIL_HTTP_TIMEOUT_SECONDS)
    authorized_http = AuthorizedHttp(credentials, http=http)
    return build("gmail", "v1", http=authorized_http, cache_discovery=False)
