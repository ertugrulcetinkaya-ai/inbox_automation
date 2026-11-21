"""Small read-only Gmail API wrapper with bounded transient retries."""

from __future__ import annotations

import random
import socket
import time

import httplib2
from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError

from ...config import GMAIL_BATCH_PAUSE_SECONDS, GMAIL_BATCH_SIZE


class GmailApiError(RuntimeError):
    pass


class GmailNotFound(GmailApiError):
    pass


class GmailHistoryExpired(GmailApiError):
    pass


class GmailApi:
    def __init__(
        self,
        service,
        max_attempts=3,
        sleep=time.sleep,
        batch_pause_seconds=GMAIL_BATCH_PAUSE_SECONDS,
    ):
        self.service = service
        self.max_attempts = max_attempts
        self.sleep = sleep
        self.batch_pause_seconds = batch_pause_seconds

    @staticmethod
    def _status(exc):
        return getattr(getattr(exc, "resp", None), "status", None)

    @staticmethod
    def _is_retryable_http(exc):
        status = GmailApi._status(exc)
        content = getattr(exc, "content", b"")
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="ignore")
        temporary_rate_limit = status == 403 and any(
            reason in content for reason in ("rateLimitExceeded", "userRateLimitExceeded")
        )
        return status in {429, 500, 502, 503, 504} or temporary_rate_limit

    def _execute(self, request_factory, not_found="error"):
        for attempt in range(self.max_attempts):
            try:
                return request_factory().execute()
            except HttpError as exc:
                status = self._status(exc)
                if status == 404:
                    if not_found == "message":
                        raise GmailNotFound("Gmail message no longer exists") from exc
                    if not_found == "history":
                        raise GmailHistoryExpired("Gmail history checkpoint expired") from exc
                if not self._is_retryable_http(exc) or attempt + 1 >= self.max_attempts:
                    raise GmailApiError(f"Gmail API request failed (HTTP {status or 'unknown'})") from exc
            except (OSError, socket.timeout, TransportError, httplib2.HttpLib2Error) as exc:
                if attempt + 1 >= self.max_attempts:
                    raise GmailApiError("Gmail API network request failed") from exc
            delay = min(4.0, 0.25 * (2 ** attempt)) + random.uniform(0, 0.1)
            self.sleep(delay)
        raise GmailApiError("Gmail API retry budget exhausted")

    def get_messages_batch(self, ids, format="full"):
        """Fetch configured message groups, retrying transient per-message errors singly."""

        message_ids = list(dict.fromkeys(ids))
        results = {}
        for offset in range(0, len(message_ids), GMAIL_BATCH_SIZE):
            group = message_ids[offset:offset + GMAIL_BATCH_SIZE]
            responses = {}
            failures = {}

            def callback(request_id, response, exception):
                if exception is None:
                    responses[request_id] = response
                else:
                    failures[request_id] = exception

            def execute_batch():
                responses.clear()
                failures.clear()
                batch = self.service.new_batch_http_request()
                for message_id in group:
                    request = self.service.users().messages().get(
                        userId="me", id=message_id, format=format
                    )
                    batch.add(request, request_id=message_id, callback=callback)
                return batch

            self._execute(execute_batch)
            retry_ids = []
            for message_id, exception in failures.items():
                if isinstance(exception, HttpError):
                    status = self._status(exception)
                    if status == 404:
                        results[message_id] = GmailNotFound(
                            "Gmail message no longer exists"
                        )
                    elif self._is_retryable_http(exception):
                        retry_ids.append(message_id)
                    else:
                        raise GmailApiError(
                            f"Gmail API request failed (HTTP {status or 'unknown'})"
                        ) from exception
                elif isinstance(
                    exception,
                    (OSError, socket.timeout, TransportError, httplib2.HttpLib2Error),
                ):
                    retry_ids.append(message_id)
                else:
                    raise GmailApiError("Gmail batch request failed") from exception

            for message_id in retry_ids:
                try:
                    results[message_id] = self.get_message(message_id, format=format)
                except GmailNotFound as exc:
                    results[message_id] = exc
            results.update(responses)
            if offset + GMAIL_BATCH_SIZE < len(message_ids):
                self.sleep(self.batch_pause_seconds)
        return results

    def get_profile(self):
        return self._execute(
            lambda: self.service.users().getProfile(userId="me")
        )

    def iter_message_ids(self, query):
        page_token = None
        while True:
            kwargs = {
                "userId": "me",
                "labelIds": ["INBOX"],
                "q": query,
                "maxResults": 500,
            }
            if page_token:
                kwargs["pageToken"] = page_token
            page = self._execute(
                lambda kwargs=kwargs: self.service.users().messages().list(**kwargs)
            )
            for message in page.get("messages", []):
                message_id = message.get("id")
                if message_id:
                    yield message_id
            page_token = page.get("nextPageToken")
            if not page_token:
                break

    def get_message(self, message_id, format="full"):
        return self._execute(
            lambda: self.service.users().messages().get(
                userId="me", id=message_id, format=format
            ),
            not_found="message",
        )

    def get_attachment(self, message_id, attachment_id):
        return self._execute(
            lambda: self.service.users().messages().attachments().get(
                userId="me", messageId=message_id, id=attachment_id
            )
        )

    def iter_history(self, start_history_id):
        page_token = None
        while True:
            kwargs = {
                "userId": "me",
                "startHistoryId": start_history_id,
                "historyTypes": [
                    "messageAdded",
                    "messageDeleted",
                    "labelAdded",
                    "labelRemoved",
                ],
            }
            if page_token:
                kwargs["pageToken"] = page_token
            page = self._execute(
                lambda kwargs=kwargs: self.service.users().history().list(**kwargs),
                not_found="history",
            )
            yield page
            page_token = page.get("nextPageToken")
            if not page_token:
                break
