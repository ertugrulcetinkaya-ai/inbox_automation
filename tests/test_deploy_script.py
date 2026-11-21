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
        preflight_output="",
        fail_bootstrap_number=0,
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
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "if [[ \"${1:-}\" == list ]]; then printf '%s\\n' \"$DEPLOY_TEST_LAUNCHD\"; exit 0; fi\n"
            "printf '%s\\n' \"$*\" >> \"$DEPLOY_TEST_LAUNCHCTL_LOG\"\n"
            "if [[ \"${1:-}\" == bootstrap ]]; then\n"
            "  count=0\n"
            "  if [[ -f \"${DEPLOY_TEST_LAUNCHCTL_LOG}.bootstrap_count\" ]]; then read -r count < \"${DEPLOY_TEST_LAUNCHCTL_LOG}.bootstrap_count\"; fi\n"
            "  count=$((count + 1))\n"
            "  printf '%s\\n' \"$count\" > \"${DEPLOY_TEST_LAUNCHCTL_LOG}.bootstrap_count\"\n"
            "  if [[ \"$count\" == \"$DEPLOY_TEST_FAIL_BOOTSTRAP_NUMBER\" ]]; then exit 1; fi\n"
            "fi\n"
            "if [[ \"${1:-}\" == print ]]; then\n"
            "  case \"$2\" in *com.ertugrul.mail.unread.summary|*com.ertugrul.mail.unread.listener) exit 0 ;; *) exit 1 ;; esac\n"
            "fi\n",
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
                "DEPLOY_TEST_PREFLIGHT_EXIT": "0" if preflight_ok else "1",
                "DEPLOY_TEST_PREFLIGHT_OUTPUT": preflight_output,
                "DEPLOY_TEST_FAIL_BOOTSTRAP_NUMBER": str(fail_bootstrap_number),
                "DEPLOY_TEST_PYTHON_CHECK_FAILURE": python_check_failure,
                "DEPLOY_TEST_QUALITY_VERSION_FAIL": "true" if quality_tools_fail else "false",
                "DEPLOY_TEST_GIT_LOG": str(git_log),
                "DEPLOY_TEST_LAUNCHCTL_LOG": str(launchctl_log),
                "DEPLOY_TEST_PYTHON_LOG": str(python_log),
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
        self.assertNotEqual(str(preflight_lock), "/tmp/mail_unread_digest.lock")
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

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Gmail ön kontrolü başarısız; önce .venv/bin/python scripts/gmail_auth.py çalıştırın.",
            result.stderr,
        )
        self.assertNotIn("preflight-line-04", result.stderr)
        self.assertIn("preflight-line-05", result.stderr)
        self.assertIn("preflight-line-24", result.stderr)
        self.assertLess(
            result.stderr.index("preflight-line-24"),
            result.stderr.index("Gmail ön kontrolü başarısız"),
        )
        calls = python_log.read_text(encoding="utf-8").splitlines()
        self.assertIn("main.py --upcoming --dry-run", calls)
        self.assertFalse(any("scripts/install_launchd.py" in call for call in calls))
        self.assertFalse(launchctl_log.exists())

    def test_digest_lock_contention_output_fails_even_with_zero_exit(self):
        result, launchctl_log, python_log = self.run_deploy(
            preflight_ok=True,
            preflight_output="Digest is already running; skipping this invocation",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Digest is already running", result.stderr)
        self.assertIn("Gmail ön kontrolü başarısız", result.stderr)
        calls = python_log.read_text(encoding="utf-8").splitlines()
        self.assertIn("main.py --upcoming --dry-run", calls)
        self.assertFalse(any("scripts/install_launchd.py" in call for call in calls))
        self.assertFalse(launchctl_log.exists())

    def test_apply_stops_on_second_bootstrap_and_reports_loaded_labels(self):
        result, launchctl_log, _ = self.run_deploy(apply=True, fail_bootstrap_number=2)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Başarısız label: com.ertugrul.mail.meeting.reminder", result.stderr)
        self.assertIn("com.ertugrul.mail.unread.summary", result.stderr)
        self.assertIn("com.ertugrul.mail.unread.listener", result.stderr)
        commands = launchctl_log.read_text(encoding="utf-8").splitlines()
        bootstrap_commands = [command for command in commands if command.startswith("bootstrap ")]
        self.assertEqual(len(bootstrap_commands), 2)
        self.assertFalse(any("listener.plist" in command for command in bootstrap_commands))


if __name__ == "__main__":
    unittest.main()
