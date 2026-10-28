"""Run the agentic engine and return the same result contract as engine.pipeline.run (AGT-5).

pipeline.run() dispatches here when PROPOSAL_ENGINE=agentic. The lead agent runs once with the working memory in its
deps; code then assembles the sections, numbers the references with the pipeline's own finish() and adds the agent's
trace under result["agent"]. A budget overrun or a model failure keeps whatever was submitted and fails the rest
honestly (status done_with_errors / failed), as the pipeline does.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart
from pydantic_ai.usage import UsageLimits

from engine import pipeline
from engine.agentic import tools as T
from engine.agentic.lead import build_critic, build_lead
from engine.agentic.models import AgentModels, build_models
from engine.agentic.state import ResearchState
from engine.agentic.workers import build_workers
from engine.knowledge import Knowledge, get_knowledge
from engine.pubmed import PubMed
from engine.schemas import PaperRecord, ProposalRequest
from engine.store import Store
from engine.titles import title

log = logging.getLogger(__name__)

T_TOKEN = re.compile(r"\{(P\d+)\}")
LIMITS = UsageLimits(request_limit=75, tool_calls_limit=140, total_tokens_limit=1_100_000)  # S3: +25% for the checks
PLAN = [("define", "Define the study"), ("design", "Choose the design"), ("literature", "Find similar studies"),
        ("evidence", "Gather the numbers"), ("sample_size", "Compute the sample size"), ("aims", "Write the aims"),
        ("methods", "Write the methods"), ("background", "Write the background"),
        ("introduction", "Write the introduction"), ("review", "Methodology review and fixes")]


class MeteredStore:
    """The engine Store, with every model call also counted for result["usage"]."""

    def __init__(self, store: Store, meter: pipeline.UsageMeter):
        self._store, self._meter = store, meter

    def log_call(self, record: dict[str, Any]) -> None:
        self._meter(record)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


def run_agentic(
    request: ProposalRequest,
    store: Store,
    pubmed: PubMed,
    now: datetime,
    options: pipeline.RunOptions | None = None,
    knowledge: Knowledge | None = None,
    env: Mapping[str, str] | None = None,
    models: AgentModels | None = None,
    services: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """`services` may carry host capabilities: "fulltext" = fetch(pmcid) -> FullText dict (open-access full text)."""
    knowledge = knowledge or get_knowledge()
    options = options or pipeline.RunOptions.from_env()
    env = os.environ if env is None else env
    meter = pipeline.UsageMeter(store)
    metered = MeteredStore(store, meter)
    state = ResearchState(metered, request.language)
    state.set_plan(PLAN)
    models = models or build_models(env)
    workers = build_workers(models.worker, knowledge, models.names.get("worker", "worker"),
                            max_parallel=int(env.get("AGENT_WORKER_PARALLEL") or 8))
    deps = T.AgentDeps(state=state, request=request, pubmed=pubmed, knowledge=knowledge, workers=workers, now=now,
                       options=options, critic=build_critic(models.critic or models.lead),
                       critic_name=models.names.get("lead", "lead"), fulltext=(services or {}).get("fulltext"))
    lead = build_lead(models.lead, request, knowledge, now)
    lead_name = models.names.get("lead", "lead")
    store.step_started("agent", "The lead researcher is planning the proposal")
    started = time.monotonic()
    report, error = None, ""
    with capture_run_messages() as messages:
        try:
            result = lead.run_sync(f"Write the research proposal for: {request.title}", deps=deps,
                                   usage_limits=LIMITS)
            report = result.output
        except Exception as exc:  # budget, provider or validation failure: keep what was submitted
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
            log.warning("agentic run stopped: %s", error)
            state.warn(f"The agent stopped before finishing: {pipeline.describe_error(exc)}", "agent")
    rejections = [{"tool": part.tool_name or "output", "message": str(part.content)[:500]}
                  for msg in messages for part in getattr(msg, "parts", []) if isinstance(part, RetryPromptPart)]
    # the lead's moves in order (tool and a short view of its arguments): shows loops and wasted steps
    moves = [f"{part.tool_name}({str(part.args)[:80]})" for msg in messages if isinstance(msg, ModelResponse)
             for part in msg.parts if isinstance(part, ToolCallPart)]
    for msg in messages:
        if isinstance(msg, ModelResponse):
            meter({"step": "agent:lead", "tier": "strong", "model": msg.model_name or lead_name, "status": "ok",
                   "prompt_tokens": msg.usage.input_tokens, "completion_tokens": msg.usage.output_tokens,
                   "cached_tokens": msg.usage.cache_read_tokens})
    if report is not None:
        state.log("review", "finished")
    elif "UsageLimitExceeded" in error:
        state.log("review", "stopped_budget")
    state.close_plan(ok=report is not None or all(k in state.drafts for k in ("aims", "methods", "introduction")))
    if error:
        store.step_failed("agent", error)
    else:
        store.step_done("agent", f"finished in {round(time.monotonic() - started)} s")
    sections, sources = assemble(deps)
    extra = {"agent": {**state.to_json(), "report": report.model_dump() if report else None, "error": error,
                       "rejections": rejections, "moves": moves,
                       "models": models.names, "seconds": round(time.monotonic() - started, 1)}}
    return pipeline.finish(request, store, sections, sources, meter, now, deps.profile,
                           (deps.design, deps.design_record) if deps.design else None, extra=extra)


def needs_sample_size(deps: T.AgentDeps) -> bool:
    return bool(deps.design and deps.knowledge.design(deps.design.label).sample_size)


def assemble(deps: T.AgentDeps) -> tuple[dict[str, dict[str, Any]], dict[str, PaperRecord]]:
    """Build the contract's sections from the submitted drafts; a missing section is failed with its reason."""
    lang, state, request = deps.lang, deps.state, deps.request
    drafts = state.drafts
    sources = {token: paper.record for token, paper in state.papers.items()}
    sections: dict[str, dict[str, Any]] = {}

    def failed(key: str, reason: str) -> dict[str, Any]:
        return pipeline.failed(key, lang, reason)

    if request.wants("introduction"):
        if "introduction" in drafts:
            text = drafts["introduction"]["text"]
            cited = sorted(set(T_TOKEN.findall(text)), key=lambda t: int(t[1:]))
            sections["introduction"] = {
                "status": "done", "title": title("introduction", lang), "text": text, "source_origin": "agent",
                "sources": [{"token": t, "pmid": state.papers[t].record.pmid, "title": state.papers[t].record.title,
                             "kind": "paper", "nbk": ""} for t in cited if t in state.papers],
                "flags": []}
        else:
            sections["introduction"] = failed("introduction", "the agent did not submit an introduction")
    if request.wants("aims"):
        if "aims" in drafts:
            a = drafts["aims"]
            sections["aims"] = {"status": "done", "title": title("aims", lang), "main_aim": a["main_aim"],
                                "secondary_aims": a["secondary_aims"], "hypotheses": a["hypotheses"],
                                "questions": a["questions"], "practical_aims": a["practical_aims"], "flags": []}
        else:
            sections["aims"] = failed("aims", "the agent did not submit the aims")
    if request.wants("background"):
        paras = sorted(deps.background_paragraphs.values(), key=lambda p: int(p["token"][1:]))
        if paras:
            papers = [p for p in state.papers.values() if p.role == "background" and p.selected]
            sections["background"] = {
                "status": "done", "title": title("background", lang), "paragraphs": paras,
                "papers": [{"token": p.token, "pmid": p.record.pmid, "title": p.record.title, "year": p.record.year,
                            "journal": p.record.journal, "score": p.screening.get("score"),
                            "aim_similarity": p.screening.get("aim_similarity"),
                            "population_similarity": p.screening.get("population_similarity"),
                            "design_match": p.screening.get("design_match"), "flags": p.screening.get("flags", [])}
                           for p in papers]}
        else:
            sections["background"] = failed("background", "the agent did not write the background paragraphs")
    if request.wants("methods"):
        if "methods" in drafts:
            m = drafts["methods"]
            by_id = {s["id"]: s["text"] for s in m["subsections"]}
            subsections = [{"id": sid, "title": deps.knowledge.section_title(sid, lang), "text": by_id.get(sid, ""),
                            "flags": []} for sid in deps.knowledge.section_ids()]
            sections["methods"] = {
                "status": "done", "title": title("methods", lang), "design": deps.design_record, "flags": [],
                "topics": [], "notes": {"variables": m["variables"],
                                        "decisions": [{"key": d.key, "value": d.value, "rationale": d.rationale}
                                                      for d in state.decisions.values()]},
                "sample_size": deps.sample_size,
                # no error for designs without a formula (e.g. a systematic review); checks read "" as not applicable
                "sample_size_error": "" if deps.sample_size or not needs_sample_size(deps) else "not computed",
                "subsections": subsections, "variables": m["variables"], "ethics": m.get("ethics", []),
                "timeline": m.get("timeline", []), "duration_months": m.get("duration_months")}
        else:
            sections["methods"] = failed("methods", "the agent did not submit the methods")
    return sections, sources

