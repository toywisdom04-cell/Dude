"""Offline regression tests for ProcedureStore's in-memory lifecycle."""
import gc
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import weakref


def _load_store_module():
    # Load only the store and its state types, without starting the orchestrator
    # package's unrelated desktop, audio, or model integrations.
    root = Path(__file__).resolve().parents[1]
    package_name = "_procedure_store_memory_tests"
    package = types.ModuleType(package_name)
    package.__path__ = [str(root)]
    with patch.dict(sys.modules, {package_name: package}):
        for name in ("state", "procedure_store"):
            module_name = f"{package_name}.{name}"
            spec = importlib.util.spec_from_file_location(module_name, root / f"{name}.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        return module


store_module = _load_store_module()
ProcedureStore = store_module.ProcedureStore
Procedure = store_module.Procedure
TaskType = store_module.TaskType


class TestProcedureStoreMemory(unittest.TestCase):
    def make_store(self, path=":memory:"):
        store = ProcedureStore(path)
        self.addCleanup(store.close)
        return store

    @staticmethod
    def procedure(goal="memory regression"):
        return Procedure(
            goal=goal,
            goal_type=TaskType.UNKNOWN,
            context={"test_app": "offline"},
            steps=[{"type": "test_action", "value": "before"}],
            confidence=0.7,
        )

    def test_save_read_update_and_queries(self):
        store = self.make_store()
        self.assertEqual(store.get_stats()["total_procedures"], 0)
        procedure = self.procedure()
        proc_id = store.save(procedure)
        saved = store.get(proc_id)
        self.assertEqual(saved.goal, procedure.goal)
        self.assertEqual(saved.steps, procedure.steps)
        self.assertEqual(saved.context, procedure.context)
        for matches in (
            store.find_by_goal("memory"),
            store.find_by_context({"test_app": "offline"}),
            store.find_by_type_and_params("test_action", ["value"]),
            store.find_similar("memory", ["test_action"], []),
        ):
            self.assertEqual([match.id for match in matches], [proc_id])

        saved.steps = [{"type": "test_action", "value": "after"}]
        self.assertEqual(store.save(saved), proc_id)
        self.assertEqual(store.get(proc_id).steps, saved.steps)
        self.assertEqual(store.get(proc_id).version, 2)
        self.assertEqual(store.get_stats()["total_procedures"], 1)
        self.assertTrue(store.get_versions(proc_id))
        self.assertEqual(store.get_version(proc_id, 1).steps, procedure.steps)
        store.record_success(proc_id)
        store.record_failure(proc_id)
        store.record_execution(proc_id, success=True)
        store.record_execution(proc_id, success=False)
        saved = store.get(proc_id)
        self.assertEqual((saved.success_count, saved.failure_count), (2, 2))
        self.assertAlmostEqual(saved.confidence, 0.6)
        self.assertIsNone(store.get(proc_id + 1))

    def test_event_schema_and_history_survive_between_calls(self):
        store = self.make_store()
        with patch.object(store, "_normalize_goal", side_effect=lambda goal, params: goal):
            event_id = store.record_event("failure", "offline goal", reason="test failure")
            store.record_event("recovery", "offline goal", recovery_outcome="test recovery")
            events = store.recent_failures("offline goal")
            self.assertEqual([event["kind"] for event in events], ["recovery", "failure"])
            self.assertEqual(events[1]["id"], event_id)
            self.assertEqual(events[1]["reason"], "test failure")
            self.assertTrue(store.has_recent_failures("offline goal", min_count=1))

    def test_instances_are_isolated(self):
        first = self.make_store()
        second = self.make_store()
        first_id = first.save(self.procedure("first instance"))
        self.assertIsNone(second.get(first_id))
        self.assertEqual(second.get_stats()["total_procedures"], 0)
        second_id = second.save(self.procedure("second instance"))
        self.assertEqual(first.get(first_id).goal, "first instance")
        self.assertEqual(second.get(second_id).goal, "second instance")
        first.close()
        self.assertEqual(second.get(second_id).goal, "second instance")

    def test_shared_instance_supports_worker_threads(self):
        store = self.make_store()
        proc_id = store.save(self.procedure())

        def work(index):
            store.record_success(proc_id)
            saved_id = store.save(self.procedure(f"worker {index}"))
            return store.get(saved_id).goal

        with ThreadPoolExecutor(max_workers=4) as executor:
            goals = list(executor.map(work, range(20)))
        self.assertEqual(goals, [f"worker {index}" for index in range(20)])
        self.assertEqual(store.get(proc_id).success_count, 20)
        self.assertEqual(store.get_stats()["total_procedures"], 21)

    def test_file_database_persists_across_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "procedures.sqlite")
            first = self.make_store(path)
            proc_id = first.save(self.procedure())
            first.close()
            second = self.make_store(path)
            self.assertEqual(second.get(proc_id).goal, "memory regression")
            second.record_success(proc_id)
            second.close()
            self.assertEqual(self.make_store(path).get(proc_id).success_count, 1)

    def capture_connections(self):
        connections = []
        connect = sqlite3.connect

        def track(*args, **kwargs):
            connection = connect(*args, **kwargs)
            connections.append(connection)
            return connection

        patcher = patch.object(store_module.sqlite3, "connect", side_effect=track)
        patcher.start()
        self.addCleanup(patcher.stop)
        return connections

    def assert_connections_closed(self, connections):
        self.assertTrue(connections)
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_close_releases_connections_and_is_idempotent(self):
        connections = self.capture_connections()
        store = self.make_store()
        store.save(self.procedure())
        store.get_stats()
        # Every operation connection is already closed; only the keeper lives.
        self.assert_connections_closed(connections[1:])
        self.assertEqual(connections[0].execute("SELECT 1").fetchone(), (1,))
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(store.close).result()
        store.close()
        self.assert_connections_closed(connections)
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
            store.get_stats()

    def test_unreferenced_store_releases_keeper(self):
        connections = self.capture_connections()
        store = ProcedureStore(":memory:")
        reference = weakref.ref(store)
        store.save(self.procedure())
        del store
        gc.collect()
        self.assertIsNone(reference())
        self.assert_connections_closed(connections)

    def test_initialization_failure_releases_keeper(self):
        connections = self.capture_connections()
        with patch.object(ProcedureStore, "_init_db", side_effect=RuntimeError("test init")):
            with self.assertRaisesRegex(RuntimeError, "test init"):
                ProcedureStore(":memory:")
        self.assert_connections_closed(connections)


if __name__ == "__main__":
    unittest.main()
