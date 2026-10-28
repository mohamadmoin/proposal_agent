"""Agentic Proposal engine, AGT-3/AGT-4: the lead's tools and the cheap sub-agents. No database, no network, no real
model: the worker model is a pydantic-ai FunctionModel that returns scripted structured outputs.

    python -m pytest tests.agentic.test_tools
"""
import asyncio
import json
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RunUsage

from engine.agentic import tools
from engine.agentic.state import ResearchState
from engine.agentic.workers import build_workers
from engine.knowledge import get_knowledge
from engine.pubmed import SearchResult
from engine.schemas import PaperRecord, ProposalRequest, StudyProfileOut
from engine.store import MemoryStore

ABSTRACT = ("Vitamin D status was assessed in pregnant women in Tabriz, Iran. A total of 400 women were enrolled. "
            "The prevalence of vitamin D deficiency (25(OH)D < 20 ng/mL) was 39.5%. Deficiency was more common in "
            "winter. Supplement use was associated with lower risk (OR 0.45).")
PAPERS = {str(1000 + i): PaperRecord(pmid=str(1000 + i), title=f"Vitamin D deficiency in pregnant women {i}",
                                    abstract=ABSTRACT + f" Study {i}.", year=2020 + i % 5, authors=["Rahimi A"])
          for i in range(8)}


class FakePubMed:
    def esearch(self, term, retmax=50, sort="relevance"):
        return SearchResult(count=len(PAPERS), pmids=list(PAPERS))

    def efetch(self, pmids):
        return [PAPERS[p] for p in pmids if p in PAPERS]


def scripted(handler):
    """A FunctionModel whose structured output is produced by handler(prompt_text) -> dict."""
    def fn(messages, info: AgentInfo) -> ModelResponse:
        prompt = "".join(getattr(p, "content", "") for m in messages for p in getattr(m, "parts", [])
                         if isinstance(getattr(p, "content", None), str))
        if info.output_tools:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, handler(prompt))])
        return ModelResponse(parts=[TextPart(handler(prompt))])
    return FunctionModel(fn)


def screen_handler(prompt):
    pmids = [p for p in PAPERS if f"PMID {p}" in prompt or p in prompt]
    return {"papers": [{"pmid": p, "aim_similarity": 3, "population_similarity": 3, "design": "cross_sectional",
                        "design_match": True, "sample_size": 400, "country": "Iran",
                        "aim": "Prevalence of deficiency.", "main_finding": "Deficiency was 39.5%.",
                        "evidence": [3]} for p in pmids]}


def extract_handler(prompt):
    return {"items": [
        {"quantity": "vitamin D deficiency (<20 ng/mL)", "value": 39.5, "unit": "%", "group": "overall",
         "population": "pregnant women, Iran", "sentence": 3},
        {"quantity": "invented number", "value": 77, "unit": "%", "group": "overall", "population": "x",
         "sentence": 3},  # 77 is not in sentence 3 → must be rejected
    ]}


def make_ctx(worker_handler=None):
    knowledge = get_knowledge()
    store = MemoryStore()
    state = ResearchState(store)
    model = scripted(worker_handler or (lambda p: "ok"))
    deps = tools.AgentDeps(state=state, request=ProposalRequest(title="شیوع کمبود ویتامین D در زنان باردار"),
                           pubmed=FakePubMed(), knowledge=knowledge,
                           workers=build_workers(model, knowledge, "fake-worker"), now=datetime(2026, 10, 28, tzinfo=UTC))
    return SimpleNamespace(deps=deps, usage=RunUsage()), store


PROFILE = StudyProfileOut(english_title="Vitamin D deficiency in pregnant women", population="pregnant women",
                          setting_country="Iran", exposure_or_intervention="", comparator="",
                          outcomes="vitamin D deficiency", design_guess="cross_sectional", design_guess_other_name="",
                          concepts=["vitamin D deficiency", "pregnancy"],
                          core_query='"vitamin D deficiency"[tiab] AND pregnan*[tiab]',
                          plain_query="vitamin D pregnancy", statpearls_terms="vitamin D deficiency")


def ready_ctx(handler):
    ctx, store = make_ctx(handler)
    tools.define_study(ctx, PROFILE.model_copy(deep=True))
    tools.choose_design(ctx, "cross_sectional", "prevalence in one period")
    return ctx, store


