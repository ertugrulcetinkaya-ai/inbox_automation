from pathlib import Path


FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def load_fixture(name, **overrides):
    raw = (FIXTURE_ROOT / name).read_text(encoding="utf-8")
    if name.endswith(".ics"):
        record = {
            "sender": "Calendar Fixture <calendar@example.com>",
            "subject": "Fixture calendar invite",
            "date": "Friday, August 14, 2026 at 09:00:00",
            "source_message_id": f"<{name}@example.com>",
            "content": raw,
        }
        record.update(overrides)
        return record

    header_text, body = raw.split("\n\n", 1)
    headers = {}
    for line in header_text.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            headers[key.casefold()] = value.strip()
    record = {
        "sender": headers.get("from", "Fixture Sender <sender@example.com>"),
        "subject": headers.get("subject", "Fixture toplantısı"),
        "date": headers.get("date", "Wednesday, August 12, 2026 at 09:00:00"),
        "source_message_id": headers.get("message-id", "<fixture@example.com>"),
        "content": body.strip(),
    }
    record.update(overrides)
    return record
