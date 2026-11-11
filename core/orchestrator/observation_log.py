"""Observation Log - Bounded TTL working memory for structured observations.

Provides a deduplicated, time-limited log of SemanticObservations.
Used by PerceptionService and ObservationLearner.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, List, Optional

from .perception_service import SemanticObservation


@dataclass
class ObservationLog:
    """Bounded TTL working memory of structured observations.

    Facts ABOUT the screen, never pixels. Identical consecutive keys are
    deduplicated (no repeat writes); entries older than ttl_seconds are
    purged on push/sweep. Promotion to long-term Memory stays explicit
    via remember_observation().
    """

    def __init__(self, max_entries: int = 64, ttl_seconds: float = 300.0):
        self._entries: Deque[tuple] = deque(maxlen=max_entries)
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self.deduped = 0
        self.expired = 0

    def push(self, obs: SemanticObservation) -> bool:
        """Store unless identical to the newest entry. Returns stored?"""
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            if self._entries and self._entries[-1][1].key() == obs.key():
                self.deduped += 1
                return False
            self._entries.append((now, obs))
            return True

    def sweep(self) -> int:
        with self._lock:
            return self._purge_locked(time.time())

    def _purge_locked(self, now: float) -> int:
        before = len(self._entries)
        self._entries = deque(
            (e for e in self._entries if now - e[0] <= self._ttl),
            maxlen=self._entries.maxlen)
        n = before - len(self._entries)
        self.expired += n
        return n

    def recent(self, n: int = 10) -> List[SemanticObservation]:
        self.sweep()
        with self._lock:
            return [e[1] for e in list(self._entries)[-n:]]

    def __len__(self) -> int:
        self.sweep()
        with self._lock:
            return len(self._entries)