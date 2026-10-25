import pytest

from engine.background import run_background
from engine.fake_llm import FakeBackend
from engine.knowledge import DesignChoice, resolve_design
from engine.llm import LLM, Completion, LLMConfig
from engine.methods import clean_query, decide_design, run_methods, sample_size, write_aims, write_subsection
from engine.profile import build_profile
from engine.samplesize import SampleSizeError
from engine.schemas import PaperRecord, ProposalRequest
from engine.store import MemoryStore

TITLE = "Prevalence of vitamin D deficiency among pregnant women in Tehran"


class Override(FakeBackend):
    """The fake model, with chosen steps answered by a list of scripted answers (used in order)."""

    def __init__(self, answers):
        super().__init__()
        self.answers = {step: list(items) for step, items in answers.items()}
        self.seen = []

    def call(self, messages, tier, schema, step=""):
        self.seen.append((step, tier))
        key = step.split(":")[0] + (":retry" if step.endswith(":retry") else "")
        if self.answers.get(key):
            answer = self.answers[key].pop(0)
            if isinstance(answer, Exception):
                raise answer
            if schema is None:
                return Completion(answer, None, 1, 1, "fake")
            parsed = schema.model_validate(answer)
            return Completion(parsed.model_dump_json(), parsed, 1, 1, "fake")
        return super().call(messages, tier, schema, step)


def llm_with(knowledge, answers=None):
    llm = LLM(LLMConfig(provider="fake"), knowledge, Override(answers or {}))
    llm.retry_sleep = 0
    return llm


@pytest.fixture
def profile(knowledge, fake_llm):
    return build_profile(ProposalRequest(title=TITLE, language="en"), knowledge, fake_llm)


def test_user_design_skips_the_router(knowledge, profile):
    llm = llm_with(knowledge)
    choice, record = decide_design(
        ProposalRequest(title=TITLE, design="Cohort"), profile, knowledge, llm, MemoryStore()
    )
    assert choice.label == "cohort" and record["source"] == "user"
    assert llm.backend.seen == []


def test_router_answers_twelve_questions_with_the_strong_model(knowledge, profile):
    llm = llm_with(knowledge)
    choice, record = decide_design(ProposalRequest(title=TITLE), profile, knowledge, llm, MemoryStore())
    assert choice.label == "cross_sectional" and record["source"] == "router"
    assert [a["number"] for a in record["answers"]] == list(range(1, 13))
    assert record["missing_information"] and llm.backend.seen == [("design_router", "strong")]
    assert record["name"]["fa"] == "مطالعه مقطعی"


def test_router_unknown_label_uses_general(knowledge, profile):
    answer = {"answers": [], "label": "diagnostic_accuracy", "other_name": "Diagnostic accuracy study",
              "rationale": "One. Two. Three. Four.", "missing_information": []}  # fmt: skip
    llm = llm_with(knowledge, {"design_router": [answer]})
    choice, record = decide_design(ProposalRequest(title=TITLE), profile, knowledge, llm, MemoryStore())
    assert choice == DesignChoice("general", "Diagnostic accuracy study")
    assert record["rationale"] == "One. Two. Three."
    assert record["answers"][0]["answer"] == "not answered"


def test_router_failure_falls_back_to_the_profile_guess(knowledge, profile):
    llm = llm_with(knowledge, {"design_router": [RuntimeError("x")]})
    store = MemoryStore()
    choice, record = decide_design(ProposalRequest(title=TITLE), profile, knowledge, llm, store)
    assert choice.label == "cross_sectional" and record["source"] == "profile" and store.warnings


def test_clean_query_keeps_six_plain_words():
    assert clean_query('vitamin[tiab] AND "D" (pregnancy) prevalence methods survey extra words') == (
        "vitamin D pregnancy prevalence methods survey"
    )


PAPER = PaperRecord(pmid="1", title="T", abstract="Deficiency was found in 30% of 400 women. The SD was 12.")


def choice(formula, params, rate=0.1, rate_source="assumption"):
    return {"formula": formula, "parameters": params, "nonresponse_rate": rate, "rate_source": rate_source,
            "rate_token": "", "reasoning": "r"}  # fmt: skip


def param(name, value, source="assumption", token=""):
    return {"name": name, "value": value, "source": source, "token": token, "note": ""}


def run_size(knowledge, profile, answer, design="cross_sectional", lang="en"):
    llm = llm_with(knowledge, {"sample_size": [answer]})
    request = ProposalRequest(title=TITLE, language=lang)
    return sample_size(profile, request, resolve_design(knowledge, design), knowledge, llm, {}, {"P1": PAPER}, lang)


def test_paper_value_found_is_cited(knowledge, profile):
    size = run_size(knowledge, profile, choice("single_proportion", [param("p", 0.3, "paper", "P1"),
                                                                        param("d", 0.05, "convention")]))  # fmt: skip
    assert size["sources"]["p"] == "{P1}" and size["sources"]["alpha"] == "convention"
    assert size["n1"] == 323 and size["final_total"] == 359
    assert "{P1}" in size["worked"] and "359" in size["worked"]


