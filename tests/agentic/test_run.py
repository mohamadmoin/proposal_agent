"""Agentic Proposal engine, AGT-5: the lead agent end to end, offline. The lead and the worker are pydantic-ai
FunctionModels that follow the workflow, so the whole run (tools, sub-agents, validators, assembly, reference
numbering, contract checks) is exercised without any network or real model.

    python -m pytest tests.agentic.test_run
"""
import json
import re
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from engine import checks, pipeline
from engine.agentic import lead as L
from engine.agentic import tools
from engine.agentic.models import AgentModels
from engine.agentic.run import run_agentic
from engine.agentic.workers import build_workers
from engine.knowledge import get_knowledge
from engine.schemas import PaperRecord, ProposalRequest
from engine.store import MemoryStore

from .test_tools import PROFILE, FakePubMed, extract_handler, ready_ctx, screen_handler, scripted

INTRO_FILLER = ("کمبود ویتامین D در دوران بارداری یکی از مسائل مهم سلامت مادر و جنین است و پیامدهای آن در "
                "مطالعات مختلف بررسی شده است. ")


def worker_handler(prompt):
    if "Task: screen papers" in prompt:
        return screen_handler(prompt)
    if "Extract the requested quantities" in prompt:
        return extract_handler(prompt)
    return "در این مطالعه روی ۴۰۰ زن باردار، شیوع کمبود ویتامین D برابر 39.5% گزارش شد."


def last_return(messages) -> dict:
    for m in reversed(messages):
        for part in getattr(m, "parts", []):
            if isinstance(part, ToolReturnPart):
                return part.content if isinstance(part.content, dict) else {}
    return {}


def lead_script():
    """The lead's moves, one tool call per model response, reading earlier tool results where it needs ids."""
    sub_ids = get_knowledge().section_ids()

    def fn(messages, info: AgentInfo) -> ModelResponse:
        step = sum(isinstance(m, ModelResponse) for m in messages)
        prev = last_return(messages)

        def call(name, **args):
            return ModelResponse(parts=[ToolCallPart(name, args)])
        if step == 0:
            return call("update_plan", item_id="define", status="doing")
        if step == 1:
            return call("define_study", profile=PROFILE.model_dump())
        if step == 2:
            return call("choose_design", label="cross_sectional", rationale="prevalence in one period")
        if step == 3:
            return call("find_studies", purpose="background")
        if step == 4:
            fn.kept = [k["id"] for k in prev["kept"]]
            return call("write_study_paragraphs", paper_ids=fn.kept)
        if step == 5:
            return call("extract_data", paper_ids=[fn.kept[0]], quantities=["prevalence of vitamin D deficiency"])
        if step == 6:
            ev = prev["stored"][0]["id"]
            return call("compute_sample_size", formula="single_proportion",
                        params={"p": {"evidence": ev}, "d": {"value": 0.05, "reason": "absolute precision of 5 points"}},
                        expected_recruitment=1200, attrition=0.1, reasoning="prevalence from the closest study")
        if step == 7:
            fn.n = prev["final_total"]
            fn.worked = prev["worked_calculation"]
            return call("submit_aims", aims={
                "main_aim": "تعیین شیوع کمبود ویتامین D در زنان باردار",
                "secondary_aims": ["تعیین شیوع بر حسب سه‌ماهه", "تعیین ارتباط مصرف مکمل با کمبود"],
                "hypotheses": [{"text": "کاربرد ندارد", "applicable": False},
                               {"text": "مصرف مکمل با کمبود کمتر همراه است", "applicable": True}],
                "questions": ["شیوع بر حسب سه‌ماهه چقدر است؟", "آیا مصرف مکمل با کمبود ارتباط دارد؟"],
                "practical_aims": ["برنامه‌ریزی غربالگری"]})
        if step == 8:
            subs = [{"id": i, "text": f"متن زیربخش {i}."} for i in sub_ids]
            for s in subs:
                if s["id"] == "sample_size":
                    s["text"] = f"حجم نمونه با فرمول برآورد یک نسبت محاسبه شد {{{fn.kept[0]}}}.\n{fn.worked}\nحجم نهایی {fn.n} نفر است."
            return call("submit_methods", methods={
                "subsections": subs,
                "variables": [{"name": "کمبود ویتامین D", "role": "dependent", "type": "qualitative nominal",
                               "unit_or_categories": "بله/خیر", "definition": "25(OH)D کمتر از ۲۰", "measurement": "الایزا"}],
                "ethics": ["اخذ کد اخلاق", "رضایت آگاهانه کتبی", "محرمانگی داده‌ها"], "duration_months": 12,
                "timeline": [{"activity": "تصویب", "start_month": 1, "end_month": 2},
                             {"activity": "نمونه‌گیری", "start_month": 3, "end_month": 9},
                             {"activity": "تحلیل و گزارش", "start_month": 10, "end_month": 12}]})
        if step == 9:
            return call("find_studies", purpose="introduction", query="vitamin D pregnancy burden", need="burden")
        if step == 10:
            tokens = fn.kept + [k["id"] for k in prev["kept"]]
            text = "".join(f"{INTRO_FILLER.strip()[:-1]} {{{tokens[i % len(tokens)]}}}. " for i in range(26))
            text += f"در یک مطالعه شیوع کمبود 39.5% بود {{{fn.kept[0]}}}. هدف این مطالعه تعیین شیوع در کرج است."
            return call("submit_introduction", text=text)
        if step == 11:
            return call("review_proposal")
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
                                                 {"summary": "A cross-sectional prevalence study.",
                                                  "open_issues": ["Name the health centres."]})])
    return FunctionModel(fn)


