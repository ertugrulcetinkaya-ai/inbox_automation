import json
import unittest
from unittest.mock import Mock

from googleapiclient.errors import HttpError

from mail_digest.sources.gmail.api import GmailApi, GmailApiError, GmailHistoryExpired, GmailNotFound


class Response(dict):
    status = 500
    reason = "error"


def http_error(status, reason="backendError"):
    response = Response()
    response.status = status
    return HttpError(response, json.dumps({"error": {"errors": [{"reason": reason}]}}).encode())


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

    def test_batch_fetches_in_groups_of_fifty(self):
        ids = [f"synthetic-{index}" for index in range(101)]
        service = FakeBatchService(
            {message_id: [{"id": message_id}] for message_id in ids}
        )

        results = GmailApi(service).get_messages_batch(ids)

        self.assertEqual(len(results), 101)
        self.assertEqual(service.batch_sizes, [50, 50, 1])

    def test_batch_marks_individual_404_as_not_found_result(self):
        service = FakeBatchService({"gone": [http_error(404)], "live": [{"id": "live"}]})

        results = GmailApi(service).get_messages_batch(["gone", "live"])

        self.assertIsInstance(results["gone"], GmailNotFound)
        self.assertEqual(results["live"], {"id": "live"})

    def test_batch_retries_rate_limit_and_server_errors_individually(self):
        transient_errors = [
            http_error(429),
            http_error(503),
            http_error(403, "rateLimitExceeded"),
        ]
        ids = ["rate-429", "server-503", "rate-403"]
        service = FakeBatchService(
            dict(zip(ids, [[error] for error in transient_errors])),
            {
                message_id: [error, {"id": message_id}]
                for message_id, error in zip(ids, transient_errors)
            },
        )
        sleeps = []

        results = GmailApi(service, max_attempts=2, sleep=sleeps.append).get_messages_batch(ids)

        self.assertEqual(set(results), set(ids))
        self.assertEqual(len(sleeps), 3)
        self.assertEqual(service.batch_sizes, [3])

    def test_batch_nontransient_failure_raises(self):
        service = FakeBatchService({"unauthorized": [http_error(401)]})

        with self.assertRaises(GmailApiError):
            GmailApi(service).get_messages_batch(["unauthorized"])


if __name__ == "__main__":
    unittest.main()