def test_value_from_a_dissimilar_population_is_flagged_for_the_researcher(knowledge, profile):
    llm = llm_with(knowledge, {"sample_size": [choice("single_proportion", [param("p", 0.3, "paper", "P1"),
                                                                             param("d", 0.05, "convention")])]})  # fmt: skip
    notes = {"baseline_numbers": [{"token": "P1", "value": "30", "population_similarity": 1}]}
    size = sample_size(profile, ProposalRequest(title=TITLE, language="en"), resolve_design(knowledge, "cross_sectional"),
                       knowledge, llm, notes, {"P1": PAPER}, "en")  # fmt: skip
    assert size["sources"]["p"] == "{P1}"
    assert "population may differ" in size["worked"]


def test_value_from_a_similar_population_is_not_flagged(knowledge, profile):
    llm = llm_with(knowledge, {"sample_size": [choice("single_proportion", [param("p", 0.3, "paper", "P1"),
                                                                             param("d", 0.05, "convention")])]})  # fmt: skip
    notes = {"baseline_numbers": [{"token": "P1", "value": "30", "population_similarity": 3}]}
    size = sample_size(profile, ProposalRequest(title=TITLE, language="en"), resolve_design(knowledge, "cross_sectional"),
                       knowledge, llm, notes, {"P1": PAPER}, "en")  # fmt: skip
    assert "population may differ" not in size["worked"]


def test_paper_value_not_found_becomes_assumption(knowledge, profile):
    size = run_size(knowledge, profile, choice("single_proportion", [param("p", 0.42, "paper", "P1"),
                                                                        param("d", 0.05, "convention")]))  # fmt: skip
    assert size["sources"]["p"] == "assumption"
    assert any("not found" in n for n in size["notes"])


def test_formula_must_be_allowed_for_the_design(knowledge, profile):
    with pytest.raises(SampleSizeError):
        run_size(knowledge, profile, choice("paired_mean", [param("sigma_d", 8), param("delta", 4)]), "case_control")


def test_systematic_review_has_no_sample_size(knowledge, profile):
    llm = llm_with(knowledge)
    design = resolve_design(knowledge, "systematic_review_or_meta_analysis")
    assert sample_size(profile, ProposalRequest(title=TITLE), design, knowledge, llm, {}, {}, "fa") is None
    assert llm.backend.seen == []


def test_section_33_must_state_the_number(knowledge, profile):
    size = run_size(knowledge, profile, choice("single_proportion", [param("p", 0.5, "convention"),
                                                                        param("d", 0.05, "convention")]))  # fmt: skip
    design = resolve_design(knowledge, "cross_sectional")
    request = ProposalRequest(title=TITLE, language="en")
    llm = llm_with(knowledge, {"methods_section": ["No number here."], "methods_section:retry": ["Still none."]})
    out = write_subsection("sample_size", design, knowledge, llm, request, profile, {}, [], size, "")
    assert out["flags"] == ["sample_size_missing"]
    llm = llm_with(knowledge, {"methods_section": ["No number here."]})
    out = write_subsection("sample_size", design, knowledge, llm, request, profile, {}, [], size, "")
    assert out["flags"] == [] and "428" in out["text"]


def test_aims_counts_are_checked(knowledge, profile):
    bad = {"main_aim": "a", "secondary_aims": ["x", "y"], "hypotheses": [{"text": "h", "applicable": True}],
           "questions": ["q1", "q2"], "practical_aims": []}  # fmt: skip
    design = resolve_design(knowledge, "cross_sectional")
    request = ProposalRequest(title=TITLE, language="en")
    aims = write_aims(request, profile, design, knowledge, llm_with(knowledge, {"aims": [bad]}), {})
    assert aims["flags"] == [] and len(aims["secondary_aims"]) == len(aims["hypotheses"]) == len(aims["questions"])
    aims = write_aims(
        request, profile, design, knowledge, llm_with(knowledge, {"aims": [bad], "aims:retry": [bad]}), {}
    )
    assert aims["flags"] == ["aims_count_mismatch"]


def test_full_methods_with_fake_model(knowledge, fake_llm, offline_pubmed, profile, now):
    store = MemoryStore()
    design = resolve_design(knowledge, "cross_sectional")
    request = ProposalRequest(title=TITLE, language="fa")
    background = run_background(profile, design, knowledge, fake_llm, offline_pubmed, store, now.year, "fa", TITLE,
                                write=False)  # fmt: skip
    methods = run_methods(request, profile, design, knowledge, fake_llm, offline_pubmed, store, background)
    assert [s["id"] for s in methods["subsections"]] == knowledge.section_ids()
    assert all(s["text"] for s in methods["subsections"])
    assert len(methods["topics"]) == 5 and all(t["summary"] for t in methods["topics"])
    size = methods["sample_size"]
    assert size["formula"] == "single_proportion" and size["sources"]["p"].startswith("{P")
    section_33 = methods["subsections"][2]
    assert str(size["final_total"]) in section_33["text"] and section_33["flags"] == []
    assert methods["notes"]["baseline_numbers"]
    assert any(s["lane"].startswith("topic") for s in store.searches)
