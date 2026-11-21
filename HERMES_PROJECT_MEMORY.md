# Hermes Project Memory

## launchd logging

All three launchd plist templates set `PYTHONUNBUFFERED=1` so Python stdout is
written to the configured launchd logs without waiting for the output buffer to
fill. Listener `print()` calls also use `flush=True`, making startup, command,
and error messages visible immediately.
