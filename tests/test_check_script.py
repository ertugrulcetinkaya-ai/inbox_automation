import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = PROJECT_ROOT / "scripts" / "check.sh"


class CheckScriptTests(unittest.TestCase):
    def test_missing_ruff_or_pyright_explains_venv_activation(self):
        for missing_tool in ("ruff", "pyright"):
            with self.subTest(tool=missing_tool), tempfile.TemporaryDirectory() as directory:
                tool_dir = Path(directory)
                (tool_dir / "python").symlink_to(sys.executable)
                if missing_tool == "pyright":
                    (tool_dir / "ruff").symlink_to("/usr/bin/true")

                result = subprocess.run(
                    ["/bin/bash", str(CHECK_SCRIPT)],
                    check=False,
                    capture_output=True,
                    env={"PATH": os.fspath(tool_dir)},
                    text=True,
                )

                self.assertEqual(result.returncode, 1)
                self.assertIn(missing_tool, result.stderr)
                self.assertIn("venv'i aktive edin: source .venv/bin/activate", result.stderr)


if __name__ == "__main__":
    unittest.main()