class DesignAndMemoryTests(unittest.TestCase):
    def test_choose_design_validates_and_returns_guidance(self):
        ctx, store = make_ctx()
        with self.assertRaises(ModelRetry):
            tools.choose_design(ctx, "made_up_design", "x")
        out = tools.choose_design(ctx, "cross_sectional", "estimate a prevalence")
        self.assertIn("single_proportion", out["allowed_sample_size_formulas"])
        self.assertEqual(len(out["methods_subsections"]), 5)
        self.assertEqual(store.design["label"], "cross_sectional")
        self.assertIn("design=cross_sectional", out["workspace"])

    def test_plan_and_decisions(self):
        ctx, _ = make_ctx()
        tools.update_plan(ctx, "design", "doing", title="Choose the design")
        out = tools.update_plan(ctx, "design", "done", "cross-sectional")
        self.assertIn("[done] design", out["workspace"])
        with self.assertRaises(ModelRetry):
            tools.record_decision(ctx, "primary_outcome", "x", "y", evidence=["E9"])


class LiteratureTests(unittest.TestCase):
    def test_background_search_screens_with_worker_and_keeps_compact(self):
        ctx, store = ready_ctx(screen_handler)
        out = asyncio.run(tools.find_studies(ctx, "background"))
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(len(out["kept"]), 5)
        self.assertNotIn("abstract", json.dumps(out))  # the lead never receives abstracts
        tasks = ctx.deps.state.tasks
        self.assertTrue(tasks and all(t.kind == "screen" and t.status == "done" for t in tasks.values()))
        self.assertTrue(any(c["step"] == "agent:screen" for c in store.calls))

    def test_extraction_keeps_only_values_found_in_their_sentence(self):
        ctx, _ = ready_ctx(screen_handler)
        asyncio.run(tools.find_studies(ctx, "background"))
        ctx.deps.workers = build_workers(scripted(extract_handler), ctx.deps.knowledge, "fake-worker")
        out = asyncio.run(tools.extract_data(ctx, ["P1"], ["prevalence of vitamin D deficiency"]))
        self.assertEqual([s["value"] for s in out["stored"]], [39.5])
        self.assertEqual(out["rejected_unverifiable"], 1)
        e = ctx.deps.state.evidence["E1"]
        self.assertTrue(e.verified)
        self.assertIn("39.5%", e.sentence)


class SampleSizeToolTests(unittest.TestCase):
    def setUp(self):
        self.ctx, _ = ready_ctx(screen_handler)
        st = self.ctx.deps.state
        p1 = st.add_paper("methods", PAPERS["1000"])
        p2 = st.add_paper("methods", PAPERS["1001"])
        st.screen(p1.token, {"population_similarity": 3}, selected=True)
        st.screen(p2.token, {"population_similarity": 2}, selected=True)
        st.add_evidence(token=p1.token, pmid="1000", quantity="deficiency", value=39.5, unit="%", verified=True,
                        sentence="The prevalence … was 39.5%.")
        st.add_evidence(token=p2.token, pmid="1001", quantity="deficiency", value=20, unit="%", verified=True,
                        sentence="… 20% …")
        st.add_evidence(token=p1.token, pmid="1000", quantity="x", value=12, unit="%", verified=False)

    def P(self, **kw):
        return tools.ParamValue(**kw)

    def test_from_verified_evidence(self):
        out = tools.compute_sample_size(self.ctx, "single_proportion",
                                        {"p": self.P(evidence="E1"), "d": self.P(value=0.05, reason="absolute precision of five points")},
                                        expected_recruitment=1000, attrition=0.1)
        self.assertTrue(out["ok"])
        self.assertEqual(out["final_total"], 409)  # 1.96² × 0.395 × 0.605 / 0.05² = 367.2 → 368 → /0.9 → 409
        ss = self.ctx.deps.sample_size
        self.assertEqual(ss["sources"]["p"], "{P1}")
        self.assertIn("368", ss["worked"])

    def test_rules(self):
        with self.assertRaises(ModelRetry):  # unverified evidence
            tools.compute_sample_size(self.ctx, "single_proportion", {"p": self.P(evidence="E3"),
                                      "d": self.P(value=0.05, reason="absolute precision of five points")}, 1000)
        with self.assertRaises(ModelRetry):  # assumption without a reason
            tools.compute_sample_size(self.ctx, "single_proportion", {"p": self.P(value=0.5), "d": self.P(value=0.05)}, 1000)
        with self.assertRaises(ModelRetry):  # formula not allowed for a cross-sectional design
            tools.compute_sample_size(self.ctx, "case_control_unmatched", {"p0": self.P(evidence="E1"),
                                      "odds_ratio": self.P(value=2, reason="smallest clinically important OR")}, 1000)

    def test_contrast_from_two_studies_needs_justification(self):
        self.ctx.deps.design = tools.DesignChoice("cohort", "")
        params = {"p1": self.P(evidence="E1"), "p2": self.P(evidence="E2")}
        with self.assertRaises(ModelRetry):
            tools.compute_sample_size(self.ctx, "two_proportions", params, 5000)
        out = tools.compute_sample_size(self.ctx, "two_proportions", params, 5000,
                                        combine_justification="both are Iranian pregnancy cohorts with the same cut-off")
        self.assertTrue(out["ok"])

    def test_infeasible_n_is_flagged(self):
        out = tools.compute_sample_size(self.ctx, "single_proportion",
                                        {"p": self.P(evidence="E1"), "d": self.P(value=0.02, reason="tight precision for planning")},
                                        expected_recruitment=300)
        self.assertFalse(out["ok"])
        self.assertIn("exceeds the expected recruitment", out["feasibility_problem"])
        self.assertTrue(self.ctx.deps.state.warnings)

    def test_design_effect_needs_reason(self):
        with self.assertRaises(ModelRetry):
            tools.compute_sample_size(self.ctx, "single_proportion", {"p": self.P(evidence="E1"),
                                      "d": self.P(value=0.05, reason="absolute precision of five points")}, 2000,
                                      design_effect=1.5)


