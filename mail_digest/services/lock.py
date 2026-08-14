"""Process-level locking for digest executions."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path

from ..config import DIGEST_LOCK_FILE


class DigestAlreadyRunning(RuntimeError):
    """Raised when another process is already generating a digest."""


@contextmanager
def digest_lock(lock_path: Path | None = None):
    """Hold an exclusive, non-blocking OS lock for one digest run.

    The file itself is only a stable rendezvous point. It is deliberately not
    removed on exit: ``flock`` releases the kernel lock when the descriptor is
    closed or the process terminates, including SIGKILL. A leftover file is
    therefore harmless and does not create a stale-lock failure.
    """

    path = Path(lock_path or DIGEST_LOCK_FILE).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DigestAlreadyRunning from exc

        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
