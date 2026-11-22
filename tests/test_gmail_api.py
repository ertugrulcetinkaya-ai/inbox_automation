import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from googleapiclient.errors import HttpError

from mail_digest.sources.gmail.api import GmailApi, GmailApiError, GmailHistoryExpired, GmailNotFound


class Response(dict):
    status = 500
    reason = "error"


def http_error(status, reason="backendError", headers=None, uri=None):
    response = Response()
    response.status = status
    response.update(headers or {})
    return HttpError(
        response,
        json.dumps({"error": {"errors": [{"reason": reason}]}}).encode(),
        uri=uri,
    )


class Request:
    def __init__(self, outcomes):
        self.outcomes = outcomes

    def execute(self):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeBatch:
    def __init__(self, service):
        self.service = service
        self.requests = []

    def add(self, request, request_id, callback):
        self.requests.append((request_id, request, callback))

    def execute(self):
        self.service.batch_sizes.append(len(self.requests))
        for request_id, _request, callback in self.requests:
            outcome = self.service.batch_outcomes[request_id].pop(0)
            if isinstance(outcome, Exception):
                callback(request_id, None, outcome)
            else:
                callback(request_id, outcome, None)


class FakeBatchService:
    def __init__(self, batch_outcomes, individual_outcomes=None):
        self.batch_outcomes = batch_outcomes
        self.individual_outcomes = individual_outcomes or {}
        self.batch_sizes = []

    def new_batch_http_request(self):
        return FakeBatch(self)

    def users(self):
        return self

    def messages(self):
        return self

    def get(self, userId, id, format):
        outcomes = self.individual_outcomes.setdefault(id, [{"id": id, "format": format}])
        return Request(outcomes)


