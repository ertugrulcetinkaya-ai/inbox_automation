"""Race-safe full sync and history-based incremental Gmail synchronization."""

from __future__ import annotations

import time

from .api import GmailHistoryExpired, GmailNotFound
from .mime import MessageStructureError, normalize_message


WINDOW_MS = 30 * 86400 * 1000


class GmailSyncError(RuntimeError):
    pass


def _affected_ids(history_pages):
    affected = set()
    final_history_id = None
    for page in history_pages:
        if page.get("historyId"):
            final_history_id = str(page["historyId"])
        for history in page.get("history", []) or []:
            for entry in history.get("messagesAdded", []) or []:
                message_id = (entry.get("message") or {}).get("id")
                if message_id:
                    affected.add(message_id)
            for entry in history.get("messagesDeleted", []) or []:
                message_id = (entry.get("message") or {}).get("id")
                if message_id:
                    affected.add(message_id)
            for array_name in ("labelsAdded", "labelsRemoved"):
                for entry in history.get(array_name, []) or []:
                    if "INBOX" not in (entry.get("labelIds") or []):
                        continue
                    message_id = (entry.get("message") or {}).get("id")
                    if message_id:
                        affected.add(message_id)
    if final_history_id is None:
        raise GmailSyncError("History response did not contain a checkpoint")
    return affected, final_history_id


class GmailSynchronizer:
    def __init__(self, api, store, now_ms=None, max_full_attempts=2):
        self.api = api
        self.store = store
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.max_full_attempts = max_full_attempts

    def _normalize(self, message):
        return normalize_message(
            message,
            attachment_loader=self.api.get_attachment,
            raw_loader=lambda message_id: self.api.get_message(message_id, format="raw"),
        )

    @staticmethod
    def _is_current_inbox(message, cutoff_ms):
        try:
            internal_date_ms = int(message["internalDate"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise MessageStructureError("invalid internalDate") from exc
        labels = message.get("labelIds")
        if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
            raise MessageStructureError("invalid labelIds")
        return "INBOX" in labels and internal_date_ms >= cutoff_ms

    def _current_operation(self, message_id, cutoff_ms):
        try:
            message = self.api.get_message(message_id, format="full")
        except GmailNotFound:
            return "delete", message_id
        if not self._is_current_inbox(message, cutoff_ms):
            return "delete", message_id
        return "upsert", self._normalize(message)

    def full_sync(self):
        last_expired = None
        for _attempt in range(self.max_full_attempts):
            cutoff_ms = self.now_ms() - WINDOW_MS
            profile = self.api.get_profile()
            start_history_id = profile.get("historyId")
            if not start_history_id:
                raise GmailSyncError("Gmail profile did not contain historyId")
            self.store.clear_staging()
            try:
                for message_id in self.api.iter_message_ids():
                    operation, payload = self._current_operation(message_id, cutoff_ms)
                    if operation == "upsert":
                        self.store.stage_upsert(payload)
                affected, new_history_id = _affected_ids(
                    self.api.iter_history(str(start_history_id))
                )
                for message_id in sorted(affected):
                    operation, payload = self._current_operation(message_id, cutoff_ms)
                    if operation == "upsert":
                        self.store.stage_upsert(payload)
                    else:
                        self.store.stage_delete(payload)
                self.store.activate_staging(new_history_id, cutoff_ms)
                return
            except GmailHistoryExpired as exc:
                last_expired = exc
                self.store.clear_staging()
                continue
        raise GmailSyncError("Gmail history expired repeatedly during full sync") from last_expired

    def incremental_sync(self, checkpoint):
        cutoff_ms = self.now_ms() - WINDOW_MS
        try:
            affected, new_history_id = _affected_ids(
                self.api.iter_history(str(checkpoint))
            )
        except GmailHistoryExpired:
            self.full_sync()
            return
        operations = [
            self._current_operation(message_id, cutoff_ms)
            for message_id in sorted(affected)
        ]
        self.store.apply_incremental(operations, new_history_id, cutoff_ms)

    def sync(self):
        checkpoint = self.store.checkpoint()
        if checkpoint is None:
            self.full_sync()
        else:
            self.incremental_sync(checkpoint)
        return self.store.records()
