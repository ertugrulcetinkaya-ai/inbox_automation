"""Offline safety tests for the Mac Mini deploy preview."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeployScriptTests(unittest.TestCase):
    def run_deploy(
        self,
        *,
        dirty=False,
        branch="main",
        launchd_output="",
        apply=False,
        preflight_ok=True,
        preflight_exit=None,
        preflight_output="",
        target_config_ok=True,
        bootstrap_failure_count=0,
        bootstrap_fail_label="",
        wait_label="",
        wait_print_successes=0,
        broken_pip_shebang=False,
        python_check_failure="",
        quality_tools_fail=False,
    ):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        temp_root = Path(temporary.name)
        repo = temp_root / "repo"
        fake_bin = temp_root / "bin"
        fake_bin.mkdir()
        (repo / "scripts").mkdir(parents=True)
        (repo / ".venv" / "bin").mkdir(parents=True)
        (repo / "scripts" / "deploy_mac_mini.sh").write_bytes(
            (PROJECT_ROOT / "scripts" / "deploy_mac_mini.sh").read_bytes()
        )
        check_script = repo / "scripts" / "check.sh"
        check_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        check_script.chmod(0o755)
        for relative in ("requirements.lock", "main.py", "scripts/install_launchd.py"):
            (repo / relative).touch()

        # Keep a real temporary Git repository while replacing its command with
        # a deterministic shim for branch, cleanliness, and network operations.
        subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
        git_stub = fake_bin / "git"
        git_stub.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "case \"$1\" in\n"
            "  rev-parse) printf '%s\\n' \"$DEPLOY_TEST_REPO\" ;;\n"
            "  branch) printf '%s\\n' \"$DEPLOY_TEST_BRANCH\" ;;\n"
            "  status) if [[ \"$DEPLOY_TEST_DIRTY\" == true ]]; then printf ' M file\\n'; fi ;;\n"
            "  *) printf '%s\\n' \"$*\" >> \"$DEPLOY_TEST_GIT_LOG\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        launchctl_stub = fake_bin / "launchctl"
        launchctl_stub.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == list ]]; then printf '%s\\n' "$DEPLOY_TEST_LAUNCHD"; exit 0; fi
printf '%s\\n' "$*" >> "$DEPLOY_TEST_LAUNCHCTL_LOG"
if [[ "${1:-}" == bootout ]]; then
  if [[ -n "$DEPLOY_TEST_WAIT_LABEL" && "$2" == *"$DEPLOY_TEST_WAIT_LABEL" ]]; then
    : > "${DEPLOY_TEST_LAUNCHCTL_LOG}.wait_pending"
  fi
  exit 0
fi
if [[ "${1:-}" == bootstrap ]]; then
  label="${3##*/}"
  label="${label%.plist}"
  count_file="${DEPLOY_TEST_LAUNCHCTL_LOG}.bootstrap_count.$label"
  count=0
  if [[ -f "$count_file" ]]; then read -r count < "$count_file"; fi
  count=$((count + 1))
  printf '%s\\n' "$count" > "$count_file"
  if [[ "$label" == "$DEPLOY_TEST_BOOTSTRAP_FAIL_LABEL" && "$count" -le "$DEPLOY_TEST_BOOTSTRAP_FAILURE_COUNT" ]]; then
    printf '%s\\n' "$DEPLOY_TEST_BOOTSTRAP_ERROR" >&2
    exit 5
  fi
  exit 0
fi
if [[ "${1:-}" == print ]]; then
  if [[ -n "$DEPLOY_TEST_WAIT_LABEL" && "$2" == *"$DEPLOY_TEST_WAIT_LABEL" && -f "${DEPLOY_TEST_LAUNCHCTL_LOG}.wait_pending" ]]; then
    count=0
    count_file="${DEPLOY_TEST_LAUNCHCTL_LOG}.print_count"
    if [[ -f "$count_file" ]]; then read -r count < "$count_file"; fi
    count=$((count + 1))
    printf '%s\\n' "$count" > "$count_file"
    if [[ "$count" -le "$DEPLOY_TEST_WAIT_PRINT_SUCCESSES" ]]; then exit 0; fi
    rm -f "${DEPLOY_TEST_LAUNCHCTL_LOG}.wait_pending"
    exit 1
  fi
  if [[ "$DEPLOY_TEST_LAUNCHD" == *"${2##*/}"* ]]; then exit 0; fi
  exit 1
fi
""",
            encoding="utf-8",
        )
        for name in ("python",):
            (fake_bin / name).write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        fake_python = repo / ".venv" / "bin" / "python"
        fake_python.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> \"$DEPLOY_TEST_PYTHON_LOG\"\n"
            "if [[ \"$DEPLOY_TEST_PYTHON_CHECK_FAILURE\" == import && \"$*\" == '-c import sys' ]]; then exit 1; fi\n"
            "if [[ \"$DEPLOY_TEST_PYTHON_CHECK_FAILURE\" == pip && \"$*\" == '-m pip --version' ]]; then exit 1; fi\n"
            "if [[ \"$DEPLOY_TEST_TARGET_CONFIG_OK\" == false && \"$*\" == '-c from mail_digest.config import target_email; target_email()' ]]; then exit 1; fi\n"
            "if [[ \"$*\" == 'main.py --upcoming --dry-run' ]]; then\n"
            "  printf '%s\\n' \"${MAIL_DIGEST_LOCK_FILE:-}\" > \"$DEPLOY_TEST_PYTHON_LOG.preflight_lock\"\n"
            "  printf '%s\\n' \"$DEPLOY_TEST_PREFLIGHT_OUTPUT\"\n"
            "  exit \"$DEPLOY_TEST_PREFLIGHT_EXIT\"\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_pip = repo / ".venv" / "bin" / "pip"
        pip_shebang = "#!/definitely/missing/python\n" if broken_pip_shebang else "#!/usr/bin/env bash\n"
        fake_pip.write_text(pip_shebang + "exit 0\n", encoding="utf-8")
        for name in ("ruff", "pyright"):
            executable = repo / ".venv" / "bin" / name
            executable.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"${1:-}\" == --version && \"$DEPLOY_TEST_QUALITY_VERSION_FAIL\" == true ]]; then exit 1; fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
        for executable in (git_stub, launchctl_stub, fake_python, fake_pip, fake_bin / "python"):
            executable.chmod(0o755)

        git_log = temp_root / "git.log"
        launchctl_log = temp_root / "launchctl.log"
        python_log = temp_root / "python.log"
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:{env['PATH']}",
                "HOME": str(temp_root / "home"),
                "DEPLOY_TEST_REPO": str(repo),
                "DEPLOY_TEST_BRANCH": branch,
                "DEPLOY_TEST_DIRTY": "true" if dirty else "false",
                "DEPLOY_TEST_LAUNCHD": launchd_output,
                "DEPLOY_TEST_PREFLIGHT_EXIT": str(
                    preflight_exit if preflight_exit is not None else (0 if preflight_ok else 1)
                ),
                "DEPLOY_TEST_PREFLIGHT_OUTPUT": preflight_output,
                "DEPLOY_TEST_TARGET_CONFIG_OK": "true" if target_config_ok else "false",
                "DEPLOY_TEST_BOOTSTRAP_FAILURE_COUNT": str(bootstrap_failure_count),
                "DEPLOY_TEST_BOOTSTRAP_FAIL_LABEL": bootstrap_fail_label,
                "DEPLOY_TEST_BOOTSTRAP_ERROR": "Bootstrap failed: 5: Input/output error",
                "DEPLOY_TEST_WAIT_LABEL": wait_label,
                "DEPLOY_TEST_WAIT_PRINT_SUCCESSES": str(wait_print_successes),
                "DEPLOY_WAIT_STEP_SECONDS": "0",
                "DEPLOY_TEST_PYTHON_CHECK_FAILURE": python_check_failure,
                "DEPLOY_TEST_QUALITY_VERSION_FAIL": "true" if quality_tools_fail else "false",
                "DEPLOY_TEST_GIT_LOG": str(git_log),
                "DEPLOY_TEST_LAUNCHCTL_LOG": str(launchctl_log),
                "DEPLOY_TEST_PYTHON_LOG": str(python_log),
                "MAIL_DIGEST_LOCK_FILE": str(temp_root / "production.lock"),
            }
        )
        args = ["bash", str(repo / "scripts" / "deploy_mac_mini.sh")]
        if apply:
            args.append("--apply")
        result = subprocess.run(args, cwd=repo, env=env, text=True, capture_output=True, check=False)
        return result, launchctl_log, python_log

    def test_dirty_working_tree_is_rejected(self):
        result, _, python_log = self.run_deploy(dirty=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("clean working tree", result.stderr)
        self.assertFalse(python_log.exists())

    def test_non_main_branch_is_rejected(self):
        result, _, python_log = self.run_deploy(branch="feature/test")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires the main branch", result.stderr)
        self.assertFalse(python_log.exists())

    def test_hermes_job_blocks_listener_deploy(self):
        result, _, python_log = self.run_deploy(launchd_output="com.example.hermes.gateway")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Hermes gateway hâlâ yüklü", result.stderr)
        calls = python_log.read_text(encoding="utf-8") if python_log.exists() else ""
        self.assertNotIn("scripts/install_launchd.py", calls)

    def test_preview_never_calls_launchctl_bootstrap(self):
        result, launchctl_log, python_log = self.run_deploy(
            preflight_output="successful preflight output must stay hidden"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would run: launchctl bootstrap", result.stdout)
        self.assertFalse(launchctl_log.exists())
        calls = python_log.read_text(encoding="utf-8").splitlines()
        install_call = next(call for call in calls if "scripts/install_launchd.py" in call)
        self.assertIn("--include-listener", install_call)
        self.assertNotIn("--install", install_call)
        self.assertIn("main.py --status --dry-run", calls)
        preflight_lock = Path(
            Path(f"{python_log}.preflight_lock").read_text(encoding="utf-8").strip()
        )
        self.assertEqual(str(preflight_lock), str(Path(python_log).parent / "production.lock"))
        self.assertNotIn("successful preflight output must stay hidden", result.stdout)
        self.assertNotIn("successful preflight output must stay hidden", result.stderr)

    def test_broken_pip_shebang_is_bypassed_using_python_module_pip(self):
        result, _, python_log = self.run_deploy(broken_pip_shebang=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = python_log.read_text(encoding="utf-8").splitlines()
        self.assertIn("-m pip --version", calls)
        self.assertIn("-m pip install -r requirements.lock", calls)

    def test_failed_python_pip_check_reports_manual_venv_rebuild(self):
        result, _, python_log = self.run_deploy(
            broken_pip_shebang=True,
            python_check_failure="pip",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(
            result.stderr,
            r"\.venv bozuk; yeniden oluşturun: mv \.venv \.venv\.broken-\d{8} "
            r"&& python3\.11 -m venv \.venv",
        )
        calls = python_log.read_text(encoding="utf-8").splitlines()
        self.assertIn("-m pip --version", calls)
        self.assertNotIn("-m pip install -r requirements.lock", calls)

    def test_failed_python_import_check_reports_manual_venv_rebuild(self):
        result, _, python_log = self.run_deploy(
            broken_pip_shebang=True,
            python_check_failure="import",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(".venv bozuk; yeniden oluşturun:", result.stderr)
        calls = python_log.read_text(encoding="utf-8").splitlines()
        self.assertIn("-c import sys", calls)
        self.assertNotIn("-m pip --version", calls)
        self.assertNotIn("-m pip install -r requirements.lock", calls)

    def test_quality_tools_must_pass_version_check(self):
        result, _, python_log = self.run_deploy(quality_tools_fail=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("running unittest and compileall only", result.stdout)
        calls = python_log.read_text(encoding="utf-8").splitlines()
        self.assertIn("-m unittest discover -s tests -p test_*.py", calls)
        self.assertIn(
            "-m compileall -q main.py telegram_listener.py mail_digest tests scripts",
            calls,
        )

    def test_gmail_preflight_failure_stops_before_plist_or_launchctl(self):
        preflight_output = "\n".join(f"preflight-line-{index:02d}" for index in range(25))
        result, launchctl_log, python_log = self.run_deploy(
            preflight_ok=False,
            preflight_output=preflight_output,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "Gmail ön kontrolü başarısız; önce .venv/bin/python scripts/gmail_auth.py çalıştırın.",
            result.stderr,
        )
        self.assertNotIn("preflight-line-", result.stderr)
        calls = python_log.read_text(encoding="utf-8").splitlines()
        self.assertIn("main.py --upcoming --dry-run", calls)
        self.assertFalse(any("scripts/install_launchd.py" in call for call in calls))
        self.assertFalse(launchctl_log.exists())

    def test_missing_target_configuration_stops_before_gmail_preflight(self):
        result, launchctl_log, python_log = self.run_deploy(target_config_ok=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "~/.hermes_local_automation/mail_digest/target.env eksik",
            result.stderr,
        )
        calls = python_log.read_text(encoding="utf-8").splitlines()
        self.assertNotIn("main.py --upcoming --dry-run", calls)
        self.assertFalse(any("scripts/install_launchd.py" in call for call in calls))
        self.assertFalse(launchctl_log.exists())

    def test_digest_lock_contention_returns_75_without_leaking_preflight_output(self):
        result, launchctl_log, python_log = self.run_deploy(
            preflight_exit=75,
            preflight_output="private meeting title must stay hidden",
        )

        self.assertEqual(result.returncode, 75)
        self.assertIn(
            "üretim işi çalışıyor; birkaç dakika sonra tekrar deneyin",
            result.stderr,
        )
        self.assertNotIn("private meeting title", result.stdout + result.stderr)
        self.assertNotIn("Gmail ön kontrolü başarısız; önce", result.stderr)
        calls = python_log.read_text(encoding="utf-8").splitlines()
        self.assertIn("main.py --upcoming --dry-run", calls)
        self.assertFalse(any("scripts/install_launchd.py" in call for call in calls))
        self.assertFalse(launchctl_log.exists())

    def test_apply_waits_until_service_is_removed_before_bootstrap(self):
        label = "com.ertugrul.mail.unread.summary"
        result, launchctl_log, _ = self.run_deploy(
            apply=True,
            wait_label=label,
            wait_print_successes=2,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        commands = launchctl_log.read_text(encoding="utf-8").splitlines()
        bootout_index = next(
            index for index, command in enumerate(commands)
            if command.startswith("bootout ") and command.endswith(label)
        )
        bootstrap_index = next(
            index for index, command in enumerate(commands)
            if command.startswith("bootstrap ") and command.endswith(f"{label}.plist")
        )
        polls = [
            command for command in commands[bootout_index + 1 : bootstrap_index]
            if command.startswith("print ") and command.endswith(label)
        ]
        self.assertEqual(len(polls), 3)

    def test_apply_retries_bootstrap_and_reports_first_error(self):
        label = "com.ertugrul.mail.unread.summary"
        result, launchctl_log, _ = self.run_deploy(
            apply=True,
            bootstrap_failure_count=1,
            bootstrap_fail_label=label,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            f"{label}: deneme 1: Bootstrap failed: 5: Input/output error",
            result.stderr,
        )
        commands = launchctl_log.read_text(encoding="utf-8").splitlines()
        bootstrap_commands = [
            command for command in commands
            if command.startswith("bootstrap ") and command.endswith(f"{label}.plist")
        ]
        self.assertEqual(len(bootstrap_commands), 2)

    def test_apply_stops_after_three_bootstrap_failures_and_reports_loaded_labels(self):
        label = "com.ertugrul.mail.unread.summary"
        loaded_labels = "com.ertugrul.mail.meeting.reminder com.ertugrul.mail.unread.listener"
        result, launchctl_log, _ = self.run_deploy(
            apply=True,
            launchd_output=loaded_labels,
            bootstrap_failure_count=3,
            bootstrap_fail_label=label,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Başarısız label: com.ertugrul.mail.unread.summary", result.stderr)
        self.assertIn("deneme 1: Bootstrap failed: 5: Input/output error", result.stderr)
        self.assertIn("deneme 2: Bootstrap failed: 5: Input/output error", result.stderr)
        self.assertIn("deneme 3: Bootstrap failed: 5: Input/output error", result.stderr)
        self.assertIn("com.ertugrul.mail.meeting.reminder", result.stderr)
        self.assertIn("com.ertugrul.mail.unread.listener", result.stderr)
        commands = launchctl_log.read_text(encoding="utf-8").splitlines()
        bootstrap_commands = [
            command for command in commands
            if command.startswith("bootstrap ") and command.endswith(f"{label}.plist")
        ]
        self.assertEqual(len(bootstrap_commands), 3)


if __name__ == "__main__":
    unittest.main()
