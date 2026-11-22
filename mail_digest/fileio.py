"""Private atomic file writes shared by application layers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_private(path, text, mode=0o600):
    """Atomically replace *path* with UTF-8 text and private permissions."""

    destination = Path(path).expanduser()
    parent = destination.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent != Path("."):
        parent.chmod(0o700)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=str(parent)
    )
    descriptor_open = True
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor_open = False
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
        directory_descriptor = os.open(
            str(parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        if descriptor_open:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        except OSError:
            pass
        raise
