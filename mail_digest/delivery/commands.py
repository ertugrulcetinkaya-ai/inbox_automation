"""Command parsing and single-worker queueing for Telegram updates."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Callable


QUEUED = "queued"
DUPLICATE = "duplicate"
FULL = "full"


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    args: str


def parse_command(text: str | None) -> ParsedCommand | None:
    """Parse a slash command's name separately from its optional arguments."""

    normalized = (text or "").strip()
    if not normalized.startswith("/"):
        return None
    parts = normalized.split(maxsplit=1)
    name = parts[0].split("@", maxsplit=1)[0].casefold()
    if not name or name == "/":
        return None
    return ParsedCommand(name=name, args=parts[1] if len(parts) > 1 else "")


@dataclass
class QueuedCommand:
    key: tuple[str, ...]
    callback: Callable[[], None]
    persisted: bool = False
    ready: threading.Event = field(default_factory=threading.Event)


class CommandWorker:
    """Run queued commands serially and only after update state is durable."""

    def __init__(self, maxsize: int = 128):
        self._queue: queue.Queue[QueuedCommand | None] = queue.Queue(maxsize=maxsize)
        self._pending: set[tuple[str, ...]] = set()
        self._pending_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="telegram-command-worker",
            daemon=True,
        )
        self._thread.start()

    def enqueue(self, key: tuple[str, ...], callback: Callable[[], None]):
        with self._pending_lock:
            if key in self._pending:
                return DUPLICATE, None
            self._pending.add(key)
            job = QueuedCommand(key=key, callback=callback)
            try:
                self._queue.put_nowait(job)
            except queue.Full:
                self._pending.remove(key)
                return FULL, None
        return QUEUED, job

    @staticmethod
    def activate(job: QueuedCommand, *, persisted: bool) -> None:
        job.persisted = persisted
        job.ready.set()

    def close(self, timeout: float | None = None) -> None:
        if self._thread is None:
            return
        self._queue.put(None)
        self._thread.join(timeout)
        self._thread = None

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                self._queue.task_done()
                return
            try:
                job.ready.wait()
                if job.persisted:
                    job.callback()
            except Exception as exc:
                print(f"Command worker failed ({exc.__class__.__name__})", flush=True)
            finally:
                with self._pending_lock:
                    self._pending.discard(job.key)
                self._queue.task_done()