def intro(ctx, text):
    import asyncio
    return asyncio.run(L.submit_introduction(ctx, text))


class AgenticRunTests(unittest.TestCase):
    def run_engine(self, lead=None):
        store = MemoryStore()
        models = AgentModels(lead=lead or lead_script(), worker=scripted(worker_handler), names={"lead": "fake-lead"},
                             critic=scripted(lambda prompt: {"issues": []}))
        req = ProposalRequest(title="شیوع کمبود ویتامین D در زنان باردار مراجعه‌کننده به مراکز بهداشتی")
        result = run_agentic(req, store, FakePubMed(), datetime(2026, 10, 28, tzinfo=UTC), models=models,
                             env={"LLM_MAX_PARALLEL": "2"})
        return result, store

    def test_full_run_meets_the_contract(self):
        result, store = self.run_engine()
        self.assertEqual(result["status"], "done", result.get("agent", {}).get("error"))
        self.assertEqual(list(result["sections"]), ["introduction", "aims", "background", "methods"])
        methods = result["sections"]["methods"]
        self.assertEqual([s["id"] for s in methods["subsections"]], get_knowledge().section_ids())
        self.assertEqual(methods["design"]["label"], "cross_sectional")
        self.assertTrue(methods["variables"])
        self.assertTrue(result["references"])
        self.assertIn("[1]", result["sections"]["introduction"]["text"])
        papers = [p for p in result["agent"]["papers"] if p["selected"]]
        selected = [{"pmid": p["pmid"], "abstract": FakePubMed().efetch([p["pmid"]])[0].abstract,
                     "screening": p["screening"]} for p in papers]
        outcome = {c.name: c.passed for c in checks.run_checks(result, selected, list(result["sections"]))}
        self.assertTrue(outcome["citations_resolve"], outcome)
        self.assertTrue(outcome["evidence_verbatim"], outcome)
        self.assertTrue(outcome["sample_size_matches"], outcome)
        self.assertTrue(outcome["aims_one_to_one"], outcome)
        self.assertTrue(outcome["no_empty_section"], outcome)
        # the trace: plan, decisions, sub-agent tasks with tokens, model calls counted in usage
        agent = result["agent"]
        self.assertTrue(any(d["key"] == "sample_size" for d in agent["decisions"]))
        self.assertTrue({"screen", "extract", "write_study"} <= {t["kind"] for t in agent["tasks"]})
        self.assertGreater(result["usage"]["calls"], 10)
        self.assertTrue(any(c["step"] == "agent:lead" for c in store.calls))
        json.dumps(result)  # the result is JSON

    def test_budget_or_model_failure_fails_honestly(self):
        def broken(messages, info):
            raise RuntimeError("provider down")
        result, _ = self.run_engine(lead=FunctionModel(broken))
        self.assertEqual(result["status"], "failed")
        self.assertIn("provider down", result["agent"]["error"])

    def test_pipeline_dispatches_on_flag(self):
        store = MemoryStore()
        called = {}

        def fake_run(*args, **kwargs):
            called["env"] = kwargs["env"]
            return {"status": "done"}
        import engine.agentic.run as R
        original = R.run_agentic
        R.run_agentic = fake_run
        try:
            out = pipeline.run(ProposalRequest(title="x"), store, SimpleNamespace(knowledge=get_knowledge()),
                               FakePubMed(), datetime(2026, 10, 28, tzinfo=UTC),
                               engine_env={"PROPOSAL_ENGINE": "agentic"})
        finally:
            R.run_agentic = original
        self.assertEqual(out, {"status": "done"})
        self.assertEqual(called["env"]["PROPOSAL_ENGINE"], "agentic")


