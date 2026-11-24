"""Offline command, access-control, state, and polling tests for the listener."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from mail_digest.commands import MEETING_COMMANDS, meeting_help_text
from mail_digest.config import PROJECT_ROOT
from mail_digest.delivery.commands import CommandWorker
from mail_digest.services.log_rotation import LISTENER_LOG_NAMES
from telegram_listener import (
    DONE,
    FAILED,
    GET_UPDATES_CONFLICT_MESSAGE,
    RETRY,
    TelegramConfigurationError,
    TelegramUpdateStateError,
    UNKNOWN_COMMAND_MESSAGE,
    _listener_conflict_backoff_seconds,
    _load_listener_state,
    _load_processed_update_ids,
    _rotate_listener_logs_if_due,
    main,
    process_update,
)


class TelegramListenerCommandTests(unittest.TestCase):
    def update(self, update_id, command, *, chat_id="fixture-chat", chat_type="private", user_id=10):
        return {
            "update_id": update_id,
            "message": {
                "chat": {"id": chat_id, "type": chat_type},
                "from": {"id": user_id},
                "text": command,
            },
        }

    def test_listener_checks_log_rotation_at_most_hourly(self):
        with patch("telegram_listener.rotate_logs") as rotate:
            self.assertEqual(_rotate_listener_logs_if_due(100, now=3699), 100)
            rotate.assert_not_called()

            self.assertEqual(_rotate_listener_logs_if_due(100, now=3700), 3700)
            rotate.assert_called_once()
            rotate.assert_called_with(
                PROJECT_ROOT / "logs",
                names=LISTENER_LOG_NAMES,
            )

            self.assertEqual(_rotate_listener_logs_if_due(3700, now=7299), 3700)
            rotate.assert_called_once()

    def test_wrong_chat_is_ignored(self):
        with patch("telegram_listener.send_text") as send, patch(
            "telegram_listener._run_digest_for_command"
        ) as run_digest:
            process_update(self.update(1, "/bugun", chat_id="another-chat"), "token", "fixture-chat")

        send.assert_not_called()
        run_digest.assert_not_called()

    def test_wrong_user_is_ignored(self):
        with patch("telegram_listener.send_text") as send, patch(
            "telegram_listener._run_digest_for_command"
        ) as run_digest:
            process_update(
                self.update(2, "/bugun", chat_type="group", user_id=99),
                "token",
                "fixture-chat",
                allowed_user_id="100",
            )

        send.assert_not_called()
        run_digest.assert_not_called()

    def test_group_without_allowed_user_id_fails_closed(self):
        with self.assertRaises(TelegramConfigurationError):
            process_update(
                self.update(3, "/bugun", chat_type="supergroup"),
                "token",
                "fixture-chat",
            )

    def test_every_meeting_command_alias_dispatches_its_mode(self):
        for index, (alias, mode) in enumerate(MEETING_COMMANDS.items(), start=10):
            with self.subTest(alias=alias), patch(
                "telegram_listener._run_digest_for_command",
                return_value=(DONE, "sent"),
            ) as run_digest:
                process_update(self.update(index, alias), "token", "fixture-chat")

            if mode == "daily":
                run_digest.assert_called_once_with()
            elif mode == "upcoming":
                run_digest.assert_called_once_with(upcoming=True)
            else:
                run_digest.assert_called_once_with(mode=mode)

    def test_command_mention_and_arguments_do_not_change_digest_alias(self):
        with patch(
            "telegram_listener._run_digest_for_command",
            return_value=(DONE, "sent"),
        ) as run_digest:
            process_update(
                self.update(39, "/HaFtA@ExampleBot sonraki 7 gün"),
                "token",
                "fixture-chat",
            )

        run_digest.assert_called_once_with(mode="weekly")

    def test_help_lists_only_table_generated_meeting_aliases(self):
        with patch("telegram_listener.send_text") as send:
            process_update(self.update(40, "/help"), "token", "fixture-chat")

        send.assert_called_once()
        sent_text = send.call_args.args[2]
        self.assertEqual(sent_text, meeting_help_text())
        for alias in MEETING_COMMANDS:
            self.assertIn(alias, sent_text)

    def test_report_commands_are_no_longer_served_by_this_bot(self):
        with patch("telegram_listener.send_text", return_value=True) as send, patch(
            "telegram_listener._run_digest_for_command"
        ) as digest:
            for update_id, text in enumerate(
                ["/servis", "/fatura son 30 gün", "/session", "/session_hazirla", "/stok"],
                start=50,
            ):
                self.assertEqual(
                    process_update(self.update(update_id, text), "token", "fixture-chat"),
                    DONE,
                )

        digest.assert_not_called()
        self.assertEqual(send.call_count, 5)
        for call in send.call_args_list:
            self.assertEqual(call.args[2], UNKNOWN_COMMAND_MESSAGE)

    def test_plain_text_is_ignored(self):
        with patch("telegram_listener.send_text") as send:
            process_update(self.update(53, "servis"), "token", "fixture-chat")

        send.assert_not_called()

    def test_unknown_command_reply_is_retried_when_delivery_fails(self):
        with patch("telegram_listener.send_text", return_value=False):
            result = process_update(self.update(54, "/servis"), "token", "fixture-chat")

        self.assertEqual(result, RETRY)

    def test_status_command_uses_network_free_report(self):
        with patch(
            "telegram_listener.build_status_report", return_value="Local status"
        ) as report, patch("telegram_listener.send_text") as send:
            process_update(self.update(60, "/status"), "token", "fixture-chat")

        report.assert_called_once_with()
        send.assert_called_once_with("token", "fixture-chat", "Local status")

    def test_corrupt_update_state_is_reported_as_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "update_state.json"
            path.write_text("not-json", encoding="utf-8")

            with self.assertRaises(TelegramUpdateStateError):
                _load_processed_update_ids(path)

    def test_main_persists_updates_and_advances_past_malformed_items(self):
        output = StringIO()
        env = {"TELEGRAM_BOT_TOKEN": "fixture-token", "TELEGRAM_CHAT_ID": "fixture-chat"}
        first_response = Mock(status_code=200)
        first_response.json.return_value = {
            "result": [None, {}, {"update_id": "bad"}, self.update(7, "/status")]
        }
        conflict = Mock(status_code=409)
        with tempfile.TemporaryDirectory() as directory, patch(
            "telegram_listener.rotate_logs"
        ), patch("telegram_listener.load_env", return_value=env), patch(
            "telegram_listener._update_state_file", return_value=Path(directory) / "state.json"
        ), patch(
            "telegram_listener.requests.get", side_effect=[first_response, conflict]
        ) as get, patch("telegram_listener.send_text") as send, patch(
            "telegram_listener.build_status_report", return_value="Local status"
        ), redirect_stdout(output):
            with patch.dict(os.environ, {"LISTENER_CONFLICT_BACKOFF_SECONDS": "0"}):
                result = main()

            state = json.loads((Path(directory) / "state.json").read_text(encoding="utf-8"))

        self.assertEqual(result, 1)
        self.assertEqual(state["processed_update_ids"], [7])
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].kwargs["params"]["offset"], 0)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["offset"], 8)
        send.assert_called_once_with("fixture-token", "fixture-chat", "Local status")
        self.assertIn(GET_UPDATES_CONFLICT_MESSAGE, output.getvalue())

    def test_main_retries_undelivered_update_without_advancing_offset(self):
        env = {"TELEGRAM_BOT_TOKEN": "fixture-token", "TELEGRAM_CHAT_ID": "fixture-chat"}
        update = self.update(8, "/status")
        first_poll = Mock(status_code=200)
        first_poll.json.return_value = {"result": [update]}
        second_poll = Mock(status_code=200)
        second_poll.json.return_value = {"result": [update]}
        conflict = Mock(status_code=409)
        with tempfile.TemporaryDirectory() as directory, patch(
            "telegram_listener.rotate_logs"
        ), patch("telegram_listener.load_env", return_value=env), patch(
            "telegram_listener._update_state_file", return_value=Path(directory) / "state.json"
        ), patch(
            "telegram_listener.requests.get", side_effect=[first_poll, second_poll, conflict]
        ) as get, patch(
            "telegram_listener.build_status_report", return_value="Local status"
        ), patch("telegram_listener.send_text", side_effect=[False, True]), patch(
            "telegram_listener.time.sleep"
        ):
            with patch.dict(os.environ, {"LISTENER_CONFLICT_BACKOFF_SECONDS": "0"}):
                result = main()

        self.assertEqual(result, 1)
        self.assertEqual(
            [call.kwargs["params"]["offset"] for call in get.call_args_list],
            [0, 0, 9],
        )

    def test_main_stops_when_persisted_state_is_corrupt(self):
        output = StringIO()
        env = {"TELEGRAM_BOT_TOKEN": "fixture-token", "TELEGRAM_CHAT_ID": "fixture-chat"}
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            state_file.write_text("not-json", encoding="utf-8")
            with patch("telegram_listener.rotate_logs"), patch(
                "telegram_listener.load_env", return_value=env
            ), patch(
                "telegram_listener._update_state_file", return_value=state_file
            ), patch("telegram_listener.requests.get") as get, redirect_stdout(output):
                result = main()

        self.assertEqual(result, 1)
        self.assertIn("Fatal error in listener", output.getvalue())
        get.assert_not_called()

    def test_status_exception_is_retried_three_times_then_acknowledged(self):
        update = self.update(70, "/status")
        output = StringIO()
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            processed = set()
            attempts = {}
            with patch(
                "telegram_listener.build_status_report",
                side_effect=RuntimeError("private report detail"),
            ) as report, redirect_stdout(output):
                outcomes = [
                    process_update(
                        update,
                        "token",
                        "fixture-chat",
                        processed_update_ids=processed,
                        state_file=state_file,
                        update_attempts=attempts,
                    )
                    for _ in range(3)
                ]
                duplicate = process_update(
                    update,
                    "token",
                    "fixture-chat",
                    processed_update_ids=processed,
                    state_file=state_file,
                    update_attempts=attempts,
                )

            self.assertEqual(outcomes, [RETRY, RETRY, FAILED])
            self.assertEqual(duplicate, DONE)
            self.assertEqual(report.call_count, 3)
            self.assertEqual(processed, {70})
            self.assertEqual(attempts, {})
            self.assertEqual(_load_listener_state(state_file), ({70}, {}))
            self.assertIn("RuntimeError", output.getvalue())
            self.assertNotIn("private report detail", output.getvalue())

    def test_status_send_failure_retries_are_bounded_and_persisted(self):
        update = self.update(71, "/status")
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            processed = set()
            attempts = {}
            with patch("telegram_listener.build_status_report", return_value="Local status"), patch(
                "telegram_listener.send_text", return_value=False
            ) as send:
                first = process_update(
                    update,
                    "token",
                    "fixture-chat",
                    processed_update_ids=processed,
                    state_file=state_file,
                    update_attempts=attempts,
                )
                self.assertEqual(_load_listener_state(state_file), (set(), {71: 1}))
                processed, attempts = _load_listener_state(state_file)
                second = process_update(
                    update,
                    "token",
                    "fixture-chat",
                    processed_update_ids=processed,
                    state_file=state_file,
                    update_attempts=attempts,
                )
                processed, attempts = _load_listener_state(state_file)
                third = process_update(
                    update,
                    "token",
                    "fixture-chat",
                    processed_update_ids=processed,
                    state_file=state_file,
                    update_attempts=attempts,
                )
                duplicate = process_update(
                    update,
                    "token",
                    "fixture-chat",
                    processed_update_ids=processed,
                    state_file=state_file,
                    update_attempts=attempts,
                )

            self.assertEqual([first, second, third], [RETRY, RETRY, FAILED])
            self.assertEqual(duplicate, DONE)
            self.assertEqual(send.call_count, 3)
            self.assertEqual(_load_listener_state(state_file), ({71}, {}))

    def test_digest_exception_is_not_retried_after_side_effect_starts(self):
        update = self.update(72, "/bugun")
        with tempfile.TemporaryDirectory() as directory, patch(
            "telegram_listener.execute_digest",
            side_effect=RuntimeError("private digest detail"),
        ) as digest, patch("telegram_listener.send_text", return_value=True) as send:
            state_file = Path(directory) / "state.json"
            processed = set()
            attempts = {}
            first = process_update(
                update,
                "token",
                "fixture-chat",
                processed_update_ids=processed,
                state_file=state_file,
                update_attempts=attempts,
            )
            second = process_update(
                update,
                "token",
                "fixture-chat",
                processed_update_ids=processed,
                state_file=state_file,
                update_attempts=attempts,
            )

        self.assertEqual(first, FAILED)
        self.assertEqual(second, DONE)
        digest.assert_called_once_with(upcoming=False, wait_seconds=30)
        send.assert_called_once()

    def test_long_digest_worker_does_not_block_status_or_duplicate_queueing(self):
        worker = CommandWorker()
        worker.start()
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def slow_digest(*args, **kwargs):
            started.set()
            release.wait(2)
            finished.set()
            return DONE

        with tempfile.TemporaryDirectory() as directory, patch(
            "telegram_listener._send_digest_command_response", side_effect=slow_digest
        ), patch("telegram_listener.build_status_report", return_value="Local status"), patch(
            "telegram_listener.send_text", side_effect=[False, True]
        ) as send:
            state_file = Path(directory) / "state.json"
            processed = set()
            attempts = {}
            try:
                queued = process_update(
                    self.update(80, "/bugun"),
                    "token",
                    "fixture-chat",
                    processed_update_ids=processed,
                    state_file=state_file,
                    update_attempts=attempts,
                    command_worker=worker,
                )
                self.assertEqual(queued, DONE)
                self.assertTrue(started.wait(1))
                self.assertEqual(_load_listener_state(state_file)[0], {80})

                duplicate = process_update(
                    self.update(81, "/bugun@ExampleBot"),
                    "token",
                    "fixture-chat",
                    processed_update_ids=processed,
                    state_file=state_file,
                    update_attempts=attempts,
                    command_worker=worker,
                )
                self.assertEqual(duplicate, DONE)

                status = process_update(
                    self.update(82, "/durum"),
                    "token",
                    "fixture-chat",
                    processed_update_ids=processed,
                    state_file=state_file,
                    update_attempts=attempts,
                    command_worker=worker,
                )
                self.assertEqual(status, DONE)
                self.assertEqual(
                    [call.args[2] for call in send.call_args_list],
                    ["Bu komut zaten sırada.", "Local status"],
                )

                release.set()
                self.assertTrue(finished.wait(1))
            finally:
                release.set()
                worker.close(timeout=2)


class TelegramListenerPollingTests(unittest.TestCase):
    def test_get_updates_409_prints_conflict_and_exits_nonzero_without_retry(self):
        output = StringIO()
        response = Mock(status_code=409)
        env = {"TELEGRAM_BOT_TOKEN": "fixture-token", "TELEGRAM_CHAT_ID": "fixture-chat"}
        with tempfile.TemporaryDirectory() as directory, patch(
            "telegram_listener.rotate_logs"
        ), patch("telegram_listener.load_env", return_value=env), patch(
            "telegram_listener._update_state_file", return_value=Path(directory) / "state.json"
        ), patch("telegram_listener._load_processed_update_ids", return_value=set()), patch(
            "telegram_listener.requests.get", return_value=response
        ) as get, patch("telegram_listener.time.sleep") as sleep, redirect_stdout(output):
            with patch.dict(os.environ, {"LISTENER_CONFLICT_BACKOFF_SECONDS": "0"}):
                result = main()

        self.assertEqual(result, 1)
        self.assertIn(GET_UPDATES_CONFLICT_MESSAGE, output.getvalue())
        get.assert_called_once()
        response.raise_for_status.assert_not_called()
        sleep.assert_called_once_with(0)

    def test_conflict_backoff_defaults_to_300_seconds(self):
        with patch.dict(os.environ):
            os.environ.pop("LISTENER_CONFLICT_BACKOFF_SECONDS", None)
            self.assertEqual(_listener_conflict_backoff_seconds(), 300)

    def test_polling_exception_logs_only_error_class_before_retry(self):
        output = StringIO()
        token = "fixture-secret-token"
        env = {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": "fixture-chat"}
        conflict = Mock(status_code=409)
        with tempfile.TemporaryDirectory() as directory, patch(
            "telegram_listener.rotate_logs"
        ), patch("telegram_listener.load_env", return_value=env), patch(
            "telegram_listener._update_state_file", return_value=Path(directory) / "state.json"
        ), patch("telegram_listener._load_processed_update_ids", return_value=set()), patch(
            "telegram_listener.requests.get",
            side_effect=[RuntimeError(f"failed at {token}"), conflict],
        ), patch("telegram_listener.time.sleep") as sleep, redirect_stdout(output):
            with patch.dict(os.environ, {"LISTENER_CONFLICT_BACKOFF_SECONDS": "0"}):
                result = main()

        self.assertEqual(result, 1)
        self.assertNotIn(token, output.getvalue())
        self.assertIn("RuntimeError", output.getvalue())
        sleep.assert_any_call(5)
        sleep.assert_any_call(0)

    def test_polling_backoff_grows_and_resets_after_successful_poll(self):
        output = StringIO()
        env = {"TELEGRAM_BOT_TOKEN": "fixture-token", "TELEGRAM_CHAT_ID": "fixture-chat"}
        success = Mock(status_code=200)
        success.json.return_value = {"result": []}
        conflict = Mock(status_code=409)
        with tempfile.TemporaryDirectory() as directory, patch(
            "telegram_listener.rotate_logs"
        ), patch("telegram_listener.load_env", return_value=env), patch(
            "telegram_listener._update_state_file", return_value=Path(directory) / "state.json"
        ), patch(
            "telegram_listener._load_listener_state", return_value=(set(), {})
        ), patch(
            "telegram_listener.requests.get",
            side_effect=[RuntimeError("first"), RuntimeError("second"), success,
                         RuntimeError("after success"), conflict],
        ), patch("telegram_listener.time.sleep") as sleep, redirect_stdout(output):
            with patch.dict(os.environ, {"LISTENER_CONFLICT_BACKOFF_SECONDS": "0"}):
                result = main()

        self.assertEqual(result, 1)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 10, 5, 0])
        self.assertIn("retrying in 10 seconds", output.getvalue())
        self.assertNotIn("first", output.getvalue())

    def test_missing_credentials_exits_nonzero(self):
        output = StringIO()
        with patch("telegram_listener.rotate_logs"), patch(
            "telegram_listener.load_env", return_value={}
        ), redirect_stdout(output):
            result = main()

        self.assertEqual(result, 1)
        self.assertIn("Missing TELEGRAM_BOT_TOKEN", output.getvalue())


if __name__ == "__main__":
    unittest.main()
