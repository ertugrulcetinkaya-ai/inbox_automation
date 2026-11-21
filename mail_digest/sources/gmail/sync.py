"""Race-safe full sync and history-based incremental Gmail synchronization."""

from __future__ import annotations

import time

from ...config import GMAIL_BATCH_PAUSE_SECONDS, GMAIL_BATCH_SIZE
from .api import GmailHistoryExpired, GmailNotFound
from .mime import MessageStructureError, normalize_message
from .store import RETENTION_VERSION


WINDOW_MS = 30 * 86400 * 1000
CALENDAR_WINDOW_MS = 366 * 86400 * 1000
FULL_SYNC_QUERIES = ("newer_than:31d", "newer_than:367d filename:ics")
STAGE_BATCH_SIZE = 100
FETCH_BATCH_SIZE = GMAIL_BATCH_SIZE


class GmailSyncError(RuntimeError):
    pass


def _affected_ids(history_pages, store):
    affected = {}
    final_history_id = None
    for page in history_pages:
        if page.get("historyId"):
            final_history_id = str(page["historyId"])
        for history in page.get("history", []) or []:
            for entry in history.get("messagesAdded", []) or []:
                message = entry.get("message") or {}
                message_id = message.get("id")
                if message_id and (
                    "INBOX" in (message.get("labelIds") or [])
                    or store.has_message(message_id)
                ):
                    affected[message_id] = "fetch"
            for entry in history.get("messagesDeleted", []) or []:
                message_id = (entry.get("message") or {}).get("id")
                if message_id:
                    affected[message_id] = "delete"
            for array_name in ("labelsAdded", "labelsRemoved"):
                for entry in history.get(array_name, []) or []:
                    if "INBOX" not in (entry.get("labelIds") or []):
                        continue
                    message_id = (entry.get("message") or {}).get("id")
                    if message_id:
                        affected[message_id] = "fetch"
    if final_history_id is None:
        raise GmailSyncError("History response did not contain a checkpoint")
    return affected, final_history_id


