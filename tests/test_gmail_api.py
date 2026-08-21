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

    def test_message_list_uses_inbox_31day_superset_and_500_page_size(self):
        request = Mock()
        request.execute.return_value = {"messages": [{"id": "a"}]}
        messages_resource = Mock()
        messages_resource.list.return_value = request
        users = Mock()
        users.messages.return_value = messages_resource
        service = Mock()
        service.users.return_value = users
        api = GmailApi(service)
        self.assertEqual(list(api.iter_message_ids()), ["a"])
        messages_resource.list.assert_called_once_with(
            userId="me", labelIds=["INBOX"], q="newer_than:31d", maxResults=500
        )


if __name__ == "__main__":
    unittest.main()
