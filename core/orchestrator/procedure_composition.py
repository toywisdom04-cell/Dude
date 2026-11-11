"""Procedure Composition - Composing multiple procedures into larger workflows."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .state import (
    Procedure,
    ProcedureComposition,
    ProcedureParameter,
    TaskState,
)
from .procedure_store import ProcedureStore, get_procedure_store

log = logging.getLogger(__name__)


class ProcedureCompositionStore:
    """SQLite-backed store for procedure compositions."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            try:
                from core.config import get_config
                cfg = get_config()
                data_dir_str = cfg.get("memory.data_dir") or "./dude_data"
            except Exception:
                data_dir_str = "./dude_data"
            data_dir = Path(data_dir_str)
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(data_dir / "procedure_compositions.db")

        self._db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self):
        """Initialize the compositions table."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS procedure_compositions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        description TEXT NOT NULL DEFAULT '',
                        sub_procedures TEXT NOT NULL DEFAULT '[]',
                        execution_order TEXT NOT NULL DEFAULT '[]',
                        parameters TEXT NOT NULL DEFAULT '[]',
                        verification TEXT NOT NULL DEFAULT '[]',
                        confidence REAL NOT NULL DEFAULT 0.0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        active INTEGER NOT NULL DEFAULT 1,
                        version INTEGER NOT NULL DEFAULT 1,
                        created_by TEXT NOT NULL DEFAULT 'composer'
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_compositions_name
                    ON procedure_compositions(name)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_compositions_active
                    ON procedure_compositions(active)
                """)
                conn.commit()
            finally:
                conn.close()

    def save(self, composition: ProcedureComposition) -> int:
        """Save or update a composition."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                cursor = conn.cursor()
                now = datetime.now().isoformat()

                if hasattr(composition, 'id') and composition.id:
                    cursor.execute("""
                        UPDATE procedure_compositions SET
                            description = ?,
                            sub_procedures = ?,
                            execution_order = ?,
                            parameters = ?,
                            verification = ?,
                            confidence = ?,
                            updated_at = ?,
                            active = ?,
                            version = ?,
                            created_by = ?
                        WHERE id = ?
                    """, (
                        composition.description,
                        json.dumps(composition.sub_procedures),
                        json.dumps(composition.execution_order),
                        json.dumps([p.__dict__ if hasattr(p, '__dict__') else p for p in composition.parameters]),
                        json.dumps(composition.verification),
                        composition.confidence,
                        now,
                        1 if composition.active else 0,
                        composition.version,
                        composition.created_by,
                        composition.id,
                    ))
                    proc_id = composition.id
                else:
                    composition.created_at = now
                    composition.updated_at = now
                    cursor.execute("""
                        INSERT INTO procedure_compositions (
                            name, description, sub_procedures, execution_order,
                            parameters, verification, confidence, created_at, updated_at,
                            active, version, created_by
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        composition.name,
                        composition.description,
                        json.dumps(composition.sub_procedures),
                        json.dumps(composition.execution_order),
                        json.dumps([p.__dict__ if hasattr(p, '__dict__') else p for p in composition.parameters]),
                        json.dumps(composition.verification),
                        composition.confidence,
                        composition.created_at,
                        composition.updated_at,
                        1 if composition.active else 0,
                        composition.version,
                        composition.created_by,
                    ))
                    proc_id = cursor.lastrowid
                    composition.id = proc_id

                conn.commit()
                return proc_id
            finally:
                conn.close()

    def get(self, composition_id: int) -> Optional[ProcedureComposition]:
        """Get a specific composition by ID."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM procedure_compositions WHERE id = ?", (composition_id,))
                row = cursor.fetchone()
                if row:
                    return self._row_to_composition(row)
                return None
            finally:
                conn.close()

    def find_by_name(self, name: str) -> list[ProcedureComposition]:
        """Find compositions by name pattern."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM procedure_compositions WHERE name LIKE ? AND active = 1 ORDER BY confidence DESC", (f"%{name}%",))
                return [self._row_to_composition(row) for row in cursor.fetchall()]
            finally:
                conn.close()

    def find_all_active(self) -> list[ProcedureComposition]:
        """Find all active compositions."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM procedure_compositions WHERE active = 1 ORDER BY confidence DESC")
                return [self._row_to_composition(row) for row in cursor.fetchall()]
            finally:
                conn.close()

    def _row_to_composition(self, row: sqlite3.Row) -> ProcedureComposition:
        return ProcedureComposition(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            sub_procedures=json.loads(row["sub_procedures"] or "[]"),
            execution_order=json.loads(row["execution_order"] or "[]"),
            parameters=[ProcedureParameter(**p) for p in json.loads(row["parameters"] or "[]")],
            verification=json.loads(row["verification"] or "[]"),
            confidence=row["confidence"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            active=bool(row["active"]),
            version=row["version"],
            created_by=row["created_by"],
        )

    def deactivate(self, composition_id: int) -> bool:
        """Deactivate a composition."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("UPDATE procedure_compositions SET active = 0, updated_at = ? WHERE id = ?", (datetime.now().isoformat(), composition_id))
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()