class GmailSynchronizer:
    def __init__(
        self,
        api,
        store,
        now_ms=None,
        max_full_attempts=2,
        stage_batch_size=STAGE_BATCH_SIZE,
        sleep=time.sleep,
        batch_pause_seconds=GMAIL_BATCH_PAUSE_SECONDS,
    ):
        self.api = api
        self.store = store
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.max_full_attempts = max_full_attempts
        self.sleep = sleep
        self.batch_pause_seconds = batch_pause_seconds
        if stage_batch_size <= 0:
            raise ValueError("stage_batch_size must be positive")
        self.stage_batch_size = stage_batch_size

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
        return "INBOX" in labels and (
            cutoff_ms is None or internal_date_ms >= cutoff_ms
        )

    def _current_operation(self, message_id, cutoff_ms, calendar_cutoff_ms):
        try:
            message = self.api.get_message(message_id, format="full")
        except GmailNotFound:
            return "delete", message_id
        return self._operation_for_message(message_id, message, cutoff_ms, calendar_cutoff_ms)

    def _operation_for_message(self, message_id, message, cutoff_ms, calendar_cutoff_ms):
        if not isinstance(message, dict):
            raise GmailSyncError("Gmail returned an invalid message payload")
        if not self._is_current_inbox(message, None):
            return "delete", message_id
        record = self._normalize(message)
        retention_cutoff_ms = (
            calendar_cutoff_ms if record.get("raw_source") else cutoff_ms
        )
        if int(record["internal_date_ms"]) < retention_cutoff_ms:
            return "delete", message_id
        return "upsert", record

    def full_sync(self):
        last_expired = None
        for _attempt in range(self.max_full_attempts):
            now_ms = self.now_ms()
            cutoff_ms = now_ms - WINDOW_MS
            calendar_cutoff_ms = now_ms - CALENDAR_WINDOW_MS
            profile = self.api.get_profile()
            start_history_id = profile.get("historyId")
            if not start_history_id:
                raise GmailSyncError("Gmail profile did not contain historyId")
            self.store.clear_staging()
            try:
                snapshot_batch = []
                message_ids = {
                    message_id
                    for query in FULL_SYNC_QUERIES
                    for message_id in self.api.iter_message_ids(query)
                }
                sorted_message_ids = sorted(message_ids)
                for offset in range(0, len(sorted_message_ids), FETCH_BATCH_SIZE):
                    message_batch = sorted_message_ids[offset:offset + FETCH_BATCH_SIZE]
                    batch_results = self.api.get_messages_batch(
                        message_batch, format="full"
                    )
                    for message_id in message_batch:
                        if message_id not in batch_results:
                            raise GmailSyncError("Gmail batch response was incomplete")
                        message = batch_results[message_id]
                        if isinstance(message, GmailNotFound):
                            operation, payload = "delete", message_id
                        else:
                            operation, payload = self._operation_for_message(
                                message_id, message, cutoff_ms, calendar_cutoff_ms
                            )
                        if operation == "upsert":
                            snapshot_batch.append(payload)
                            if len(snapshot_batch) >= self.stage_batch_size:
                                self.store.stage_many(snapshot_batch)
                                snapshot_batch = []
                    if offset + FETCH_BATCH_SIZE < len(sorted_message_ids):
                        self.sleep(self.batch_pause_seconds)
                if snapshot_batch:
                    self.store.stage_many(snapshot_batch)
                affected, new_history_id = _affected_ids(
                    self.api.iter_history(str(start_history_id)), self.store
                )
                affected_batch = []
                deleted_batch = []
                for message_id, change_type in sorted(affected.items()):
                    if change_type == "delete":
                        deleted_batch.append(message_id)
                        if len(deleted_batch) >= self.stage_batch_size:
                            self.store.stage_delete_many(deleted_batch)
                            deleted_batch = []
                        continue
                    operation, payload = self._current_operation(
                        message_id, cutoff_ms, calendar_cutoff_ms
                    )
                    if operation == "upsert":
                        affected_batch.append(payload)
                        if len(affected_batch) >= self.stage_batch_size:
                            self.store.stage_many(affected_batch)
                            affected_batch = []
                    else:
                        deleted_batch.append(payload)
                        if len(deleted_batch) >= self.stage_batch_size:
                            self.store.stage_delete_many(deleted_batch)
                            deleted_batch = []
                if affected_batch:
                    self.store.stage_many(affected_batch)
                if deleted_batch:
                    self.store.stage_delete_many(deleted_batch)
                # Each staging transaction is bounded and contains no Gmail
                # request. The active cache/checkpoint still change together
                # only after the complete network reconciliation succeeds.
                self.store.activate_staging(
                    new_history_id, cutoff_ms, calendar_cutoff_ms
                )
                return
            except GmailHistoryExpired as exc:
                last_expired = exc
                self.store.clear_staging()
                continue
        raise GmailSyncError("Gmail history expired repeatedly during full sync") from last_expired

    def incremental_sync(self, checkpoint):
        now_ms = self.now_ms()
        cutoff_ms = now_ms - WINDOW_MS
        calendar_cutoff_ms = now_ms - CALENDAR_WINDOW_MS
        try:
            affected, new_history_id = _affected_ids(
                self.api.iter_history(str(checkpoint)), self.store
            )
        except GmailHistoryExpired:
            self.full_sync()
            return
        operations = []
        for message_id, change_type in sorted(affected.items()):
            if change_type == "delete":
                operations.append(("delete", message_id))
            else:
                operations.append(
                    self._current_operation(message_id, cutoff_ms, calendar_cutoff_ms)
                )
        self.store.apply_incremental(
            operations, new_history_id, cutoff_ms, calendar_cutoff_ms
        )

    def sync(self):
        checkpoint = self.store.checkpoint()
        if checkpoint is None or self.store.retention_version() != str(RETENTION_VERSION):
            self.full_sync()
        else:
            self.incremental_sync(checkpoint)
        return self.store.records()
