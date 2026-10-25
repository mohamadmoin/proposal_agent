"""Automatic quality checks of a finished proposal (used by `manage.py eval_run`, see docs/EVALUATION.md).

1. Citations resolve: every [n] has a reference, every reference is cited, no raw token is left.
2. Evidence is verbatim: each selected study's evidence sentences are in its abstract, and the numbers of its main
   finding are in the abstract.
3. The ۳-۳ number equals the computed one (and the computation reproduces from the stored parameters).
4. Aims, hypotheses and questions match one to one.
5. No requested section is empty.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from engine.samplesize import compute
from engine.verify import contains_number, evidence_text, missing_numbers, split_sentences, valid_evidence

CITATION = re.compile(r"\[(\d+(?:, \d+)*)\]")
RAW_TOKEN = re.compile(r"\{[A-Z]\d+\}")


@dataclass
class Check:
    name: str
    passed: bool | None  # None = not applicable
    detail: str = ""


def document_texts(result: dict[str, Any]) -> list[str]:
    """Every text of the document that may carry citations."""
    sections = result.get("sections", {})
    texts: list[str] = []
    intro = sections.get("introduction", {})
    if intro.get("status") == "done":
        texts.append(intro.get("text", ""))
    aims = sections.get("aims", {})
    if aims.get("status") == "done":
        texts += [aims.get("main_aim", "")] + aims.get("secondary_aims", []) + aims.get("questions", [])
        texts += [h.get("text", "") for h in aims.get("hypotheses", [])] + aims.get("practical_aims", [])
    background = sections.get("background", {})
    if background.get("status") == "done":
        texts += [p.get("text", "") for p in background.get("paragraphs", [])]
    methods = sections.get("methods", {})
    if methods.get("status") == "done":
        texts += [s.get("text", "") for s in methods.get("subsections", [])]
        if methods.get("sample_size"):
            texts.append(methods["sample_size"].get("worked", ""))
    return texts


def check_citations(result: dict[str, Any]) -> Check:
    texts = document_texts(result)
    cited = {int(n) for text in texts for group in CITATION.findall(text) for n in group.split(", ")}
    listed = {r["number"] for r in result.get("references", [])}
    raw = sorted({t for text in texts for t in RAW_TOKEN.findall(text)})
    problems = []
    if cited - listed:
        problems.append(f"citations without a reference: {sorted(cited - listed)}")
    if listed - cited:
        problems.append(f"references never cited: {sorted(listed - cited)}")
    if raw:
        problems.append(f"raw tokens left: {raw}")
    return Check("citations_resolve", not problems, "; ".join(problems) or f"{len(listed)} references, all cited")


def check_evidence(selected_papers: list[dict[str, Any]]) -> Check:
    """`selected_papers`: {"pmid", "abstract", "screening"} for each selected background study."""
    if not selected_papers:
        return Check("evidence_verbatim", None, "no background studies")
    problems = []
    for paper in selected_papers:
        abstract, screening = paper.get("abstract", ""), paper.get("screening") or {}
        sentences = split_sentences(abstract)
        numbers = screening.get("evidence") or []
        evidence = screening.get("evidence_sentences", "")
        if not evidence or not valid_evidence(numbers, len(sentences)):
            problems.append(f"{paper['pmid']}: no valid evidence sentences")
            continue
        if evidence_text(numbers, sentences) != evidence or not all(s in abstract for s in sentences):
            problems.append(f"{paper['pmid']}: evidence not verbatim")
        if missing_numbers(screening.get("main_finding", ""), abstract):
            problems.append(f"{paper['pmid']}: finding numbers not in abstract")
    return Check("evidence_verbatim", not problems, "; ".join(problems) or f"{len(selected_papers)} studies checked")


def check_sample_size(result: dict[str, Any]) -> Check:
    methods = result.get("sections", {}).get("methods", {})
    if methods.get("status") != "done":
        return Check("sample_size_matches", None, "methods not written")
    size = methods.get("sample_size")
    if not size:
        reason = methods.get("sample_size_error") or "design without a sample-size formula"
        return Check("sample_size_matches", None if not methods.get("sample_size_error") else False, reason)
    recomputed = compute(size["formula"], size["params"], size.get("rate")).final_total
    section = next((s for s in methods.get("subsections", []) if s.get("id") == "sample_size"), {})
    stated = contains_number(section.get("text", ""), size["final_total"])
    ok = stated and recomputed == size["final_total"]
    detail = f"computed {size['final_total']} (recomputed {recomputed}); stated in ۳-۳: {'yes' if stated else 'no'}"
    return Check("sample_size_matches", ok, detail)


def check_aims(result: dict[str, Any]) -> Check:
    aims = result.get("sections", {}).get("aims", {})
    if aims.get("status") != "done":
        return Check("aims_one_to_one", None, "aims not written")
    counts = (len(aims.get("secondary_aims", [])), len(aims.get("hypotheses", [])), len(aims.get("questions", [])))
    return Check(
        "aims_one_to_one",
        counts[0] == counts[1] == counts[2] > 0,
        f"aims/hypotheses/questions = {counts[0]}/{counts[1]}/{counts[2]}",
    )


def check_not_empty(result: dict[str, Any], requested: list[str]) -> Check:
    sections = result.get("sections", {})
    empty = [key for key in requested if sections.get(key, {}).get("status") != "done"]
    return Check(
        "no_empty_section", not empty, f"failed or empty: {empty}" if empty else "all requested sections written"
    )


def run_checks(result: dict[str, Any], selected_papers: list[dict[str, Any]], requested: list[str]) -> list[Check]:
    return [
        check_citations(result),
        check_evidence(selected_papers),
        check_sample_size(result),
        check_aims(result),
        check_not_empty(result, requested),
    ]
