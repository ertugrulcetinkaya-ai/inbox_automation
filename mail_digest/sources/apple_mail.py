"""Read-only Apple Mail source adapter."""

import os
import signal
import subprocess

from ..config import APPLE_SCRIPT_TIMEOUT_SECONDS, FIELD_DELIMITER, SCRIPT_PATH, log
from ..parsing.dates import parse_received_date
from ..utils import sanitize, sanitize_content, sanitize_transport_field


def _run_applescript():
    """Run AppleScript with a killable process group.

    ``subprocess.run(timeout=...)`` only terminates the direct ``osascript``
    process. Apple Mail can leave descendants holding stdout/stderr open,
    which would keep ``communicate()`` blocked and retain the digest lock.
    Starting a new session lets timeout handling terminate the whole tree.
    """

    process = subprocess.Popen(
        ["osascript", str(SCRIPT_PATH)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=APPLE_SCRIPT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        log("AppleScript timeout while reading Apple Mail")
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        return None

    return subprocess.CompletedProcess(
        process.args,
        process.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def fetch_mail():
    """Fetch candidate messages from Apple Mail without changing message state."""
    try:
        result = _run_applescript()
        if result is None:
            return None

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
