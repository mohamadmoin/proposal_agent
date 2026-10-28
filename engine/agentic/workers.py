"""Cheap sub-agents for bulk work whose output code can check (AGT-4).

The lead never sees raw abstracts: screening, data extraction and one-study paragraphs run on the worker model inside
the lead's tools, and come back as small typed results that code verifies against the abstract. Each call is a task
(T#) in the working memory, with its own tokens, and its usage is added to the lead's run (one budget per proposal).
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from engine.agentic.state import ResearchState
from engine.knowledge import Knowledge
from engine.schemas import ScreeningBatchOut

T = TypeVar("T")


class ExtractedQuantity(BaseModel):
    quantity: str = Field(description="What was measured, with its definition or cut-off, e.g. 'vitamin D deficiency "
                                      "(25(OH)D < 20 ng/mL)' or 'surgical site infection within 30 days'")
    value: float = Field(description="The number exactly as written in the sentence (39.5 for '39.5%')")
    unit: str = Field(description="'%', 'mean', 'SD', 'OR', 'RR', 'HR', 'n', 'ng/mL', … as written")
    group: str = Field(description="Which group the value belongs to: 'overall', 'cases', 'mesh repair', …")
    population: str = Field(description="Who was measured (people, setting, country) in a few words")
    sentence: int = Field(description="The number of the abstract sentence that states the value")


class ExtractionOut(BaseModel):
    items: list[ExtractedQuantity]


class FullTextQuantity(BaseModel):
    quantity: str = Field(description="What was measured, with its definition, cut-off and group")
    value: float = Field(description="The number exactly as written in that sentence or table row")
    unit: str = Field(description="'%', 'n', 'mean', 'SD', 'OR', … as written (a table column name often says it)")
    group: str = Field(description="Which group or row the value belongs to")
    population: str = Field(description="Who was measured, in a few words")
    locator: str = Field(description="Where the value is: S<n> for a numbered sentence or T<k>.r<i> for a table row")


class FullTextExtractionOut(BaseModel):
    items: list[FullTextQuantity]


class SupportVerdict(BaseModel):
    sentence: int = Field(description="The sentence number")
    verdict: Literal["supported", "partial", "unsupported"]
    reason: str = Field(description="For partial or unsupported: what the source says instead, in a few words")


class SupportOut(BaseModel):
    items: list[SupportVerdict]


SUPPORT_PROMPT = """Check whether each numbered sentence is supported by the source(s) it cites. Judge support only,
not style or completeness.
- supported: the cited source states this claim (a paraphrase is fine).
- partial: the source supports only part of it, or reports it for a different population, setting, outcome, measure
  or condition (e.g. a study of children cited for adults, a preeclampsia meta-analysis cited for preterm birth, a
  prevalence reported as an accuracy, a weekly dose reported as daily, one city cited for another).
- unsupported: the cited source does not address the claim.
Return one item per sentence.

Sources:
{sources}

Sentences:
{sentences}
"""


FULLTEXT_PROMPT = """Extract the requested quantities from this full-text article. Sentences are numbered {{S1}} {{S2}}
…, and table rows are given as {{T2.r3}} followed by «column: value» cells. Return one item per value that answers a
requested quantity, with its locator. Copy each value exactly as written; never compute, round or convert; if a
quantity is reported only as n and %, return the one that matches the request. Leave out what is not reported.

Requested quantities:
{quantities}

Article: {title}
{passage}
"""


EXTRACT_PROMPT = """Extract the requested quantities from this abstract. The abstract is split into numbered
sentences {{1}} {{2}} …. Return one item per value that answers one of the requested quantities. Copy each value
exactly as written in its sentence; never compute, round or convert. If a requested quantity is not reported, leave it
out. Report the definition or cut-off in `quantity` when the abstract gives one.

Requested quantities:
{quantities}

Paper:
{paper}
"""


@dataclass
class Workers:
    screener: Agent[None, ScreeningBatchOut]
    extractor: Agent[None, ExtractionOut]
    writer: Agent[None, str]
    model_name: str
    max_parallel: int = 4
    fulltext_extractor: Agent[None, FullTextExtractionOut] | None = None
    support_checker: Agent[None, SupportOut] | None = None


def build_workers(model: Model, knowledge: Knowledge, model_name: str, max_parallel: int = 8) -> Workers:
    persona = knowledge.persona
    return Workers(
        screener=Agent(model, output_type=ScreeningBatchOut, instructions=persona, retries=1, name="screener"),
        extractor=Agent(model, output_type=ExtractionOut, instructions=persona, retries=1, name="extractor"),
        writer=Agent(model, output_type=str, instructions=persona, retries=1, name="study_writer"),
        model_name=model_name,
        max_parallel=max_parallel,
        fulltext_extractor=Agent(model, output_type=FullTextExtractionOut, instructions=persona, retries=1,
                                 name="fulltext_extractor"),
        support_checker=Agent(model, output_type=SupportOut, instructions=persona, retries=1, name="support_checker"),
    )


async def delegate(
    state: ResearchState,
    lead_usage: RunUsage | None,
    kind: str,
    summary: str,
    run: Callable[[], Awaitable[Any]],
    describe: Callable[[Any], str],
    model_name: str,
) -> Any:
    """Run one sub-agent job as a task: register it, run it, record its tokens and a compact result.

    Returns the agent's output, or None when the job failed (the failure is in the task list and the trace)."""
    task = state.start_task(kind, summary, model=model_name)
    started = time.monotonic()
    try:
        result = await run()
    except Exception as exc:  # a failed sub-task is data for the lead, never a crash of the run
        state.finish_task(task, f"failed: {type(exc).__name__}: {str(exc)[:200]}", failed=True)
        state.store.log_call({"step": f"agent:{kind}", "tier": "small", "model": model_name, "status": "error",
                              "error": str(exc)[:500], "latency": round(time.monotonic() - started, 2)})
        return None
    usage = result.usage
    if lead_usage is not None:
        # the sub-agent's tokens count against the run's token budget, but not its requests or tool calls: the
        # request limit is the lead's own budget of decisions (case 3 of the S1 gate hit it through sub-agents)
        lead_usage.incr(dataclasses.replace(usage, requests=0, tool_calls=0))
    state.finish_task(task, describe(result.output), usage=usage)
    state.store.log_call({"step": f"agent:{kind}", "tier": "small", "model": model_name, "status": "ok",
                          "prompt_tokens": usage.input_tokens, "completion_tokens": usage.output_tokens,
                          "cached_tokens": usage.cache_read_tokens,
                          "latency": round(time.monotonic() - started, 2)})
    return result.output


async def gather_limited(jobs: list[Callable[[], Awaitable[T]]], limit: int) -> list[T]:  # noqa: UP047 (the Academy runs Python 3.11)
    """Run jobs concurrently, at most `limit` at a time, keeping their order."""
    semaphore = asyncio.Semaphore(max(1, limit))

    async def one(job: Callable[[], Awaitable[T]]) -> T:
        async with semaphore:
            return await job()

    return list(await asyncio.gather(*(one(j) for j in jobs)))
