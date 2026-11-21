"""Best-effort size-based rotation for project log files."""

from pathlib import Path


DEFAULT_MAX_LOG_BYTES = 5 * 1024 * 1024
DEFAULT_LOG_ARCHIVES = 3
CLI_LOG_NAMES = (
    "output.log",
    "error.log",
    "reminder_output.log",
    "reminder_error.log",
)
LISTENER_LOG_NAMES = ("listener_output.log", "listener_error.log")


def _rotate_one(log_path: Path, keep: int) -> None:
    for archive in log_path.parent.glob(f"{log_path.name}.*"):
        try:
            archive_number = int(archive.suffix[1:])
        except ValueError:
            continue
        if archive_number > keep:
            archive.unlink(missing_ok=True)

    for archive_number in range(keep, 0, -1):
        source = (
            log_path
            if archive_number == 1
            else log_path.with_name(f"{log_path.name}.{archive_number - 1}")
        )
        destination = log_path.with_name(f"{log_path.name}.{archive_number}")
        destination.unlink(missing_ok=True)
        if source.exists():
            source.replace(destination)


def rotate_logs(
    log_dir,
    names=None,
    max_bytes=DEFAULT_MAX_LOG_BYTES,
    keep=DEFAULT_LOG_ARCHIVES,
) -> None:
    """Rotate oversized selected logs without interrupting the caller."""

    if max_bytes < 0 or keep < 1:
        return
    try:
        directory = Path(log_dir)
        selected_names = (names,) if isinstance(names, str) else names
        candidates = (
            directory.glob("*.log")
            if selected_names is None
            else (directory / name for name in selected_names)
        )
        for log_path in candidates:
            try:
                if log_path.parent != directory or log_path.suffix != ".log":
                    continue
                if log_path.is_file() and log_path.stat().st_size > max_bytes:
                    _rotate_one(log_path, keep)
            except Exception:
                # Logging maintenance must never interrupt digest delivery.
                continue
    except Exception:
        return
