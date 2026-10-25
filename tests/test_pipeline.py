"""End-to-end runs with the fake model and offline PubMed."""

import re

import pytest

from engine.fake_llm import FakeBackend
from engine.llm import LLM, LLMConfig
from engine.pipeline import RunOptions, run
from engine.schemas import ProposalRequest
from engine.store import MemoryStore

FA_TITLE = "شیوع کمبود ویتامین D در زنان باردار مراجعه‌کننده به مراکز بهداشتی تهران"
CITATION = re.compile(r"\[(\d+(?:, \d+)*)\]")


def all_texts(result):
    sections = result["sections"]
    texts = [sections["introduction"]["text"], sections["aims"]["main_aim"]]
    texts += [p["text"] for p in sections["background"]["paragraphs"]]
    texts += [s["text"] for s in sections["methods"]["subsections"]]
    return texts


def cited_numbers(texts):
    numbers = set()
    for text in texts:
        for group in CITATION.findall(text):
            numbers.update(int(n) for n in group.split(", "))
    return numbers


@pytest.fixture
def fa_result(fake_llm, offline_pubmed, now):
    store = MemoryStore()
    result = run(ProposalRequest(title=FA_TITLE), store, fake_llm, offline_pubmed, now, RunOptions())
    return result, store


def test_every_section_is_produced(fa_result):
    result, store = fa_result
    assert result["status"] == "done"
    assert set(result["sections"]) == {"introduction", "aims", "background", "methods"}
    assert all(s["status"] == "done" for s in result["sections"].values())
    assert result["sections"]["introduction"]["source_origin"] == "api"
    assert len(result["sections"]["methods"]["subsections"]) == 5
    assert result["design"]["source"] == "router" and result["design"]["label"] == "cross_sectional"
    assert store.result == result


def test_citations_resolve_and_every_reference_is_cited(fa_result):
    result, _ = fa_result
    texts = all_texts(result)
    assert not any(re.search(r"\{[A-Z]\d+\}", t) for t in texts)  # no token left
    numbers = cited_numbers(texts)
    references = [r["number"] for r in result["references"]]
    assert references == list(range(1, len(references) + 1))
    assert numbers == set(references)
    assert result["unknown_tokens"] == []


def test_first_citation_is_the_introduction(fa_result):
    result, _ = fa_result
    first = CITATION.search(result["sections"]["introduction"]["text"])
    assert first.group(1).startswith("1")
    assert "StatPearls" in result["references"][0]["text"]


def test_methods_33_states_the_computed_number(fa_result):
    result, _ = fa_result
    methods = result["sections"]["methods"]
    assert str(methods["sample_size"]["final_total"]) in methods["subsections"][2]["text"]


def test_usage_and_calls_are_recorded(fa_result):
    result, store = fa_result
    assert result["usage"]["calls"] == len(store.calls) > 20
    assert result["usage"]["cost_usd"] is None
    steps = {c["step"] for c in store.calls}
    assert {"profile", "screening", "design_router", "methods_notes", "aims", "intro_problem"} <= steps


def test_english_and_section_subset(fake_llm, offline_pubmed, now):
    request = ProposalRequest(title="Prevalence of vitamin D deficiency among pregnant women", language="en",
                              sections=["background", "aims"], design="cross sectional")  # fmt: skip
    result = run(request, MemoryStore(), fake_llm, offline_pubmed, now, RunOptions())
    assert set(result["sections"]) == {"background", "aims"} and result["status"] == "done"
    assert result["design"]["source"] == "user"


class Broken(FakeBackend):
    def __init__(self, broken_steps):
        super().__init__()
        self.broken = broken_steps

    def call(self, messages, tier, schema, step=""):
        if step.split(":")[0] in self.broken:
            raise ValueError(f"{step} is broken")
        return super().call(messages, tier, schema, step)


def broken_llm(knowledge, steps):
    llm = LLM(LLMConfig(provider="fake"), knowledge, Broken(steps))
    llm.retry_sleep = 0
    return llm


def test_a_failed_section_does_not_stop_the_others(knowledge, offline_pubmed, now):
    store = MemoryStore()
    result = run(ProposalRequest(title=FA_TITLE), store, broken_llm(knowledge, {"aims"}), offline_pubmed, now)
    assert result["status"] == "done_with_errors"
    assert result["sections"]["aims"]["status"] == "failed" and "broken" in result["sections"]["aims"]["error"]
    assert result["sections"]["methods"]["status"] == "done"
    assert any(s["status"] == "failed" for s in store.steps)


def test_empty_section_is_never_done(knowledge, offline_pubmed, now):
    result = run(ProposalRequest(title=FA_TITLE, sections=["background"]), MemoryStore(),
                 broken_llm(knowledge, {"background_paragraph"}), offline_pubmed, now)  # fmt: skip
    assert result["sections"]["background"]["status"] == "failed"
    assert result["status"] == "failed"


def test_profile_failure_fails_the_job(knowledge, offline_pubmed, now):
    result = run(ProposalRequest(title=FA_TITLE), MemoryStore(), broken_llm(knowledge, {"profile"}), offline_pubmed,
                 now)  # fmt: skip
    assert result["status"] == "failed"
    assert all(s["status"] == "failed" for s in result["sections"].values())
