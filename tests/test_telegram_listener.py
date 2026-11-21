"""Offline command, access-control, state, and polling tests for the listener."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from mail_digest.commands import MEETING_COMMANDS, meeting_help_text
from mail_digest.integrations.company_reporting import REPORT_SERVICE_UNAVAILABLE
from telegram_listener import (
    GET_UPDATES_CONFLICT_MESSAGE,
    TelegramConfigurationError,
    TelegramUpdateStateError,
    _load_processed_update_ids,
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
                return_value=(True, "sent"),
            ) as run_digest:
                process_update(self.update(index, alias), "token", "fixture-chat")

            if mode == "daily":
                run_digest.assert_called_once_with()
            elif mode == "upcoming":
                run_digest.assert_called_once_with(upcoming=True)
            else:
                run_digest.assert_called_once_with(mode=mode)

    def test_help_starts_with_table_generated_meeting_aliases_then_report_help(self):
        with patch(
            "telegram_listener._run_company_report_command",
            return_value={"response": "Rapor komutları"},
        ), patch("telegram_listener.send_text") as send:
            process_update(self.update(40, "/help"), "token", "fixture-chat")

        send.assert_called_once()
        sent_text = send.call_args.args[2]
        self.assertTrue(sent_text.startswith("Toplantı komutları:\n"))
        for alias in MEETING_COMMANDS:
            self.assertIn(alias, sent_text)
        self.assertLess(sent_text.index("Toplantı komutları:"), sent_text.index("Rapor komutları"))
        self.assertEqual(meeting_help_text().splitlines()[0], "Toplantı komutları:")

    def test_help_includes_optional_service_unavailable_response(self):
        with patch(
            "telegram_listener._run_company_report_command",
            return_value=REPORT_SERVICE_UNAVAILABLE,
        ), patch("telegram_listener.send_text") as send:
            process_update(self.update(41, "/komutlar"), "token", "fixture-chat")

        self.assertIn(REPORT_SERVICE_UNAVAILABLE, send.call_args.args[2])

    def test_company_command_and_session_routes_use_lazy_integration_helpers(self):
        with patch(
            "telegram_listener._run_company_report_command",
            return_value=REPORT_SERVICE_UNAVAILABLE,
        ) as report, patch(
            "telegram_listener._run_report_session_status",
            return_value="session status",
        ) as session, patch(
            "telegram_listener._run_prepare_report_session",
            return_value="session prepared",
        ) as prepare, patch("telegram_listener.send_text") as send:
            process_update(self.update(50, "/servis"), "token", "fixture-chat")
            process_update(self.update(51, "/session"), "token", "fixture-chat")
            process_update(self.update(52, "/session_hazirla"), "token", "fixture-chat")

        report.assert_called_once_with("/servis")
        session.assert_called_once_with()
        prepare.assert_called_once_with()
        self.assertEqual(send.call_count, 4)
        self.assertEqual(send.call_args_list[-2].args[2], "SYS oturumu hazırlanıyor. MFA tamamlayın.")
        self.assertEqual(send.call_args_list[-1].args[2], "session prepared")

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
            result = main()

            state = json.loads((Path(directory) / "state.json").read_text(encoding="utf-8"))

        self.assertEqual(result, 1)
        self.assertEqual(state["processed_update_ids"], [7])
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].kwargs["params"]["offset"], 0)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["offset"], 8)
        send.assert_called_once_with("fixture-token", "fixture-chat", "Local status")
        self.assertIn(GET_UPDATES_CONFLICT_MESSAGE, output.getvalue())

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
            result = main()

        self.assertEqual(result, 1)
        self.assertIn(GET_UPDATES_CONFLICT_MESSAGE, output.getvalue())
        get.assert_called_once()
        response.raise_for_status.assert_not_called()
        sleep.assert_not_called()

    def test_polling_exception_redacts_token_before_retry(self):
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
            result = main()

        self.assertEqual(result, 1)
        self.assertNotIn(token, output.getvalue())
        self.assertIn("[REDACTED]", output.getvalue())
        sleep.assert_called_once_with(5)

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
