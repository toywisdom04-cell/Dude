"""Offline diagnostic regressions; fixture imports never touch live services."""

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


APP_ROOT = Path(__file__).resolve().parents[3]
CHECKER_PATH = APP_ROOT / "environment_check.py"
spec = importlib.util.spec_from_file_location("_environment_checker_regression", CHECKER_PATH)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def successful_probe(check, timeout):
    return {"ok": True, "detail": "fixture check", "duration_ms": 1.25}


class TestEnvironmentReport(unittest.TestCase):
    def report(self, probe_fn=successful_probe, platform_name="win32"):
        return checker.collect_checks(platform_name=platform_name, probe_fn=probe_fn)

    def test_current_recovery_task_engine_and_latency_exports(self):
        checks = {item[0]: item for item in checker.CHECKS}
        self.assertEqual(checks["core.orchestrator.recovery"][1], ("RecoveryEngine",))
        self.assertEqual(checks["core.orchestrator.task_engine"][1], ("TaskEngine",))
        self.assertEqual(checks["core.orchestrator.perception_service"][1], ("LatencyMetrics",))
        self.assertNotIn("core.recovery", checks)
        self.assertNotIn("latency_measurement", checks)

    def test_success_is_measured_and_returns_zero(self):
        report = self.report()
        self.assertTrue(report["ready"])
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["summary"]["fail"], 0)
        self.assertEqual(report["checks"][1]["duration_ms"], 1.25)
        self.assertGreaterEqual(report["duration_ms"], 0)
        with patch.object(checker, "collect_checks", return_value=report), patch("sys.stdout", new=io.StringIO()) as out:
            self.assertEqual(checker.main(["--json"]), 0)
            self.assertEqual(json.loads(out.getvalue()), report)

    def test_required_failure_controls_exit_and_reports_reason(self):
        def probe(check, timeout):
            return {"ok": check[0] != "core.tools", "detail": "ImportError: missing dependency", "duration_ms": 2}
        report = self.report(probe)
        self.assertFalse(report["ready"])
        self.assertEqual(report["summary"]["fail"], 1)
        with patch.object(checker, "collect_checks", return_value=report), patch("sys.stdout", new=io.StringIO()) as out:
            self.assertEqual(checker.main([]), 1)
            self.assertIn("[FAIL] core.tools", out.getvalue())
            self.assertIn("ImportError: missing dependency", out.getvalue())

    def test_missing_optional_packages_are_warnings(self):
        def probe(check, timeout):
            return {"ok": check[2], "detail": "not installed", "duration_ms": 0}
        report = self.report(probe)
        self.assertTrue(report["ready"])
        self.assertEqual(report["exit_code"], 0)
        self.assertGreater(report["summary"]["warn"], 0)
        self.assertEqual(report["summary"]["fail"], 0)
        self.assertTrue(all(item["status"] == "warn" for item in report["checks"] if not item["required"]))

    def test_non_windows_skips_windows_imports_and_explains_platform_failure(self):
        probe = Mock(side_effect=successful_probe)
        report = self.report(probe, "linux")
        self.assertEqual(report["exit_code"], 1)
        self.assertIn("detected linux", report["checks"][0]["detail"])
        self.assertEqual(report["summary"]["fail"], 1)
        for call in probe.call_args_list:
            self.assertFalse(call.args[0][4], "Windows-only import must not be attempted")
        self.assertEqual(report["summary"]["skip"], 5)

    def test_console_and_json_are_safe_with_cp1252_and_unicode_errors(self):
        report = self.report()
        report["app_root"] = "C:/\u6d4b\u8bd5/DUDE"
        report["checks"][1]["detail"] = "cannot import \u2717 \U0001f680"
        for args in ([], ["--json"]):
            raw = io.BytesIO()
            stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
            with patch.object(checker, "collect_checks", return_value=report), patch("sys.stdout", stream):
                checker.main(args)
                stream.flush()
            output = raw.getvalue().decode("ascii")
            if args:
                self.assertEqual(json.loads(output)["app_root"], report["app_root"])
            else:
                self.assertIn("\\u6d4b", output)
                self.assertIn("not live validation", output)
                self.assertNotIn("Cannot launch", output)
            stream.close()

    def test_exports_are_inspected_without_live_service_construction(self):
        for check in checker.CHECKS:
            if check[3] != "import":
                continue
            module = type("Module", (), {})()
            exports = []
            for name in check[1]:
                export = Mock(side_effect=AssertionError("live service called"))
                setattr(module, name, export)
                exports.append(export)
            with patch.object(checker.importlib, "import_module", return_value=module):
                self.assertTrue(checker._probe_in_process(check)["ok"], check[0])
            for export in exports:
                export.assert_not_called()

    def test_missing_export_is_a_failure(self):
        with patch.object(checker.importlib, "import_module", return_value=object()):
            result = checker._probe_in_process(checker.CHECKS[0])
        self.assertFalse(result["ok"])
        self.assertIn("core.brain.Brain", result["detail"])

    def test_audio_discovery_does_not_import_packages(self):
        with patch.object(checker.importlib, "import_module", side_effect=AssertionError("audio import")), \
                patch.object(checker.importlib.util, "find_spec", return_value=object()):
            for check in checker.CHECKS:
                if check[3] == "discovery":
                    result = checker._probe_in_process(check)
                    self.assertTrue(result["ok"])
                    self.assertIn("not tested", result["detail"])

    def test_missing_optional_discovery_is_not_reported_as_success(self):
        check = next(item for item in checker.CHECKS if item[0] == "sounddevice")
        with patch.object(checker.importlib.util, "find_spec", return_value=None):
            result = checker._probe_in_process(check)
        self.assertFalse(result["ok"])
        self.assertIn("not installed", result["detail"])

    def test_timeout_crash_and_malformed_worker_output_are_failures(self):
        outcomes = [subprocess.TimeoutExpired("fixture", 0.01),
                    subprocess.CompletedProcess([], 42, "", ""),
                    subprocess.CompletedProcess([], 0, "not json", ""),
                    subprocess.CompletedProcess([], 0, '{"ok": "true"}', "")]
        for outcome in outcomes:
            kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
            with patch.object(checker.subprocess, "run", **kwargs) as run:
                result = checker.probe(checker.CHECKS[0], timeout=0.01)
            self.assertFalse(result["ok"])
            self.assertGreaterEqual(result["duration_ms"], 0)
            self.assertEqual(run.call_args.kwargs["timeout"], 0.01)

    def test_invalid_timeouts_rejected(self):
        for value in ("0", "-1", "nan", "inf"):
            with self.assertRaises(SystemExit) as result, patch("sys.stderr", new=io.StringIO()):
                checker.main(["--timeout", value])
            self.assertEqual(result.exception.code, 2)

    def test_real_module_probes_do_not_start_services_or_load_user_config(self):
        # Run real application imports in an isolated interpreter with tripwires
        # installed BEFORE imports. Missing dependencies are legitimate results;
        # touching a live boundary is always a regression, even if caught.
        code = r'''
import importlib.util, json, pathlib, sys
path = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(path.parent))
spec = importlib.util.spec_from_file_location("checker", path)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
violations = []
def forbid(message):
    violations.append(message)
    raise AssertionError(message)
def profile(frame, event, arg):
    if event != "call":
        return
    module = frame.f_globals.get("__name__", "")
    name = frame.f_code.co_name
    owner = frame.f_locals.get("self")
    if module == "core.config" and name in ("get_config", "__init__"):
        forbid("user configuration loaded")
    if name == "__init__" and type(owner).__name__ in (
            "ScreenMap", "Voice", "Ear", "Brain", "TaskEngine", "WhisperModel", "SileroVAD"):
        forbid("live service constructed: " + type(owner).__name__)
    if module == "threading" and name == "start":
        forbid("background thread started")
    if module.startswith("uiautomation") and name in (
            "GetRootControl", "GetForegroundControl", "GetFocusedControl"):
        forbid("desktop read")
def audit(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
        forbid("external operation: " + event)
    if event == "import" and args[0] in ("sounddevice", "faster_whisper", "pygame", "core.ear"):
        forbid("audio/model initialization import: " + args[0])
sys.addaudithook(audit)
sys.setprofile(profile)
for check in checker.CHECKS:
    if not check[4] or sys.platform == "win32":
        checker._probe_in_process(check)
sys.setprofile(None)
assert not violations, violations
print("safe import boundaries verified")
'''
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", code, str(CHECKER_PATH)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("safe import boundaries verified", result.stdout)


class TestEnvironmentCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dude-environment-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "dude"
        self.app.mkdir()
        shutil.copyfile(CHECKER_PATH, self.app / "environment_check.py")
        # All fixture constructors and actions fail if the checker calls them.
        for module, symbols, _, method, _ in checker.CHECKS:
            target = self.app.joinpath(*module.split(".")).with_suffix(".py")
            target.parent.mkdir(parents=True, exist_ok=True)
            parent = target.parent
            while parent != self.app:
                (parent / "__init__.py").touch()
                parent = parent.parent
            if method == "discovery":
                source = "raise AssertionError('optional hardware/model package imported')\n"
            else:
                source = "print('library chatter must not corrupt JSON')\n"
                for symbol in symbols:
                    source += "def %s(*args, **kwargs):\n    raise AssertionError('live service constructed')\n" % symbol
            target.write_text(source, encoding="utf-8")
        # These are forbidden even as imports; constructors in actual modules
        # would load config/vault, start threads, or download models.
        for name in ("config", "secrets", "ear"):
            (self.app / "core" / (name + ".py")).write_text(
                "raise AssertionError('configuration/vault/audio must not be loaded')\n", encoding="utf-8")

    def run_checker(self, *args, cwd=None):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "cp1252:strict"
        env.pop("PYTHONPATH", None)
        return subprocess.run([sys.executable, "-B", *args], cwd=cwd or self.root,
                              env=env, capture_output=True, text=True,
                              encoding="ascii", timeout=40)

    def test_script_repo_app_and_module_invocations_are_consistent(self):
        commands = [(self.root, ("dude/environment_check.py", "--json")),
                    (self.app, ("environment_check.py", "--json")),
                    (self.root, ("-m", "dude.environment_check", "--json"))]
        reports = []
        for cwd, command in commands:
            result = self.run_checker(*command, cwd=cwd)
            self.assertEqual(result.returncode, 0 if sys.platform == "win32" else 1, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["summary"]["warn"], 0, result.stdout)
            self.assertEqual(report["summary"]["fail"], 0 if sys.platform == "win32" else 1, result.stdout)
            reports.append([(row["name"], row["status"], row["method"]) for row in report["checks"]])
        self.assertEqual(reports[0], reports[1])
        self.assertEqual(reports[0], reports[2])

    def test_required_import_error_has_process_exit_one(self):
        (self.app / "core" / "tools.py").write_text("raise ImportError('fixture required failure')\n", encoding="utf-8")
        result = self.run_checker("dude/environment_check.py", "--json")
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        tools = next(item for item in report["checks"] if item["name"] == "core.tools")
        self.assertEqual(tools["status"], "fail")
        self.assertIn("fixture required failure", tools["detail"])


if __name__ == "__main__":
    unittest.main()
