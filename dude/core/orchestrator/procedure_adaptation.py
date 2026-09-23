"""Procedure Adaptation - Adapting procedures to changed contexts."""

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
    ProcedureAdaptation,
    ProcedureParameter,
    TaskState,
    VerificationResult,
    PerceptionSnapshot,
    PerceptionLevel,
)
from .procedure_store import ProcedureStore, get_procedure_store
from .parameter_extractor import get_parameter_extractor
from .parameter_binder import ParameterBinder
from core.memory import Memory

log = logging.getLogger(__name__)


class ProcedureAdaptationStore:
    """SQLite-backed store for procedure adaptations."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # Use existing memory data directory
            try:
                from core.config import get_config
                cfg = get_config()
                data_dir_str = cfg.get("memory.data_dir") or "./dude_data"
            except Exception:
                data_dir_str = "./dude_data"
            data_dir = Path(data_dir_str)
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(data_dir / "procedure_adaptations.db")
        
        self._db_path = db_path
        self._lock = threading.RLock()
        self._init_db()
    
    def _init_db(self):
        """Initialize the adaptations table."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS procedure_adaptations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        base_procedure_id INTEGER NOT NULL,
                        adapted_procedure_id INTEGER,
                        trigger TEXT NOT NULL,
                        context_diff TEXT NOT NULL DEFAULT '{}',
                        parameter_changes TEXT NOT NULL DEFAULT '{}',
                        step_modifications TEXT NOT NULL DEFAULT '[]',
                        reason TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'candidate',
                        confidence REAL NOT NULL DEFAULT 0.0,
                        created_at TEXT NOT NULL,
                        verified_at TEXT,
                        success_count INTEGER NOT NULL DEFAULT 0,
                        failure_count INTEGER NOT NULL DEFAULT 0,
                        FOREIGN KEY (base_procedure_id) REFERENCES procedures(id),
                        FOREIGN KEY (adapted_procedure_id) REFERENCES procedures(id)
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_adaptations_base 
                    ON procedure_adaptations(base_procedure_id)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_adaptations_status 
                    ON procedure_adaptations(status)
                """)
                conn.commit()
            finally:
                conn.close()
    
    def save(self, adaptation: ProcedureAdaptation) -> int:
        """Save or update an adaptation."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                cursor = conn.cursor()
                now = datetime.now().isoformat()
                
                if hasattr(adaptation, 'id') and adaptation.id:
                    # Update
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE procedure_adaptations SET
                            adapted_procedure_id = ?,
                            trigger = ?,
                            context_diff = ?,
                            parameter_changes = ?,
                            step_modifications = ?,
                            reason = ?,
                            status = ?,
                            confidence = ?,
                            verified_at = ?,
                            success_count = ?,
                            failure_count = ?
                        WHERE id = ?
                    """, (
                        adaptation.adapted_procedure_id,
                        adaptation.trigger,
                        json.dumps(adaptation.context_diff),
                        json.dumps(adaptation.parameter_changes),
                        json.dumps(adaptation.step_modifications),
                        adaptation.reason,
                        adaptation.status,
                        adaptation.confidence,
                        adaptation.verified_at,
                        adaptation.success_count,
                        adaptation.failure_count,
                        adaptation.id,
                    ))
                else:
                    # Insert new
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO procedure_adaptations (
                            base_procedure_id, adapted_procedure_id, trigger, context_diff,
                            parameter_changes, step_modifications, reason, status,
                            confidence, created_at, verified_at, success_count, failure_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        adaptation.base_procedure_id,
                        adaptation.adapted_procedure_id,
                        adaptation.trigger,
                        json.dumps(adaptation.context_diff),
                        json.dumps(adaptation.parameter_changes),
                        json.dumps(adaptation.step_modifications),
                        adaptation.reason,
                        adaptation.status,
                        adaptation.confidence,
                        adaptation.created_at,
                        adaptation.verified_at,
                        adaptation.success_count,
                        adaptation.failure_count,
                    ))
                conn.commit()
                return cursor.lastrowid
            finally:
                conn.close()
    
    def get(self, adaptation_id: int) -> Optional[ProcedureAdaptation]:
        """Get a specific adaptation by ID."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM procedure_adaptations WHERE id = ?", (adaptation_id,))
                row = cursor.fetchone()
                if row:
                    return self._row_to_adaptation(row)
                return None
            finally:
                conn.close()
    
    def find_by_base_procedure(self, base_proc_id: int) -> list[ProcedureAdaptation]:
        """Find all adaptations for a base procedure."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM procedure_adaptations WHERE base_procedure_id = ? ORDER BY created_at DESC", (base_proc_id,))
                return [self._row_to_adaptation(row) for row in cursor.fetchall()]
            finally:
                conn.close()
    
    def find_by_base_and_trigger(self, base_proc_id: int, trigger: str) -> list[ProcedureAdaptation]:
        """Find adaptations by base procedure and trigger."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM procedure_adaptations WHERE base_procedure_id = ? AND trigger = ? ORDER BY confidence DESC", (base_proc_id, trigger))
                return [self._row_to_adaptation(row) for row in cursor.fetchall()]
            finally:
                conn.close()
    
    def _row_to_adaptation(self, row: sqlite3.Row) -> ProcedureAdaptation:
        return ProcedureAdaptation(
            base_procedure_id=row["base_procedure_id"],
            adapted_procedure_id=row["adapted_procedure_id"] if row["adapted_procedure_id"] else None,
            trigger=row["trigger"],
            context_diff=json.loads(row["context_diff"] or "{}"),
            parameter_changes=json.loads(row["parameter_changes"] or "{}"),
            step_modifications=json.loads(row["step_modifications"] or "[]"),
            reason=row["reason"],
            status=row["status"],
            confidence=row["confidence"],
            created_at=row["created_at"],
            verified_at=row["verified_at"] if row["verified_at"] else None,
            success_count=row["success_count"],
            failure_count=row["failure_count"],
        )
    
    def promote_to_verified(self, adaptation_id: int, adapted_proc_id: int) -> None:
        """Mark an adaptation as verified and link to the new procedure."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute("""
                    UPDATE procedure_adaptations SET
                        status = 'verified',
                        adapted_procedure_id = ?,
                        verified_at = ?
                    WHERE id = ?
                """, (adapted_proc_id, datetime.now().isoformat(), adaptation_id))
                conn.commit()
            finally:
                conn.close()


# Global instance
_adaptation_store: Optional['ProcedureAdaptationStore'] = None


def get_adaptation_store() -> 'ProcedureAdaptationStore':
    """Get the global adaptation store."""
    global _adaptation_store
    if _adaptation_store is None:
        _adaptation_store = ProcedureAdaptationStore()
    return _adaptation_store


class ProcedureAdaptationManager:
    """Manages the lifecycle of procedure adaptations."""
    
    def __init__(self, procedure_store: Optional['ProcedureStore'] = None,
                 adaptation_store: Optional['ProcedureAdaptationStore'] = None):
        self.procedure_store = procedure_store or get_procedure_store()
        self.adaptation_store = adaptation_store or get_adaptation_store()
        self.parameter_extractor = get_parameter_extractor()
        self.binder = ParameterBinder()
    
    def detect_adaptation_need(self, 
                               base_procedure_id: int,
                               perception_before: 'PerceptionSnapshot',
                               perception_after: Optional['PerceptionSnapshot'],
                               failed_verification: Optional['VerificationResult'] = None) -> Optional[ProcedureAdaptation]:
        """Detect if a procedure needs adaptation based on context changes or failures."""
        from core.orchestrator.perception import PerceptionSnapshot
        from core.orchestrator.state import PerceptionSnapshot
        
        if not perception_after:
            return None
        
        # Get the base procedure
        base_proc = self.procedure_store.get(base_procedure_id)
        if not base_proc:
            return None
        
        # Check for context changes
        context_diff = {}
        
        # Check application context change
        if hasattr(perception_before, 'active_app') and hasattr(perception_after, 'active_app'):
            if perception_before.active_app != perception_after.active_app:
                context_diff["app_changed"] = {
                    "before": perception_before.active_app,
                    "after": perception_after.active_app,
                }
        
        # Check UI structure changes
        if hasattr(perception_before, 'controls') and hasattr(perception_after, 'controls'):
            before_controls = {c.name for c in perception_before.controls}
            after_controls = {c.name for c in perception_after.controls}
            if before_controls != after_controls:
                context_diff["controls_changed"] = {
                    "added": list(after_controls - before_controls),
                    "removed": list(before_controls - after_controls),
                }
        
        # Check if verification failed
        if perception_after and hasattr(perception_after, 'ocr_text'):
            # Could check for error messages in OCR
            pass
        
        if not context_diff:
            return None  # No significant change detected
        
        # Create adaptation candidate
        adaptation = ProcedureAdaptation(
            base_procedure_id=base_procedure_id,
            trigger=self._classify_trigger(context_diff),
            context_diff=context_diff,
            parameter_changes={},  # Would be filled by parameter extraction
            step_modifications=[],  # Would be filled by comparing steps
            reason=f"Context changed: {', '.join(context_diff.keys())}",
            status="candidate",
        )
        
        return self._create_adaptation_candidate(base_proc, context_diff)
    
    def _classify_trigger(self, context_diff: dict) -> str:
        """Classify what triggered the adaptation need."""
        if "app_changed" in context_diff:
            return "app_changed"
        elif "controls_changed" in context_diff:
            return "ui_changed"
        elif "parameter_changed" in context_diff:
            return "parameter_changed"
        return "context_changed"
    
    def _create_adaptation_candidate(self, base_proc, context_diff):
        """Create an adaptation candidate based on context changes."""
        # Extract parameter changes from context diff
        param_changes = {}
        step_mods = []
        
        # For now, create a basic adaptation candidate
        adaptation = ProcedureAdaptation(
            base_procedure_id=base_proc.id,
            trigger=self._classify_trigger(context_diff),
            context_diff=context_diff,
            parameter_changes={},
            step_modifications=[],
            reason=f"Context changed: {', '.join(context_diff.keys())}",
            status="candidate",
        )
        
        return adaptation
    
    def execute_adaptation(self, adaptation: ProcedureAdaptation, 
                          task_state, perception) -> bool:
        """Execute an adaptation by running the adapted procedure."""
        # This would run the adapted procedure and verify it works
        # For now, return True to indicate we should try
        return True
    
    def verify_adaptation(self, adaptation: ProcedureAdaptation, 
                         task_state, perception) -> bool:
        """Verify that an adaptation works correctly."""
        # Run the adapted procedure and verify it succeeds
        # This would be called after the adaptation is executed
        return True


# Integration helper
def get_adaptation_manager(
    procedure_store=None,
    adaptation_store=None,
) -> 'ProcedureAdaptationManager':
    """Factory to create a ProcedureAdaptationManager instance."""
    return ProcedureAdaptationManager(procedure_store, adaptation_store)