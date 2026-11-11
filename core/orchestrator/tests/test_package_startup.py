"""Offline startup contracts. Never open a user's config, vault or devices."""

import contextlib
import importlib.util
import io
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from core import config
import startup


APP = Path(__file__).resolve().parents[3]
REPO = APP.parent


def load_runtime():
    name = "_dude_startup_contract_runtime"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, APP / "dude.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


class PackageStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dude-startup-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "safe.json"
        self.path.write_text('{"brain": {"providers": []}, "vault_path": ""}', encoding="utf-8")
        self.data = self.root / "data"
        self.args = ["--config", str(self.path), "--data-dir", str(self.data)]
        self.addCleanup(patch.stopall)
        patch.object(config, "_config", None).start()
        patch.object(config, "CONFIG_PATH", str(self.root / "default.json")).start()
        patch.object(config, "LOCAL_CONFIG_PATH", str(self.root / "local.json")).start()
        patch.object(config, "ROOT", str(self.root)).start()
        patch.object(config, "ENV_KEYS", {}).start()
        patch.object(config.Config, "_read_vault", side_effect=AssertionError("vault access")).start()
        self.runtime = load_runtime()

    def process(self, command, *, input_text=None):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("DUDE_") and key != "PYTHONPATH"}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run([sys.executable, "-B", *command], cwd=REPO,
                              env=env, input=input_text, capture_output=True,
                              text=True, timeout=15)

    def test_package_and_direct_cli_help_and_startup_check(self):
        for command in (["-m", "dude"], [str(APP / "dude.py")]):
            with self.subTest(command=command):
                help_result = self.process([*command, "--help"])
                self.assertEqual(help_result.returncode, 0, help_result.stderr)
                self.assertIn("--headless", help_result.stdout)
                self.assertIn("--text", help_result.stdout)
                result = self.process([*command, "--startup-check"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("dude/dude.py:run_agent", result.stdout)
                self.assertIn("not checked", result.stdout)

    def test_probe_imports_without_config_reads_writes_or_runtime_dependencies(self):
        # Fail on ANY write, any config read, or any heavy/runtime import.
        script = r'''
import importlib.abc, os, runpy, sys
class BlockRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ("core.brain", "core.ear", "core.tools", "core.secrets",
                        "core.orchestrator", "openai", "sounddevice", "tkinter"):
            raise AssertionError("runtime import: " + fullname)
sys.meta_path.insert(0, BlockRuntime())
def audit(event, args):
    if event == "open":
        path, mode, flags = args
        if isinstance(path, (str, bytes)) and os.fsdecode(path).endswith(".json"):
            raise AssertionError("configuration read")
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
            raise AssertionError("write")
    if event in ("os.mkdir", "subprocess.Popen", "socket.__new__"):
        raise AssertionError(event)
sys.addaudithook(audit)
sys.argv = ["dude", "--startup-check"]
try:
    runpy.run_module("dude", run_name="__main__")
except SystemExit as exc:
    assert exc.code == 0, exc.code
from core import config
assert config._config is None
assert "dude.core.agent" not in sys.modules
'''
        result = self.process(["-c", script])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_package_dispatches_to_canonical_run_agent_and_propagates_exit(self):
        script = r'''
import runpy, sys
from unittest.mock import patch
from dude import dude as runtime
from core import config
arguments = sys.argv[1:]
def run(args):
    assert runtime.get_config() is config.get_config()
    assert args.no_ui and args.debug
    return 17
sys.argv = ["dude", *arguments, "--headless", "--verbose"]
with patch.object(runtime, "run_agent", side_effect=run) as mocked:
    try:
        runpy.run_module("dude", run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 17, exc.code
    else:
        raise AssertionError("entry point did not propagate exit")
    mocked.assert_called_once()
'''
        result = self.process(["-c", script, *self.args])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_legacy_aliases_and_mode_conflicts(self):
        args = startup.parse_args(["--headless", "--verbose", "--text", *self.args])
        self.assertTrue(args.no_ui)
        self.assertTrue(args.debug)
        self.assertEqual(args.config, str(self.path))
        self.assertEqual(args.data_dir, str(self.data))
        self.assertEqual(startup.parse_args(["--export-memory"]).export_memory, "")
        for arguments in (["--text", "--mic-test"], ["--text-max-turns", "2"],
                          ["--text", "--text-max-turns", "0"], ["--unknown"],
                          ["--startup-check", *self.args]):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    startup.parse_args(arguments)
                self.assertEqual(caught.exception.code, 2)

    def test_canonical_dispatch_config_singleton_and_return_code(self):
        def run(args):
            cfg = config.get_config()
            self.assertIs(cfg, self.runtime.get_config())
            self.assertEqual(cfg.data_dir, str(self.data))
            self.assertEqual(cfg.get("data_dir"), str(self.data))
            self.assertTrue(args.no_ui)
            self.assertTrue(args.debug)
            return 7

        with patch.object(self.runtime, "run_agent", side_effect=run) as run_mock:
            self.assertEqual(self.runtime.main([*self.args, "--headless", "--verbose"]), 7)
        run_mock.assert_called_once()

    def test_explicit_config_bypasses_local_overlay(self):
        Path(config.LOCAL_CONFIG_PATH).write_text("not valid json", encoding="utf-8")
        cfg = config.configure(self.path, self.data)
        self.assertEqual(cfg.get("brain", "providers"), [])
        self.assertIs(config.get_config(), cfg)
        self.assertFalse(cfg._vault_loaded)

    def test_default_config_keeps_local_merge_without_creating_config(self):
        Path(config.LOCAL_CONFIG_PATH).write_text('{"user_title":"tester"}', encoding="utf-8")
        cfg = config.configure(data_dir=self.data)
        self.assertEqual(cfg.get("user_title"), "tester")
        self.assertFalse(Path(config.CONFIG_PATH).exists())

    def test_invalid_config_fails_once_without_runtime_or_data_writes(self):
        for content in ("{", "[]", '{"brain":[]}', '{"brain":{"providers":{}}}',
                        '{"brain":{"providers":[{}]}}', '{"data_dir":3}'):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                with patch.object(self.runtime, "run_agent") as run_mock, \
                        contextlib.redirect_stderr(io.StringIO()) as error:
                    self.assertEqual(self.runtime.main(self.args), 2)
                run_mock.assert_not_called()
                self.assertFalse(self.data.exists())
                self.assertIn("invalid configuration", error.getvalue())

    def test_invalid_data_directory_never_falls_back(self):
        self.data.write_text("occupied", encoding="utf-8")
        with patch.object(self.runtime, "run_agent") as run_mock, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.runtime.main(self.args), 2)
        run_mock.assert_not_called()
        self.assertIsNone(config._config)

    def test_missing_config_and_wizard_subprocess_fail_without_writes(self):
        missing = self.root / "missing.json"
        for flag in ([], ["--wizard"]):
            result = self.process(["-m", "dude", "--config", str(missing),
                                   "--data-dir", str(self.data), *flag])
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(missing.exists())
            self.assertFalse(self.data.exists())

    def test_runtime_failure_is_not_retried_and_added_handlers_are_closed(self):
        handler = Mock(spec=logging.Handler)
        handler.level = logging.NOTSET
        def fail(_args):
            logging.getLogger().addHandler(handler)
            raise RuntimeError("do not print provider-secret")
        with patch.object(self.runtime, "run_agent", side_effect=fail) as run_mock, \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(self.runtime.main(self.args), 1)
        run_mock.assert_called_once()
        handler.close.assert_called_once()
        self.assertNotIn(handler, logging.getLogger().handlers)
        self.assertNotIn("provider-secret", error.getvalue())

    def test_windowed_output_uses_selected_data_and_restores_streams(self):
        cfg = config.configure(self.path, self.data)
        with patch.object(sys, "stdout", None), patch.object(sys, "stderr", None):
            with startup._windowed_stdio(cfg):
                stream = sys.stdout
                self.assertIs(sys.stdout, sys.stderr)
                print("windowed startup")
            self.assertIsNone(sys.stdout)
            self.assertIsNone(sys.stderr)
            self.assertTrue(stream.closed)
        self.assertIn("windowed startup", (self.data / "logs" / "stdio.log").read_text(encoding="utf-8"))

    def test_keyboard_interrupt_and_system_exit_propagation(self):
        with patch.object(self.runtime, "run_agent", side_effect=KeyboardInterrupt):
            self.assertEqual(self.runtime.main(self.args), 130)
        with patch.object(self.runtime, "run_agent", side_effect=SystemExit(9)):
            with self.assertRaises(SystemExit) as caught:
                self.runtime.main(self.args)
        self.assertEqual(caught.exception.code, 9)

    def text_mocks(self):
        memory = Mock()
        memory.recent_messages.return_value = [{"role": "assistant", "content": "earlier"}]
        client = Mock()
        brain = Mock(providers=[SimpleNamespace(_client=client)], _fallback_cache={})
        brain.think.return_value = "hello"
        module = SimpleNamespace(Brain=Mock(return_value=brain))
        return memory, client, brain, module

    def test_text_uses_canonical_storage_no_chat_tools_and_closes(self):
        memory, client, brain, module = self.text_mocks()
        with patch("core.memory.Memory", return_value=memory), \
                patch.dict(sys.modules, {"core.brain": module}), \
                patch("builtins.input", side_effect=["hello", "quit"]), \
                patch.object(self.runtime, "_load_runtime_dependencies", side_effect=AssertionError("hardware")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.runtime.main([*self.args, "--text"]), 0)
        memory.add_message.assert_any_call("user", "hello")
        memory.add_message.assert_any_call("assistant", "hello")
        brain.think.assert_called_once()
        brain.chat.assert_not_called()
        self.assertIn("earlier", brain.think.call_args.args[0])
        self.assertFalse(module.Brain.call_args.args[1]("perform action?"))
        client.close.assert_called_once()
        memory.close.assert_called_once()

    def test_text_failure_and_interrupt_close_memory_and_clients(self):
        for failure, code in ((RuntimeError("provider failed"), 1), (KeyboardInterrupt(), 130)):
            with self.subTest(code=code):
                memory, client, brain, module = self.text_mocks()
                brain.think.side_effect = failure
                client.close.side_effect = OSError("cleanup failure")
                with patch("core.memory.Memory", return_value=memory), \
                        patch.dict(sys.modules, {"core.brain": module}), \
                        patch("builtins.input", return_value="hello"), \
                        contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(self.runtime.main([*self.args, "--text"]), code)
                memory.close.assert_called_once()
                client.close.assert_called_once()

    def test_text_constructor_failure_closes_memory(self):
        memory, _, _, module = self.text_mocks()
        module.Brain.side_effect = RuntimeError("cannot construct")
        with patch("core.memory.Memory", return_value=memory), \
                patch.dict(sys.modules, {"core.brain": module}), \
                patch("builtins.input", return_value="hello"), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.runtime.main([*self.args, "--text"]), 1)
        memory.close.assert_called_once()

    def test_text_eof_quit_and_input_bound_do_not_construct_brain(self):
        for inputs in ([EOFError()], ["quit"], ["", ""]):
            memory, _, _, module = self.text_mocks()
            with self.subTest(inputs=inputs), patch("core.memory.Memory", return_value=memory), \
                    patch.dict(sys.modules, {"core.brain": module}), \
                    patch("builtins.input", side_effect=inputs) as input_mock, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.runtime.main([*self.args, "--text", "--text-max-turns", "2"]), 0)
            module.Brain.assert_not_called()
            memory.close.assert_called_once()
            self.assertLessEqual(input_mock.call_count, 2)

    def test_real_text_quit_subprocess_uses_only_temporary_memory(self):
        for command in (["-m", "dude"], [str(APP / "dude.py")]):
            with self.subTest(command=command):
                result = self.process([*command, *self.args, "--text"], input_text="quit\n")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((self.data / "memory.db").is_file())
                self.assertFalse((self.data / "dude.lock").exists())
                self.assertFalse((self.data / "logs" / "dude.log").exists())


if __name__ == "__main__":
    unittest.main()