class StudyWriterTests(unittest.TestCase):
    def test_paragraph_numbers_checked_and_token_appended(self):
        ctx, _ = ready_ctx(screen_handler)
        asyncio.run(tools.find_studies(ctx, "background"))
        calls = []

        def writer(prompt):
            if "Check whether each numbered sentence" in prompt:  # the claim checker (AGT-16): all supported
                return {"items": []}
            calls.append(1)
            return "در این مطالعه شیوع کمبود ۳۹٫۵ درصد بود و ۹۹ درصد ..." if len(calls) == 1 else \
                "در این مطالعه روی ۴۰۰ زن، شیوع کمبود ۳۹٫۵ درصد بود."
        ctx.deps.workers = build_workers(scripted(writer), ctx.deps.knowledge, "fake-worker")
        out = asyncio.run(tools.write_study_paragraphs(ctx, ["P1"]))
        self.assertEqual(out["written"], ["P1"])
        self.assertEqual(out["flagged"], [])  # the retry removed the invented 99
        para = ctx.deps.background_paragraphs["P1"]
        self.assertTrue(para["text"].endswith("{P1}"))
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()


class SubAgentBudgetTests(unittest.TestCase):
    def test_sub_agents_add_tokens_not_requests_to_the_lead(self):
        ctx, _ = ready_ctx(screen_handler)
        asyncio.run(tools.find_studies(ctx, "background"))
        self.assertEqual(ctx.usage.requests, 0)
        self.assertGreater(ctx.usage.input_tokens, 0)


class RepresentativenessTests(SampleSizeToolTests):
    def test_dissimilar_population_needs_a_reason(self):
        st = self.ctx.deps.state
        st.screen("P1", {"population_similarity": 1}, selected=True)
        d = self.P(value=0.05, reason="absolute precision of five points")
        with self.assertRaisesRegex(ModelRetry, "population is not similar"):
            tools.compute_sample_size(self.ctx, "single_proportion", {"p": self.P(evidence="E1"), "d": d}, 1000)
        out = tools.compute_sample_size(
            self.ctx, "single_proportion",
            {"p": self.P(evidence="E1", reason="same country and trimester; only the city differs, so it applies"),
             "d": d}, 1000)
        self.assertTrue(out["ok"])


class DiagnosticDesignTests(unittest.TestCase):
    """AGT-17: a diagnostic-accuracy design with its guide and Buderer's formula; formulas come with parameters."""

    def test_design_guide_and_formula_parameters(self):
        ctx, _ = make_ctx(screen_handler)
        tools.define_study(ctx, PROFILE.model_copy(deep=True))
        out = tools.choose_design(ctx, "diagnostic_accuracy", "accuracy of ultrasound against pathology")
        formulas = out["allowed_sample_size_formulas"]
        self.assertEqual(list(formulas), ["diagnostic_accuracy"])
        self.assertIn("prevalence", formulas["diagnostic_accuracy"]["required"])
        self.assertIn("design_diagnostic", out["guides"])
        self.assertIn("Buderer", out["guides"]["design_diagnostic"])


class StallGuardTests(unittest.TestCase):
    def test_reading_without_acting_names_the_next_step(self):
        ctx, _ = ready_ctx(screen_handler)
        notes = [tools.get_evidence(ctx, ["P1"]).get("NOTE", "") for _ in range(tools.IDLE_READS)]
        self.assertFalse(any(notes[:-1]))
        self.assertIn("take the next step", notes[-1])
        tools.record_decision(ctx, "x", "y", "a reason long enough to be recorded properly")  # progress resets it
        self.assertNotIn("NOTE", tools.get_evidence(ctx, ["P1"]))

