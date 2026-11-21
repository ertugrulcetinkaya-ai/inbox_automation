"""Offline safety tests for the Mac Mini deploy preview."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeployScriptTests(unittest.TestCase):
    def run_deploy(self, *, dirty=False, branch="main", launchd_output="", apply=False):
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
            "printf '%s\\n' \"$*\" >> \"$DEPLOY_TEST_LAUNCHCTL_LOG\"\n",
            encoding="utf-8",
        )
        for name in ("python",):
            (fake_bin / name).write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        fake_python = repo / ".venv" / "bin" / "python"
        fake_python.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> \"$DEPLOY_TEST_PYTHON_LOG\"\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_pip = repo / ".venv" / "bin" / "pip"
        fake_pip.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        for name in ("ruff", "pyright"):
            executable = repo / ".venv" / "bin" / name
            executable.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
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
        result, launchctl_log, python_log = self.run_deploy()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would run: launchctl bootstrap", result.stdout)
        self.assertFalse(launchctl_log.exists())
        calls = python_log.read_text(encoding="utf-8").splitlines()
        install_call = next(call for call in calls if "scripts/install_launchd.py" in call)
        self.assertIn("--include-listener", install_call)
        self.assertNotIn("--install", install_call)
        self.assertIn("main.py --status --dry-run", calls)


if __name__ == "__main__":
    unittest.main()
