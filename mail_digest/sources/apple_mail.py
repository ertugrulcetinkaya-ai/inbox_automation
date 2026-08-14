"""Read-only Apple Mail source adapter."""

import subprocess

from ..config import FIELD_DELIMITER, SCRIPT_PATH, log
from ..parsing.dates import parse_received_date
from ..utils import sanitize, sanitize_content, sanitize_transport_field


def fetch_mail():
    """Fetch candidate messages from Apple Mail without changing message state."""
    try:
        result = subprocess.run(
            ["osascript", str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            check=False,
        )

        raw_output = result.stdout + "\n" + result.stderr
        records = []
        for line in raw_output.splitlines():
            line = line.strip()
            if not line or FIELD_DELIMITER not in line:
                continue
            if line.startswith("log:"):
                line = line[4:].strip()

            parts = line.split(FIELD_DELIMITER)
            if len(parts) < 6:
                continue
            received_text = sanitize_transport_field(parts[4])
            records.append({
                "account": sanitize_transport_field(parts[0]),
                "mailbox": sanitize_transport_field(parts[1]),
                "sender": sanitize_transport_field(parts[2]),
                "subject": sanitize_transport_field(parts[3]),
                "date": received_text,
                "received_date": parse_received_date(received_text),
                "content": sanitize_content(parts[5]),
                "source_message_id": sanitize_transport_field(parts[6]) if len(parts) >= 7 else "",
                "raw_source": sanitize_content(parts[7]) if len(parts) >= 8 else "",
            })

        if result.returncode != 0 and not records:
            log(f"AppleScript error: {sanitize(result.stderr)}")
            return None
        return records
    except Exception as exc:
        log(f"Error fetching mail: {exc}")
        return None
