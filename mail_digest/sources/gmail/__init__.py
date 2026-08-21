"""Public Gmail mail-source adapter."""

from ...config import gmail_cache_file, log
from .api import GmailApi
from .auth import build_service
from .store import GmailStore
from .sync import GmailSynchronizer


def fetch_mail():
    store = None
    try:
        service = build_service()
        api = GmailApi(service)
        store = GmailStore(gmail_cache_file())
        return GmailSynchronizer(api, store).sync()
    except Exception as exc:
        log(f"Gmail sync failed ({exc.__class__.__name__}); run scripts/gmail_auth.py if authorization is required")
        return None
    finally:
        if store is not None:
            store.close()
