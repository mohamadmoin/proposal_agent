"""Agentic Proposal engine, AGT-1/AGT-2 (docs/platform/proposal/AGENTIC_PLAN.md): the model factory and the agent's
working memory. Pure unit tests: no database, no network, no model request.

    docker exec proposal-agentic-dev python -m unittest tests.agentic.test_core
"""
import json
import logging
import unittest

from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded
from pydantic_ai.models.fallback import FallbackModel

from engine.agentic.models import ModelConfigError, build_models, google_key, should_fall_back
from engine.agentic.state import DIGEST_MAX_CHARS, ResearchState
from engine.schemas import PaperRecord
from engine.store import MemoryStore


class ModelFactoryTests(unittest.TestCase):
    def test_builds_gemini_models_without_fallback(self):
        m = build_models({"GOOGLE_API_KEY": "dummy"})
        self.assertEqual(m.names, {"lead": "gemini-3.8-flash", "worker": "gemini-3.1-flash-lite",
                                   "lead_thinking": "medium", "worker_thinking": "low"})
        self.assertEqual(m.lead.settings["google_thinking_config"], {"thinking_level": "medium"})
        self.assertEqual(m.worker.settings["google_thinking_config"], {"thinking_level": "low"})

    def test_openai_key_adds_fallback(self):
        m = build_models({"GOOGLE_API_KEY": "g", "OPENAI_API_KEY": "o"})
        self.assertIsInstance(m.lead, FallbackModel)
        self.assertEqual(m.names["lead_fallback"], "gpt-5.4-mini")

    def test_overrides_and_validation(self):
        m = build_models({"GEMINI_TOKEN": "t", "AGENT_LEAD_MODEL": "gemini-x", "AGENT_LEAD_THINKING": "high"})
        self.assertEqual(m.names["lead"], "gemini-x")
        with self.assertRaises(ModelConfigError):
            build_models({"GOOGLE_API_KEY": "g", "AGENT_LEAD_THINKING": "minimal"})
        with self.assertRaises(ModelConfigError):
            build_models({})

    def test_key_order_prefers_gemini_token(self):
        self.assertEqual(google_key({"GOOGLE_API_KEY": "a", "GEMINI_TOKEN": "b"}), "b")

    def test_fallback_policy(self):
        with self.assertLogs("engine.agentic.models", logging.WARNING):
            self.assertTrue(should_fall_back(ModelHTTPError(503, "gemini-3.8-flash", body="overloaded")))
        self.assertFalse(should_fall_back(UsageLimitExceeded("limit")))
        self.assertFalse(should_fall_back(ValueError("ours")))


class ResearchStateTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.st = ResearchState(self.store)

    def test_plan_transitions_mirror_to_store(self):
        self.st.set_plan([("design", "Choose the design"), ("studies", "Find similar studies")])
        self.st.update_plan("design", "doing")
        self.st.update_plan("design", "done", "cross-sectional")
        self.st.update_plan("studies", "blocked", "PubMed down")
        self.assertEqual([(s["step"], s["status"]) for s in self.store.steps],
                         [("plan:design", "started"), ("plan:design", "done"), ("plan:studies", "failed")])
        self.assertIn("[blocked] studies", self.st.digest())

    def test_papers_tasks_evidence_and_trace(self):
        p = self.st.add_paper("background", PaperRecord(pmid="1", title="A", abstract="x"), lanes=["core"])
        self.assertEqual(p.token, "P1")
        self.assertEqual(self.st.add_paper("background", PaperRecord(pmid="1")).token, "P1")
        self.st.screen("P1", {"aim_similarity": 3}, selected=True)
        t = self.st.start_task("screen", "screen 1 paper", model="cheap")
        self.st.finish_task(t, "1 selected")
        self.st.add_evidence(token="P1", pmid="1", quantity="prevalence", value=0.39, unit="proportion",
                             sentence="Prevalence was 39%.", verified=True)
        d = self.st.digest()
        self.assertIn("selected P1", d)
        self.assertIn("E1(P1) prevalence=0.39", d)
        trace = json.loads(json.dumps(self.st.to_json()))
        self.assertEqual(trace["tasks"][0]["id"], "T1")
        self.assertNotIn("abstract", json.dumps(trace["papers"]))

    def test_digest_stays_compact(self):
        self.st.set_plan([(f"s{i}", f"step {i}") for i in range(12)])
        for i in range(40):
            self.st.add_paper("background", PaperRecord(pmid=str(i), title=f"T{i}"))
        for i in range(60):
            self.st.add_evidence(token="P1", pmid="1", quantity=f"quantity number {i} with a long name", value=i, unit="%")
        self.assertLessEqual(len(self.st.digest()), DIGEST_MAX_CHARS)


if __name__ == "__main__":
    unittest.main()
