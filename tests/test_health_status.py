import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import requests

from mail_digest.cli import main, run_digest
from mail_digest.services.health import build_status_report
from mail_digest.services.run_status import (
    record_run_finished,
    record_run_skipped_lock,
    record_run_started,
)
from telegram_listener import process_update


class HealthStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.status_path = self.root / "status.json"
        self.token_path = self.root / "token.json"
        self.cache_path = self.root / "cache.sqlite3"
        self.reminder_path = self.root / "reminders.json"
        self.now = datetime(2026, 11, 21, 12, 0)
        self.environment = patch.dict(os.environ, {
            "MAIL_DIGEST_STATUS_FILE": str(self.status_path),
            "GMAIL_TOKEN_FILE": str(self.token_path),
            "GMAIL_CACHE_FILE": str(self.cache_path),
            "MEETING_REMINDER_STATE_FILE": str(self.reminder_path),
            "MAIL_SOURCE": "gmail",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.write_token(expiry=self.now + timedelta(days=1))
        self.write_cache(updated_at=self.now - timedelta(minutes=5), messages=2)
        self.write_reminder_scan(self.now - timedelta(minutes=5))

    def tearDown(self):
        self.temp.cleanup()

    def write_token(self, *, expiry, permissions=0o600, refresh=True):
        payload = {
            "expiry": expiry.replace(tzinfo=ZoneInfo("Europe/Istanbul")).isoformat(),
        }
        if refresh:
            payload["refresh_token"] = "present"
        self.token_path.write_text(json.dumps(payload), encoding="utf-8")
        self.token_path.chmod(permissions)

    def write_cache(self, *, updated_at, messages):
        self.cache_path.unlink(missing_ok=True)
        connection = sqlite3.connect(self.cache_path)
        with connection:
            connection.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
            connection.execute("CREATE TABLE messages (id TEXT)")
            connection.execute(
                "INSERT INTO metadata VALUES ('updated_at_ms', ?)",
                (str(int(updated_at.replace(tzinfo=ZoneInfo("Europe/Istanbul")).timestamp() * 1000)),),
            )
            connection.executemany(
                "INSERT INTO messages VALUES (?)",
                [(str(index),) for index in range(messages)],
            )
        connection.close()

    def write_reminder_scan(self, scan_at):
        self.reminder_path.write_text(
            json.dumps({
                "sent": {},
                "last_successful_reminder_scan": scan_at.isoformat(),
            }),
            encoding="utf-8",
        )

    def write_status(self, *, daily_success):
        self.status_path.write_text(
            json.dumps({
                "modes": {
                    "daily": {
                        "last_success_at": daily_success.isoformat(),
                        "last_failure_at": (self.now - timedelta(days=2)).isoformat(),
                        "last_error_type": "ValueError",
                    },
                    "weekly": {"last_success_at": self.now.isoformat()},
                },
            }),
            encoding="utf-8",
        )
        self.status_path.chmod(0o600)

    def test_missing_run_status_is_reported_without_network_access(self):
        with patch.object(requests, "get") as get, patch.object(
            requests, "post"
        ) as post:
            report = build_status_report(now=self.now)

        self.assertIn("Çalışma kaydı: yok", report)
        self.assertIn("Mail kaynağı: gmail", report)
        self.assertIn("Planlı günlük özet (08:00): henüz gönderilmedi", report)
        self.assertIn("bugünkü planlı özet gönderilmedi", report)
        self.assertIn("Gmail önbelleği: son güncelleme", report)
        self.assertIn("mesaj sayısı 2", report)
        get.assert_not_called()
        post.assert_not_called()

    def test_scheduled_digest_waits_before_daily_deadline(self):
        with patch.dict(os.environ, {"MAIL_DIGEST_DAILY_DEADLINE": "08:15"}):
            report = build_status_report(now=datetime(2026, 11, 21, 7, 59))

        self.assertIn("Planlı günlük özet (08:00): bekleniyor", report)
        self.assertNotIn("bugünkü planlı özet gönderilmedi", report)

    def test_missing_scheduled_digest_warns_after_daily_deadline(self):
        with patch.dict(os.environ, {"MAIL_DIGEST_DAILY_DEADLINE": "08:15"}):
            report = build_status_report(now=datetime(2026, 11, 21, 8, 20))

        self.assertIn("bugünkü planlı özet gönderilmedi: henüz gönderilmedi", report)

    def test_corrupt_status_is_reported(self):
        self.status_path.write_text("not-json", encoding="utf-8")

        report = build_status_report(now=self.now)

        self.assertIn("Çalışma kaydı: bozuk veya okunamıyor", report)
        self.assertIn("daily: son başarı yok; son hata yok", report)

    def test_stale_status_cache_reminder_and_expired_token_emit_warnings(self):
        self.write_status(daily_success=self.now - timedelta(hours=27))
        self.write_cache(updated_at=self.now - timedelta(minutes=31), messages=3)
        self.write_reminder_scan(self.now - timedelta(minutes=21))
        self.write_token(expiry=self.now - timedelta(minutes=1))

        report = build_status_report(now=self.now)

        self.assertIn("ValueError", report)
        self.assertIn("Gmail token: sorun var", report)
        self.assertIn("token süresi dolmuş", report)
        self.assertIn("bugünkü planlı özet gönderilmedi", report)
        self.assertIn("Gmail önbelleği 30 dakikadan eski", report)
        self.assertIn("hatırlatma taraması 20 dakikadan eski", report)

    def test_missing_token_is_flagged(self):
        self.token_path.unlink()

        report = build_status_report(now=self.now)

        self.assertIn("Gmail token: dosya yok", report)
        self.assertIn("token dosyası yok", report)

    def test_manual_daily_success_does_not_hide_missing_scheduled_digest(self):
        self.write_status(daily_success=self.now)
        payload = json.loads(self.status_path.read_text(encoding="utf-8"))
        payload["modes"]["daily_scheduled"] = {
            "last_started_at": None,
            "last_success_at": None,
            "last_failure_at": (self.now.replace(hour=8, minute=5)).isoformat(),
            "last_error_type": "ValueError",
            "last_skipped_lock_at": None,
        }
        self.status_path.write_text(json.dumps(payload), encoding="utf-8")

        report = build_status_report(now=self.now)

        self.assertIn("• daily: son başarı 2026-11-21 12:00", report)
        self.assertIn("Planlı günlük özet (08:00): başarısız", report)
        self.assertIn("bugünkü planlı özet gönderilmedi: başarısız (ValueError)", report)

    def test_scheduled_daily_skip_is_recorded_and_reported(self):
        skipped_at = self.now.replace(hour=8, minute=1)
        record_run_skipped_lock("daily_scheduled", skipped_at)

        payload = json.loads(self.status_path.read_text(encoding="utf-8"))
        entry = payload["modes"]["daily_scheduled"]
        self.assertEqual(entry["last_error_type"], "skipped_lock")
        self.assertEqual(entry["last_skipped_lock_at"], skipped_at.isoformat())

        report = build_status_report(now=self.now)

        self.assertIn("Planlı günlük özet (08:00): atlandı (kilit dolu)", report)
        self.assertIn("bugünkü planlı özet gönderilmedi: atlandı (kilit dolu)", report)

    def test_invalid_lazy_setting_does_not_break_listener_status_and_is_reported(self):
        with patch.dict(os.environ, {"GMAIL_BATCH_SIZE": "51"}):
            report = build_status_report(now=self.now)

        self.assertIn("⚠️ Yapılandırma hatası: GMAIL_BATCH_SIZE", report)

        environment = os.environ.copy()
        environment["GMAIL_BATCH_SIZE"] = "51"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from datetime import datetime; import telegram_listener; "
                "from mail_digest.services.health import build_status_report; "
                "print(build_status_report(now=datetime(2026, 11, 21, 12)))",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("⚠️ Yapılandırma hatası: GMAIL_BATCH_SIZE", result.stdout)

    def test_token_with_broad_permissions_is_flagged(self):
        self.write_token(expiry=self.now + timedelta(days=1), permissions=0o644)

        report = build_status_report(now=self.now)

        self.assertIn("0600: hayır", report)
        self.assertIn("token dosyasının izni 0600 değil", report)

    def test_token_without_refresh_token_is_flagged_without_refresh_attempt(self):
        self.write_token(expiry=self.now + timedelta(days=1), refresh=False)

        report = build_status_report(now=self.now)

        self.assertIn("refresh token: yok", report)
        self.assertIn("refresh token yok", report)

    def test_run_digest_records_start_and_result_with_private_permissions(self):
        lock_path = self.root / "digest.lock"
        with patch.dict(os.environ, {"MAIL_DIGEST_LOCK_FILE": str(lock_path)}), patch(
            "mail_digest.cli._run_digest", return_value=0
        ):
            self.assertEqual(run_digest(mode="weekly"), 0)

        payload = json.loads(self.status_path.read_text(encoding="utf-8"))
        weekly = payload["modes"]["weekly"]
        self.assertEqual(
            set(weekly),
            {
                "last_started_at",
                "last_success_at",
                "last_failure_at",
                "last_error_type",
                "last_skipped_lock_at",
            },
        )
        self.assertIn("last_started_at", weekly)
        self.assertIn("last_success_at", weekly)
        self.assertEqual(stat.S_IMODE(self.status_path.stat().st_mode), 0o600)
        self.assertNotIn("TELEGRAM", self.status_path.read_text(encoding="utf-8"))

    def test_run_digest_records_only_exception_class_on_failure(self):
        lock_path = self.root / "digest.lock"
        with patch.dict(os.environ, {"MAIL_DIGEST_LOCK_FILE": str(lock_path)}), patch(
            "mail_digest.cli._run_digest", side_effect=ValueError("private content")
        ):
            with self.assertRaisesRegex(ValueError, "private content"):
                run_digest(mode="attention")

        attention = json.loads(self.status_path.read_text(encoding="utf-8"))["modes"]["attention"]
        self.assertIn("last_started_at", attention)
        self.assertIn("last_failure_at", attention)
        self.assertEqual(attention["last_error_type"], "ValueError")
        self.assertNotIn("private content", self.status_path.read_text(encoding="utf-8"))

    def test_nonzero_digest_result_records_failure(self):
        lock_path = self.root / "digest.lock"
        with patch.dict(os.environ, {"MAIL_DIGEST_LOCK_FILE": str(lock_path)}), patch(
            "mail_digest.cli._run_digest", return_value=1
        ):
            self.assertEqual(run_digest(mode="daily"), 1)

        daily = json.loads(self.status_path.read_text(encoding="utf-8"))["modes"]["daily"]
        self.assertIn("last_failure_at", daily)
        self.assertEqual(daily["last_error_type"], "DigestFailed")

    def test_dry_run_does_not_change_status_file(self):
        status_before = b'{"modes":{"daily":{"sentinel":"unchanged"}}}\n'
        self.status_path.write_bytes(status_before)
        lock_path = self.root / "digest.lock"
        with patch.dict(os.environ, {"MAIL_DIGEST_LOCK_FILE": str(lock_path)}), patch(
            "mail_digest.cli._run_digest", return_value=0
        ), patch("mail_digest.cli.record_run_started") as started, patch(
            "mail_digest.cli.record_run_finished"
        ) as finished:
            self.assertEqual(run_digest(dry_run=True), 0)

        self.assertEqual(self.status_path.read_bytes(), status_before)
        started.assert_not_called()
        finished.assert_not_called()

    def test_status_write_error_does_not_escape(self):
        with patch(
            "mail_digest.services.run_status._write_status",
            side_effect=PermissionError("unavailable"),
        ):
            record_run_started("daily", self.now)
            record_run_finished("daily", succeeded=False, error_type=OSError)

    def test_cli_status_dry_run_prints_report_without_running_digest_or_lock(self):
        output = StringIO()
        with patch("mail_digest.cli.rotate_logs"), patch(
            "mail_digest.cli.build_status_report", return_value="Local status"
        ), patch("mail_digest.cli.run_digest") as digest, patch(
            "mail_digest.cli.send_telegram"
        ) as send, patch.object(
            sys, "argv", ["main.py", "--status", "--dry-run"]
        ), redirect_stdout(output):
            self.assertEqual(main(), 0)

        self.assertEqual(output.getvalue().strip(), "Local status")
        digest.assert_not_called()
        send.assert_not_called()

    def test_cli_status_sends_report_without_running_digest(self):
        with patch("mail_digest.cli.rotate_logs"), patch(
            "mail_digest.cli.build_status_report", return_value="Local status"
        ), patch("mail_digest.cli.run_digest") as digest, patch(
            "mail_digest.cli.send_telegram", return_value=True
        ) as send, patch.object(
            sys, "argv", ["main.py", "--status"]
        ):
            self.assertEqual(main(), 0)

        digest.assert_not_called()
        send.assert_called_once_with("Local status")

    def test_listener_status_command_sends_local_health_report(self):
        for update_id, command in ((7, "/durum"), (8, "/status")):
            update = {
                "update_id": update_id,
                "message": {"chat": {"id": "fixture-chat"}, "text": command},
            }
            with self.subTest(command=command), patch(
                "telegram_listener.build_status_report", return_value="Local status"
            ) as report, patch("telegram_listener.send_text") as send:
                process_update(
                    update,
                    "fixture-token",
                    "fixture-chat",
                    processed_update_ids=set(),
                    state_file=self.root / f"telegram-state-{update_id}.json",
                )

            report.assert_called_once_with()
            send.assert_called_once_with("fixture-token", "fixture-chat", "Local status")


if __name__ == "__main__":
    unittest.main()