# Global instance
_composition_store: Optional['ProcedureCompositionStore'] = None


def get_composition_store() -> 'ProcedureCompositionStore':
    """Get the global composition store."""
    global _composition_store
    if _composition_store is None:
        _composition_store = ProcedureCompositionStore()
    return _composition_store


class ProcedureCompositionManager:
    """Manages the lifecycle of procedure compositions."""

    def __init__(self, procedure_store: Optional['ProcedureStore'] = None,
                 composition_store: Optional['ProcedureCompositionStore'] = None):
        self.procedure_store = procedure_store or get_procedure_store()
        self.composition_store = composition_store or get_composition_store()

    def create_composition(self,
                          name: str,
                          sub_procedure_ids: list[int],
                          execution_order: list[int],
                          description: str = "",
                          shared_parameters: list[ProcedureParameter] = None,
                          verification: list[dict] = None,
                          created_by: str = "composer") -> ProcedureComposition:
        """Create a new composition from sub-procedures."""
        # Validate sub-procedures exist
        valid_procs = []
        for pid in sub_procedure_ids:
            proc = self.procedure_store.get(pid)
            if proc:
                valid_procs.append(proc)
            else:
                log.warning(f"Sub-procedure {pid} not found, skipping")

        if not valid_procs:
            raise ValueError("No valid sub-procedures provided")

        # Calculate confidence as average of sub-procedure confidences
        confidence = sum(p.confidence for p in valid_procs) / len(valid_procs)

        composition = ProcedureComposition(
            name=name,
            description=description,
            sub_procedures=[p.id for p in valid_procs],
            execution_order=execution_order,
            parameters=shared_parameters or [],
            verification=verification or [],
            confidence=confidence,
            created_by=created_by,
        )

        self.composition_store.save(composition)
        log.info(f"Created composition '{name}' with {len(valid_procs)} sub-procedures")
        return composition

    def execute_composition(self,
                           composition: ProcedureComposition,
                           task_state: TaskState,
                           perception) -> bool:
        """Execute a composition by running sub-procedures in order."""
        # This would be implemented to run sub-procedures sequentially
        # For now, return True to indicate we should try
        log.info(f"Executing composition '{composition.name}'")
        return True

    def suggest_composition(self, goal: str, context: dict) -> Optional[ProcedureComposition]:
        """Suggest a composition based on goal and context."""
        # Find procedures that might compose to achieve the goal
        # This is a placeholder for more sophisticated composition suggestion
        procs = self.procedure_store.find_similar(goal, [], [], min_confidence=0.6)
        if len(procs) >= 2:
            # Create a composition from the top matching procedures
            sub_ids = [p.id for p in procs[:3]]
            exec_order = list(range(len(sub_ids)))
            name = f"compose_{goal.lower().replace(' ', '_')}"
            return self.create_composition(name, sub_ids, exec_order, f"Auto-composed for: {goal}")
        return None


def get_composition_manager(
    procedure_store=None,
    composition_store=None,
) -> 'ProcedureCompositionManager':
    """Factory to create a ProcedureCompositionManager instance."""
    return ProcedureCompositionManager(procedure_store, composition_store)