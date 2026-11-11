"""Phase 18 generic Human Semantic Memory + Jobs tests.

Real Memory against a TEMP database (real behavior, no mocks for the
semantic layer), real job_memory, real ContextPrimer. No model, no
network, no applications.
"""
import os
import tempfile
import time
import unittest

import core.config as config_mod
from core.memory import Memory
from core import job_memory as jm
from core import human_memory as hm
from core.capability_bus import CapabilityBus
from core.orchestrator.context_primer import ContextPrimer


def _memory(tempdir):
    """Real Memory bound to a temp DB (the migration path is exercised)."""
    orig = config_mod.get_config

    class _Cfg:
        def db_path(self):
            return os.path.join(tempdir, "memory.db")

        def __getattr__(self, name):
            return getattr(orig(), name, None)

    import core.memory as mem_mod
    real_get = mem_mod.get_config
    mem_mod.get_config = lambda: _Cfg()
    try:
        m = Memory()
    finally:
        mem_mod.get_config = real_get
    return m


class Phase18Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = _memory(self.tmp.name)

    def tearDown(self):
        try:
            self.mem.close()
        except Exception:
            pass
        self.tmp.cleanup()

    # ---- semantic facts ----
    def test_remember_and_recall(self):
        self.assertTrue(hm.remember_about_human(
            self.mem, "prefers concise answers", category="preferences",
            subject="answer_style", confidence=0.8))
        rows = hm.recall_about_human(self.mem, "concise")
        self.assertTrue(rows)
        self.assertEqual(rows[0]["subject"], "answer_style")
        self.assertGreaterEqual(rows[0]["confidence"], 0.79)
        self.assertEqual(rows[0]["source"], "user_stated")

    def test_supersede_keeps_one_current(self):
        hm.remember_about_human(self.mem, "lives in London",
                                category="personal", subject="home_city",
                                confidence=0.7)
        hm.remember_about_human(self.mem, "lives in Berlin",
                                category="personal", subject="home_city",
                                confidence=0.9)
        current = [r for r in hm.recall_about_human(self.mem, "lives")
                   if r["subject"] == "home_city"]
        self.assertEqual(len(current), 1)
        self.assertIn("Berlin", current[0]["fact"])
        hist = hm.fact_history(self.mem, "home_city")
        self.assertGreaterEqual(len(hist), 2)
        self.assertTrue(any("superseded" in h["status"] for h in hist))

    def test_superseded_excluded_by_default(self):
        hm.remember_about_human(self.mem, "works nights",
                                subject="schedule", confidence=0.6)
        hm.remember_about_human(self.mem, "works days",
                                subject="schedule", confidence=0.6)
        default = hm.recall_about_human(self.mem, "works")
        self.assertEqual(len(default), 1)
        with_old = hm.recall_about_human(self.mem, "works",
                                         include_superseded=True)
        self.assertEqual(len(with_old), 2)

    def test_reinforcement_not_duplication(self):
        hm.remember_about_human(self.mem, "uses metric units",
                                subject="units", confidence=0.5)
        hm.remember_about_human(self.mem, "uses metric units",
                                subject="units", confidence=0.5)
        rows = [r for r in hm.recall_about_human(self.mem, "metric")]
        self.assertEqual(len(rows), 1)

    def test_confidence_bounded(self):
        for _ in range(30):
            hm.remember_about_human(self.mem, "likes tea", subject="drink",
                                    confidence=5.0)
        rows = [r for r in hm.recall_about_human(self.mem, "tea")]
        self.assertLessEqual(rows[0]["confidence"], 1.0)

    def test_legacy_remember_fact_still_works(self):
        self.assertTrue(self.mem.remember_fact("legacy fact", "work"))
        self.assertIn("legacy fact", self.mem.recall_facts("legacy"))

    # ---- profile ----
    def test_profile_bounded_and_cached(self):
        # 'personal' is a CATEGORY; the profile section is 'identity'.
        hm.remember_about_human(self.mem, "name is Alex", "personal",
                                subject="name", confidence=0.9)
        for i in range(10):
            hm.remember_about_human(self.mem, f"work fact {i}", "work",
                                    subject=f"f{i}", confidence=0.5)
        jm.upsert_job(self.mem, "reporting", purpose="monthly reports",
                      confidence=0.6)
        prof = hm.build_profile(self.mem)
        # Sections are identity/preferences/work/context; 'personal' is a
        # category that feeds the identity section.
        self.assertIn("identity", prof["sections"])
        self.assertTrue(any(i["category"] == "personal"
                            for i in prof["sections"]["identity"]))
        self.assertLessEqual(len(prof["sections"]["work"]), 4)
        self.assertLessEqual(len(prof["jobs"]), 3)
        cached = hm.cached_profile(self.mem)
        self.assertTrue(cached)
        self.assertTrue(hm.profile_summary(prof))

    # ---- jobs ----
    def test_job_outcome_confidence_curve(self):
        jm.upsert_job(self.mem, "j1", purpose="p")
        c = jm.record_outcome(self.mem, "j1", True)
        self.assertGreater(c["confidence"], 0)
        c2 = jm.record_outcome(self.mem, "j1", False)
        self.assertLess(c2["confidence"], c["confidence"])
        for _ in range(20):
            c2 = jm.record_outcome(self.mem, "j1", True)
        self.assertLessEqual(c2["confidence"], 1.0)

    def test_job_store_survives_oversize(self):
        # The old implementation sliced JSON and silently lost ALL jobs.
        jm.upsert_job(self.mem, "keep_me", purpose="x" * 5000,
                      confidence=0.95)
        for i in range(40):
            jm.upsert_job(self.mem, f"j{i}", purpose="y" * 2000,
                          confidence=float(i) / 40.0)
        jm.upsert_job(self.mem, "also_keep", purpose="z", confidence=1.0)
        raw = self.mem.get_state(jm._STATE_KEY, "")
        import json
        data = json.loads(raw)  # must still be valid JSON
        self.assertIn("also_keep", data)
        self.assertIn("keep_me", data)

    def test_job_fields_bounded(self):
        jm.upsert_job(self.mem, "big", purpose="p" * 5000,
                      procedure=["s" * 1000] * 50)
        card = jm.get_job(self.mem, "big")
        self.assertLessEqual(len(card["purpose"]), 600)
        self.assertLessEqual(len(card["procedure"]), 12)

    # ---- capability + priming ----
    def test_capability_human_memory(self):
        bus = CapabilityBus()
        bus.attach(memory=self.mem)
        r = bus.request("HUMAN_MEMORY", {"op": "remember",
                                         "fact": "likes tea",
                                         "subject": "drink"})
        self.assertTrue(r["success"])
        r2 = bus.request("HUMAN_MEMORY", {"query": "tea"})
        self.assertTrue(r2["success"])
        r3 = bus.request("HUMAN_MEMORY", {"op": "profile"})
        self.assertTrue(r3["success"])

    def test_primer_includes_profile_and_current_facts(self):
        hm.remember_about_human(self.mem, "works in Berlin",
                                subject="home_city", confidence=0.9)
        hm.remember_about_human(self.mem, "lives in Paris",
                                subject="home_city", confidence=0.9)
        jm.upsert_job(self.mem, "reporting", purpose="monthly reports")
        primer = ContextPrimer(memory=self.mem)
        b = primer.prime("where do I work")
        self.assertTrue(b.profile_summary)
        joined = " ".join(str(m.get("fact", "")) for m in b.memory)
        self.assertNotIn("Berlin", joined)  # superseded, not presented
        self.assertTrue(b.sources_ok.get("profile"))

    def test_no_store_creation(self):
        import sqlite3
        rows = self.mem.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        names = {r[0] for r in rows}
        for expected in ("facts", "messages", "session_state"):
            self.assertIn(expected, names)
        # No new semantic-memory table was created.
        self.assertFalse(any("semantic" in n or "human" in n
                             for n in names))


if __name__ == "__main__":
    unittest.main()
