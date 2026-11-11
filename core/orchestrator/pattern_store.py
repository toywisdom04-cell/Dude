"""Pattern Store - Persistent storage for learned observation patterns.

Persists detected patterns and their metadata to survive process restarts.
Only stores compact semantic information - never raw screenshots.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .state import TaskType, VerificationMethod, RiskLevel, ProcedureParameter, SubGoal
from core.config import get_config

log = logging.getLogger(__name__)


def _enum_to_value(obj: Any) -> Any:
    """Convert Enum values to their underlying values for JSON serialization."""
    from enum import Enum
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _enum_to_value(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_enum_to_value(v) for v in obj]
    return obj


@dataclass
class PersistedPattern:
    """A pattern that has been persisted to disk."""
    pattern_key: str
    app: str
    window_title: str
    control_sequence: list[str]
    action_types: list[str]
    frequency: int
    first_seen: float
    last_seen: float
    contexts: list[dict]
    confidence: float
    promoted: bool
    app_sequence: list[str] = field(default_factory=list)  # Multi-app sequence
    workflow_id: Optional[str] = None


class PatternStore:
    """SQLite-backed store for persisted observation patterns."""
    
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            try:
                cfg = get_config()
                data_dir_str = cfg.get("memory.data_dir") or "./dude_data"
            except Exception:
                data_dir_str = "./dude_data"
            data_dir = Path(data_dir_str)
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(data_dir / "observation_patterns.db")
        
        self._db_path = db_path
        self._lock = threading.RLock()
        self._init_db()
    
    def _init_db(self):
        """Initialize the patterns table."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS observation_patterns (
                        pattern_key TEXT PRIMARY KEY,
                        app TEXT NOT NULL,
                        window_title TEXT NOT NULL DEFAULT '',
                        control_sequence TEXT NOT NULL DEFAULT '[]',
                        action_types TEXT NOT NULL DEFAULT '[]',
                        frequency INTEGER NOT NULL DEFAULT 1,
                        first_seen REAL NOT NULL,
                        last_seen REAL NOT NULL,
                        contexts TEXT NOT NULL DEFAULT '[]',
                        confidence REAL NOT NULL DEFAULT 0.0,
                        promoted INTEGER NOT NULL DEFAULT 0,
                        app_sequence TEXT NOT NULL DEFAULT '[]',
                        workflow_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_patterns_app 
                    ON observation_patterns(app)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_patterns_workflow 
                    ON observation_patterns(workflow_id)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_patterns_frequency 
                    ON observation_patterns(frequency DESC)
                """)
                conn.commit()
            finally:
                conn.close()
    
    def save(self, pattern: PersistedPattern) -> None:
        """Save or update a pattern."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                cursor = conn.cursor()
                
                # Check if pattern exists
                cursor.execute(
                    "SELECT pattern_key FROM observation_patterns WHERE pattern_key = ?",
                    (pattern.pattern_key,)
                )
                existing = cursor.fetchone()
                
                now = datetime.now().isoformat()
                if existing:
                    cursor.execute("""
                        UPDATE observation_patterns SET
                            app = ?, window_title = ?, control_sequence = ?,
                            action_types = ?, frequency = ?, last_seen = ?,
                            contexts = ?, confidence = ?, promoted = ?,
                            app_sequence = ?, workflow_id = ?, updated_at = ?
                        WHERE pattern_key = ?
                    """, (
                        pattern.app,
                        pattern.window_title,
                        json.dumps(pattern.control_sequence),
                        json.dumps(pattern.action_types),
                        pattern.frequency,
                        pattern.last_seen,
                        json.dumps(pattern.contexts),
                        pattern.confidence,
                        1 if pattern.promoted else 0,
                        json.dumps(pattern.app_sequence),
                        pattern.workflow_id,
                        now,
                        pattern.pattern_key,
                    ))
                else:
                    cursor.execute("""
                        INSERT INTO observation_patterns (
                            pattern_key, app, window_title, control_sequence,
                            action_types, frequency, first_seen, last_seen,
                            contexts, confidence, promoted,
                            app_sequence, workflow_id, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        pattern.pattern_key,
                        pattern.app,
                        pattern.window_title,
                        json.dumps(pattern.control_sequence),
                        json.dumps(pattern.action_types),
                        pattern.frequency,
                        pattern.first_seen,
                        pattern.last_seen,
                        json.dumps(pattern.contexts),
                        pattern.confidence,
                        1 if pattern.promoted else 0,
                        json.dumps(pattern.app_sequence),
                        pattern.workflow_id,
                        datetime.now().isoformat(),
                        datetime.now().isoformat(),
                    ))
                conn.commit()
            finally:
                conn.close()
    
    def get(self, pattern_key: str) -> Optional[PersistedPattern]:
        """Get a specific pattern by key."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM observation_patterns WHERE pattern_key = ?", (pattern_key,))
                row = cursor.fetchone()
                if row:
                    return self._row_to_pattern(row)
                return None
            finally:
                conn.close()
    
    def find_by_app(self, app: str, min_frequency: int = 1) -> list[PersistedPattern]:
        """Find patterns for a specific application."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM observation_patterns 
                    WHERE app = ? AND frequency >= ?
                    ORDER BY frequency DESC, last_seen DESC
                """, (app, min_frequency))
                return [self._row_to_pattern(row) for row in cursor.fetchall()]
            finally:
                conn.close()
    
    def find_by_workflow(self, workflow_id: str) -> list[PersistedPattern]:
        """Find patterns belonging to a workflow."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM observation_patterns 
                    WHERE workflow_id = ?
                    ORDER BY first_seen ASC
                """, (workflow_id,))
                return [self._row_to_pattern(row) for row in cursor.fetchall()]
            finally:
                conn.close()
    
    def find_all(self, min_frequency: int = 1, limit: int = 100) -> list[PersistedPattern]:
        """Get all patterns, optionally filtered by frequency."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM observation_patterns 
                    WHERE frequency >= ?
                    ORDER BY frequency DESC, last_seen DESC
                    LIMIT ?
                """, (min_frequency, limit))
                return [self._row_to_pattern(row) for row in cursor.fetchall()]
            finally:
                conn.close()
    
    def get_promoted_patterns(self) -> list[PersistedPattern]:
        """Get all promoted patterns."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM observation_patterns 
                    WHERE promoted = 1
                    ORDER BY last_seen DESC
                """)
                return [self._row_to_pattern(row) for row in cursor.fetchall()]
            finally:
                conn.close()
    
    def _row_to_pattern(self, row: sqlite3.Row) -> PersistedPattern:
        return PersistedPattern(
            pattern_key=row["pattern_key"],
            app=row["app"],
            window_title=row["window_title"],
            control_sequence=json.loads(row["control_sequence"] or "[]"),
            action_types=json.loads(row["action_types"] or "[]"),
            frequency=row["frequency"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            contexts=json.loads(row["contexts"] or "[]"),
            confidence=row["confidence"],
            promoted=bool(row["promoted"]),
            app_sequence=json.loads(row["app_sequence"] or "[]"),
            workflow_id=row["workflow_id"],
        )
    
    def mark_promoted(self, pattern_key: str) -> bool:
        """Mark a pattern as promoted."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE observation_patterns SET
                        promoted = 1,
                        updated_at = ?
                    WHERE pattern_key = ?
                """, (datetime.now().isoformat(), pattern_key))
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()
    
    def update_workflow_id(self, pattern_keys: list[str], workflow_id: str) -> None:
        """Assign a workflow ID to multiple patterns."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                cursor = conn.cursor()
                now = datetime.now().isoformat()
                for key in pattern_keys:
                    cursor.execute("""
                        UPDATE observation_patterns SET
                            workflow_id = ?,
                            updated_at = ?
                        WHERE pattern_key = ?
                    """, (workflow_id, now, key))
                conn.commit()
            finally:
                conn.close()
    
    def get_stats(self) -> dict:
        """Get store statistics."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM observation_patterns")
                total = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM observation_patterns WHERE promoted = 1")
                promoted = cursor.fetchone()[0]
                cursor.execute("SELECT SUM(frequency) FROM observation_patterns")
                total_freq = cursor.fetchone()[0] or 0
                cursor.execute("SELECT COUNT(DISTINCT app) FROM observation_patterns")
                unique_apps = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(DISTINCT workflow_id) FROM observation_patterns WHERE workflow_id IS NOT NULL")
                workflows = cursor.fetchone()[0]
                return {
                    "total_patterns": total,
                    "promoted_patterns": promoted,
                    "total_observations": total_freq,
                    "unique_apps": unique_apps,
                    "workflows": workflows,
                }
            finally:
                conn.close()


# Global instance
_store: Optional[PatternStore] = None
_store_db_path: Optional[str] = None


def get_pattern_store(db_path: Optional[str] = None) -> PatternStore:
    """Get the global pattern store."""
    global _store, _store_db_path
    if _store is None or (db_path is not None and db_path != _store_db_path):
        _store = PatternStore(db_path)
        _store_db_path = db_path
    return _store