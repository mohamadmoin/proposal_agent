"""The introduction: parts 1–2 from StatPearls (strong model), parts 3–4 from the background (small model).

Follows knowledge/introduction.md. Each part sees only its own sources and cites them with tokens.
"""

from __future__ import annotations

from typing import Any

from engine.background import BackgroundResult
from engine.knowledge import DesignChoice, Knowledge
from engine.llm import LLM
from engine.pubmed import PubMed
from engine.retrieval import profile_query_text
from engine.schemas import ProposalRequest, StudyProfileOut
from engine.statpearls import IntroSource, gather_sources
from engine.store import Store
from engine.verify import missing_numbers

STEP = "introduction"


def source_block(source: IntroSource) -> str:
    record = source.record
    kind = "StatPearls chapter" if source.kind == "chapter" else "paper cited by the chapter"
    return (
        f'<source token="{source.token}">\n'
        f"Type: {kind}\n"
        f"Title: {record.title}\n"
        f"Year: {record.year or 'not given'}\n"
        f"Text: {source.text}\n"
        f"</source>"
    )


def studies_block(background: BackgroundResult | None) -> str:
    if not background or not background.selected:
        return "none"
    blocks = []
    for candidate in background.selected:
        screening = candidate.screening
        blocks.append(
            f'<paper token="{candidate.token}">\n'
            f"Title: {candidate.record.title}\n"
            f"Year: {candidate.record.year or 'not given'}\n"
            f"Aim: {screening.get('aim', '')}\n"
            f"Main finding: {screening.get('main_finding', '')}\n"
            f"</paper>"
        )
    return "\n\n".join(blocks)


def run_introduction(
    request: ProposalRequest,
    profile: StudyProfileOut,
    design: DesignChoice,
    knowledge: Knowledge,
    llm: LLM,
    pubmed: PubMed,
    store: Store,
    background: BackgroundResult | None,
) -> dict[str, Any]:
    """Find the sources, write parts 1–2 and 3–4, and return the introduction payload."""
    lang = request.language
    store.step_started("introduction_sources", "Finding the StatPearls chapter and its cited papers")
    sources, origin = gather_sources(
        pubmed, profile.statpearls_terms, profile_query_text(profile), request.statpearls_html, store
    )
    for source in sources:
        store.save_paper("intro", source.record, token=source.token, selected=True, stage="selected",
                         flags=[source.kind])  # fmt: skip
    store.step_done("introduction_sources", f"{len(sources)} sources ({origin})")

    flags: list[str] = []
    part_12 = ""
    if sources:
        store.step_started("introduction_problem", "Writing the problem and the possible solution")
        prompt = llm.prose_prompt(
            "intro_problem",
            introduction_rules=knowledge.text("introduction"),
            study_title=request.title,
            english_title=profile.english_title,
            purpose=request.purpose or "not given",
            sources="\n\n".join(source_block(s) for s in sources),
            language=lang,
        )
        part_12 = llm.complete_text("intro_problem", prompt, "strong")
        unverified = missing_numbers(part_12, "\n".join(f"{s.record.title}\n{s.text}" for s in sources))
        if unverified:
            flags.append("numbers_unverified")
            store.warn("Introduction parts 1–2 contain numbers not found in the sources; check them: "
                       + ", ".join(unverified[:10]) + ".", STEP)
        store.step_done("introduction_problem")
    else:
        store.warn("No StatPearls chapter was found, so the introduction covers only similar studies and the gap.",
                   STEP)  # fmt: skip

    store.step_started("introduction_studies", "Writing similar studies, the gap and the rationale")
    prompt = llm.prose_prompt(
        "intro_studies",
        introduction_rules=knowledge.text("introduction"),
        study_title=request.title,
        english_title=profile.english_title,
        design_name=design.name(knowledge, lang),
        purpose=request.purpose or "not given",
        papers=studies_block(background),
        language=lang,
    )
    part_34 = llm.complete_text("intro_studies", prompt, "small")
    store.step_done("introduction_studies")
    return {
        "text": "\n\n".join(p.strip() for p in (part_12, part_34) if p.strip()),
        "source_origin": origin,
        "sources": [
            {
                "token": s.token,
                "pmid": s.record.pmid,
                "title": s.record.title,
                "kind": s.kind,
                "nbk": s.record.book_accession,
            }
            for s in sources
        ],  # fmt: skip
        "flags": flags,
        "source_records": {s.token: s.record for s in sources},
    }
