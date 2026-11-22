"""Checks that README remains the canonical behavior reference."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import unittest
from dataclasses import fields
from pathlib import Path

from mail_digest.commands import MEETING_COMMANDS
from mail_digest.models import Meeting


PROJECT_ROOT = Path(__file__).resolve().parents[1]
README_PATH = PROJECT_ROOT / "README.md"
AGENTS_PATH = PROJECT_ROOT / "AGENTS.md"
PROJECT_MEMORY_PATH = PROJECT_ROOT / "HERMES_PROJECT_MEMORY.md"
ENV_NAME_RE = re.compile(
    r"\b(?:MAIL_SOURCE|MAIL_DIGEST_[A-Z0-9_]+|GMAIL_[A-Z0-9_]+|"
    r"TELEGRAM_[A-Z0-9_]+|MEETING_REMINDER_[A-Z0-9_]+|LISTENER_[A-Z0-9_]+)\b"
)
ENV_PREFIX_RE = re.compile(
    r"^(?:MAIL_SOURCE|MAIL_DIGEST_|GMAIL_|TELEGRAM_|MEETING_REMINDER_|LISTENER_)"
)


def _readme_text():
    return README_PATH.read_text(encoding="utf-8")


def _meeting_command_table(readme):
    section = readme.split("### Toplantı komutları", 1)[1].split("###", 1)[0]
    commands = {}
    for row in section.splitlines():
        if not row.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in row.strip("|").split("|")]
        if len(cells) != 2 or cells[0] not in {"daily", "upcoming", "weekly", "attention"}:
            continue
        commands[cells[1].strip("`")] = cells[0]
    return commands


def _meeting_field_table(readme):
    section = readme.split("### Meeting modeli", 1)[1].split("###", 1)[0]
    return re.findall(r"\|\s*`([a-z_]+)`\s*\|", section)


def _literal_env_names(tree):
    names = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
            and ENV_PREFIX_RE.match(node.slice.value)
        ):
            names.add(node.slice.value)
            continue
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        function_name = function.id if isinstance(function, ast.Name) else ""
        if isinstance(function, ast.Attribute):
            function_name = function.attr
        if function_name not in {
            "get", "getenv", "_positive_int_env", "_bounded_int_env",
            "_nonnegative_float_env",
        } or not node.args:
            continue
        first_arg = node.args[0]
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            if ENV_PREFIX_RE.match(first_arg.value):
                names.add(first_arg.value)
    return names


def _code_environment_names():
    code_files = [PROJECT_ROOT / "telegram_listener.py", PROJECT_ROOT / "main.py"]
    code_files.extend((PROJECT_ROOT / "mail_digest").rglob("*.py"))
    names = set()
    for path in code_files:
        names.update(_literal_env_names(ast.parse(path.read_text(encoding="utf-8"))))
    return names


class DocumentationContractTests(unittest.TestCase):
    def test_readme_meeting_commands_match_the_dispatch_table(self):
        readme_commands = _meeting_command_table(_readme_text())

        self.assertEqual(readme_commands, MEETING_COMMANDS)
        self.assertEqual(len(readme_commands), len(MEETING_COMMANDS))

    def test_readme_meeting_fields_match_the_dataclass(self):
        self.assertEqual(_meeting_field_table(_readme_text()), [item.name for item in fields(Meeting)])

    def test_readme_is_canonical_and_agents_forbid_project_memory_file(self):
        readme = README_PATH.read_text(encoding="utf-8")
        self.assertNotIn("HERMES_PROJECT_MEMORY", readme)
        self.assertIn("PYTHONUNBUFFERED=1", readme)
        self.assertIn("flush=True", readme)
        self.assertIn(
            "Davranış ve operasyon değişiklikleri yalnızca README.md'de belgelenir; "
            "HERMES_PROJECT_MEMORY.md oluşturulmaz.",
            AGENTS_PATH.read_text(encoding="utf-8"),
        )

    def test_project_memory_file_is_absent(self):
        self.assertFalse(PROJECT_MEMORY_PATH.exists())

    def test_documented_environment_names_match_code_reads(self):
        documented = set(ENV_NAME_RE.findall(_readme_text()))
        code_names = _code_environment_names()

        self.assertEqual(documented, code_names)

    def test_env_example_covers_supported_names_without_real_secrets(self):
        env_example = PROJECT_ROOT / ".env.example"
        values = {}
        for line in env_example.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            name, separator, value = stripped.partition("=")
            self.assertTrue(separator, line)
            values[name] = value

        self.assertEqual(set(values), _code_environment_names())
        self.assertEqual(values["MAIL_DIGEST_TARGET_EMAIL"], "user@example.com")
        self.assertEqual(values["TELEGRAM_BOT_TOKEN"], "")
        self.assertEqual(values["TELEGRAM_CHAT_ID"], "")

    def test_troubleshooting_runbook_has_required_sections_and_actions(self):
        readme = _readme_text()
        for heading in (
            "## Sorun giderme",
            "### 08:00 özeti gelmedi",
            "### Gmail token'ı geçersiz veya geçici hata",
            "### Telegram 409",
            "### Bozuk Gmail veya parse cache'i",
            "### Logda token görünürse",
            "### Log konumu",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, readme)
        self.assertIn("skipped_lock", readme)
        self.assertIn("BotFather", readme)
        self.assertIn("aynı inode'a yerinde yaz", readme)

    def test_launchd_listener_help_describes_single_poller_ownership(self):
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "install_launchd.py"), "--help"],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no other poller or webhook is active", result.stdout)
        self.assertNotIn("Hermes Gateway", result.stdout)


if __name__ == "__main__":
    unittest.main()
