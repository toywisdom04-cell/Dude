"""Procedure Store for DUDE Orchestrator.

Stores verified multi-step procedures for reuse.
Only successful, verified procedures are promoted.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .state import Procedure, ProcedureParameter, TaskType, VerificationMethod, RiskLevel, SubGoal, parse_verification_method


def _enum_to_value(obj: Any) -> Any:
    """Convert Enum values to their underlying values for JSON serialization."""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _enum_to_value(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_enum_to_value(v) for v in obj]
    return obj


@dataclass
class ProcedureCandidate:
    """A candidate procedure awaiting verification."""
    goal: str
    context: dict
    steps: list[dict]
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    source_trace: str = ""  # e.g., "task_<id>"
    verification_results: list[dict] = field(default_factory=list)


class ProcedureStore:
    """SQLite-backed store for verified procedures."""
    
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
            db_path = str(data_dir / "procedures.db")
        
        self._db_path = db_path
        self._lock = threading.RLock()
        self._init_db()
    
    def _init_db(self):
        """Initialize the procedures table."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS procedures (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        goal TEXT NOT NULL,
                        goal_type TEXT NOT NULL,
                        context TEXT NOT NULL DEFAULT '{}',
                        preconditions TEXT NOT NULL DEFAULT '[]',
                        steps TEXT NOT NULL DEFAULT '[]',
                        parameters TEXT NOT NULL DEFAULT '[]',
                        expected_states TEXT NOT NULL DEFAULT '[]',
                        verification TEXT NOT NULL DEFAULT '[]',
                        exceptions TEXT NOT NULL DEFAULT '{}',
                        success_count INTEGER NOT NULL DEFAULT 0,
                        failure_count INTEGER NOT NULL DEFAULT 0,
                        confidence REAL NOT NULL DEFAULT 0.0,
                        source TEXT NOT NULL DEFAULT 'observed',
                        version INTEGER NOT NULL DEFAULT 1,
                        version_history TEXT NOT NULL DEFAULT '[]',
                        change_log TEXT NOT NULL DEFAULT '[]',
                        adaptation_parent_id INTEGER,
                        adaptation_reason TEXT,
                        adaptation_count INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_procedures_goal 
                    ON procedures(goal)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_procedures_confidence 
                    ON procedures(confidence DESC)
                """)
                conn.commit()
            finally:
                conn.close()
    
    def save(self, procedure: Procedure) -> int:
        """Save or update a verified procedure with version history."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                cursor = conn.cursor()
                
                # Check if similar procedure exists (by goal)
                cursor.execute(
                    "SELECT id, version, steps, parameters, verification, confidence FROM procedures WHERE goal = ? ORDER BY confidence DESC LIMIT 1",
                    (procedure.goal,)
                )
                existing = cursor.fetchone()
                
                now = datetime.now().isoformat()
                if existing:
                    proc_id, version, old_steps, old_params, old_verification, old_confidence = existing
                    
                    # Check if there are meaningful changes
                    old_steps_json = json.dumps(json.loads(old_steps or "[]"), sort_keys=True)
                    new_steps_json = json.dumps(procedure.steps, sort_keys=True)
                    old_params_list = json.loads(old_params or "[]")
                    old_params_json = json.dumps([p.__dict__ if hasattr(p, '__dict__') else p for p in old_params_list], sort_keys=True)
                    new_params_json = json.dumps([p.__dict__ if hasattr(p, '__dict__') else p for p in procedure.parameters], sort_keys=True)
                    old_verification_json = json.dumps(json.loads(old_verification or "[]"), sort_keys=True)
                    new_verification_json = json.dumps(procedure.verification, sort_keys=True)
                    
                    # Create change log entry if there are meaningful changes
                    change_log = procedure.change_log.copy()
                    has_changes = False
                    change_desc = []
                    
                    if old_steps_json != new_steps_json:
                        has_changes = True
                        change_log.append(f"v{procedure.version}: Steps modified")
                    if old_params_json != new_params_json:
                        has_changes = True
                        change_log.append(f"v{procedure.version}: Parameters modified")
                    if old_verification_json != new_verification_json:
                        has_changes = True
                        change_log.append(f"v{procedure.version}: Verification updated")
                    
                    # Add version to history before updating
                    if has_changes:
                        history_entry = {
                            "version": procedure.version,
                            "timestamp": procedure.updated_at,
                            "steps": json.loads(old_steps_json),
                            "parameters": json.loads(old_params_json),
                            "verification": json.loads(old_verification_json),
                            "confidence": old_confidence,
                            "changes": change_log
                        }
                        procedure.version_history.append(history_entry)
                        procedure.change_log.append(f"v{procedure.version}: {', '.join(change_log)}")
                    
                    procedure.version += 1
                    procedure.updated_at = datetime.now().isoformat()
                    cursor.execute("""
                        UPDATE procedures SET
                            goal_type = ?, context = ?, preconditions = ?, steps = ?,
                            parameters = ?, expected_states = ?, verification = ?, exceptions = ?,
                            success_count = ?, failure_count = ?, confidence = ?,
                            source = ?, version = ?, updated_at = ?, version_history = ?, change_log = ?,
                            adaptation_parent_id = ?, adaptation_reason = ?, adaptation_count = ?
                        WHERE id = ?
                    """, (
                        procedure.goal_type.value if hasattr(procedure.goal_type, 'value') else procedure.goal_type,
                        json.dumps(_enum_to_value(procedure.context)),
                        json.dumps(_enum_to_value(procedure.preconditions)),
                        json.dumps(_enum_to_value(procedure.steps)),
                        json.dumps(_enum_to_value([p.__dict__ if hasattr(p, '__dict__') else p for p in procedure.parameters])),
                        json.dumps(_enum_to_value(procedure.expected_states)),
                        json.dumps(_enum_to_value(procedure.verification)),
                        json.dumps(_enum_to_value(procedure.exceptions)),
                        procedure.success_count,
                        procedure.failure_count,
                        procedure.confidence,
                        procedure.source,
                        procedure.version,
                        procedure.updated_at,
                        json.dumps(_enum_to_value(procedure.version_history)),
                        json.dumps(_enum_to_value(procedure.change_log)),
                        procedure.adaptation_parent_id,
                        procedure.adaptation_reason,
                        procedure.adaptation_count,
                        proc_id,
                    ))
                else:
                    procedure.created_at = now
                    procedure.updated_at = now
                    # Initialize version history for new procedure
                    procedure.version_history = [{
                        "version": 1,
                        "timestamp": now,
                        "steps": procedure.steps,
                        "parameters": [p.__dict__ if hasattr(p, '__dict__') else p for p in procedure.parameters],
                        "verification": procedure.verification,
                        "confidence": procedure.confidence,
                        "changes": ["Initial version"]
                    }]
                    procedure.change_log = ["v1: Initial version"]
                    
                    cursor.execute("""
                        INSERT INTO procedures (
                            goal, goal_type, context, preconditions, steps,
                            parameters, expected_states, verification, exceptions,
                            success_count, failure_count, confidence,
                            source, version, created_at, updated_at, version_history, change_log,
                            adaptation_parent_id, adaptation_reason, adaptation_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        procedure.goal,
                        procedure.goal_type.value if hasattr(procedure.goal_type, 'value') else procedure.goal_type,
                        json.dumps(_enum_to_value(procedure.context)),
                        json.dumps(_enum_to_value(procedure.preconditions)),
                        json.dumps(_enum_to_value(procedure.steps)),
                        json.dumps(_enum_to_value([p.__dict__ if hasattr(p, '__dict__') else p for p in procedure.parameters])),
                        json.dumps(_enum_to_value(procedure.expected_states)),
                        json.dumps(_enum_to_value(procedure.verification)),
                        json.dumps(_enum_to_value(procedure.exceptions)),
                        procedure.success_count,
                        procedure.failure_count,
                        procedure.confidence,
                        procedure.source,
                        procedure.version,
                        procedure.created_at,
                        procedure.updated_at,
                        json.dumps(_enum_to_value(procedure.version_history)),
                        json.dumps(_enum_to_value(procedure.change_log)),
                        procedure.adaptation_parent_id,
                        procedure.adaptation_reason,
                        procedure.adaptation_count,
                    ))
                    proc_id = cursor.lastrowid
                    procedure.id = proc_id
                
                conn.commit()
                return proc_id
            finally:
                conn.close()
    
    def find_by_goal(self, goal: str, min_confidence: float = 0.5) -> list[Procedure]:
        """Find procedures matching a goal pattern."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM procedures 
                    WHERE goal LIKE ? AND confidence >= ?
                    ORDER BY confidence DESC, success_count DESC
                    LIMIT 10
                """, (f"%{goal}%", min_confidence))
                
                results = []
                for row in cursor.fetchall():
                    results.append(self._row_to_procedure(row))
                return results
            finally:
                conn.close()
    
    def find_by_context(self, context: dict, min_confidence: float = 0.5) -> list[Procedure]:
        """Find procedures matching context (key overlap in context JSON)."""
        # For simplicity, search by goal containing context keys
        # A more sophisticated implementation would use JSON querying
        keys = list(context.keys())
        if not keys:
            return []
        
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # Build query for context matching
                placeholders = " OR ".join(["context LIKE ?"] * len(keys))
                query = f"""
                    SELECT * FROM procedures 
                    WHERE ({placeholders}) AND confidence >= ?
                    ORDER BY confidence DESC, success_count DESC
                    LIMIT 10
                """
                params = [f"%{k}%" for k in keys] + [min_confidence]
                
                cursor.execute(query, params)
                
                results = []
                for row in cursor.fetchall():
                    results.append(self._row_to_procedure(row))
                return results
            finally:
                conn.close()
    
    def find_by_type_and_params(self, action_type: str, param_names: list[str], 
                                 min_confidence: float = 0.5) -> list[Procedure]:
        """Find procedures by action type and parameter names."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # Search for procedures with matching action type and parameter names
                placeholders = " OR ".join(["steps LIKE ?"] * len(param_names))
                query = f"""
                    SELECT * FROM procedures 
                    WHERE ({placeholders}) AND confidence >= ?
                    ORDER BY confidence DESC, success_count DESC
                    LIMIT 10
                """
                params = [f"%{p}%" for p in param_names] + [0.5]
                
                cursor.execute(query, params)
                
                results = []
                for row in cursor.fetchall():
                    results.append(self._row_to_procedure(row))
                return results
            finally:
                conn.close()
    
    def find_similar(self, goal: str, action_types: list[str], 
                     param_names: list[str], min_confidence: float = 0.5) -> list[Procedure]:
        """Find procedures similar to a goal using multiple criteria."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # Build a query that matches goal, action types, and parameter names
                conditions = []
                params = []
                
                # Goal similarity
                conditions = ["goal LIKE ?"]
                params = [f"%{goal}%"]
                
                # Action type matching
                if action_types:
                    at_placeholders = " OR ".join(["steps LIKE ?"] * len(action_types))
                    conditions.append(f"({at_placeholders})")
                    params.extend([f"%{at}%" for at in action_types])
                
                # Parameter name matching
                if param_names:
                    pn_placeholders = " OR ".join(["parameters LIKE ?"] * len(param_names))
                    conditions.append(f"({pn_placeholders})")
                    params.extend([f"%{pn}%" for pn in param_names])
                
                conditions.append("confidence >= ?")
                params.append(min_confidence)
                
                query = f"""
                    SELECT * FROM procedures 
                    WHERE {" AND ".join(conditions)}
                    ORDER BY confidence DESC, success_count DESC
                    LIMIT 10
                """
                
                cursor.execute(query, params)
                
                results = []
                for row in cursor.fetchall():
                    results.append(self._row_to_procedure(row))
                return results
            finally:
                conn.close()
    
    def find_by_goal(self, goal: str, min_confidence: float = 0.5) -> list[Procedure]:
        """Find procedures matching a goal pattern."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM procedures 
                    WHERE goal LIKE ? AND confidence >= ?
                    ORDER BY confidence DESC, success_count DESC
                    LIMIT 10
                """, (f"%{goal}%", min_confidence))
                
                results = []
                for row in cursor.fetchall():
                    results.append(self._row_to_procedure(row))
                return results
            finally:
                conn.close()
    
    def find_by_normalized_goal(self, goal: str, min_confidence: float = 0.5) -> list[Procedure]:
        """Find procedures by normalizing the query goal and matching against stored normalized goals.
        
        This allows matching "create folder Projects" against a stored procedure with goal "create folder {folder_name}".
        """
        normalized_query = self._normalize_goal(goal, [])
        return self.find_by_goal(normalized_query, min_confidence)
    
    def find_by_context(self, context: dict, min_confidence: float = 0.5) -> list[Procedure]:
        """Find procedures matching context (simple key overlap)."""
        # For simplicity, just search by goal containing context keys
        # A more sophisticated implementation would use JSON querying
        keys = list(context.keys())
        if not keys:
            return []
        
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # Build query for context matching
                placeholders = " OR ".join(["context LIKE ?"] * len(keys))
                query = f"""
                    SELECT * FROM procedures 
                    WHERE ({placeholders}) AND confidence >= ?
                    ORDER BY confidence DESC, success_count DESC
                    LIMIT 10
                """
                params = [f"%{k}%" for k in keys] + [min_confidence]
                
                cursor.execute(query, params)
                
                results = []
                for row in cursor.fetchall():
                    results.append(self._row_to_procedure(row))
                return results
            finally:
                conn.close()
    
    def find_by_type_and_params(self, action_type: str, param_names: list[str], 
                                 min_confidence: float = 0.5) -> list[Procedure]:
        """Find procedures by action type and parameter names."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # Search for procedures with matching action type and parameter names
                placeholders = " OR ".join(["steps LIKE ?"] * len(param_names))
                query = f"""
                    SELECT * FROM procedures 
                    WHERE ({placeholders}) AND confidence >= ?
                    ORDER BY confidence DESC, success_count DESC
                    LIMIT 10
                """
                params = [f"%{p}%" for p in param_names] + [0.5]
                
                cursor.execute(query, params)
                
                results = []
                for row in cursor.fetchall():
                    results.append(self._row_to_procedure(row))
                return results
            finally:
                conn.close()
    
    def get(self, proc_id: int) -> Optional[Procedure]:
        """Get a specific procedure by ID."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM procedures WHERE id = ?", (proc_id,))
                row = cursor.fetchone()
                if row:
                    return self._row_to_procedure(row)
                return None
            finally:
                conn.close()
    
    def _row_to_procedure(self, row: sqlite3.Row) -> Procedure:
        """Convert database row to Procedure object."""
        # Load parameters
        params_data = json.loads(row["parameters"] or "[]")
        parameters = []
        for p in params_data:
            if isinstance(p, dict):
                parameters.append(ProcedureParameter(**p))
        
        # Load steps and convert verification_method strings back to enums
        steps = json.loads(row["steps"] or "[]")
        for step in steps:
            if isinstance(step.get("verification_method"), str):
                step["verification_method"] = parse_verification_method(
                    step["verification_method"])

        # Also convert top-level verification list
        verification = json.loads(row["verification"] or "[]")
        verification = [parse_verification_method(v) if isinstance(v, str) else v
                        for v in verification]

        return Procedure(
            id=row["id"],
            goal=row["goal"],
            goal_type=TaskType(row["goal_type"]),
            context=json.loads(row["context"] or "{}"),
            preconditions=json.loads(row["preconditions"] or "[]"),
            steps=steps,
            parameters=parameters,
            expected_states=json.loads(row["expected_states"] or "[]"),
            verification=verification,
            exceptions=json.loads(row["exceptions"] or "{}"),
            success_count=row["success_count"],
            failure_count=row["failure_count"],
            confidence=row["confidence"],
            source=row["source"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            version_history=json.loads(row["version_history"] or "[]"),
            change_log=json.loads(row["change_log"] or "[]"),
        )
    
    def record_success(self, proc_id: int) -> None:
        """Record a successful execution."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute("""
                    UPDATE procedures SET
                        success_count = success_count + 1,
                        confidence = MIN(1.0, confidence + 0.05),
                        updated_at = ?
                    WHERE id = ?
                """, (datetime.now().isoformat(), proc_id))
                conn.commit()
            finally:
                conn.close()
    
    def record_failure(self, proc_id: int) -> None:
        """Record a failed execution."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute("""
                    UPDATE procedures SET
                        failure_count = failure_count + 1,
                        confidence = MAX(0.0, confidence - 0.1),
                        updated_at = ?
                    WHERE id = ?
                """, (datetime.now().isoformat(), proc_id))
                conn.commit()
            finally:
                conn.close()
    
    def promote_candidate(self, candidate: ProcedureCandidate) -> Procedure:
        """Promote a verified candidate to a full procedure."""
        # Convert steps to SubGoal format, preserving each step's
        # verified check: a retrieved procedure that verifies everything
        # as CUSTOM could never prove its own replay.
        subgoals = []
        for i, step in enumerate(candidate.steps):
            subgoals.append(SubGoal(
                description=step.get("description", ""),
                intent=step.get("intent", ""),
                action_type=step.get("action_type", "execute"),
                target_description=step.get("target", ""),
                expected_result=step.get("expected", ""),
                verification_method=parse_verification_method(
                    step.get("verification_method")),
                risk_level=RiskLevel.LOW,
                order=i,
            ))
        
        # Extract parameters from steps
        parameters = []
        param_names = set()
        for step in candidate.steps:
            if "parameters" in step and isinstance(step["parameters"], dict):
                for param_name, param_info in step["parameters"].items():
                    if param_name not in param_names:
                        param_names.add(param_name)
                        if isinstance(param_info, dict):
                            parameters.append(ProcedureParameter(
                                name=param_info.get("name", param_info.get("param_name", param_name)),
                                type=param_info.get("type", "string"),
                                required=param_info.get("required", True),
                                description=param_info.get("description", ""),
                                default=param_info.get("default"),
                                constraints=param_info.get("constraints", {}),
                            ))
                        else:
                            # param_info is the value, use param_name (the key) as the parameter name
                            parameters.append(ProcedureParameter(
                                name=param_name,
                                type="string",
                                required=True,
                                default=param_info,
                            ))
        
        # Convert steps to have both 'type' and 'action_type' for compatibility
        compat_steps = []
        for step in candidate.steps:
            compat_step = step.copy()
            if "action_type" in step and "type" not in step:
                compat_step["type"] = step["action_type"]
            elif "type" in step and "action_type" not in step:
                compat_step["action_type"] = step["type"]
            compat_steps.append(compat_step)
        
        # Normalize the goal by replacing parameter values with placeholders
        normalized_goal = self._normalize_goal(candidate.goal, parameters)
        
        procedure = Procedure(
            goal=normalized_goal,
            goal_type=TaskType.UNKNOWN.value,
            context=candidate.context,
            steps=compat_steps,
            parameters=parameters,
            verification=candidate.verification_results,
            confidence=0.7,  # Initial confidence for promoted procedure
            source="verified_trace",
        )
        
        self.save(procedure)
        return procedure
    
    def _normalize_goal(self, goal: str, parameters: list[ProcedureParameter]) -> str:
        """Normalize a goal by replacing recognized parameter values with placeholders."""
        import re
        normalized = goal
        # Replace extracted parameter values with placeholders
        for param in parameters:
            if param.default is not None and isinstance(param.default, str) and param.default in normalized:
                normalized = normalized.replace(param.default, f"{{{param.name}}}")
        # Also try to extract and replace using the parameter extractor
        try:
            from .parameter_extractor import get_parameter_extractor
            extractor = get_parameter_extractor()
            extraction = extractor.extract(goal, None)
            for param_name, value in extraction.parameters.items():
                if isinstance(value, str) and value in normalized:
                    normalized = normalized.replace(value, f"{{{param_name}}}")
        except Exception:
            pass
        return normalized
    
    def get_stats(self) -> dict:
        """Get store statistics."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("SELECT COUNT(*) FROM procedures")
                total = cursor.fetchone()[0]
                cursor.execute("SELECT AVG(confidence) FROM procedures")
                avg_conf = cursor.fetchone()[0] or 0.0
                cursor.execute("SELECT SUM(success_count) FROM procedures")
                total_success = cursor.fetchone()[0] or 0
                cursor.execute("SELECT SUM(failure_count) FROM procedures")
                total_fail = cursor.fetchone()[0] or 0
                return {
                    "total_procedures": total,
                    "avg_confidence": avg_conf,
                    "total_successes": total_success,
                    "total_failures": total_fail,
                }
            finally:
                conn.close()

    def find_similar(self, goal: str, action_types: list[str], 
                     param_names: list[str], min_confidence: float = 0.5) -> list[Procedure]:
        """Find procedures similar to a goal using multiple criteria with weighted scoring."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # Build query with optional filters
                conditions = ["confidence >= ?"]
                params = [min_confidence]
                
                # Goal similarity - use LIKE for partial matching
                conditions.append("goal LIKE ?")
                params.append(f"%{goal}%")
                
                # Action type matching - filter at database level if provided
                if action_types:
                    # Build OR conditions for each action type
                    action_conditions = []
                    for at in action_types:
                        action_conditions.append("steps LIKE ?")
                        params.append(f"%\"type\": \"{at}\"%")
                    if action_conditions:
                        conditions.append(f"({' OR '.join(action_conditions)})")
                
                # Parameter name matching - filter at database level if provided
                if param_names:
                    param_conditions = []
                    for pn in param_names:
                        param_conditions.append("parameters LIKE ?")
                        params.append(f"%\"name\": \"{pn}\"%")
                    if param_conditions:
                        conditions.append(f"({' OR '.join(param_conditions)})")
                
                query = f"""
                    SELECT * FROM procedures 
                    WHERE {" AND ".join(conditions)}
                    ORDER BY confidence DESC, success_count DESC
                    LIMIT 50
                """
                
                cursor.execute(query, params)
                
                candidates = [self._row_to_procedure(row) for row in cursor.fetchall()]
                
                if not candidates:
                    return []
                
                # Score each candidate for ranking
                scored = []
                goal_lower = goal.lower()
                goal_words = set(goal_lower.split())
                
                for proc in candidates:
                    score = 0.0
                    
                    # 1. Goal similarity (0-30 points)
                    proc_goal_lower = proc.goal.lower()
                    proc_goal_words = set(proc_goal_lower.split())
                    
                    # Word overlap
                    word_overlap = len(goal_words & proc_goal_words)
                    word_total = len(goal_words | proc_goal_words)
                    if word_total > 0:
                        score += 30.0 * (word_overlap / word_total)
                    
                    # Substring match bonus
                    if goal_lower in proc_goal_lower or proc_goal_lower in goal_lower:
                        score += 15.0
                    
                    # 2. Action type overlap (0-20 points) - additional scoring beyond filter
                    if action_types:
                        proc_actions = set()
                        for step in proc.steps:
                            if isinstance(step, dict) and 'type' in step:
                                proc_actions.add(step['type'])
                        action_overlap = len(set(action_types) & proc_actions)
                        if action_types:
                            score += 20.0 * (action_overlap / len(action_types))
                    
                    # 3. Parameter name overlap (0-15 points) - additional scoring beyond filter
                    if param_names:
                        proc_params = {p.name for p in proc.parameters if hasattr(p, 'name')}
                        param_overlap = len(set(param_names) & proc_params)
                        if param_names:
                            score += 15.0 * (param_overlap / len(param_names))
                    
                    # 4. Confidence weight (0-20 points)
                    score += 20.0 * proc.confidence
                    
                    # 5. Success rate bonus (0-10 points)
                    total_exec = proc.success_count + proc.failure_count
                    if total_exec > 0:
                        success_rate = proc.success_count / total_exec
                        score += 10.0 * success_rate
                    
                    # 6. Recency bonus (0-5 points)
                    score += 5.0
                    
                    scored.append((score, proc))
                
                # Sort by score descending and return top 10
                scored.sort(key=lambda x: x[0], reverse=True)
                return [proc for score, proc in scored[:10]]
            finally:
                conn.close()
    
    def get_versions(self, proc_id: int) -> list[dict]:
        """Get version history for a procedure."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT version_history FROM procedures WHERE id = ?", (proc_id,))
                row = cursor.fetchone()
                if row:
                    return json.loads(row["version_history"] or "[]")
                return []
            finally:
                conn.close()
    
    def get_version(self, proc_id: int, version: int) -> Optional[Procedure]:
        """Get a specific version of a procedure."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT version_history FROM procedures WHERE id = ?", (proc_id,))
                row = cursor.fetchone()
                if row and row["version_history"]:
                    history = json.loads(row["version_history"])
                    for entry in history:
                        if entry.get("version") == version:
                            # Reconstruct procedure from version history
                            proc = self.get(proc_id)
                            if proc:
                                proc.version = entry["version"]
                                proc.steps = entry["steps"]
                                proc.parameters = [ProcedureParameter(**p) for p in entry["parameters"]]
                                proc.verification = entry["verification"]
                                proc.confidence = entry["confidence"]
                                proc.updated_at = entry["timestamp"]
                                return proc
                return None
            finally:
                conn.close()
    
    def record_execution(self, proc_id: int, success: bool, 
                         verification_result: Optional[dict] = None) -> None:
        """Record an execution result for a procedure."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                cursor = conn.cursor()
                if success:
                    cursor.execute("""
                        UPDATE procedures SET
                            success_count = success_count + 1,
                            confidence = MIN(1.0, confidence + 0.05),
                            updated_at = ?
                        WHERE id = ?
                    """, (datetime.now().isoformat(), proc_id))
                else:
                    cursor.execute("""
                        UPDATE procedures SET
                            failure_count = failure_count + 1,
                            confidence = MAX(0.0, confidence - 0.1),
                            updated_at = ?
                        WHERE id = ?
                    """, (datetime.now().isoformat(), proc_id))
                conn.commit()
            finally:
                conn.close()

# Global instance
_store: Optional[ProcedureStore] = None
_store_db_path: Optional[str] = None


def get_procedure_store(db_path: Optional[str] = None) -> ProcedureStore:
    """Get the global procedure store."""
    global _store, _store_db_path
    if _store is None or (db_path is not None and db_path != _store_db_path):
        _store = ProcedureStore(db_path)
        _store_db_path = db_path
    return _store


def create_procedure_from_trace(
    goal: str,
    context: dict,
    steps: list[dict],
    verification_results: list[dict],
    goal_type: str = "UNKNOWN",
) -> Procedure:
    """Factory to create a Procedure from a successful task trace."""
    from .state import TaskType
    
    try:
        gt = TaskType(goal_type.lower())
    except ValueError:
        gt = TaskType.UNKNOWN
    
    candidate = ProcedureCandidate(
        goal=goal,
        context=context,
        steps=steps,
        verification_results=verification_results,
    )
    
    store = get_procedure_store()
    return store.promote_candidate(candidate)