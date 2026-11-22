"""Small read-only Gmail API wrapper with bounded transient retries."""

from __future__ import annotations

import json
import random
import re
import socket
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import cast

import httplib2
from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError

from ...config import gmail_batch_pause_seconds, gmail_batch_size

RATE_LIMIT_MAX_ATTEMPTS = 6


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
        batch_pause_seconds=None,
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
        return (
            status in {429, 500, 502, 503, 504}
            or GmailApi._is_rate_limit_http(exc)
        )

    @staticmethod
    def _google_reason(exc):
        content = getattr(exc, "content", b"")
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="ignore")
        try:
            data = json.loads(content)
        except (TypeError, ValueError):
            return ""
        if not isinstance(data, dict):
            return ""
        error_data = data.get("error")
        errors = error_data.get("errors", []) if isinstance(error_data, dict) else []
        if not isinstance(errors, list):
            return ""
        return ",".join(
            reason
            for error in errors
            if isinstance(error, dict)
            and isinstance(error.get("reason"), str)
            for reason in [error["reason"]]
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", reason)
        )

    @staticmethod
    def _http_error_message(exc):
        status = GmailApi._status(exc)
        status_text = str(status) if status is not None else "unknown"
        reason = GmailApi._google_reason(exc)
        if reason:
            status_text = f"{status_text} {reason}"
        return f"Gmail API request failed (HTTP {status_text})"

    @staticmethod
    def _retry_after_seconds(exc):
        response = cast(Mapping[str, object] | None, getattr(exc, "resp", None))
        if response is None:
            return None
        value = next(
            (
                str(header_value)
                for key, header_value in response.items()
                if key.lower() == "retry-after"
            ),
            None,
        )
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())

    @staticmethod
    def _is_rate_limit_http(exc):
        status = GmailApi._status(exc)
        return status == 429 or (
            status == 403
            and bool(
                set(GmailApi._google_reason(exc).split(","))
                & {"rateLimitExceeded", "userRateLimitExceeded"}
            )
        )

    @staticmethod
    def _rate_limit_delay(attempt):
        return min(32, 2 ** attempt) + random.uniform(0, 1)

    def _pause_between_batches(self):
        pause_seconds = (
            gmail_batch_pause_seconds()
            if self.batch_pause_seconds is None
            else self.batch_pause_seconds
        )
        if pause_seconds > 0:
            self.sleep(pause_seconds)

    @classmethod
    def _rate_limit_delay_for_errors(cls, exceptions, attempt):
        delay = cls._rate_limit_delay(attempt)
        retry_after_values = [
            value
            for value in (cls._retry_after_seconds(exc) for exc in exceptions)
            if value is not None
        ]
        if retry_after_values:
            delay = max(delay, max(retry_after_values))
        return delay

    def _execute(self, request_factory, not_found="error"):
        for attempt in range(max(self.max_attempts, RATE_LIMIT_MAX_ATTEMPTS)):
            try:
                return request_factory().execute()
            except HttpError as exc:
                status = self._status(exc)
                if status == 404:
                    if not_found == "message":
                        raise GmailNotFound("Gmail message no longer exists") from exc
                    if not_found == "history":
                        raise GmailHistoryExpired("Gmail history checkpoint expired") from exc
                rate_limited = self._is_rate_limit_http(exc)
                max_attempts = RATE_LIMIT_MAX_ATTEMPTS if rate_limited else self.max_attempts
                if not self._is_retryable_http(exc) or attempt + 1 >= max_attempts:
                    raise GmailApiError(self._http_error_message(exc)) from exc
                if rate_limited:
                    delay = self._rate_limit_delay_for_errors([exc], attempt)
                else:
                    delay = min(4.0, 0.25 * (2 ** attempt)) + random.uniform(0, 0.1)
            except (OSError, socket.timeout, TransportError, httplib2.HttpLib2Error) as exc:
                if attempt + 1 >= self.max_attempts:
                    raise GmailApiError(
                        f"Gmail API network request failed ({type(exc).__name__})"
                    ) from exc
                delay = min(4.0, 0.25 * (2 ** attempt)) + random.uniform(0, 0.1)
            self.sleep(delay)
        raise GmailApiError("Gmail API retry budget exhausted")

    def _execute_batch(self, group, format):
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
        return responses, failures

    def _fetch_batch_group(self, group, format):
        results = {}
        pending = list(group)
        batch_size = max(1, len(group))
        for attempt in range(RATE_LIMIT_MAX_ATTEMPTS):
            rate_limited = []
            rate_limit_errors = []
            retry_ids = []
            for offset in range(0, len(pending), batch_size):
                attempt_group = pending[offset:offset + batch_size]
                responses, failures = self._execute_batch(attempt_group, format)
                results.update(responses)
                if offset + batch_size < len(pending):
                    self._pause_between_batches()
                for message_id, exception in failures.items():
                    if isinstance(exception, HttpError):
                        status = self._status(exception)
                        if status == 404:
                            results[message_id] = GmailNotFound(
                                "Gmail message no longer exists"
                            )
                        elif self._is_rate_limit_http(exception):
                            rate_limited.append(message_id)
                            rate_limit_errors.append(exception)
                        elif self._is_retryable_http(exception):
                            retry_ids.append(message_id)
                        else:
                            raise GmailApiError(self._http_error_message(exception)) from exception
                    elif isinstance(
                        exception,
                        (OSError, socket.timeout, TransportError, httplib2.HttpLib2Error),
                    ):
                        retry_ids.append(message_id)
                    else:
                        raise GmailApiError(
                            f"Gmail batch request failed ({type(exception).__name__})"
                        ) from exception

            for message_id in retry_ids:
                try:
                    results[message_id] = self.get_message(message_id, format=format)
                except GmailNotFound as exc:
                    results[message_id] = exc

            if not rate_limited:
                return results
            last_error = rate_limit_errors[-1]
            if attempt + 1 >= RATE_LIMIT_MAX_ATTEMPTS:
                raise GmailApiError(self._http_error_message(last_error)) from last_error
            self.sleep(self._rate_limit_delay_for_errors(rate_limit_errors, attempt))
            pending = rate_limited
            batch_size = max(1, batch_size // 2)
        raise GmailApiError("Gmail batch retry budget exhausted")

    def get_messages_batch(self, ids, format="full"):
        """Fetch configured message groups with smaller retry batches for rate limits."""

        message_ids = list(dict.fromkeys(ids))
        results = {}
        batch_size = gmail_batch_size()
        for offset in range(0, len(message_ids), batch_size):
            group = message_ids[offset:offset + batch_size]
            results.update(self._fetch_batch_group(group, format))
            if offset + batch_size < len(message_ids):
                self._pause_between_batches()
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
