import ast
import plistlib
import re
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import patch

import main
from mail_digest.models import Meeting
from mail_digest.parsing.meeting_parser import extract_meetings


class ArchitectureTests(unittest.TestCase):
    def test_main_remains_a_thin_compatibility_facade(self):
        main_lines = Path(main.__file__).read_text(encoding="utf-8").splitlines()

        self.assertLess(len(main_lines), 160)
        self.assertIs(main.Meeting, Meeting)
        self.assertIs(main.extract_meetings, extract_meetings)

    def test_main_environment_file_compatibility_alias_is_lazy(self):
        from unittest.mock import patch

        with patch.dict("os.environ", {"TELEGRAM_ENV_FILE": "/tmp/synthetic-telegram.env"}):
            self.assertEqual(str(main.ENV_FILE), "/tmp/synthetic-telegram.env")

    def test_shared_configuration_has_no_duplicate_keywords(self):
        from mail_digest.config import MEETING_KEYWORDS

        self.assertEqual(len(MEETING_KEYWORDS), len(set(MEETING_KEYWORDS)))

    def test_listener_reuses_shared_environment_loader(self):
        project_root = Path(main.__file__).parent
        listener = (project_root / "telegram_listener.py").read_text(encoding="utf-8")

        self.assertIn("from mail_digest.config import PROJECT_ROOT, load_env", listener)
        self.assertNotIn("def load_env(", listener)

    def test_ci_workflow_is_manually_triggered_only(self):
        project_root = Path(main.__file__).parent
        workflow = (project_root / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

        trigger_section = workflow.split("permissions:", maxsplit=1)[0]
        self.assertIn("on:\n  workflow_dispatch:", trigger_section)
        self.assertNotIn("push:", trigger_section)
        self.assertNotIn("pull_request:", trigger_section)

    def test_digest_logs_message_count_without_mailbox_identity(self):
        from mail_digest.cli import _run_digest

        output = StringIO()
        with patch.dict("os.environ", {"MAIL_DIGEST_TARGET_EMAIL": "user@example.com"}), patch(
            "mail_digest.cli.fetch_mail", return_value=[]
        ), patch(
            "mail_digest.cli.selected_source_name", return_value="synthetic"
        ), patch("mail_digest.cli.local_now", return_value=datetime(2026, 11, 21, 9)), redirect_stdout(
            output
        ):
            self.assertEqual(_run_digest(dry_run=True), 0)

        self.assertNotIn("user@example.com", output.getvalue())

    def _applescript_source(self):
        script_path = Path(main.__file__).parent / "mail_fetcher.applescript"
        raw = script_path.read_text(encoding="utf-8")
        return raw, raw.lower()

    def test_applescript_reads_body_for_every_message_without_subject_gate(self):
        _, script = self._applescript_source()

        self.assertIn("on run argv", script)
        self.assertIn("item 1 of argv", script)
        # Every message in the 30-day window gets a body-read attempt.
        self.assertIn("content of themsg", script)

        # Subject retrieval is fail-open: an empty default is set before the read,
        # so a broken subject cannot drop the message.
        self.assertIn('set thesubject to ""', script)

        # The body is read after the subject is obtained...
        subject_read_idx = script.find("subject of themsg")
        content_read_idx = script.find("content of themsg")
        self.assertGreater(content_read_idx, subject_read_idx)

        # ...and there is no subject-based inclusion gate in between. A keyword
        # gate on the subject would discard body-only meetings before Python sees them.
        region = script[subject_read_idx:content_read_idx]
        self.assertNotIn("thesubject contains", region)

        # Any subject-based check (raw-source optimization only) happens after the body read.
        self.assertLess(content_read_idx, script.find("thesubject contains"))

    def test_applescript_preserves_30day_boundary_and_avoids_full_mailbox_query(self):
        _, script = self._applescript_source()

        # The 30-day boundary is located by binary search, not a full-mailbox date scan.
        self.assertIn("firstnonrecent", script)
        # No unbounded full-mailbox `whose` query that would scan every message.
        self.assertNotIn("every message of themailbox whose", script)
        self.assertNotIn("messages of themailbox whose", script)

    def test_applescript_raw_source_is_selective_and_calendar_signal_driven(self):
        _, script = self._applescript_source()

        # Raw source is still fetched conditionally, not for every message.
        self.assertIn("needsrawsource", script)
        self.assertIn("if needsrawsource is true then", script)
        self.assertIn("source of themsg", script)

        # Turkish "Davet:" subject prefix is a raw-source signal.
        self.assertIn('"davet:"', script)

        # .ics attachment metadata is a raw-source signal.
        self.assertIn("attachments of themsg", script)
        self.assertIn('".ics"', script)

        # Attachment enumeration happens only inside the raw-source decision path:
        # a needsRawSource-is-false guard precedes it.
        attach_idx = script.find("attachments of themsg")
        self.assertGreater(attach_idx, -1)
        guard_idx = script.rfind("if needsrawsource is false then", 0, attach_idx)
        self.assertGreater(guard_idx, -1)

    def test_launchd_templates_have_no_old_checkout_path(self):
        project_root = Path(main.__file__).parent
        template_paths = sorted(project_root.glob("launchd/*.plist.template"))
        self.assertTrue(template_paths)
        user_home_prefix = "/" + "Users" + "/"

        for template_path in template_paths:
            template = template_path.read_text(encoding="utf-8")
            self.assertIn("__PROJECT_ROOT__", template)
            self.assertIn("__PYTHON_PATH__", template)
            self.assertNotIn(user_home_prefix, template)

    def test_sources_and_fixtures_use_only_synthetic_email_domains(self):
        project_root = Path(main.__file__).parent
        email_pattern = re.compile(
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            re.IGNORECASE,
        )
        allowed_domains = {
            "example.com",
            "example.org",
            "example.net",
            "invalid",
        }
        user_home_prefix = "/" + "Users" + "/"

        paths = []
        for directory in ("tests", "mail_digest", "scripts", "launchd"):
            paths.extend((project_root / directory).rglob("*"))
        paths.extend(
            project_root / filename
            for filename in ("README.md", "mail_fetcher.applescript")
        )
        for path in paths:
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue

            self.assertNotIn(user_home_prefix, content, path.name)
            for email in email_pattern.findall(content):
                domain = email.rsplit("@", 1)[-1].casefold()
                self.assertTrue(
                    domain in allowed_domains,
                    f"Unexpected non-synthetic email domain in {path.name}",
                )

    def test_target_account_is_resolved_from_configuration(self):
        from mail_digest.config import target_email

        with patch.dict("os.environ", {"MAIL_DIGEST_TARGET_EMAIL": "user@example.com"}):
            self.assertEqual(target_email(), "user@example.com")

        config_source = (Path(main.__file__).parent / "mail_digest" / "config.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('os.environ["MAIL_DIGEST_TARGET_EMAIL"]', config_source)
        self.assertNotIn('TARGET_EMAIL = "', config_source)

    def test_all_launchd_templates_enable_unbuffered_python_output(self):
        project_root = Path(main.__file__).parent
        template_paths = sorted(project_root.glob("launchd/*.plist.template"))

        self.assertEqual(len(template_paths), 3)
        for template_path in template_paths:
            with self.subTest(template=template_path.name):
                template = plistlib.loads(template_path.read_bytes())
                self.assertEqual(
                    template["EnvironmentVariables"].get("PYTHONUNBUFFERED"), "1"
                )

    def test_install_launchd_renders_scheduled_daily_argument(self):
        from scripts.install_launchd import TEMPLATES, render_template

        rendered = plistlib.loads(
            render_template(TEMPLATES["summary"], target_account="user@example.com")
        )

        self.assertIn("--scheduled", rendered["ProgramArguments"])
        self.assertEqual(
            rendered["EnvironmentVariables"]["MAIL_DIGEST_TARGET_EMAIL"],
            "user@example.com",
        )

    def test_listener_print_calls_flush_stdout(self):
        project_root = Path(main.__file__).parent
        listener_path = project_root / "telegram_listener.py"
        tree = ast.parse(listener_path.read_text(encoding="utf-8"))
        print_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ]

        self.assertTrue(print_calls)
        for call in print_calls:
            with self.subTest(line=call.lineno):
                flush = next((kw.value for kw in call.keywords if kw.arg == "flush"), None)
                self.assertIsInstance(flush, ast.Constant)
                self.assertIs(flush.value, True)

    def test_deploy_script_mktemp_calls_use_portable_templates(self):
        project_root = Path(main.__file__).parent
        deploy_script = (project_root / "scripts" / "deploy_mac_mini.sh").read_text(
            encoding="utf-8"
        )
        mktemp_calls = [line.strip() for line in deploy_script.splitlines() if "mktemp" in line]

        self.assertTrue(mktemp_calls)
        for call in mktemp_calls:
            with self.subTest(call=call):
                self.assertIn("XXXXXX", call)


if __name__ == "__main__":
    unittest.main()
