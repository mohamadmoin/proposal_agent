"""AGT-14: the agentic engine reads open-access full texts and their tables when the abstract lacks a number; every
value is verified against its sentence or table row; the lead never sees the full text.

    python -m pytest tests.agentic.test_fulltext
"""
import asyncio
import json
import unittest
from pathlib import Path

from engine.agentic import lead as L
from engine.agentic import tools
from engine.agentic.fulltext import build_passage
from engine.agentic.workers import build_workers

from .test_tools import ready_ctx, screen_handler, scripted

# parsed by the host's JATS parser (DrNext platform_core.agent.pubmed) from a PMC full-text fixture
FT = json.loads((Path(__file__).parent / "fixtures" / "pmc_fulltext_tables.json").read_text(encoding="utf-8"))


def worker(prompt):
    if "Task: screen papers" in prompt:
        return screen_handler(prompt)
    if "from this full-text article" in prompt:
        return {"items": [
            {"quantity": "age < 25 y among deficient", "value": 38.6, "unit": "%", "group": "deficient",
             "population": "pregnant women", "locator": "T1.r1"},
            {"quantity": "invented", "value": 99, "unit": "%", "group": "x", "population": "x", "locator": "T1.r1"},
        ]}
    if "from this abstract" in prompt:
        return {"items": []}  # the abstract does not report it
    return "ok"


class PassageTests(unittest.TestCase):
    def test_sentences_and_table_rows_with_column_names(self):
        p = build_passage(FT)
        self.assertIn("S1", p.lines)
        self.assertIn("Deficient (n = 158) %: 38.6", p.lines["T1.r1"])
        self.assertIn("Table 1 Baseline characteristics by group", p.lines["T1.r1"])
        self.assertIn("(T1 note) Deficiency: 25(OH)D < 20 ng/mL.", p.text)
        self.assertIsNone(build_passage({"body_available": False}))


class FullTextExtractionTests(unittest.TestCase):
    def setUp(self):
        self.ctx, _ = ready_ctx(worker)
        asyncio.run(tools.find_studies(self.ctx, "background"))
        self.ctx.deps.workers = build_workers(scripted(worker), self.ctx.deps.knowledge, "fake-worker")
        self.calls = []

        def fetch(pmcid):
            self.calls.append(pmcid)
            return FT
        self.ctx.deps.fulltext = fetch
        self.ctx.deps.state.papers["P1"].record.pmcid = "PMC1234567"

    def test_full_text_used_when_abstract_lacks_the_number(self):
        out = asyncio.run(tools.extract_data(self.ctx, ["P1"], ["share of women under 25 among the deficient"]))
        self.assertEqual([s["value"] for s in out["stored"]], [38.6])
        self.assertEqual(out["rejected_unverifiable"], 1)
        self.assertEqual(out["full_texts_read"], ["P1"])
        ev = next(iter(self.ctx.deps.state.evidence.values()))
        self.assertEqual((ev.source, ev.locator), ("full_text", "T1.r1"))
        self.assertTrue(any(t.kind == "read_full_text" for t in self.ctx.deps.state.tasks.values()))
        # fetched once per paper, then cached for the run
        asyncio.run(tools.extract_data(self.ctx, ["P1"], ["something else"]))
        self.assertEqual(self.calls, ["PMC1234567"])

    def test_no_pmcid_or_no_service_keeps_the_abstract(self):
        self.ctx.deps.state.papers["P1"].record.pmcid = ""
        out = asyncio.run(tools.extract_data(self.ctx, ["P1"], ["x"]))
        self.assertEqual(out["full_texts_read"], [])

    def test_lead_may_cite_a_full_text_number(self):
        asyncio.run(tools.extract_data(self.ctx, ["P1"], ["share of women under 25 among the deficient"]))
        L.check_text(self.ctx, "سهم زنان زیر ۲۵ سال در گروه دچار کمبود 38.6% بود {P1}.", "methods")


if __name__ == "__main__":
    unittest.main()