class SubmissionRuleTests(unittest.TestCase):
    def setUp(self):
        self.ctx, _ = ready_ctx(screen_handler)
        import asyncio
        asyncio.run(tools.find_studies(self.ctx, "background"))

    def test_introduction_rejects_meta_text_unknown_tokens_and_unsourced_numbers(self):
        ok_body = "".join(f"{INTRO_FILLER.strip()[:-1]} {{P{1 + i % 5}}}. " for i in range(25))
        with self.assertRaisesRegex(ModelRetry, "sources, tools or model"):
            intro(self.ctx, ok_body + "منابع ارائه‌شده درباره این موضوع چیزی نمی‌گویند.")
        with self.assertRaisesRegex(ModelRetry, "unknown paper tokens"):
            intro(self.ctx, ok_body + "شیوع بالا است {P99}.")
        with self.assertRaisesRegex(ModelRetry, "not in the cited paper"):
            intro(self.ctx, ok_body + "شیوع 55% بود {P1}.")
        with self.assertRaisesRegex(ModelRetry, "has no citation"):
            intro(self.ctx, ok_body + "شیوع 39.5% است.")
        with self.assertRaisesRegex(ModelRetry, "have no source"):
            intro(self.ctx, INTRO_FILLER * 25 + "شیوع 39.5% بود {P1}.")
        intro(self.ctx, ok_body + "شیوع 39.5% بود {P1}.")
        self.assertIn("introduction", self.ctx.deps.state.drafts)

    def test_aims_must_be_one_to_one(self):
        with self.assertRaisesRegex(ModelRetry, "exactly one of each"):
            L.submit_aims(self.ctx, L.AimsIn(main_aim="a", secondary_aims=["b", "c"],
                                             hypotheses=[L.HypothesisIn(text="h", applicable=True)],
                                             questions=["q1", "q2"], practical_aims=[]))

    def test_methods_need_the_computed_n(self):
        subs = [L.SubsectionIn(id=i, text="متن") for i in get_knowledge().section_ids()]
        with self.assertRaisesRegex(ModelRetry, "compute_sample_size"):
            L.submit_methods(self.ctx, L.MethodsIn(subsections=subs, variables=[], ethics=[], duration_months=12,
                                                   timeline=[]))


if __name__ == "__main__":
    unittest.main()


