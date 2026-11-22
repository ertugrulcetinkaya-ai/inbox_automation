"""Gmail account identity validation without logging mailbox identifiers."""


class GmailAccountMismatch(ValueError):
    """The authorized Gmail profile does not match the configured account."""


def validate_gmail_profile(profile, expected_email):
    actual_email = profile.get("emailAddress") if isinstance(profile, dict) else None
    if (
        not isinstance(actual_email, str)
        or actual_email.strip().casefold() != expected_email.strip().casefold()
    ):
        raise GmailAccountMismatch("Gmail account mismatch")
