"""Offline tests of opt-in/deadline boundaries; never activates hardware."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


APP = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("bounded_live_validation", APP / "live_validation.py")
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class TestLiveValidationBoundary(unittest.TestCase):
    def test_requires_explicit_opt_in(self):
        with patch.object(live.subprocess, "run") as run, contextlib.redirect_stderr(io.StringIO()):
            for argv in ([], ["--microphone"]):
                with self.subTest(argv=argv), self.assertRaises(SystemExit) as error:
                    live.main(argv)
                self.assertEqual(error.exception.code, 2)
        run.assert_not_called()

    def test_selected_worker_deadline_and_temporary_cleanup(self):
        directories = []

        def run(command, **kwargs):
            self.assertEqual(kwargs["timeout"], 60)
            self.assertTrue(kwargs["capture_output"])
            self.assertEqual(command[command.index("--_worker") + 1], "voice")
            self.assertIn("--microphone", command)
            directory = Path(command[command.index("--_directory") + 1])
            self.assertTrue(directory.is_dir())
            directories.append(directory)
            return subprocess.CompletedProcess(command, 0,
                'DUDE_VALIDATION_RESULT={"status":"pass"}\n', '')

        output = io.StringIO()
        with patch.object(live.subprocess, "run", side_effect=run), contextlib.redirect_stdout(output):
            self.assertEqual(live.main(["--voice", "--microphone"]), 0)
        self.assertEqual(list(json.loads(output.getvalue())["results"]), ["voice"])
        self.assertTrue(all(not directory.exists() for directory in directories))

    def test_timeout_and_missing_receipt_are_failures(self):
        for outcome in (subprocess.TimeoutExpired("worker", 60),
                        subprocess.CompletedProcess([], 1, "", ""),
                        subprocess.CompletedProcess([], 1,
                            'DUDE_VALIDATION_RESULT={"status":"pass"}', ""),
                        subprocess.CompletedProcess([], 0,
                            'DUDE_VALIDATION_RESULT=invalid', "")):
            with self.subTest(outcome=type(outcome).__name__):
                output = io.StringIO()
                kwargs = ({"side_effect": outcome} if isinstance(outcome, Exception)
                          else {"return_value": outcome})
                with patch.object(live.subprocess, "run", **kwargs), contextlib.redirect_stdout(output):
                    self.assertEqual(live.main(["--desktop"]), 1)
                self.assertEqual(json.loads(output.getvalue())["results"]["desktop"]["status"], "fail")

    def test_worker_failure_is_structured(self):
        output = io.StringIO()
        with patch.object(live, "desktop_check", side_effect=RuntimeError("fixture failure")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(live.main(["--_worker", "desktop"]), 1)
        self.assertIn('"status": "fail"', output.getvalue())


if __name__ == "__main__":
    unittest.main()
