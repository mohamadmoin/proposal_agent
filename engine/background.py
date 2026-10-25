"""The background (پیشینه): search → cut → screen → verify → select → one paragraph per study.

Follows knowledge/background_checklist.md. The model screens and writes; the code searches, verifies every claim
against the abstract, selects, and owns the citation tokens.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from engine import prompts
from engine.knowledge import DesignChoice, Knowledge
from engine.llm import LLM, describe_error
from engine.pubmed import PubMed
from engine.retrieval import (
    MIN_UNION,
    WIDE_RETMAX,
    Candidate,
    Lane,
    Ranked,
    build_lanes,
    cut,
    profile_query_text,
    select,
)
from engine.schemas import PaperRecord, ScreeningBatchOut, ScreeningItemOut, StudyProfileOut
from engine.store import Store
from engine.verify import (
    TOKEN_RE,
    evidence_text,
    missing_numbers,
    numbered_sentences,
    split_sentences,
    valid_evidence,
)

BATCH_SIZE = 5
STEP = "background"


@dataclass
class BackgroundResult:
    """Everything the later steps need from the background."""

    candidates: list[Candidate] = field(default_factory=list)  # every screened paper
    selected: list[Candidate] = field(default_factory=list)  # in score order, with tokens P1, P2, …
    paragraphs: list[dict[str, Any]] = field(default_factory=list)

    def by_token(self) -> dict[str, Candidate]:
        return {c.token: c for c in self.selected}


# --- shared text helpers (also used by methods and introduction) --------------------------------------------------


def paper_block(record: PaperRecord, token: str = "", numbered: bool = False) -> str:
    """A paper as the model sees it: metadata, then the abstract (optionally as numbered sentences)."""
    attr = f'token="{token}"' if token else f'pmid="{record.pmid}"'
    abstract = numbered_sentences(record.abstract)[0] if numbered else record.abstract
    return (
        f"<paper {attr}>\n"
        f"Title: {record.title}\n"
        f"Journal: {record.journal}\n"
        f"Year: {record.year or 'not given'}\n"
        f"Publication types: {', '.join(record.publication_types) or 'not given'}\n"
        f"Abstract: {abstract}\n"
        f"</paper>"
    )


def keep_only_token_at_end(text: str, token: str) -> str:
    """Remove every citation token, then put this paper's token once at the very end."""
    body = TOKEN_RE.sub("", text)
    body = re.sub(r"[ \t]+([.,;:،؛])", r"\1", body)
    body = re.sub(r"[ \t]{2,}", " ", body).strip()
    return f"{body} {{{token}}}"


def write_checked(
    llm: LLM, step: str, prompt: str, source: str, allowed: set[str] | None = None, tier: str = "small"
) -> tuple[str, list[str]]:
    """Write text whose numbers must all be in `source`; retry once with a reminder, then flag it.

    Returns (text, missing numbers after the retry). An empty list means every number was found.
    """
    text = llm.complete_text(step, prompt, tier)
    missing = missing_numbers(text, source, allowed)
    if not missing:
        return text, []
    reminder = llm.prose_prompt("reminder_numbers", missing=", ".join(missing), previous=text)
    text = llm.complete_text(f"{step}:retry", prompt + "\n\n" + reminder, tier)
    return text, missing_numbers(text, source, allowed)


# --- search and cut -----------------------------------------------------------------------------------------------


def run_lanes(
    pubmed: PubMed, lanes: list[Lane], store: Store, plain_query: str
) -> tuple[dict[str, list[str]], list[str]]:
    """Run each lane; a failing lane is logged and skipped. Returns (lane → PMIDs, union in first-seen order)."""
    hits: dict[str, list[str]] = {}
    for lane in lanes:
        try:
            result = pubmed.esearch(lane.query, retmax=lane.retmax, sort="relevance")
        except Exception as exc:  # one broken lane must not stop the others
            store.log_search(STEP, lane.name, lane.query, 0, [], error=str(exc))
            store.warn(f"PubMed search lane '{lane.name}' failed: {describe_error(exc)}", STEP)
            continue
        store.log_search(STEP, lane.name, lane.query, result.count, result.pmids)
        if lane.name == "core" and not result.pmids:
            store.warn("The core PubMed query returned no records; the other lanes were used.", STEP)
        hits[lane.name] = result.pmids
    union = list(dict.fromkeys(p for pmids in hits.values() for p in pmids))
    if len(union) < MIN_UNION and plain_query:
        wide = Lane("plain_wide", plain_query, WIDE_RETMAX)
        try:
            result = pubmed.esearch(wide.query, retmax=wide.retmax)
            store.log_search(STEP, wide.name, wide.query, result.count, result.pmids)
            hits[wide.name] = result.pmids
            union = list(dict.fromkeys(union + result.pmids))
        except Exception as exc:
            store.log_search(STEP, wide.name, wide.query, 0, [], error=str(exc))
    return hits, union


