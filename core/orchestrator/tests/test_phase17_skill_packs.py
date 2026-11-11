"""Phase 17 generic Skill Pack + Context Priming tests.

No model, no network, no camera, no applications. Packs are written to
temp dirs by the test; sources are fakes or None. Nothing names a
specific application.
"""
import json
import os
import tempfile
import time
import unittest

from core.orchestrator.skill_packs import (
    SkillPack, SkillPackRegistry, rank_packs)
from core.orchestrator.context_primer import (
    ContextBundle, ContextPrimer)


def _pack(pid="sp.test", name="Test Pack", domain="knowledge-work",
          purpose="Produce checked reports from source material.",
          summary="Gather sources, follow structure, verify totals.",
          knowledge=("inspect the source data and identify the period "
                     "preserve template structure calculate metrics"),
          caps=("read_file", "write_file"), enabled=True,
          version="1.0", applicable=("preparing a monthly report",)):
    return {
        "id": pid, "name": name, "version": version,
        "domain": domain, "purpose": purpose, "summary": summary,
        "applicable_context": list(applicable),
        "knowledge": [knowledge] if isinstance(knowledge, str)
        else list(knowledge),
        "prerequisites": ["source data exists"],
        "useful_capabilities": list(caps),
        "verification_guidance": ["verify totals against source"],
        "recovery_guidance": ["re-open the file to confirm the write"],
        "provenance": "test", "enabled": enabled,
    }


def _write(d, name, data):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(data, f)
    return p


