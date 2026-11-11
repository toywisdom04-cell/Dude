"""Isolated runner regressions using copied pure config/store code, never hardware."""

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


APP_ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = APP_ROOT / "run_tests.py"
PYTEST_CONFIG = APP_ROOT.parent / "pytest.ini"
spec = importlib.util.spec_from_file_location("_dude_test_runner_regression", RUNNER_PATH)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class TestNameFiltering(unittest.TestCase):
    def make_suite(self):
        class AlphaCase(unittest.TestCase):
            def test_first(self):
                pass

            def test_second(self):
                pass

        class BetaCase(unittest.TestCase):
            def test_third(self):
                pass

        cases = [AlphaCase("test_first"), AlphaCase("test_second"), BetaCase("test_third")]
        return unittest.TestSuite([
            unittest.TestSuite([unittest.TestSuite(cases[:2])]),
            unittest.TestSuite(cases[2:]),
        ])

    def test_method_filter_selects_only_matching_leaf(self):
        selected = runner.filter_tests(self.make_suite(), ["FIRST"])
        self.assertEqual([case._testMethodName for case in selected], ["test_first"])

    def test_class_filter_and_multiple_patterns_use_or(self):
        selected = runner.filter_tests(self.make_suite(), ["ALPHACASE", "third"])
        self.assertEqual(selected.countTestCases(), 3)

    def test_module_filter(self):
        selected = runner.filter_tests(self.make_suite(), [__name__.upper()])
        self.assertEqual(selected.countTestCases(), 3)

    def test_empty_filter_keeps_all_cases_and_unknown_filter_keeps_none(self):
        self.assertEqual(runner.filter_tests(self.make_suite(), []).countTestCases(), 3)
        self.assertEqual(runner.filter_tests(self.make_suite(), ["absent"]).countTestCases(), 0)


class TestBoundedDiscovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dude-runner-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "dude"
        self.tests = self.app / "core" / "orchestrator" / "tests"
        self.tests.mkdir(parents=True)
        # Match the real layout: core/orchestrator are packages, tests is not.
        (self.app / "core" / "__init__.py").write_text("", encoding="utf-8")
        (self.app / "core" / "orchestrator" / "__init__.py").write_text("", encoding="utf-8")
        shutil.copyfile(RUNNER_PATH, self.app / "run_tests.py")
        shutil.copyfile(PYTEST_CONFIG, self.root / "pytest.ini")
        shutil.copyfile(APP_ROOT / "_test_sandbox.py", self.app / "_test_sandbox.py")
        shutil.copyfile(APP_ROOT.parent / "conftest.py", self.root / "conftest.py")
        shutil.copyfile(APP_ROOT / "core" / "config.py", self.app / "core" / "config.py")
        (self.tests / "test_perception.py").write_text(
            "import unittest\n"
            "from pathlib import Path\n"
            "from core.config import get_config\n"
            "cfg = get_config()\n"
            "assert cfg._vault_loaded and cfg._vault_cache == ''\n"
            "assert cfg.get('brain', 'providers') == []\n"
            "assert cfg.get('memory.data_dir') == cfg.get('memory', 'data_dir') == cfg.data_dir\n"
            "assert Path(cfg.data_dir).is_dir()\n"
            "(Path(__file__).resolve().parents[4] / 'sandbox-root.txt').write_text(cfg.root, encoding='utf-8')\n"
            "class FixtureCase(unittest.TestCase):\n"
            "    def test_chosen(self): pass\n"
            "    def test_other(self): pass\n",
            encoding="utf-8",
        )
        for path in (
            self.root / "test_perception.py",
            self.app / "test_live_audio.py",
            self.app / "test_tts.py",
            self.app / "_quarantine" / "tests" / "test_perception.py",
            self.app / "core" / "orchestrator" / "tests_live" / "test_external.py",
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "from pathlib import Path\n"
                f"Path({str(self.root / 'unsafe-import.txt')!r}).write_text('imported', encoding='utf-8')\n"
                "raise AssertionError('unmaintained test imported')\n",
                encoding="utf-8",
            )

    def run_python(self, *args, cwd=None):
        env = os.environ.copy()
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        env.pop("PYTEST_ADDOPTS", None)
        env.pop("PYTEST_PLUGINS", None)
        env.pop("PYTHONPATH", None)
        return subprocess.run(
            [sys.executable, *args], cwd=cwd or self.root, env=env,
            capture_output=True, text=True, timeout=30,
        )

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_runtime_cleaned(self):
        root = Path((self.root / "sandbox-root.txt").read_text(encoding="utf-8"))
        self.assertFalse(root.exists(), f"Runtime sandbox leaked: {root}")

    def test_default_runner_is_bounded_from_unrelated_working_directory(self):
        outside = self.root / "elsewhere"
        outside.mkdir()
        result = self.run_python(str(self.app / "run_tests.py"), cwd=outside)
        self.assert_success(result)
        self.assertIn("Ran 2 tests", result.stderr)
        self.assert_runtime_cleaned()

    def test_cli_preserves_verbose_and_case_insensitive_method_filters(self):
        result = self.run_python("dude/run_tests.py", "-v", "CHOSEN")
        self.assert_success(result)
        self.assertIn("test_chosen", result.stderr)
        self.assertNotIn("test_other", result.stderr)
        self.assertIn("Ran 1 test", result.stderr)
        self.assert_runtime_cleaned()

    def test_standard_library_entry_point_does_not_import_pytest(self):
        result = self.run_python(
            "-c",
            "import runpy, sys; sys.modules['pytest'] = None; "
            "sys.argv = ['dude/run_tests.py', '--verbose', 'CHOSEN']; "
            "runpy.run_path('dude/run_tests.py', run_name='__main__')",
        )
        self.assert_success(result)
        self.assertIn("Ran 1 test", result.stderr)
        self.assert_runtime_cleaned()

    def test_qualified_loading_ignores_cached_duplicate_basename(self):
        result = self.run_python(
            "-c",
            "import runpy, sys, types; "
            "sys.modules['test_perception'] = types.ModuleType('test_perception'); "
            "module = runpy.run_path('dude/run_tests.py'); "
            "sys.path.insert(0, 'dude'); "
            "from _test_sandbox import runtime_sandbox; "
            "sandbox = runtime_sandbox(); sandbox.__enter__(); "
            "suite = module['discover_tests'](); "
            "ids = [case.id() for case in module['iter_tests'](suite)]; "
            "sandbox.__exit__(None, None, None); "
            "assert len(ids) == 2, ids; "
            "assert all(name.startswith('core.orchestrator.tests.test_perception.') "
            "for name in ids), ids",
        )
        self.assert_success(result)

    def test_no_matches_returns_failure(self):
        result = self.run_python("dude/run_tests.py", "nonexistent")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("No maintained tests matched", result.stderr)

    def test_name_filter_cannot_hide_import_failure(self):
        (self.tests / "test_broken.py").write_text(
            "raise ImportError('fixture import failure')\n", encoding="utf-8",
        )
        result = self.run_python("dude/run_tests.py", "CHOSEN")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("fixture import failure", result.stderr)
        self.assert_runtime_cleaned()

    def test_test_failure_returns_failure(self):
        (self.tests / "test_failure.py").write_text(
            "import unittest\n"
            "class FailureCase(unittest.TestCase):\n"
            "    def test_failure(self): self.fail('fixture assertion failure')\n",
            encoding="utf-8",
        )
        result = self.run_python("dude/run_tests.py", "test_failure")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("fixture assertion failure", result.stderr)
        self.assert_runtime_cleaned()

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is not installed")
    def test_default_pytest_is_bounded_and_ignores_cached_duplicate_basename(self):
        result = self.run_python(
            "-c",
            "import sys, types, pytest; "
            "sys.modules['test_perception'] = types.ModuleType('test_perception'); "
            "raise SystemExit(pytest.main(['-q']))",
        )
        self.assert_success(result)
        self.assertIn("2 passed", result.stdout)
        self.assert_runtime_cleaned()

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is not installed")
    def test_default_pytest_from_app_directory_is_bounded(self):
        result = self.run_python("-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", cwd=self.app)
        self.assert_success(result)
        self.assertIn("2 passed", result.stdout)
        self.assertFalse((self.root / "unsafe-import.txt").exists())
        self.assert_runtime_cleaned()

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is not installed")
    def test_pytest_explicit_broad_paths_are_bounded_from_both_directories(self):
        for cwd, argument in ((self.root, "."), (self.app, "."),
                              (self.root, "dude"), (self.app, "..")):
            with self.subTest(cwd=cwd, argument=argument):
                result = self.run_python("-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                         argument, cwd=cwd)
                self.assert_success(result)
                self.assertIn("2 passed", result.stdout)
                self.assertFalse((self.root / "unsafe-import.txt").exists())
                self.assert_runtime_cleaned()

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is not installed")
    def test_pytest_rejects_explicit_unmaintained_files_before_import(self):
        result = self.run_python("-B", "-m", "pytest", "-q", "test_tts.py", cwd=self.app)
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertIn("outside the maintained test boundary", result.stderr)
        self.assertFalse((self.root / "unsafe-import.txt").exists())

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is not installed")
    def test_pytest_boundary_preserves_maintained_failures(self):
        (self.tests / "test_failure.py").write_text(
            "def test_maintained_failure():\n"
            "    assert False, 'maintained failure must be reported'\n",
            encoding="utf-8",
        )
        result = self.run_python("-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", ".", cwd=self.app)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("1 failed, 2 passed", result.stdout)
        self.assertIn("maintained failure must be reported", result.stdout)
        self.assertFalse((self.root / "unsafe-import.txt").exists())
        self.assert_runtime_cleaned()

    def run_sandbox_script(self, source):
        result = self.run_python(
            "-c", "import sys; sys.path.insert(0, 'dude')\n" + textwrap.dedent(source),
        )
        self.assert_success(result)

    def test_sandbox_restores_config_and_cleans_up_after_exception(self):
        self.run_sandbox_script('''
            from copy import deepcopy
            from pathlib import Path
            from unittest.mock import patch
            from core import config
            from _test_sandbox import runtime_sandbox

            original = {name: getattr(config, name) for name in
                        ('_config', 'ROOT', 'CONFIG_PATH', 'LOCAL_CONFIG_PATH', 'ENV_KEYS')}
            defaults = deepcopy(config.DEFAULTS)
            sentinel = object()
            with patch.object(config, '_config', sentinel):
                try:
                    with patch.object(config.Config, '__init__', side_effect=AssertionError('constructor called')):
                        with runtime_sandbox() as cfg:
                            root = Path(cfg.root)
                            assert config.get_config() is cfg
                            assert cfg.get('memory.data_dir') == cfg.get('memory', 'data_dir') == cfg.data_dir
                            assert Path(cfg.db_path()).parent == Path(cfg.data_dir)
                            assert cfg._read_vault() == ''
                            assert cfg.api_keys_for('openai') == []
                            cfg._data['brain']['providers'].append({'name': 'test-only'})
                            raise RuntimeError('expected test interruption')
                except RuntimeError as error:
                    assert str(error) == 'expected test interruption'
                else:
                    raise AssertionError('sandbox swallowed the exception')
                assert config._config is sentinel
                assert not root.exists()
            assert config.DEFAULTS == defaults
            assert all(getattr(config, name) is value for name, value in original.items())
        ''')

    def test_nested_sandboxes_are_independent_and_restore_outer_config(self):
        self.run_sandbox_script('''
            from pathlib import Path
            from core import config
            from _test_sandbox import runtime_sandbox
            with runtime_sandbox() as outer:
                marker = Path(outer.data_dir) / 'outer-only.txt'
                marker.write_text('outer data', encoding='utf-8')
                outer._data['brain']['providers'].append({'name': 'outer-only'})
                with runtime_sandbox() as inner:
                    inner_root = Path(inner.root)
                    assert inner.data_dir != outer.data_dir
                    assert inner.get('brain', 'providers') == []
                    assert not (Path(inner.data_dir) / marker.name).exists()
                assert config.get_config() is outer
                assert marker.read_text(encoding='utf-8') == 'outer data'
                assert not inner_root.exists()
            assert not marker.exists()
        ''')

    def test_real_default_stores_use_sandbox_and_leave_external_data_untouched(self):
        # Copy only pure modules; an empty orchestrator __init__ avoids importing
        # the app/desktop stack while exercising the actual storage code.
        for name in ('state', 'procedure_store', 'pattern_store', 'procedure_adaptation',
                     'procedure_composition', 'parameter_extractor', 'parameter_binder'):
            shutil.copyfile(APP_ROOT / 'core' / 'orchestrator' / (name + '.py'),
                            self.app / 'core' / 'orchestrator' / (name + '.py'))
        shutil.copyfile(APP_ROOT / 'core' / 'memory.py', self.app / 'core' / 'memory.py')
        self.run_sandbox_script('''
            from contextlib import closing
            import sqlite3
            from pathlib import Path
            from unittest.mock import patch
            from core import config
            from _test_sandbox import runtime_sandbox

            external = Path('external-user-data').resolve()
            external.mkdir()
            (external / 'config.json').write_text('do not load', encoding='utf-8')
            (external / 'config.local.json').write_text('do not load local', encoding='utf-8')
            (external / 'vault.txt').write_text('synthetic vault marker', encoding='utf-8')
            filenames = ('procedures.db', 'observation_patterns.db',
                         'procedure_adaptations.db', 'procedure_compositions.db', 'memory.db')
            for name in filenames:
                with closing(sqlite3.connect(external / name)) as conn:
                    conn.execute('CREATE TABLE user_marker (value TEXT)')
                    conn.execute("INSERT INTO user_marker VALUES ('outside sandbox')")
                    conn.commit()
            before = {path.name: path.read_bytes() for path in external.iterdir()}

            with patch.object(config, 'CONFIG_PATH', str(external / 'config.json')), \\
                 patch.object(config, 'LOCAL_CONFIG_PATH', str(external / 'config.local.json')):
                with runtime_sandbox() as cfg:
                    from core.orchestrator import procedure_store, pattern_store
                    from core.orchestrator import procedure_adaptation, procedure_composition
                    from core.memory import Memory
                    stores = (procedure_store.get_procedure_store(), pattern_store.get_pattern_store(),
                              procedure_adaptation.get_adaptation_store(),
                              procedure_composition.get_composition_store())
                    for store in stores:
                        assert Path(store._db_path).parent == Path(cfg.data_dir)
                        with closing(sqlite3.connect(store._db_path)) as conn:
                            assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='user_marker'").fetchone()[0] == 0
                            conn.execute('CREATE TABLE sandbox_marker (value TEXT)')
                            conn.commit()
                    memory = Memory()
                    memory.close()
                    assert all((Path(cfg.data_dir) / name).is_file() for name in filenames)
                    with runtime_sandbox() as inner:
                        inner_store = procedure_store.get_procedure_store()
                        assert inner_store is not stores[0]
                        assert Path(inner_store._db_path).parent == Path(inner.data_dir)
                    assert procedure_store.get_procedure_store() is stores[0]
                assert not Path(cfg.root).exists()
                assert procedure_store._store is None and procedure_store._store_db_path is None
                assert pattern_store._store is None and pattern_store._store_db_path is None
                assert procedure_adaptation._adaptation_store is None
                assert procedure_composition._composition_store is None
            assert {path.name: path.read_bytes() for path in external.iterdir()} == before
            assert not Path('dude_data').exists()
        ''')


if __name__ == "__main__":
    unittest.main()