class GmailApiTests(unittest.TestCase):
    def test_batch_settings_are_shared_and_environment_configurable(self):
        environment = os.environ.copy()
        environment.update(
            {"GMAIL_BATCH_SIZE": "4", "GMAIL_BATCH_PAUSE_SECONDS": "0.25"}
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from mail_digest.config import gmail_batch_size, gmail_batch_pause_seconds; "
                "print(gmail_batch_size(), gmail_batch_size(), gmail_batch_pause_seconds())",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "4 4 0.25")

        environment["GMAIL_BATCH_SIZE"] = "51"
        invalid = subprocess.run(
            [
                sys.executable,
                "-c",
                "import telegram_listener; "
                "from mail_digest.config import gmail_batch_size; "
                "gmail_batch_size()",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("GMAIL_BATCH_SIZE must be between 1 and 50", invalid.stderr)

    def test_api_uses_batch_and_pause_environment_at_operation_time(self):
        ids = ["synthetic-1", "synthetic-2", "synthetic-3"]
        service = FakeBatchService(
            {message_id: [{"id": message_id}] for message_id in ids}
        )
        sleeps = []
        api = GmailApi(service, sleep=sleeps.append)

        with patch.dict(
            os.environ,
            {"GMAIL_BATCH_SIZE": "2", "GMAIL_BATCH_PAUSE_SECONDS": "0.25"},
        ):
            self.assertEqual(len(api.get_messages_batch(ids)), 3)
        self.assertEqual(service.batch_sizes, [2, 1])
        self.assertEqual(sleeps, [0.25])

        service = FakeBatchService(
            {message_id: [{"id": message_id}] for message_id in ids}
        )
        api = GmailApi(service, sleep=sleeps.append)
        with patch.dict(
            os.environ,
            {"GMAIL_BATCH_SIZE": "1", "GMAIL_BATCH_PAUSE_SECONDS": "0"},
        ):
            self.assertEqual(len(api.get_messages_batch(ids)), 3)
        self.assertEqual(service.batch_sizes, [1, 1, 1])
        self.assertEqual(sleeps, [0.25])

    def test_transient_failure_retries_with_bound(self):
        outcomes = [http_error(503), {"ok": True}]
        sleeps = []
        api = GmailApi(None, max_attempts=3, sleep=sleeps.append)
        self.assertEqual(api._execute(lambda: Request(outcomes)), {"ok": True})
        self.assertEqual(len(sleeps), 1)

    def test_rate_limit_403_retries_but_auth_401_does_not(self):
        rate_outcomes = [http_error(403, "rateLimitExceeded"), {"ok": True}]
        api = GmailApi(None, max_attempts=2, sleep=lambda _: None)
        self.assertEqual(api._execute(lambda: Request(rate_outcomes)), {"ok": True})
        with self.assertRaises(GmailApiError):
            api._execute(lambda: Request([http_error(401)]))

    def test_http_error_message_has_only_status_and_reason(self):
        private_uri = (
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/"
            "synthetic-message-id?format=full"
        )
        error = http_error(
            403,
            "rateLimitExceeded",
            uri=private_uri,
        )
        api = GmailApi(None, sleep=lambda _: None)

        with self.assertRaises(GmailApiError) as raised:
            api._execute(lambda: Request([error] * 6))

        message = str(raised.exception)
        self.assertEqual(
            message,
            "Gmail API request failed (HTTP 403 rateLimitExceeded)",
        )
        self.assertNotIn("synthetic-message-id", message)
        self.assertNotIn("googleapis.com", message)

    def test_network_error_message_contains_exception_type_only(self):
        api = GmailApi(None, max_attempts=1, sleep=lambda _: None)

        with self.assertRaises(GmailApiError) as raised:
            api._execute(
                lambda: Request([TimeoutError("private-request-details")])
            )

        self.assertEqual(
            str(raised.exception),
            "Gmail API network request failed (TimeoutError)",
        )
        self.assertNotIn("private-request-details", str(raised.exception))

    def test_message_and_history_404_have_distinct_semantics(self):
        api = GmailApi(None, max_attempts=1)
        with self.assertRaises(GmailNotFound):
            api._execute(lambda: Request([http_error(404)]), not_found="message")
        with self.assertRaises(GmailHistoryExpired):
            api._execute(lambda: Request([http_error(404)]), not_found="history")

    def test_message_list_uses_caller_query_and_500_page_size(self):
        request = Mock()
        request.execute.return_value = {"messages": [{"id": "a"}]}
        messages_resource = Mock()
        messages_resource.list.return_value = request
        users = Mock()
        users.messages.return_value = messages_resource
        service = Mock()
        service.users.return_value = users
        api = GmailApi(service)
        query = "newer_than:367d filename:ics"
        self.assertEqual(list(api.iter_message_ids(query)), ["a"])
        messages_resource.list.assert_called_once_with(
            userId="me", labelIds=["INBOX"], q=query, maxResults=500
        )

    def test_history_requests_only_message_and_label_changes_without_label_filter(self):
        request = Mock()
        request.execute.return_value = {"historyId": "91"}
        history_resource = Mock()
        history_resource.list.return_value = request
        users = Mock()
        users.history.return_value = history_resource
        service = Mock()
        service.users.return_value = users

        list(GmailApi(service).iter_history("90"))

        history_resource.list.assert_called_once_with(
            userId="me",
            startHistoryId="90",
            historyTypes=["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"],
        )

    def test_batch_fetches_in_configured_groups(self):
        from mail_digest.config import gmail_batch_size

        batch_size = gmail_batch_size()
        ids = [f"synthetic-{index}" for index in range(2 * batch_size + 1)]
        service = FakeBatchService(
            {message_id: [{"id": message_id}] for message_id in ids}
        )

        results = GmailApi(service, sleep=lambda _: None).get_messages_batch(ids)

        self.assertEqual(len(results), len(ids))
        self.assertEqual(
            service.batch_sizes,
            [batch_size, batch_size, 1],
        )

    def test_batch_pause_is_injected_between_groups(self):
        from mail_digest.config import gmail_batch_size

        ids = [f"synthetic-{index}" for index in range(gmail_batch_size() + 1)]
        service = FakeBatchService(
            {message_id: [{"id": message_id}] for message_id in ids}
        )
        sleeps = []

        results = GmailApi(
            service, sleep=sleeps.append, batch_pause_seconds=0.25
        ).get_messages_batch(ids)

        self.assertEqual(len(results), len(ids))
        self.assertEqual(sleeps, [0.25])

    def test_batch_marks_individual_404_as_not_found_result(self):
        service = FakeBatchService({"gone": [http_error(404)], "live": [{"id": "live"}]})

        results = GmailApi(service).get_messages_batch(["gone", "live"])

        self.assertIsInstance(results["gone"], GmailNotFound)
        self.assertEqual(results["live"], {"id": "live"})

    def test_batch_retries_rate_limits_in_smaller_batches_and_other_transients_singly(self):
        transient_errors = [
            http_error(429),
            http_error(503),
            http_error(403, "rateLimitExceeded"),
        ]
        ids = ["rate-429", "server-503", "rate-403"]
        service = FakeBatchService(
            {
                "rate-429": [transient_errors[0], {"id": "rate-429"}],
                "server-503": [transient_errors[1]],
                "rate-403": [transient_errors[2], {"id": "rate-403"}],
            },
            {"server-503": [transient_errors[1], {"id": "server-503"}]},
        )
        sleeps = []

        with patch("mail_digest.sources.gmail.api.random.uniform", return_value=0):
            results = GmailApi(
                service,
                max_attempts=2,
                sleep=sleeps.append,
                batch_pause_seconds=0,
            ).get_messages_batch(ids)

        self.assertEqual(set(results), set(ids))
        self.assertEqual(sleeps, [0.25, 1])
        self.assertEqual(service.batch_sizes, [3, 1, 1])
        self.assertEqual(service.individual_outcomes["server-503"], [])
        self.assertEqual(len(service.individual_outcomes["rate-429"]), 1)
        self.assertEqual(len(service.individual_outcomes["rate-403"]), 1)

    def test_rate_limited_batch_items_retry_together_in_a_half_sized_batch(self):
        ids = ["a", "b", "c", "d"]
        rate_limit = http_error(403, "rateLimitExceeded")
        service = FakeBatchService(
            {
                "a": [{"id": "a"}],
                "b": [{"id": "b"}],
                "c": [rate_limit, {"id": "c"}],
                "d": [rate_limit, {"id": "d"}],
            }
        )
        sleeps = []

        with patch("mail_digest.sources.gmail.api.random.uniform", return_value=0):
            results = GmailApi(
                service, sleep=sleeps.append, batch_pause_seconds=0
            ).get_messages_batch(ids)

        self.assertEqual(set(results), set(ids))
        self.assertEqual(service.batch_sizes, [4, 2])
        self.assertEqual(sleeps, [1])

    def test_persistent_rate_limited_item_raises_after_six_batch_attempts(self):
        rate_limit = http_error(429)
        service = FakeBatchService({"stuck": [rate_limit] * 6})
        sleeps = []

        with patch("mail_digest.sources.gmail.api.random.uniform", return_value=0):
            with self.assertRaises(GmailApiError):
                GmailApi(
                    service, sleep=sleeps.append, batch_pause_seconds=0
                ).get_messages_batch(["stuck"])

        self.assertEqual(service.batch_sizes, [1] * 6)
        self.assertEqual(sleeps, [1, 2, 4, 8, 16])

    def test_retry_after_sets_the_minimum_rate_limit_delay(self):
        outcomes = [
            http_error(429, headers={"Retry-After": "9"}),
            {"ok": True},
        ]
        sleeps = []

        with patch("mail_digest.sources.gmail.api.random.uniform", return_value=0):
            result = GmailApi(None, sleep=sleeps.append)._execute(
                lambda: Request(outcomes)
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(sleeps, [9.0])

    def test_batch_nontransient_failure_raises(self):
        service = FakeBatchService({"unauthorized": [http_error(401)]})

        with self.assertRaises(GmailApiError):
            GmailApi(service).get_messages_batch(["unauthorized"])


if __name__ == "__main__":
    unittest.main()
