import base64
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from mail_digest.sources.gmail.api import GmailApiError, GmailHistoryExpired, GmailNotFound
from mail_digest.sources.gmail.auth import GmailAuthError
from mail_digest.sources.gmail.store import GmailStore
from mail_digest.services.meeting_service import build_digest_result
from mail_digest.sources.gmail.sync import (
    CALENDAR_WINDOW_MS,
    FETCH_BATCH_SIZE,
    FULL_SYNC_QUERIES,
    GmailSynchronizer,
    RETENTION_UPGRADE_RETRY_INTERVAL_MS,
    WINDOW_MS,
)
from parse_cache_test_support import isolate_parse_cache

isolate_parse_cache()


NOW_MS = 1_800_000_000_000


def encoded(value):
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def full_message(message_id, *, inbox=True, age_ms=1000, content="ordinary", subject="Subject"):
    return {
        "id": message_id,
        "threadId": "thread-" + message_id,
        "internalDate": str(NOW_MS - age_ms),
        "labelIds": ["INBOX"] if inbox else ["SENT"],
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": "Fixture <fixture@example.test>"},
                {"name": "Date", "value": "Wed, 19 Aug 2026 10:00:00 +0300"},
                {"name": "Message-ID", "value": f"<{message_id}@example.test>"},
            ],
            "body": {"data": encoded(content)},
        },
    }


def calendar_message(message_id, *, age_ms, calendar):
    value = full_message(message_id, age_ms=age_ms)
    value["payload"] = {
        "mimeType": "multipart/mixed",
        "filename": "",
        "headers": value["payload"]["headers"],
        "parts": [{
            "mimeType": "text/calendar",
            "filename": "invite.ics",
            "headers": [],
            "body": {"data": encoded(calendar)},
        }],
    }
    return value


def history_page(history_id, records=None, next_token=None):
    page = {"historyId": str(history_id), "history": records or []}
    if next_token:
        page["nextPageToken"] = next_token
    return page


def event(array_name, message_id, labels=None):
    value = {"message": {"id": message_id}}
    if array_name == "messagesAdded":
        value["message"]["labelIds"] = labels if labels is not None else ["INBOX"]
    elif labels is not None:
        value["labelIds"] = labels
    return {array_name: [value]}


def message_added_without_labels(message_id):
    return {"messagesAdded": [{"message": {"id": message_id}}]}


class FakeApi:
    def __init__(self):
        self.profiles = [{"historyId": "100"}]
        self.profile_calls = 0
        self.listed = []
        self.listed_by_query = {}
        self.list_queries = []
        self.history = {"100": [history_page("101")]}
        self.messages = {}
        self.get_calls = []
        self.batch_calls = []
        self.history_calls = []

    def get_profile(self):
        self.profile_calls += 1
        value = self.profiles.pop(0) if len(self.profiles) > 1 else self.profiles[0]
        if isinstance(value, Exception):
            raise value
        return value

    def iter_message_ids(self, query):
        self.list_queries.append(query)
        yield from self.listed_by_query.get(query, self.listed)

    def iter_history(self, start):
        self.history_calls.append(start)
        value = self.history[start]
        if isinstance(value, Exception):
            raise value
        yield from value

    def get_message(self, message_id, format="full"):
        self.get_calls.append((message_id, format))
        value = self.messages[message_id]
        if isinstance(value, list):
            value = value.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def get_messages_batch(self, message_ids, format="full"):
        message_ids = list(message_ids)
        self.batch_calls.append((message_ids, format))
        results = {}
        for message_id in message_ids:
            try:
                results[message_id] = self.get_message(message_id, format=format)
            except GmailNotFound as exc:
                results[message_id] = exc
        return results

    def get_attachment(self, message_id, attachment_id):
        raise AssertionError("unexpected attachment call")


class GmailSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "cache.sqlite3"
        self.store = GmailStore(self.path)
        self.api = FakeApi()
        self.sleeps = []
        self.sync = GmailSynchronizer(
            self.api,
            self.store,
            now_ms=lambda: NOW_MS,
            sleep=self.sleeps.append,
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def ids(self):
        return [row[0] for row in self.store.connection.execute(
            "SELECT gmail_message_id FROM messages ORDER BY gmail_message_id"
        )]

    def seed_old(self):
        record = {
            "gmail_message_id": "old", "thread_id": "t", "internal_date_ms": NOW_MS - 100,
            "sender": "old", "subject": "old", "date": "old", "received_local_date": "2026-08-19",
            "content": "old", "source_message_id": "old", "raw_source": "",
        }
        self.store.apply_incremental(
            [("upsert", record)],
            "90",
            NOW_MS - WINDOW_MS,
            NOW_MS - CALENDAR_WINDOW_MS,
        )
        with self.store.connection:
            self.store.connection.execute(
                "INSERT INTO metadata(key, value) VALUES('retention_version', '2') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )

    def test_full_sync_arrival_after_boundary_is_reconciled(self):
        self.api.messages["new"] = full_message("new")
        self.api.history["100"] = [history_page("110", [event("messagesAdded", "new")])]
        self.sync.full_sync()
        self.assertEqual(self.ids(), ["new"])
        self.assertEqual(self.store.checkpoint(), "110")

    def test_full_sync_stages_snapshot_in_bounded_batches(self):
        self.api.listed = ["one", "two", "three", "four", "five"]
        self.api.messages = {
            message_id: full_message(message_id)
            for message_id in self.api.listed
        }
        self.sync.stage_batch_size = 2

        with patch.object(self.store, "stage_many", wraps=self.store.stage_many) as stage:
            self.sync.full_sync()

        self.assertEqual(
            [len(call.args[0]) for call in stage.call_args_list],
            [2, 2, 1],
        )
        self.assertEqual(self.ids(), sorted(self.api.listed))
        self.assertEqual(self.api.batch_calls, [(sorted(self.api.listed), "full")])

    def test_full_sync_fetches_snapshot_in_configured_batches(self):
        self.api.listed = [
            f"message-{index:03d}" for index in range(2 * FETCH_BATCH_SIZE + 1)
        ]
        self.api.messages = {
            message_id: full_message(message_id) for message_id in self.api.listed
        }

        self.sync.full_sync()

        self.assertEqual(
            [len(ids) for ids, _format in self.api.batch_calls],
            [FETCH_BATCH_SIZE, FETCH_BATCH_SIZE, 1],
        )
        self.assertEqual(self.sleeps, [1.0, 1.0])
        self.assertEqual(len(self.ids()), len(self.api.listed))

    def test_full_sync_marks_individual_not_found_batch_result_for_removal(self):
        self.api.listed = ["gone"]
        self.api.messages["gone"] = GmailNotFound("gone")

        self.sync.full_sync()

        self.assertEqual(self.ids(), [])
        self.assertEqual(self.api.batch_calls, [(["gone"], "full")])

    def test_full_sync_initial_message_moved_out_is_removed(self):
        self.api.listed = ["moving"]
        self.api.messages["moving"] = [full_message("moving"), full_message("moving", inbox=False)]
        self.api.history["100"] = [history_page("110", [event("labelsRemoved", "moving", ["INBOX"])])]
        self.sync.full_sync()
        self.assertEqual(self.ids(), [])

    def test_full_sync_message_moved_into_inbox_is_added(self):
        self.api.messages["incoming"] = full_message("incoming")
        self.api.history["100"] = [history_page("110", [event("labelsAdded", "incoming", ["INBOX"])])]
        self.sync.full_sync()
        self.assertEqual(self.ids(), ["incoming"])

    def test_multiple_transitions_use_final_current_state_once(self):
        self.api.messages["flip"] = full_message("flip", inbox=False)
        changes = [
            event("labelsAdded", "flip", ["INBOX"]),
            event("labelsRemoved", "flip", ["INBOX"]),
            event("labelsAdded", "flip", ["INBOX"]),
        ]
        self.api.history["100"] = [history_page("111", changes)]
        self.sync.full_sync()
        self.assertEqual(self.ids(), [])
        self.assertEqual(self.api.get_calls.count(("flip", "full")), 1)

    def test_full_get_failure_preserves_active_and_checkpoint(self):
        self.seed_old()
        self.api.listed = ["broken"]
        self.api.messages["broken"] = GmailApiError("temporary")
        with self.assertRaises(GmailApiError):
            self.sync.full_sync()
        self.assertEqual(self.ids(), ["old"])
        self.assertEqual(self.store.checkpoint(), "90")

    def test_full_history_failure_preserves_active_and_checkpoint(self):
        self.seed_old()
        self.api.listed = ["snapshot"]
        self.api.messages["snapshot"] = full_message("snapshot")
        self.api.history["100"] = GmailApiError("temporary")
        with self.assertRaises(GmailApiError):
            self.sync.full_sync()
        self.assertEqual(self.ids(), ["old"])
        self.assertEqual(self.store.checkpoint(), "90")

    def test_activation_failure_rolls_back_old_cache_and_checkpoint(self):
        self.seed_old()
        self.api.listed = ["snapshot"]
        self.api.messages["snapshot"] = full_message("snapshot")
        self.store.connection.execute(
            "CREATE TRIGGER fail_history_update BEFORE UPDATE OF value ON metadata "
            "WHEN OLD.key='history_id' BEGIN SELECT RAISE(ABORT, 'disk failure'); END"
        )
        self.store.connection.commit()
        with self.assertRaises(Exception):
            self.sync.full_sync()
        self.assertEqual(self.ids(), ["old"])
        self.assertEqual(self.store.checkpoint(), "90")

    def test_history_404_restarts_full_sync_with_new_profile_bounded(self):
        self.api.profiles = [{"historyId": "100"}, {"historyId": "200"}]
        self.api.history = {
            "100": GmailHistoryExpired("stale"),
            "200": [history_page("201")],
        }
        self.sync.full_sync()
        self.assertEqual(self.api.history_calls, ["100", "200"])
        self.assertEqual(self.store.checkpoint(), "201")

    def test_repeated_history_404_fails_without_looping(self):
        self.seed_old()
        self.api.profiles = [{"historyId": "100"}, {"historyId": "200"}]
        self.api.history = {
            "100": GmailHistoryExpired("stale"), "200": GmailHistoryExpired("stale")
        }
        with self.assertRaisesRegex(Exception, "repeatedly"):
            self.sync.full_sync()
        self.assertEqual(self.api.history_calls, ["100", "200"])
        self.assertEqual(self.ids(), ["old"])
        self.assertEqual(self.store.checkpoint(), "90")

    def test_incremental_history_pagination_and_specific_arrays(self):
        self.seed_old()
        self.api.messages.update({"add": full_message("add"), "label": full_message("label")})
        self.api.history["90"] = [
            history_page("91", [event("messagesAdded", "add")], "next"),
            history_page("92", [event("labelsAdded", "label", ["INBOX"])])
        ]
        self.sync.incremental_sync("90")
        self.assertEqual(self.ids(), ["add", "label", "old"])
        self.assertEqual(self.store.checkpoint(), "92")

    def test_incremental_delete_out_old_and_404_all_delete(self):
        for message_id in ("deleted", "out", "aged"):
            self.store.apply_incremental([("upsert", {
                "gmail_message_id": message_id, "thread_id": "t", "internal_date_ms": NOW_MS - 100,
                "sender": "x", "subject": "x", "date": "x", "received_local_date": "2026-08-19",
                "content": "x", "source_message_id": message_id, "raw_source": "",
            })], "90", NOW_MS - WINDOW_MS, NOW_MS - CALENDAR_WINDOW_MS)
        self.api.messages = {
            "deleted": GmailNotFound("gone"),
            "out": full_message("out", inbox=False),
            "aged": full_message("aged", age_ms=WINDOW_MS + 1),
        }
        changes = [
            event("messagesDeleted", "deleted"),
            event("labelsRemoved", "out", ["INBOX"]),
            event("messagesAdded", "aged"),
        ]
        self.api.history["90"] = [history_page("95", changes)]
        self.sync.incremental_sync("90")
        self.assertEqual(self.ids(), [])

    def test_sent_message_added_is_skipped_without_get(self):
        self.api.history["90"] = [
            history_page("91", [event("messagesAdded", "sent", ["SENT"])])
        ]

        self.sync.incremental_sync("90")

        self.assertEqual(self.api.get_calls, [])
        self.assertEqual(self.ids(), [])
        self.assertEqual(self.store.checkpoint(), "91")

    def test_message_added_without_label_ids_is_fetched_and_cached(self):
        self.seed_old()
        self.api.messages["new"] = full_message("new")
        self.api.history["90"] = [
            history_page("91", [message_added_without_labels("new")])
        ]

        self.sync.incremental_sync("90")

        self.assertEqual(self.ids(), ["new", "old"])
        self.assertEqual(self.api.get_calls, [("new", "full")])
        self.assertEqual(self.store.checkpoint(), "91")

    def test_message_added_without_labels_is_filtered_by_full_message_labels(self):
        self.api.messages["sent"] = full_message("sent", inbox=False)
        self.api.history["90"] = [
            history_page("91", [message_added_without_labels("sent")])
        ]

        self.sync.incremental_sync("90")

        self.assertEqual(self.api.get_calls, [("sent", "full")])
        self.assertEqual(self.ids(), [])
        self.assertEqual(self.store.checkpoint(), "91")

    def test_deleted_message_is_removed_without_get_in_incremental_sync(self):
        self.seed_old()
        self.api.history["90"] = [
            history_page("91", [event("messagesDeleted", "old")])
        ]

        self.sync.incremental_sync("90")

        self.assertEqual(self.api.get_calls, [])
        self.assertEqual(self.ids(), [])

    def test_full_sync_reconciliation_deletes_without_get(self):
        self.seed_old()
        self.api.history["100"] = [
            history_page("101", [event("messagesDeleted", "old")])
        ]

        self.sync.full_sync()

        self.assertEqual(self.api.get_calls, [])
        self.assertEqual(self.ids(), [])

    def test_full_sync_reconciliation_skips_sent_message_added_without_get(self):
        self.api.history["100"] = [
            history_page("101", [event("messagesAdded", "sent", ["SENT"])])
        ]

        self.sync.full_sync()

        self.assertEqual(self.api.get_calls, [])
        self.assertEqual(self.ids(), [])

    def test_cached_message_added_without_inbox_label_is_still_reconciled(self):
        self.seed_old()
        self.api.messages["old"] = full_message("old")
        self.api.history["90"] = [
            history_page("91", [event("messagesAdded", "old", ["SENT"])])
        ]

        self.sync.incremental_sync("90")

        self.assertEqual(self.ids(), ["old"])
        self.assertEqual(self.api.get_calls, [("old", "full")])

    def test_last_history_operation_wins_for_readded_message(self):
        self.api.messages["readded"] = full_message("readded")
        self.api.history["90"] = [
            history_page(
                "91",
                [
                    event("messagesDeleted", "readded"),
                    event("messagesAdded", "readded", ["INBOX"]),
                ],
            )
        ]

        self.sync.incremental_sync("90")

        self.assertEqual(self.ids(), ["readded"])
        self.assertEqual(self.api.get_calls, [("readded", "full")])

    def _today(self):
        return datetime.fromtimestamp(
            NOW_MS / 1000, ZoneInfo("Europe/Istanbul")
        ).date()

    def _forty_day_calendar(self, message_id="calendar-40d"):
        today = self._today()
        first_date = today - timedelta(days=40)
        calendar = (
            "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\n"
            "UID:synthetic-retention-series\n"
            f"DTSTART;TZID=Europe/Istanbul:{first_date:%Y%m%d}T100000\n"
            "RRULE:FREQ=DAILY;COUNT=41\n"
            "SUMMARY:Synthetic retention test\n"
            "END:VEVENT\nEND:VCALENDAR"
        )
        return calendar_message(
            message_id,
            age_ms=40 * 86400 * 1000,
            calendar=calendar,
        )

    def test_full_sync_keeps_40_day_calendar_invite_and_today_occurrence(self):
        self.api.listed_by_query = {
            FULL_SYNC_QUERIES[0]: [],
            FULL_SYNC_QUERIES[1]: ["calendar-40d"],
        }
        self.api.messages["calendar-40d"] = self._forty_day_calendar()

        self.sync.full_sync()

        digest = build_digest_result(
            self.store.records(), "daily", target_date=self._today()
        )
        self.assertEqual(self.ids(), ["calendar-40d"])
        self.assertEqual(self.store.retention_version(), "2")
        self.assertEqual(self.api.list_queries, list(FULL_SYNC_QUERIES))
        self.assertEqual(digest.count, 1)
        self.assertIn("10:00", digest.message)

    def test_incremental_sync_keeps_40_day_calendar_invite_and_today_occurrence(self):
        self.api.messages["calendar-40d"] = self._forty_day_calendar()
        self.api.history["90"] = [
            history_page("91", [event("messagesAdded", "calendar-40d")])
        ]

        self.sync.incremental_sync("90")

        digest = build_digest_result(
            self.store.records(), "daily", target_date=self._today()
        )
        self.assertEqual(self.ids(), ["calendar-40d"])
        self.assertEqual(digest.count, 1)
        self.assertIn("10:00", digest.message)

    def test_incremental_sync_removes_40_day_normal_mail(self):
        self.api.messages["normal-40d"] = full_message(
            "normal-40d", age_ms=40 * 86400 * 1000
        )
        self.api.history["90"] = [
            history_page("91", [event("messagesAdded", "normal-40d")])
        ]

        self.sync.incremental_sync("90")

        self.assertEqual(self.ids(), [])

    def test_full_sync_prunes_cached_400_day_calendar_invite(self):
        old_record = {
            "gmail_message_id": "calendar-400d",
            "thread_id": "synthetic-thread",
            "internal_date_ms": NOW_MS - 400 * 86400 * 1000,
            "sender": "Synthetic Organizer <organizer@example.test>",
            "subject": "Synthetic calendar event",
            "date": "synthetic date",
            "received_local_date": "synthetic date",
            "content": "",
            "source_message_id": "synthetic-400d@example.test",
            "raw_source": "BEGIN:VCALENDAR\\nEND:VCALENDAR",
        }
        self.store.stage_upsert(old_record)
        self.store.activate_staging(
            "90",
            NOW_MS - 500 * 86400 * 1000,
            NOW_MS - 500 * 86400 * 1000,
        )
        self.api.listed_by_query = {query: [] for query in FULL_SYNC_QUERIES}

        self.sync.full_sync()

        self.assertEqual(self.ids(), [])

    def test_irrelevant_label_event_does_not_fetch(self):
        self.seed_old()
        self.api.history["90"] = [history_page("91", [event("labelsAdded", "old", ["STARRED"])])]
        self.sync.incremental_sync("90")
        self.assertEqual(self.api.get_calls, [])
        self.assertEqual(self.store.checkpoint(), "91")

    def test_no_events_still_advances_to_final_response_history_id(self):
        self.seed_old()
        self.api.history["90"] = [history_page("99")]
        self.sync.incremental_sync("90")
        self.assertEqual(self.store.checkpoint(), "99")

    def test_incremental_required_failure_makes_no_db_mutation(self):
        self.seed_old()
        self.api.messages["new"] = GmailApiError("transient exhausted")
        self.api.history["90"] = [
            history_page("99", [message_added_without_labels("new")])
        ]
        with self.assertRaises(GmailApiError):
            self.sync.incremental_sync("90")
        self.assertEqual(self.ids(), ["old"])
        self.assertEqual(self.store.checkpoint(), "90")

    def test_incremental_auth_failure_keeps_checkpoint(self):
        self.seed_old()
        self.api.messages["new"] = GmailAuthError("revoked")
        self.api.history["90"] = [history_page("99", [event("messagesAdded", "new")])]
        with self.assertRaises(GmailAuthError):
            self.sync.incremental_sync("90")
        self.assertEqual(self.ids(), ["old"])
        self.assertEqual(self.store.checkpoint(), "90")

    def test_incremental_history_404_runs_full_sync(self):
        self.seed_old()
        self.api.history["90"] = GmailHistoryExpired("stale")
        self.api.history["100"] = [history_page("101")]
        self.sync.incremental_sync("90")
        self.assertEqual(self.store.checkpoint(), "101")

    def test_unchanged_incremental_does_not_list_or_get_mailbox(self):
        self.seed_old()
        self.api.history["90"] = [history_page("91")]
        self.sync.sync()
        self.assertEqual(self.api.get_calls, [])
        self.assertEqual(self.store.checkpoint(), "91")

    def test_legacy_retention_cache_full_syncs_once_then_uses_incremental(self):
        self.seed_old()
        with self.store.connection:
            self.store.connection.execute(
                "DELETE FROM metadata WHERE key='retention_version'"
            )
        self.api.listed_by_query = {
            FULL_SYNC_QUERIES[0]: [],
            FULL_SYNC_QUERIES[1]: ["calendar-40d"],
        }
        self.api.messages["calendar-40d"] = self._forty_day_calendar()
        self.api.history["100"] = [history_page("101")]

        self.sync.sync()

        self.assertEqual(self.ids(), ["calendar-40d"])
        self.assertEqual(self.store.retention_version(), "2")
        self.assertEqual(self.api.list_queries, list(FULL_SYNC_QUERIES))

        self.api.history["101"] = [history_page("102")]
        self.sync.sync()

        self.assertEqual(self.api.list_queries, list(FULL_SYNC_QUERIES))
        self.assertEqual(self.api.history_calls[-1], "101")
        self.assertEqual(self.store.checkpoint(), "102")

    def test_unknown_retention_version_triggers_full_sync(self):
        self.seed_old()
        with self.store.connection:
            self.store.connection.execute(
                "UPDATE metadata SET value='1' WHERE key='retention_version'"
            )
        self.api.listed_by_query = {query: [] for query in FULL_SYNC_QUERIES}
        self.api.history["100"] = [history_page("101")]

        self.sync.sync()

        self.assertEqual(self.api.list_queries, list(FULL_SYNC_QUERIES))
        self.assertEqual(self.store.retention_version(), "2")

    def test_failed_retention_upgrade_continues_incrementally(self):
        self.seed_old()
        with self.store.connection:
            self.store.connection.execute(
                "DELETE FROM metadata WHERE key='retention_version'"
            )
        self.api.profiles = [GmailApiError("Gmail API request failed (HTTP 403)")]
        self.api.history["90"] = [history_page("91")]

        with patch("mail_digest.sources.gmail.sync.log") as logger:
            records = self.sync.sync()

        self.assertEqual([record["source_message_id"] for record in records], ["old"])
        self.assertEqual(self.store.checkpoint(), "91")
        self.assertIsNone(self.store.retention_version())
        self.assertEqual(
            self.store.retention_upgrade_last_attempt_ms(), NOW_MS
        )
        logger.assert_called_once_with(
            "Retention upgrade full sync failed "
            "(GmailApiError: Gmail API request failed (HTTP 403)); "
            "continuing with incremental sync"
        )

    def test_retention_upgrade_is_skipped_within_six_hours(self):
        self.seed_old()
        with self.store.connection:
            self.store.connection.execute(
                "DELETE FROM metadata WHERE key='retention_version'"
            )
        self.store.set_retention_upgrade_last_attempt_ms(
            NOW_MS - RETENTION_UPGRADE_RETRY_INTERVAL_MS + 1
        )
        prior_failure = GmailApiError("must not run full sync")
        self.api.profiles = [prior_failure, {"historyId": "100"}]
        self.api.history["90"] = [history_page("91")]

        self.sync.sync()

        self.assertEqual(self.api.profile_calls, 0)
        self.assertEqual(self.api.profiles[0], prior_failure)
        self.assertEqual(self.api.list_queries, [])
        self.assertEqual(self.api.history_calls, ["90"])
        self.assertIsNone(self.store.retention_version())

    def test_retention_upgrade_retries_after_six_hours_and_sets_version(self):
        self.seed_old()
        with self.store.connection:
            self.store.connection.execute(
                "UPDATE metadata SET value='1' WHERE key='retention_version'"
            )
        self.store.set_retention_upgrade_last_attempt_ms(
            NOW_MS - RETENTION_UPGRADE_RETRY_INTERVAL_MS
        )
        self.api.listed_by_query = {query: [] for query in FULL_SYNC_QUERIES}
        self.api.history["100"] = [history_page("101")]

        self.sync.sync()

        self.assertEqual(self.api.profile_calls, 1)
        self.assertEqual(self.api.list_queries, list(FULL_SYNC_QUERIES))
        self.assertEqual(self.store.retention_version(), "2")

    def test_initial_full_sync_failure_does_not_fall_back_or_mark_upgrade(self):
        self.api.profiles = [GmailApiError("initial full sync failure")]

        with self.assertRaises(GmailApiError):
            self.sync.sync()

        self.assertIsNone(self.store.checkpoint())
        self.assertIsNone(self.store.retention_upgrade_last_attempt_ms())

    def test_exact_cutoff_is_inclusive_and_prunes_older(self):
        self.api.listed = ["edge", "old"]
        self.api.messages = {
            "edge": full_message("edge", age_ms=WINDOW_MS),
            "old": full_message("old", age_ms=WINDOW_MS + 1),
        }
        self.sync.full_sync()
        self.assertEqual(self.ids(), ["edge"])

    def test_process_restart_reads_existing_cache_and_checkpoint(self):
        self.seed_old()
        self.store.close()
        self.store = GmailStore(self.path)
        self.assertEqual(self.store.checkpoint(), "90")
        self.assertEqual(self.ids(), ["old"])
        self.assertEqual(self.store.records()[0]["thread_id"], "t")

    def test_replay_same_history_is_idempotent(self):
        self.seed_old()
        self.api.messages["new"] = full_message("new")
        page = history_page("91", [event("messagesAdded", "new"), event("messagesAdded", "new")])
        self.api.history.update({"90": [page], "91": [page]})
        self.sync.incremental_sync("90")
        self.sync.incremental_sync("91")
        self.assertEqual(self.ids(), ["new", "old"])


if __name__ == "__main__":
    unittest.main()