class ContextEconomyTests(unittest.TestCase):
    def test_old_tool_results_are_shortened_recent_kept_pairs_intact(self):
        from pydantic_ai.messages import ModelRequest, UserPromptPart
        big = {"ok": True, "kept": [{"id": f"P{i}", "title": "x" * 200} for i in range(8)], "workspace": "w" * 3000}
        msgs = [ModelRequest(parts=[UserPromptPart("go")])]
        for i in range(4):
            msgs.append(ModelResponse(parts=[ToolCallPart("find_studies", {}, tool_call_id=f"c{i}")]))
            msgs.append(ModelRequest(parts=[ToolReturnPart("find_studies", dict(big), tool_call_id=f"c{i}")]))
        out = L.compact_history(msgs)
        returns = [p for m in out if isinstance(m, L.ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
        self.assertEqual([r.tool_call_id for r in returns], ["c0", "c1", "c2", "c3"])  # pairing intact
        self.assertEqual(returns[0].content["kept"], [f"P{i}" for i in range(8)])  # old: ids only
        self.assertNotIn("workspace", returns[0].content)
        self.assertEqual(returns[-1].content, big)  # recent: complete
        self.assertIs(out[1], msgs[1])  # responses other than submissions are untouched
        self.assertLess(len(json.dumps(out[2].parts[0].content)), len(json.dumps(big)) / 10)

    def test_compaction_is_idempotent(self):
        content = {"ok": True, "kept": [{"id": "P1"}], "stored": [{"id": "E1", "value": 3, "unit": "%"}]}
        once = L.compact_result("find_studies", content)
        self.assertEqual(L.compact_result("find_studies", once), once)

    def test_old_submission_args_become_a_note(self):
        from pydantic_ai.messages import ModelRequest, UserPromptPart
        long = {"text": "متن " * 2000}
        msgs = [ModelRequest(parts=[UserPromptPart("go")]),
                ModelResponse(parts=[ToolCallPart("submit_introduction", long, tool_call_id="s1")]),
                ModelRequest(parts=[ToolReturnPart("submit_introduction", {"ok": True}, tool_call_id="s1")])]
        for i in range(3):
            msgs.append(ModelResponse(parts=[ToolCallPart("update_plan", {"item_id": "x"}, tool_call_id=f"u{i}")]))
            msgs.append(ModelRequest(parts=[ToolReturnPart("update_plan", {"ok": True}, tool_call_id=f"u{i}")]))
        out = L.compact_history(msgs)
        self.assertEqual(out[1].parts[0].args, {"submitted": L.SUBMITTED_NOTE})
        self.assertEqual(out[1].parts[0].tool_call_id, "s1")
        self.assertEqual(L.compact_history(out)[1].parts[0].args, {"submitted": L.SUBMITTED_NOTE})


class AuthorNameRuleTests(SubmissionRuleTests):
    def test_author_names_rejected_outside_background(self):
        body = "".join(f"{INTRO_FILLER.strip()[:-1]} {{P{1 + i % 5}}}. " for i in range(25))
        with self.assertRaisesRegex(ModelRetry, "do not name authors"):
            intro(self.ctx, body + "مطالعه میرزایی و همکاران شیوع 39.5% را گزارش کرد {P1}.")
        with self.assertRaisesRegex(ModelRetry, "do not name authors"):
            intro(self.ctx, body + "Mirzaei et al. reported 39.5% {P1}.")


class ReviewLoopTests(unittest.TestCase):
    def test_major_issue_must_be_fixed_or_explained(self):
        from engine.agentic.state import ResearchState
        st = ResearchState(MemoryStore())
        st.save_draft("methods", {"x": 1})
        st.reviews.append({"at": st.version, "issues": [{"id": "R1", "section": "methods", "severity": "major",
                                                         "problem": "p", "fix": "f"}]})
        issue = st.reviews[-1]["issues"][0]
        self.assertFalse(st.issue_resolved(issue, st.reviews[-1]["at"]))
        st.save_draft("methods", {"x": 2})  # resubmitted after the review
        self.assertTrue(st.issue_resolved(issue, st.reviews[-1]["at"]))
        st2 = ResearchState(MemoryStore())
        st2.record_decision("review:R1", "not applicable", "the sampling is not clustered")
        self.assertTrue(st2.issue_resolved(issue, 0))


class NoFormulaDesignTests(unittest.TestCase):
    def test_systematic_review_has_no_sample_size_error(self):
        from engine.agentic.run import needs_sample_size
        ctx, _ = ready_ctx(screen_handler)
        tools.choose_design(ctx, "systematic_review_or_meta_analysis", "pooled prevalence")
        self.assertFalse(needs_sample_size(ctx.deps))
        tools.choose_design(ctx, "cross_sectional", "prevalence")
        self.assertTrue(needs_sample_size(ctx.deps))


class CalendarAndHygieneTests(SubmissionRuleTests):
    """AGT-15 / AGT-18: past study dates, unfilled placeholders and validation results reported in advance."""

    def aims(self, main):
        return L.AimsIn(main_aim=main, secondary_aims=["تعیین شیوع کمبود"],
                        hypotheses=[L.HypothesisIn(text="کاربرد ندارد", applicable=False)],
                        questions=["شیوع کمبود چقدر است؟"], practical_aims=[])

    def test_past_study_year_rejected_current_and_records_allowed(self):
        with self.assertRaisesRegex(ModelRetry, "have passed"):
            L.submit_aims(self.ctx, self.aims("تعیین شیوع کمبود در زنان باردار کرج در سال ۱۴۰۳"))
        with self.assertRaisesRegex(ModelRetry, "have passed"):
            L.submit_aims(self.ctx, self.aims("تعیین شیوع از مهر ۱۴۰۳ تا اسفند ۱۴۰۴"))
        L.submit_aims(self.ctx, self.aims("تعیین شیوع کمبود در زنان باردار کرج در سال ۱۴۰۵"))
        L.submit_aims(self.ctx, self.aims("تعیین شیوع با بررسی پرونده‌های سال‌های ۱۴۰۲ تا ۱۴۰۴"))
        L.submit_aims(self.ctx, self.aims("تعیین شیوع کمبود در ۱۳۵۰ زن باردار"))  # a count, not a year

    def test_placeholders_rejected(self):
        with self.assertRaisesRegex(ModelRetry, "placeholders"):
            L.submit_aims(self.ctx, self.aims("تعیین شیوع کمبود در [نام بیمارستان]"))
        L.check_text(self.ctx, "شاخص HOMA-IR = [FPG (mg/dL) × Fasting Insulin (μIU/mL)] / 405 محاسبه می‌شود.", "methods")

    def test_search_range_must_reach_this_year_and_title_years_allowed(self):
        with self.assertRaisesRegex(ModelRetry, "have passed"):
            L.check_text(self.ctx, "جست‌وجو بدون محدودیت زمانی آغازین تا پایان فروردین ماه ۱۴۰۳ انجام خواهد شد.",
                         "methods", study_period=True)
        L.check_text(self.ctx, "جست‌وجو بدون محدودیت زمانی تا پایان آذر ۱۴۰۵ انجام خواهد شد.", "methods",
                     study_period=True)
        self.ctx.deps.request = ProposalRequest(title="عوامل مرتبط با عفونت در بیمارستان شهید مدنی (سال ۱۴۰۴)")
        L.check_text(self.ctx, "تعیین عوامل مرتبط با عفونت در سال ۱۴۰۴", "aims", study_period=True)

    def test_validation_results_must_not_be_reported_in_advance(self):
        with self.assertRaisesRegex(ModelRetry, "already done"):
            L.check_text(self.ctx, "پایایی ابزار با ICC بالاتر از ۰/۸۸ به دست آمد.", "methods")
        L.check_text(self.ctx, "پایایی ابزار با ICC سنجیده می‌شود و مقدار بالاتر از ۰/۷ پذیرفته خواهد شد.", "methods")
        L.check_text(self.ctx, "روایی نسخه فارسی پرسش‌نامه پیش‌تر تأیید شده است {P1}.", "methods")

    def test_instructions_carry_today(self):
        text = L.instructions(ProposalRequest(title="t"), get_knowledge(), datetime(2026, 10, 28, tzinfo=UTC))
        self.assertIn("6 آبان 1405", text)
        self.assertIn("2026-10-28", text)


class CriticDigestTests(SubmissionRuleTests):
    def test_digest_shows_parameter_sources_and_the_evidence_table(self):
        st = self.ctx.deps.state
        st.add_evidence(token="P1", pmid=st.papers["P1"].record.pmid, quantity="deficiency", value=39.5, unit="%",
                        verified=True, sentence="The prevalence was 39.5%.")
        tools.compute_sample_size(self.ctx, "single_proportion",
                                  {"p": tools.ParamValue(evidence="E1"),
                                   "d": tools.ParamValue(value=0.05, reason="absolute precision of five points")},
                                  expected_recruitment=1000, reasoning="closest study")
        digest = L.proposal_digest(self.ctx.deps)
        self.assertIn("EVIDENCE TABLE", digest)
        self.assertIn("flags:", digest)
        self.assertIn("evidence table", L.CRITIC_INSTRUCTIONS)


def support_handler(verdict):
    """Screens like screen_handler; the claim checker answers `verdict` for every sentence."""
    def handler(prompt):
        if "Check whether each numbered sentence" in prompt:
            n = len(re.findall(r"^\d+\. ", prompt.split("Sentences:")[-1], re.M))
            return {"items": [{"sentence": i + 1, "verdict": verdict, "reason": "the paper studied adults"}
                              for i in range(n)]}
        return screen_handler(prompt)
    return handler


class ClaimSupportTests(unittest.TestCase):
    """AGT-16: the claim check advises; its findings come back with the submission and join the review."""

    def setUp(self):
        import asyncio
        self.ctx, _ = ready_ctx(support_handler("unsupported"))
        asyncio.run(tools.find_studies(self.ctx, "background"))
        self.text = "".join(f"{INTRO_FILLER.strip()[:-1]} {{P{1 + i % 5}}}. " for i in range(25))

    def test_flagged_claims_are_returned_and_reviewed(self):
        import asyncio
        out = intro(self.ctx, self.text)
        self.assertIn("introduction", self.ctx.deps.state.drafts)  # never blocks the submission
        self.assertTrue(out["claims_to_check"])
        self.ctx.deps.state.save_draft("aims", {"main_aim": "x", "secondary_aims": [], "hypotheses": [],
                                                "questions": [], "practical_aims": []})
        self.ctx.deps.state.save_draft("methods", {"subsections": [], "variables": [], "ethics": [], "timeline": [],
                                                   "duration_months": 12})
        self.ctx.deps.critic = build_critic_stub()
        review = asyncio.run(L.review_proposal(self.ctx))
        claims = [i for i in review["issues"] if i["id"].startswith("C")]
        self.assertTrue(claims and all(i["severity"] == "major" for i in claims))

    def test_supported_claims_pass_quietly(self):
        self.ctx.deps.workers = build_workers(scripted(support_handler("supported")), get_knowledge(), "fake")
        out = intro(self.ctx, self.text)
        self.assertNotIn("claims_to_check", out)


def build_critic_stub():
    from pydantic_ai import Agent
    return Agent(scripted(lambda prompt: {"issues": []}), output_type=L.ReviewOut)


class LastChanceTests(SubmissionRuleTests):
    def test_text_rule_never_ends_the_run_on_the_last_attempt(self):
        with self.assertRaises(ModelRetry):
            L.check_text(self.ctx, "شیوع 55% بود {P1}.", "introduction")
        last = SimpleNamespace(deps=self.ctx.deps, usage=self.ctx.usage, retry=4, max_retries=4)
        L.check_text(last, "شیوع 55% بود {P1}.", "introduction")  # kept, with a warning
        self.assertTrue(any("last attempt" in w for w in self.ctx.deps.state.warnings))


class DuplicateStudyTests(unittest.TestCase):
    def test_same_registry_id_is_reported_and_can_be_dropped(self):
        import asyncio
        ctx, _ = ready_ctx(screen_handler)
        st = ctx.deps.state
        a = st.add_paper("background", PaperRecord(pmid="1", title="Trial A", abstract="Registered IRCT2015081223654N1."))
        b = st.add_paper("background", PaperRecord(pmid="2", title="Trial B", abstract="Follow-up of IRCT2015081223654N1."))
        self.assertEqual(tools.possible_duplicates(st, [a.token, b.token]), [[a.token, b.token]])
        ctx.deps.background_paragraphs = {a.token: {"token": a.token, "text": "x"}, b.token: {"token": b.token, "text": "y"}}
        asyncio.run(tools.write_study_paragraphs(ctx, [], drop=[b.token]))
        self.assertEqual(list(ctx.deps.background_paragraphs), [a.token])



class FalsePositiveTests(SubmissionRuleTests):
    """S3 gate check: rules must not reject the study's own numbers."""

    def test_closing_aim_sentence_may_state_this_year(self):
        body = "".join(f"{INTRO_FILLER.strip()[:-1]} {{P{1 + i % 5}}}. " for i in range(25))
        intro(self.ctx, body + "این مطالعه با هدف تعیین شیوع کمبود در سال ۱۴۰۵ انجام خواهد شد.")
        self.assertIn("introduction", self.ctx.deps.state.drafts)

    def test_evidence_ids_must_not_leak_into_the_text(self):
        st = self.ctx.deps.state
        st.add_evidence(token="P1", pmid=st.papers["P1"].record.pmid, quantity="x", value=39.5, unit="%",
                        verified=True, sentence="39.5%")
        with self.assertRaisesRegex(ModelRetry, "workspace ids"):
            L.check_text(self.ctx, "شیوع کمبود ۳۹٫۵ درصد بود (E1) {P1}.", "methods")
        L.check_text(self.ctx, "سطح استرادیول (E2) اندازه‌گیری می‌شود.", "methods")  # not a stored id
        with self.assertRaisesRegex(ModelRetry, "workspace ids"):
            L.check_text(self.ctx, "پارامترها از مطالعه (P1) گرفته شد.", "methods")

    def test_bare_ids_and_past_months(self):
        with self.assertRaisesRegex(ModelRetry, "workspace ids"):
            L.check_text(self.ctx, "شیوع از مطالعه P1 گرفته شد.", "methods")
        with self.assertRaisesRegex(ModelRetry, "have passed"):
            L.check_text(self.ctx, "نمونه‌گیری از تیرماه ۱۴۰۵ تا پایان آذرماه ۱۴۰۵ انجام می‌شود.", "methods",
                         study_period=True)
        L.check_text(self.ctx, "نمونه‌گیری از دی ۱۴۰۵ تا خرداد ۱۴۰۶ انجام می‌شود.", "methods", study_period=True)
        L.check_text(self.ctx, "جست‌وجو از فروردین ۱۳۸۰ تا آذر ۱۴۰۵ انجام خواهد شد.", "methods", study_period=True)

