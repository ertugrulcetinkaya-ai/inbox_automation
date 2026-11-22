"""Public Gmail mail-source adapter."""

import sqlite3

from ...config import TargetEmailConfigurationError, gmail_cache_file, log, target_email
from .api import GmailApi, GmailApiError
from .auth import GmailAuthError, build_service
from .mime import MessageStructureError
from .store import GmailStore
from .sync import GmailSynchronizer, GmailSyncError


def fetch_mail():
    store = None
    try:
        account = target_email()
        service = build_service()
        api = GmailApi(service)
        store = GmailStore(gmail_cache_file(), account=account)
        return GmailSynchronizer(api, store).sync()
    except (
        GmailAuthError,
        GmailApiError,
        GmailSyncError,
        MessageStructureError,
        TargetEmailConfigurationError,
        OSError,
        sqlite3.Error,
    ) as exc:
        error_detail = type(exc).__name__
        if isinstance(exc, GmailApiError) and exc.http_status is not None:
            reason = f" {exc.reason}" if exc.reason else ""
            error_detail = f"{error_detail}: HTTP {exc.http_status}{reason}"
        log(
            "Gmail sync failed "
            f"({error_detail}); run scripts/gmail_auth.py "
            "if authorization is required"
        )
        return None
    except Exception as exc:
        # Do not turn programming regressions into an apparently normal empty
        # mailbox. Keep the exception type visible to the scheduler while
        # avoiding private message contents in the operational log.
        log(f"Unexpected Gmail sync failure ({exc.__class__.__name__})")
        raise
    finally:
        if store is not None:
            store.close()