def search_and_cut(
    profile: StudyProfileOut,
    pubmed_filter: str,
    pubmed: PubMed,
    store: Store,
    now_year: int,
    screen_top: int,
) -> list[Ranked]:
    """Lanes → efetch → drop retracted/short → TF-IDF + RRF → the best `screen_top` records."""
    lanes = build_lanes(profile, pubmed_filter, now_year)
    hits, union = run_lanes(pubmed, lanes, store, profile.plain_query)
    records = pubmed.efetch(union) if union else []
    kept, dropped = cut(records, hits, profile_query_text(profile), screen_top)
    for record, reason in dropped:
        lanes_of = [name for name, pmids in hits.items() if record.pmid in pmids]
        store.save_paper("background", record, lanes=lanes_of, stage="retrieved", flags=[reason])
    return kept


# --- screening ----------------------------------------------------------------------------------------------------


def screen(
    ranked: list[Ranked],
    profile: StudyProfileOut,
    design: DesignChoice,
    knowledge: Knowledge,
    llm: LLM,
    lang: str,
) -> dict[str, ScreeningItemOut]:
    """Screen in batches of 5, in parallel. Returns PMID → the model's screening item."""
    batches = [ranked[i : i + BATCH_SIZE] for i in range(0, len(ranked), BATCH_SIZE)]
    labels = ", ".join(sorted(knowledge.designs)) + ", other"

    def screen_batch(batch: list[Ranked]) -> ScreeningBatchOut:
        # Screening returns data, not proposal prose, so the style rules are not appended.
        user = prompts.render(
            "screening",
            background_checklist=knowledge.text("background_checklist"),
            profile_json=profile.model_dump_json(indent=1),
            design_name=design.name(knowledge, "en"),
            design_label=design.label,
            design_labels=labels,
            papers="\n\n".join(paper_block(r.record, numbered=True) for r in batch),
        )
        return llm.complete_json("screening", ScreeningBatchOut, user, "small")

    items: dict[str, ScreeningItemOut] = {}
    for result in llm.map(screen_batch, batches):
        if isinstance(result, Exception):
            continue
        for item in result.papers:
            items.setdefault(item.pmid.strip(), item)
    return items


def clamp(value: int | None, low: int = 0, high: int = 3) -> int:
    return max(low, min(high, int(value or 0)))


def verify_screening(record: PaperRecord, item: ScreeningItemOut | None) -> tuple[dict[str, Any], list[str]]:
    """Check the model's screening record against the abstract. Returns (verified record, flags).

    - Evidence sentence numbers must exist, otherwise aim_similarity is capped at 1.
    - Every number in main_finding must be in the abstract, otherwise main_finding becomes the evidence sentences
      (the model's text is kept for audit).
    """
    if item is None:
        return {"aim_similarity": 0, "population_similarity": 0, "design_match": False}, ["not_screened"]
    sentences = split_sentences(record.abstract)
    data = item.model_dump()
    flags: list[str] = []
    data["aim_similarity"] = clamp(item.aim_similarity)
    data["population_similarity"] = clamp(item.population_similarity)
    evidence_ok = valid_evidence(item.evidence, len(sentences))
    if not evidence_ok:
        flags.append("evidence_invalid")
        data["aim_similarity"] = min(data["aim_similarity"], 1)
    if missing_numbers(item.main_finding, record.abstract):
        flags.append("finding_replaced")
        data["model_main_finding"] = item.main_finding
        data["main_finding"] = evidence_text(item.evidence, sentences) if evidence_ok else ""
    data["evidence_sentences"] = evidence_text(item.evidence, sentences) if evidence_ok else ""
    return data, flags


# --- writing ------------------------------------------------------------------------------------------------------


