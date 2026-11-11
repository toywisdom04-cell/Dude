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
    RiskLevel,
    SubGoal,
    TaskState,
    VerificationMethod,
    VerificationResult,
    PerceptionSnapshot,
    PerceptionLevel,
    parse_verification_method,
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

    def record_adaptation_outcome(self, adaptation_id: int,
                                  success: bool) -> None:
        """Fold a real execution outcome back into an adaptation row.

        Success marks it verified; failure only increments the failure
        count (a single failure never deletes the record — evidence
        accumulates instead).
        """
        if not adaptation_id:
            return
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                if success:
                    conn.execute("""
                        UPDATE procedure_adaptations SET
                            status = 'verified',
                            success_count = success_count + 1,
                            verified_at = ?
                        WHERE id = ?
                    """, (datetime.now().isoformat(), adaptation_id))
                else:
                    conn.execute("""
                        UPDATE procedure_adaptations SET
                            failure_count = failure_count + 1
                        WHERE id = ?
                    """, (adaptation_id,))
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
    
    def adapt_plan(self, base_procedure, new_goal, perception=None):
        """Rebind a verified procedure to a new goal plus current screen.

        Pure function (no execution): parameter literals are rebound to the
        new goal's values, step targets are re-checked against the live UI,
        and verification methods are inferred the same way the router does.
        Returns (adapted SubGoal list, modification record). The engine runs
        the result through its normal act/verify loop.
        """
        record = {"base_procedure_id": getattr(base_procedure, "id", None),
                  "trigger": "recovery_replan", "parameter_changes": {},
                  "step_modifications": [], "reason": "",
                  "adapted_orders": []}
        try:
            base_params = list(getattr(base_procedure, "parameters", "") or [])
            try:
                extraction = self.parameter_extractor.extract(
                    new_goal, base_params or None)
                new_params = dict(getattr(extraction, "parameters", "") or {})
            except Exception:
                new_params = {}
            old_defaults = {}
            for p in base_params:
                try:
                    d = getattr(p, "default", None)
                    if isinstance(d, str) and d:
                        old_defaults[getattr(p, "name", "")] = d
                except Exception:
                    continue
            param_changes = {}
            for pname, pval in new_params.items():
                if not isinstance(pval, str) or not pval:
                    continue
                old_v = old_defaults.get(pname, "")
                if pval != old_v:
                    param_changes[pname] = {"from": old_v, "to": pval}
            live_names = []
            try:
                ctrls = getattr(perception, "controls", "") or []
                for c in ctrls:
                    n = str(getattr(c, "name", "") or "").strip().lower()
                    if n:
                        live_names.append(n)
            except Exception:
                live_names = []
            adapted, mods = [], []
            for i, step in enumerate(getattr(base_procedure, "steps", "") or []):
                s = dict(step) if isinstance(step, dict) else {}
                atype = str(s.get("action_type", s.get("type", "execute"))
                            or "execute")
                rebound = []
                for pname, ch in param_changes.items():
                    new_v, old_v = ch["to"], ch["from"]
                    for field in ("target", "expected", "description",
                                  "intent"):
                        cur = s.get(field) or ""
                        if not isinstance(cur, str) or not cur:
                            continue
                        if ("{%s}" % pname) in cur:
                            cur = cur.replace("{%s}" % pname, new_v)
                            rebound.append(pname)
                        elif old_v and len(old_v) >= 3 and old_v in cur:
                            cur = cur.replace(old_v, new_v)
                            rebound.append(pname)
                        s[field] = cur
                target = s.get("target", "")
                tname = ""
                if isinstance(target, str):
                    tname = (target.split("|", 1)[0].strip().lower()
                             if "|" in target else target.strip().lower())
                if live_names and tname and len(tname) >= 3 \
                        and not any(tname in n or n in tname
                                    for n in live_names):
                    mods.append({"order": i,
                                 "change": "target-unmatched-live-ui",
                                 "target": target})
                elif rebound:
                    mods.append({"order": i, "change": "params-rebound",
                                 "params": sorted(set(rebound))})
                vm = parse_verification_method(s.get("verification_method"))
                if vm is None:
                    if atype in ("create_folder", "create_directory",
                                 "make_folder", "write_file", "create_file"):
                        vm = VerificationMethod.FILE_EXISTS
                    elif atype in ("open_app", "open_application"):
                        vm = VerificationMethod.WINDOW_APPEARED
                    elif atype in ("close_app", "close_application"):
                        vm = VerificationMethod.WINDOW_DISAPPEARED
                    else:
                        vm = VerificationMethod.CUSTOM
                adapted.append(SubGoal(
                    description=s.get("description", ""),
                    intent=new_goal,
                    action_type=atype,
                    target_description=s.get("target", ""),
                    expected_result=s.get("expected", ""),
                    verification_method=vm,
                    risk_level=RiskLevel.LOW,
                    order=i,
                ))
            if not adapted:
                return [], record
            record["parameter_changes"] = param_changes
            record["step_modifications"] = mods
            record["trigger"] = (
                "parameter_changed" if param_changes
                else ("ui_changed" if any(
                    m.get("change") == "target-unmatched-live-ui"
                    for m in mods) else "recovery_replan"))
            record["reason"] = (
                "rebind %s to new goal" % ", ".join(sorted(param_changes))
                if param_changes else "replan under current perception")
            record["adapted_orders"] = list(range(len(adapted)))
            return adapted, record
        except Exception as e:
            log.warning(f"adapt_plan failed: {e}")
            return [], {"error": str(e)}

    def execute_adaptation(self, adaptation, task_state, adapted_subgoals) -> bool:
        """Apply adapted subgoals onto live task state.

        The engine executes them through its normal act/verify loop
        afterwards; this only swaps the REMAINING plan (completed work is
        kept), fixes ordering, and tags the record. Returns False when
        there is nothing safe to apply.
        """
        try:
            if task_state is None or not adapted_subgoals:
                return False
            done = int(getattr(task_state, "current_step", 0) or 0)
            kept = list(getattr(task_state, "subgoals", "") or [])[:max(0, done)]
            new_subs = kept + list(adapted_subgoals)
            if not new_subs:
                return False
            for k, sg in enumerate(new_subs):
                try:
                    sg.order = k
                except Exception:
                    pass
            task_state.subgoals = new_subs
            task_state.pending_steps = list(new_subs[max(0, done):])
            try:
                if isinstance(adaptation, dict):
                    adaptation["adapted_orders"] = list(
                        range(max(0, done), len(new_subs)))
                task_state.adaptation_record = adaptation \
                    if isinstance(adaptation, dict) else {}
            except Exception:
                pass
            return True
        except Exception as e:
            log.warning(f"execute_adaptation failed: {e}")
            return False

    def verify_adaptation(self, adaptation, task_state,
                          perception=None) -> bool:
        """Check an applied adaptation against real outcomes.

        True only when every adapted step completed successfully and the
        task carries no failure — read from the task state's own verified
        results, never asserted.
        """
        try:
            if task_state is None:
                return False
            rec = adaptation if isinstance(adaptation, dict) else {}
            orders = rec.get("adapted_orders") or []
            if not orders:
                return False
            done_ok = set()
            for sr in (getattr(task_state, "completed_steps", "") or []):
                try:
                    if not getattr(sr, "success", False):
                        continue
                    sg = getattr(sr, "subgoal", None)
                    if sg is not None:
                        done_ok.add(getattr(sg, "order", -1))
                except Exception:
                    continue
            if not all(o in done_ok for o in orders):
                return False
            if getattr(task_state, "failure_reason", None):
                return False
            vr = getattr(task_state, "verification_result", None)
            if vr is not None and not bool(getattr(vr, "success", True)):
                return False
            return True
        except Exception:
            return False


# Integration helper
def get_adaptation_manager(
    procedure_store=None,
    adaptation_store=None,
) -> 'ProcedureAdaptationManager':
    """Factory to create a ProcedureAdaptationManager instance."""
    return ProcedureAdaptationManager(procedure_store, adaptation_store)