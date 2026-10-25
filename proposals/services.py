"""Glue between Django and the engine, shared by the API, the web pages and the worker."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from django.conf import settings

from engine.docx_export import build_docx
from engine.knowledge import Knowledge, get_knowledge
from engine.llm import LLM, make_llm
from engine.pubmed import PubMed, make_pubmed
from engine.schemas import SECTION_KEYS, ProposalRequest
from proposals.models import Paper, Proposal, StepLog

TITLE_MIN, TITLE_MAX = 5, 300
PURPOSE_MAX = 2000
HTML_MAX_BYTES = 3 * 1024 * 1024


class InputError(ValueError):
    """A request that breaks the input limits."""


def knowledge() -> Knowledge:
    return get_knowledge()


@lru_cache(maxsize=1)
def engine_dependencies() -> tuple[LLM, PubMed]:
    """The model client and PubMed client, built once per process from the environment."""
    return make_llm(get_knowledge()), make_pubmed()


def validate_input(data: dict[str, Any]) -> dict[str, Any]:
    """Apply the limits (title 5–300 characters, purpose ≤ 2,000, HTML ≤ 3 MB) and normalise the fields."""
    title = (data.get("title") or "").strip()
    if not TITLE_MIN <= len(title) <= TITLE_MAX:
        raise InputError(f"The title must be {TITLE_MIN}–{TITLE_MAX} characters long.")
    purpose = (data.get("purpose") or "").strip()
    if len(purpose) > PURPOSE_MAX:
        raise InputError(f"The purpose must be at most {PURPOSE_MAX} characters long.")
    html = data.get("statpearls_html") or ""
    if len(html.encode("utf-8")) > HTML_MAX_BYTES:
        raise InputError("The uploaded HTML page must be at most 3 MB.")
    language = data.get("language") or "fa"
    if language not in ("fa", "en"):
        raise InputError("The language must be 'fa' or 'en'.")
    sections = [s for s in (data.get("sections") or []) if s]
    unknown = [s for s in sections if s not in SECTION_KEYS]
    if unknown:
        raise InputError(f"Unknown section(s): {', '.join(unknown)}. Use {', '.join(SECTION_KEYS)}.")
    design = (data.get("design") or "").strip()[:100]
    other = (data.get("design_other_name") or "").strip()[:100]
    if design.lower() == "other" and not other:
        raise InputError("Give the design's name when the design is 'other'.")
    return {
        "title": title,
        "purpose": purpose,
        "design_input": design,
        "design_other_name": other,
        "language": language,
        "sections": [s for s in SECTION_KEYS if s in sections] or list(SECTION_KEYS),
        "uploaded_html": html,
        "client_ref": (data.get("client_ref") or "").strip()[:100],
    }


def create_proposal(data: dict[str, Any]) -> Proposal:
    """Validate and queue a new proposal (the worker picks it up)."""
    return Proposal.objects.create(**validate_input(data))


def engine_request(proposal: Proposal) -> ProposalRequest:
    return ProposalRequest(
        title=proposal.title,
        purpose=proposal.purpose,
        design=proposal.design_input,
        design_other_name=proposal.design_other_name,
        language=proposal.language,
        sections=proposal.sections or list(SECTION_KEYS),
        statpearls_html=proposal.uploaded_html,
    )


def steps_of(proposal: Proposal) -> list[dict[str, Any]]:
    """Progress events as the user sees them."""
    return [
        {"step": s.step, "status": s.status, "message": s.message, "at": s.created_at.isoformat()}
        for s in StepLog.objects.filter(proposal=proposal).order_by("id")
    ]


def selected_papers(proposal: Proposal) -> list[dict[str, Any]]:
    papers = Paper.objects.filter(proposal=proposal, selected=True).order_by("role", "id")
    return [
        {
            "role": p.role,
            "token": p.token,
            "pmid": p.pmid,
            "title": p.title,
            "journal": p.journal,
            "year": p.year,
            "scores": p.scores,
            "flags": p.flags,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{p.pmid}/" if p.pmid else "",
        }
        for p in papers
    ]


def stage_counts(proposal: Proposal) -> dict[str, int]:
    """How many background records reached each stage (for "How this was made")."""
    background = Paper.objects.filter(proposal=proposal, role="background")
    return {
        "retrieved": background.count(),
        "screened": background.exclude(stage="retrieved").count(),
        "selected": background.filter(selected=True).count(),
        "methods_papers": Paper.objects.filter(proposal=proposal, role="methods").count(),
        "intro_sources": Paper.objects.filter(proposal=proposal, role="intro").count(),
    }


def calls_of(proposal: Proposal) -> list[dict[str, Any]]:
    return [
        {"step": c.step, "model": c.model, "status": c.status, "prompt_tokens": c.prompt_tokens,
         "completion_tokens": c.completion_tokens, "latency_ms": c.latency_ms, "cost_usd": c.cost_usd}
        for c in proposal.llm_calls.order_by("id")
    ]  # fmt: skip


def proposal_payload(proposal: Proposal, trace: bool = False) -> dict[str, Any]:
    """What the API and the web page show: status, steps, warnings and, when finished, the results."""
    payload: dict[str, Any] = {
        "id": str(proposal.id),
        "client_ref": proposal.client_ref,
        "title": proposal.title,
        "language": proposal.language,
        "sections_requested": proposal.sections,
        "status": proposal.status,
        "current_step": proposal.current_step,
        "created_at": proposal.created_at.isoformat(),
        "started_at": proposal.started_at.isoformat() if proposal.started_at else None,
        "finished_at": proposal.finished_at.isoformat() if proposal.finished_at else None,
        "error": proposal.error,
        "steps": steps_of(proposal),
        "warnings": proposal.warnings,
    }
    if proposal.is_finished and proposal.result and not proposal.result.get("partial"):
        result = proposal.result
        payload.update(
            {
                "profile": result.get("profile"),
                "design": result.get("design"),
                "sections": result.get("sections", {}),
                "references": result.get("references", []),
                "papers": selected_papers(proposal),
                "usage": proposal.usage,
            }
        )
    if trace:
        payload["trace"] = {
            "searches": proposal.searches,
            "stages": stage_counts(proposal),
            "calls": calls_of(proposal),
            "cost_shown": settings_cost_shown(),
        }
    return payload


def settings_cost_shown() -> bool:
    llm, _ = engine_dependencies()
    return llm.config.price_input_per_m is not None and llm.config.price_output_per_m is not None


def docx_bytes(proposal: Proposal) -> bytes:
    return build_docx(proposal.result or {})


def docx_filename(proposal: Proposal) -> str:
    return f"proposal-{str(proposal.id)[:8]}.docx"


def is_debug() -> bool:
    return bool(settings.DEBUG)