class Phase17Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    # A/B/F: schema + provenance/version
    def test_a_schema(self):
        p = SkillPack.from_dict(_pack())
        self.assertIsNotNone(p)
        for f in ("id", "name", "version", "purpose", "domain", "summary",
                  "applicable_context", "knowledge", "prerequisites",
                  "useful_capabilities", "verification_guidance",
                  "recovery_guidance", "provenance", "enabled"):
            self.assertTrue(hasattr(p, f), f)
        self.assertTrue(p.enabled)
        self.assertEqual(p.version, "1.0")
        # Bounded: oversized fields clip.
        big = _pack(summary="x" * 9000, knowledge=["y" * 9000] * 40)
        pb = SkillPack.from_dict(big)
        self.assertLessEqual(len(pb.summary), 800)
        self.assertLessEqual(len(pb.knowledge), 24)

    def test_b_discovery(self):
        _write(self.dir, "a.json", _pack("sp.alpha"))
        _write(self.dir, "b.json", _pack("sp.beta"))
        reg = SkillPackRegistry(self.dir)
        self.assertEqual(reg.load_dir(), 2)
        self.assertEqual(reg.count(), 2)
        self.assertIsNotNone(reg.get("sp.alpha"))
        self.assertIn("sp.alpha", reg.describe())

    def test_c_duplicate_ids(self):
        _write(self.dir, "a_first.json", _pack("sp.dup", name="First"))
        _write(self.dir, "b_second.json", _pack("sp.dup", name="Second"))
        reg = SkillPackRegistry(self.dir)
        self.assertEqual(reg.load_dir(), 1)
        self.assertEqual(reg.get("sp.dup").name, "First")  # sorted first
        self.assertTrue(any("duplicate" in e for e in reg.load_errors))

    def test_d_disabled_ignored(self):
        _write(self.dir, "on.json", _pack("sp.on"))
        _write(self.dir, "off.json", _pack("sp.off", enabled=False))
        reg = SkillPackRegistry(self.dir)
        reg.load_dir()
        self.assertIsNotNone(reg.get("sp.on"))
        self.assertIsNone(reg.get("sp.off"))

    def test_e_malformed(self):
        _write(self.dir, "broken.json", "{not json")
        _write(self.dir, "noid.json", {"name": "No ID"})
        _write(self.dir, "notdict.json", ["a", "b"])
        reg = SkillPackRegistry(self.dir)
        reg.load_dir()
        self.assertEqual(reg.count(), 0)
        self.assertGreaterEqual(len(reg.load_errors), 3)

    def test_f_version_provenance(self):
        _write(self.dir, "p.json", _pack("sp.v", version="2.5"))
        reg = SkillPackRegistry(self.dir)
        reg.load_dir()
        self.assertEqual(reg.get("sp.v").version, "2.5")
        self.assertIn("local_file:", reg.get("sp.v").provenance)

    # G/H: selection
    def test_g_relevant_selection(self):
        packs = [SkillPack.from_dict(_pack(
            "sp.reporting",
            purpose="Turn source business data into a verified report.",
            knowledge=("inspect the source data identify the reporting "
                       "period preserve the template structure calculate "
                       "the required metrics verify the totals")))]
        ranked = rank_packs(
            "prepare the monthly report from the source data and verify "
            "the totals", packs, {"app": "editor"}, limit=2)
        self.assertTrue(ranked)
        self.assertEqual(ranked[0][1].id, "sp.reporting")
        self.assertIn("overlap", ranked[0][2])

    def test_h_irrelevant_rejected(self):
        packs = [SkillPack.from_dict(_pack(
            "sp.reporting",
            knowledge=("inspect source data reporting period template "
                       "metrics totals")))]
        ranked = rank_packs("play some music quietly", packs, {}, 2)
        self.assertEqual(ranked, [])
        self.assertEqual(rank_packs("", packs, {}, 2), [])

    def test_ranking_does_not_route(self):
        # A pack matching by text still yields advisory context only:
        # rank_packs returns tuples, no plan/action/skill objects.
        packs = [SkillPack.from_dict(_pack("sp.x"))]
        out = rank_packs("metrics totals report", packs, {}, 1)
        self.assertTrue(out)
        score, pack_obj, reason = out[0]
        self.assertIsInstance(score, float)
        self.assertIsInstance(pack_obj, SkillPack)
        self.assertIsInstance(reason, str)
        for attr in ("plan", "action", "executor", "steps"):
            self.assertFalse(hasattr(pack_obj, attr), attr)

    # I-L: ContextBundle
    def _primer(self, **kw):
        return ContextPrimer(**kw)

    def test_i_bundle_construction(self):
        b = self._primer().prime("some goal")
        self.assertIsInstance(b, ContextBundle)
        self.assertEqual(b.goal, "some goal")
        self.assertGreater(b.captured_at, 0)
        self.assertIn("Context primed", b.describe())

    def test_j_bounded_retrieval(self):
        class FakeMem:
            def recall_facts(self, q, limit=8):
                return [f"fact {i}" for i in range(50)]

            def get_state(self, k, d=None):
                return d

            def set_state(self, k, v):
                pass
        b = self._primer(memory=FakeMem()).prime("goal")
        self.assertLessEqual(len(b.memory), 5)

    def test_k_visual_freshness(self):
        class VBus:
            stale_after_seconds = 5.0

            def __init__(self, fresh):
                self._fresh = fresh

            def get_fresh(self):
                return self._state if self._fresh else None

            def get(self):
                return self._state

            class _S:
                active_app = "app"
                window_title = "win"
                focused_name = "f"
                source = "src"

                def __init__(self, age):
                    self._age = age

                def age_seconds(self):
                    return self._age

                def is_stale(self, ttl):
                    return self._age > ttl
        fresh = VBus(True)
        fresh._state = VBus._S(0.1)
        b = self._primer(visual_bus=fresh).prime("g")
        self.assertTrue(b.visual_fresh)
        self.assertIn("app", b.visual_summary)
        stale = VBus(False)
        stale._state = VBus._S(99.0)
        b2 = self._primer(visual_bus=stale).prime("g")
        self.assertFalse(b2.visual_fresh)
        self.assertEqual(b2.visual_summary, "")

    def test_l_stale_context_rejected(self):
        class VBus:
            stale_after_seconds = 1.0

            def get_fresh(self):
                return None

            def get(self):
                class S:
                    active_app = "a"
                    window_title = "w"
                    focused_name = ""
                    source = "s"

                    def age_seconds(self):
                        return 50

                    def is_stale(self, ttl):
                        return True
                return S()
        b = self._primer(visual_bus=VBus()).prime("g")
        self.assertFalse(b.visual_fresh)
        self.assertEqual(b.visual_summary, "")

    # M/N: concept separation
    def test_m_procedure_skill_distinct(self):
        class P:
            id = 7
            goal = "make report"
            confidence = 0.8
            success_count = 3
            source = "verified_trace"

        class Store:
            def find_by_goal(self, g, c=0.5):
                return [P()]

            def recent_failures(self, g, limit=2):
                return []
        b = self._primer(procedure_store=Store()).prime("make report")
        self.assertEqual(len(b.procedures), 1)
        self.assertEqual(b.procedures[0]["source"], "verified_trace")
        self.assertEqual(b.skill_packs, [])  # no packs configured

    def test_n_job_skill_distinct(self):
        import core.job_memory as jm
        from core.memory import Memory
        mem = Memory()
        jm.upsert_job(mem, "monthly reporting", purpose="reports")
        b = self._primer(memory=mem).prime("monthly reporting")
        self.assertTrue(any(j["name"] == "monthly reporting"
                            for j in b.jobs))
        self.assertEqual(b.skill_packs, [])

    # O/P/S
    def test_o_capability_integration(self):
        class Bus:
            perception = object()
            action_executor = None
            verification = object()
            recovery = None
            memory = object()
            visual_state_bus = None
            spatial_workspace = None
            hand_tracker = None
        b = self._primer(capability_bus=Bus()).prime("g")
        self.assertIn("perception", b.capabilities)
        self.assertNotIn("hand_tracker", b.capabilities)

    def test_p_no_skill_fallback(self):
        reg = SkillPackRegistry(os.path.join(self.dir, "empty"))
        reg.load_dir()
        b = self._primer(pack_registry=reg).prime("anything")
        self.assertEqual(b.skill_packs, [])
        self.assertTrue(b.sources_ok.get("packs"))

    def test_failure_paths_continue(self):
        # Every source broken/unavailable -> bundle still returned.
        class Boom:
            def __getattr__(self, n):
                raise RuntimeError("boom")
        b = self._primer(memory=Boom(), procedure_store=Boom(),
                         capability_bus=Boom(),
                         visual_bus=Boom(),
                         pack_registry=Boom()).prime("g")
        self.assertIsInstance(b, ContextBundle)
        self.assertEqual(b.goal, "g")
        # Empty goal context is safe.
        b2 = self._primer().prime("")
        self.assertEqual(b2.goal, "")

    # Q: architecture invariant (no second router in the primer)
    def test_q_no_second_router(self):
        p = ContextPrimer()
        for attr in ("route", "classify", "decide", "translate",
                     "brain", "agent"):
            self.assertFalse(hasattr(p, attr), attr)
        src = open(
            r"E:\Dude\dude\core\orchestrator\context_primer.py",
            encoding="utf-8").read()
        self.assertNotIn("IntelligenceRouter", src)
        self.assertNotIn("TaskEngine", src)

    def test_s_no_action_from_skill(self):
        src = open(r"E:\Dude\dude\core\orchestrator\skill_packs.py",
                   encoding="utf-8").read()
        for bad in ("exec(", "eval(", "__import__", "subprocess",
                    "os.system", "urllib", "requests"):
            self.assertNotIn(bad, src, bad)
        p = SkillPack.from_dict(_pack())
        self.assertFalse(hasattr(p, "executor"))
        self.assertFalse(hasattr(p, "run"))


if __name__ == "__main__":
    unittest.main()
