"""Process-level locking for digest executions."""

from __future__ import annotations

import fcntl
import time
from contextlib import contextmanager
from pathlib import Path

from ..config import digest_lock_file


class DigestAlreadyRunning(RuntimeError):
    """Raised when another process is already generating a digest."""


@contextmanager
def digest_lock(lock_path: Path | None = None, wait_seconds: float = 0.0):
    """Hold an exclusive OS lock, optionally waiting in half-second intervals.

    The file itself is only a stable rendezvous point. It is deliberately not
    removed on exit: ``flock`` releases the kernel lock when the descriptor is
    closed or the process terminates, including SIGKILL. A leftover file is
    therefore harmless and does not create a stale-lock failure.
    """

    path = Path(lock_path or digest_lock_file()).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    locked = False
    try:
        deadline = time.monotonic() + max(0.0, float(wait_seconds))
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except BlockingIOError as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DigestAlreadyRunning from exc
                time.sleep(min(0.5, remaining))

        yield
    finally:
        try:
            if locked:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
