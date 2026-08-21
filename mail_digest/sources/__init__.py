"""Mail source selection with no cross-source fallback."""

import os

from ..config import MAIL_SOURCE_DEFAULT


class MailSourceConfigurationError(ValueError):
    pass


def selected_source_name():
    return os.environ.get("MAIL_SOURCE", MAIL_SOURCE_DEFAULT).strip().casefold()


def fetch_mail():
    source = selected_source_name()
    if source == "apple_mail":
        from .apple_mail import fetch_mail as source_fetch
    elif source == "gmail":
        from .gmail import fetch_mail as source_fetch
    else:
        raise MailSourceConfigurationError(
            f"Unknown MAIL_SOURCE={source!r}; expected 'apple_mail' or 'gmail'"
        )
    return source_fetch()
