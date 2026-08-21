import base64
import tempfile
import unittest
from pathlib import Path

from mail_digest.sources.gmail.api import GmailApiError, GmailHistoryExpired, GmailNotFound
from mail_digest.sources.gmail.auth import GmailAuthError
from mail_digest.sources.gmail.store import GmailStore
from mail_digest.sources.gmail.sync import GmailSynchronizer, WINDOW_MS


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


def history_page(history_id, records=None, next_token=None):
    page = {"historyId": str(history_id), "history": records or []}
    if next_token:
        page["nextPageToken"] = next_token
    return page


def event(array_name, message_id, labels=None):
    value = {"message": {"id": message_id}}
    if labels is not None:
        value["labelIds"] = labels
    return {array_name: [value]}


class FakeApi:
    def __init__(self):
        self.profiles = [{"historyId": "100"}]
        self.listed = []
        self.history = {"100": [history_page("101")]}
        self.messages = {}
        self.get_calls = []
        self.history_calls = []

    def get_profile(self):
        value = self.profiles.pop(0) if len(self.profiles) > 1 else self.profiles[0]
        if isinstance(value, Exception):
            raise value
        return value

    def iter_message_ids(self):
        yield from self.listed

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

    def get_attachment(self, message_id, attachment_id):
        raise AssertionError("unexpected attachment call")


class GmailSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "cache.sqlite3"
        self.store = GmailStore(self.path)
        self.api = FakeApi()
        self.sync = GmailSynchronizer(self.api, self.store, now_ms=lambda: NOW_MS)

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
        self.store.apply_incremental([("upsert", record)], "90", NOW_MS - WINDOW_MS)

    def test_full_sync_arrival_after_boundary_is_reconciled(self):
        self.api.messages["new"] = full_message("new")
        self.api.history["100"] = [history_page("110", [event("messagesAdded", "new")])]
        self.sync.full_sync()
        self.assertEqual(self.ids(), ["new"])
        self.assertEqual(self.store.checkpoint(), "110")

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
            })], "90", NOW_MS - WINDOW_MS)
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
        self.api.history["90"] = [history_page("99", [event("messagesAdded", "new")])]
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