def write_paragraph(
    candidate: Candidate, study_title: str, knowledge: Knowledge, llm: LLM, lang: str
) -> dict[str, Any]:
    """One paragraph from this paper only. Numbers must be in the title or abstract (year and n allowed)."""
    record = candidate.record
    screening = {k: v for k, v in candidate.screening.items() if k != "model_main_finding"}
    prompt = llm.prose_prompt(
        "background_paragraph",
        study_title=study_title,
        background_checklist=knowledge.text("background_checklist"),
        token="{" + candidate.token + "}",
        token_id=candidate.token,
        paper_title=record.title,
        first_author=record.first_author_last_name or "not given",
        year=record.year or "not given",
        journal=record.journal or "not given",
        publication_types=", ".join(record.publication_types) or "not given",
        abstract=record.abstract,
        screening_json=json.dumps(screening, ensure_ascii=False, indent=1),
        language=lang,
    )
    allowed = {str(record.year)} if record.year else set()
    if candidate.screening.get("sample_size"):
        allowed.add(str(candidate.screening["sample_size"]))
    text, missing = write_checked(llm, "background_paragraph", prompt, f"{record.title}\n{record.abstract}", allowed)
    flags = ["numbers_unverified"] if missing else []
    return {
        "token": candidate.token,
        "pmid": record.pmid,
        "text": keep_only_token_at_end(text, candidate.token),
        "flags": flags,
        "missing_numbers": missing,
    }


# --- the whole background -----------------------------------------------------------------------------------------


def run_background(
    profile: StudyProfileOut,
    design: DesignChoice,
    knowledge: Knowledge,
    llm: LLM,
    pubmed: PubMed,
    store: Store,
    now_year: int,
    lang: str,
    study_title: str,
    max_papers: int = 8,
    screen_top: int = 40,
    write: bool = True,
) -> BackgroundResult:
    """Find, screen, select and (when `write`) write the background studies."""
    store.step_started("background_search", "Searching PubMed for similar studies")
    pubmed_filter = knowledge.design(design.label).pubmed_filter
    ranked = search_and_cut(profile, pubmed_filter, pubmed, store, now_year, screen_top)
    store.step_done("background_search", f"{len(ranked)} records kept for screening")

    store.step_started("background_screening", f"Screening {len(ranked)} abstracts")
    items = screen(ranked, profile, design, knowledge, llm, lang)
    candidates = []
    for item in ranked:
        verified, flags = verify_screening(item.record, items.get(item.record.pmid))
        candidates.append(
            Candidate(item.record, verified, lanes=item.lanes, tfidf=item.tfidf, rrf=item.rrf, flags=flags)
        )
    if ranked and not items:
        raise RuntimeError("screening failed for every batch")
    store.step_done("background_screening", f"{len(items)} of {len(ranked)} abstracts screened")

    selection = select(candidates, now_year, max_papers)
    for number, candidate in enumerate(selection.selected, start=1):
        candidate.token = f"P{number}"
    if selection.warning:
        store.warn(selection.warning, "background")
    result = BackgroundResult(candidates=candidates, selected=selection.selected)
    save_candidates(store, candidates, {})  # the chosen studies are visible while their paragraphs are written

    if write and selection.selected:
        store.step_started("background_writing", f"Writing {len(selection.selected)} paragraphs")
        outputs = llm.map(lambda c: write_paragraph(c, study_title, knowledge, llm, lang), selection.selected)
        for candidate, output in zip(selection.selected, outputs, strict=True):
            if isinstance(output, Exception):
                store.warn(f"The paragraph for PMID {candidate.record.pmid} could not be written: {describe_error(output)}", STEP)
                continue
            if output["flags"]:
                candidate.flags.extend(output["flags"])
                store.warn(
                    f"Paragraph {candidate.token} (PMID {candidate.record.pmid}): numbers unverified "
                    f"({', '.join(output['missing_numbers'])}); check them against the abstract.",
                    STEP,
                )
            result.paragraphs.append(output)
        store.step_done("background_writing", f"{len(result.paragraphs)} paragraphs written")

    save_candidates(store, [c for c in candidates if c.selected], {p["token"]: p["text"] for p in result.paragraphs})
    return result


def save_candidates(store: Store, candidates: list[Candidate], paragraphs: dict[str, str]) -> None:
    """Store every screened paper (stores upsert by role + PMID, so saving again adds the paragraphs)."""
    for candidate in candidates:
        store.save_paper(
            "background",
            candidate.record,
            token=candidate.token,
            lanes=sorted(candidate.lanes),
            scores={"tfidf": round(candidate.tfidf, 4), "rrf": round(candidate.rrf, 5), "score": candidate.score},
            screening=candidate.screening,
            selected=candidate.selected,
            paragraph=paragraphs.get(candidate.token, ""),
            flags=candidate.flags,
            stage="selected" if candidate.selected else "screened",
        )
