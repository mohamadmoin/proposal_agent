"""The lead researcher's tools (AGT-3). Facts, retrieval and arithmetic are code; judgement is the lead's.

Every tool returns a small JSON-able result plus `workspace` (the digest of the working memory), so the lead always
sees its plan, decisions, evidence ids and open sub-tasks. Bad arguments raise ModelRetry with a concrete fix; other
problems come back as data (`ok: false`) and never crash the run.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry, RunContext

from engine import prompts, retrieval, samplesize
from engine.agentic.fulltext import FullTextFetch, build_passage
from engine.agentic.state import ResearchState
from engine.agentic.workers import EXTRACT_PROMPT, FULLTEXT_PROMPT, SUPPORT_PROMPT, Workers, delegate, gather_limited
from engine.background import paper_block, search_and_cut, verify_screening
from engine.knowledge import GENERAL, DesignChoice, Knowledge, normalise_label
from engine.methods import decision_record
from engine.pipeline import RunOptions
from engine.pubmed import PubMed
from engine.schemas import PaperRecord, ProposalRequest, StudyProfileOut
from engine.verify import contains_number, missing_numbers, split_sentences

SCREEN_BATCH = 5
DESIGN_GUIDES = {"cross_sectional": "design_cross_sectional", "cohort": "design_cohort",
                 "case_control": "design_case_control", "randomized_controlled_trial": "design_rct",
                 "quasi_experimental": "design_quasi_experimental",
                 "systematic_review_or_meta_analysis": "design_systematic_review",
                 "diagnostic_accuracy": "design_diagnostic"}
EVIDENCE_TOP = 15  # records screened for one evidence search
TOKEN_RE = re.compile(r"\{(P\d+)\}")
MIN_ASSUMPTION_REASON = 15  # characters: an assumption must be explained, not just labelled


@dataclass
class AgentDeps:
    state: ResearchState
    request: ProposalRequest
    pubmed: PubMed
    knowledge: Knowledge
    workers: Workers
    now: datetime
    options: RunOptions = field(default_factory=RunOptions)
    profile: StudyProfileOut | None = None
    design: DesignChoice | None = None
    design_record: dict[str, Any] | None = None
    sample_size: dict[str, Any] | None = None
    background_paragraphs: dict[str, dict[str, Any]] = field(default_factory=dict)
    critic: Any = None  # the consistency critic agent (AGT-11), built by run.py
    critic_name: str = ""
    fulltext: FullTextFetch | None = None  # open-access full text by PMCID, injected by the host (AGT-14)
    fulltext_cache: dict[str, Any] = field(default_factory=dict)
    claim_issues: list[dict[str, Any]] = field(default_factory=list)  # the last claim check of the introduction
    idle_reads: int = 0  # consecutive read-only calls with no change to the working memory (stall guard)
    last_progress: tuple[Any, ...] = ()

    @property
    def lang(self) -> str:
        return self.request.language


def with_workspace(ctx: RunContext[AgentDeps], payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "workspace": ctx.deps.state.digest()}


# ---------------------------------------------------------------- claim support (AGT-16)

SUPPORT_ABSTRACT_CHARS = 2500  # per cited paper in the checker's prompt


def support_source(state: ResearchState, token: str) -> str:
    """What the checker may use for one paper: its title, abstract and verified full-text values."""
    paper = state.papers[token]
    extra = [f"[full text {e.locator}] {e.sentence}" for e in state.evidence.values()
             if e.token == token and e.verified and e.source == "full_text"]
    return (f"{{{token}}} {paper.record.title}\n{paper.record.abstract[:SUPPORT_ABSTRACT_CHARS]}"
            + ("\n" + "\n".join(extra[:8]) if extra else ""))


async def check_support(ctx: RunContext[AgentDeps], sentences: list[str], summary: str) -> list[dict[str, Any]]:
    """Have the cheap checker judge each cited sentence against the papers it cites. Returns the partial and
    unsupported ones as {sentence, tokens, verdict, reason}; [] when there is no checker or the check failed (it only
    advises: the lead or the writer decides)."""
    deps = ctx.deps
    state = deps.state
    if deps.workers.support_checker is None:
        return []
    cited = [(s, [t for t in TOKEN_RE.findall(s) if t in state.papers]) for s in sentences]
    cited = [(s, toks) for s, toks in cited if toks]
    if not cited:
        return []
    tokens = sorted({t for _, toks in cited for t in toks}, key=lambda t: int(t[1:]))
    prompt = SUPPORT_PROMPT.format(sources="\n\n".join(support_source(state, t) for t in tokens),
                                   sentences="\n".join(f"{i + 1}. {s}" for i, (s, _) in enumerate(cited)))
    checker = deps.workers.support_checker
    out = await delegate(state, ctx.usage, "check_support", summary, lambda: checker.run(prompt),
                         lambda o: f"{sum(v.verdict != 'supported' for v in o.items)} of {len(o.items)} flagged",
                         deps.workers.model_name)
    if out is None:
        return []
    flagged = []
    for v in out.items:
        if v.verdict != "supported" and 1 <= v.sentence <= len(cited):
            sentence, toks = cited[v.sentence - 1]
            flagged.append({"sentence": sentence, "tokens": toks, "verdict": v.verdict, "reason": v.reason})
    return flagged


# ---------------------------------------------------------------- duplicate studies (AGT-19)

REGISTRY = re.compile(r"\b(IRCT\d{6,}N\d+|NCT\d{8}|ISRCTN\d{6,}|ACTRN\d{10,})\b", re.I)


def possible_duplicates(state: ResearchState, tokens: list[str]) -> list[list[str]]:
    """Pairs of papers that probably report the same trial or sample: the same registry id, or the same first author
    and the same sample size."""
    pairs = []
    for i, a in enumerate(tokens):
        for b in tokens[i + 1:]:
            pa, pb = state.papers[a], state.papers[b]
            reg_a = {m.upper() for m in REGISTRY.findall(pa.record.abstract or "")}
            reg_b = {m.upper() for m in REGISTRY.findall(pb.record.abstract or "")}
            same_author = (pa.record.first_author_last_name or "").lower() == (
                pb.record.first_author_last_name or "x").lower()
            n_a, n_b = pa.screening.get("sample_size"), pb.screening.get("sample_size")
            if (reg_a & reg_b) or (same_author and n_a and n_a == n_b):
                pairs.append([a, b])
    return pairs


# ---------------------------------------------------------------- memory tools


IDLE_READS = 6  # read-only calls in a row without progress before the lead is told its next step


def idle_note(ctx: RunContext[AgentDeps]) -> str:
    """Stall guard (S3 gate, case 2 spent its whole request budget without progress): after IDLE_READS read-only calls
    in a row with no change to the working memory, name the next plan step. Empty otherwise."""
    deps, state = ctx.deps, ctx.deps.state
    progress = (state.version, len(state.papers), len(state.evidence), len(state.tasks), len(state.decisions),
                tuple(p.status for p in state.plan))
    deps.idle_reads = deps.idle_reads + 1 if progress == deps.last_progress else 1
    deps.last_progress = progress
    if deps.idle_reads < IDLE_READS:
        return ""
    step = next((p for p in state.plan if p.status != "done"), None)
    target = f"«{step.id}: {step.title}»" if step else "finish"
    return (f"You have read the workspace {deps.idle_reads} times in a row without acting. You have what you need: "
            f"take the next step now ({target}) with its tool.")


def get_workspace(ctx: RunContext[AgentDeps]) -> str:
    """Show the working memory: plan with status, decisions, kept papers, evidence ids, sub-tasks, submitted sections.
    Use it whenever you are unsure what is done or what comes next."""
    note = idle_note(ctx)
    return ctx.deps.state.digest() + (f"\n\nNOTE: {note}" if note else "")


def update_plan(ctx: RunContext[AgentDeps], item_id: str, status: Literal["todo", "doing", "done", "blocked"],
                note: str = "", title: str = "") -> dict[str, Any]:
    """Create or update one item of your plan. Mark an item `doing` when you start it and `done` (with a one-line
    note of the outcome) when it is finished; `blocked` with the reason when you cannot complete it."""
    state = ctx.deps.state
    if title and not any(p.id == item_id for p in state.plan):
        state.set_plan([(p.id, p.title) for p in state.plan] + [(item_id, title)])
    state.update_plan(item_id, status, note)
    return with_workspace(ctx, {"ok": True})


def record_decision(ctx: RunContext[AgentDeps], key: str, value: str, rationale: str,
                    evidence: list[str] | None = None) -> dict[str, Any]:
    """Record a research decision (e.g. primary_outcome, sampling_method, analysis, setting) with its rationale and
    the evidence ids (E#/P#) that support it. Later sections must stay consistent with recorded decisions."""
    unknown = [e for e in evidence or [] if e not in ctx.deps.state.evidence and e not in ctx.deps.state.papers]
    if unknown:
        raise ModelRetry(f"Unknown evidence ids {unknown}; use ids shown in the workspace or none.")
    ctx.deps.state.record_decision(key, value, rationale, evidence)
    return with_workspace(ctx, {"ok": True})


# ---------------------------------------------------------------- study definition and design


def define_study(ctx: RunContext[AgentDeps], profile: StudyProfileOut) -> dict[str, Any]:
    """Define the study from the title (PICO and the PubMed queries). `core_query` must be valid PubMed syntax with
    MeSH and [tiab] terms joined by AND/OR; `plain_query` a few plain words; `concepts` at most 5; all in English."""
    if not profile.core_query.strip():
        raise ModelRetry("core_query is empty: write a PubMed query for the study's population AND main concept.")
    if profile.core_query.count("(") != profile.core_query.count(")"):
        raise ModelRetry("core_query has unbalanced parentheses.")
    profile.concepts = profile.concepts[:5]
    ctx.deps.profile = profile
    ctx.deps.state.store.save_profile(profile.model_dump())
    ctx.deps.state.record_decision("study", profile.english_title, f"{profile.population} | {profile.outcomes}")
    ctx.deps.state.log("define", "defined", title=profile.english_title[:160], population=profile.population[:120],
                       outcome=profile.outcomes[:120], query=profile.core_query[:240])
    ctx.deps.state.update_plan("define", "done", profile.english_title[:80])
    return with_workspace(ctx, {"ok": True})


def choose_design(ctx: RunContext[AgentDeps], label: str, rationale: str, other_name: str = "",
                  missing_information: list[str] | None = None) -> dict[str, Any]:
    """Choose the study design. `label` is one of the design labels listed in your instructions (or 'other' with
    `other_name`). Returns the design's allowed sample-size formulas and the instructions for each methods
    sub-section; follow them when you write the methods."""
    deps = ctx.deps
    knowledge = deps.knowledge
    norm = normalise_label(label)
    if norm in knowledge.designs and norm != GENERAL:
        choice = DesignChoice(norm, "")
    elif norm in ("other", GENERAL) and other_name.strip():
        choice = DesignChoice(GENERAL, other_name.strip())
    else:
        labels = ", ".join(sorted(k for k in knowledge.designs if k != GENERAL))
        raise ModelRetry(f"Unknown design label {label!r}. Use one of: {labels}, or 'other' with other_name.")
    source = "user" if deps.request.design else "agent"
    record = decision_record(knowledge, choice, source=source, rationale=rationale,
                             missing_information=missing_information or [])
    deps.design, deps.design_record = choice, record
    deps.state.store.save_design(record)
    deps.state.record_decision("design", choice.label if choice.label != GENERAL else choice.other_name, rationale)
    deps.state.log("design", "design", label=choice.label, name_fa=choice.name(knowledge, "fa"),
                   name_en=choice.name(knowledge, "en"), source=source, rationale=rationale[:200])
    deps.state.update_plan("design", "done", choice.name(knowledge, "en"))
    design = knowledge.design(choice.label)
    lang = deps.lang
    guides_dir = Path(__file__).resolve().parents[2] / "knowledge" / "guides"
    guide_name = DESIGN_GUIDES.get(choice.label)
    guides = {name: (guides_dir / f"{name}.md").read_text(encoding="utf-8")
              for name in filter(None, [guide_name, "sample_size_principles"]) if (guides_dir / f"{name}.md").exists()}
    return with_workspace(ctx, {
        "guides": guides,  # the design guide and the sample-size principles: follow them
        "design": choice.name(knowledge, lang),
        # each allowed formula with its parameter names, so the first compute_sample_size call can be right
        "allowed_sample_size_formulas": {
            f: {"required": list(samplesize.FORMULAS[f].required), "optional": list(samplesize.FORMULAS[f].optional),
                "formula": samplesize.FORMULAS[f].expression} for f in design.sample_size},
        "methods_subsections": {sid: {"title": knowledge.section_title(sid, lang),
                                      "instructions": design.sections[sid][lang].replace("{design_name}",
                                                                                         choice.name(knowledge, lang))}
                                for sid in knowledge.section_ids()},
    })


# ---------------------------------------------------------------- literature


def _brief(record: PaperRecord, screening: dict[str, Any]) -> dict[str, Any]:
    return {"pmid": record.pmid, "year": record.year, "title": record.title[:160],
            "design": screening.get("design"), "n": screening.get("sample_size"), "country": screening.get("country"),
            "aim_similarity": screening.get("aim_similarity"), "population_similarity": screening.get("population_similarity"),
            "design_match": screening.get("design_match"), "main_finding": (screening.get("main_finding") or "")[:300]}


async def _screen(ctx: RunContext[AgentDeps], records: list[PaperRecord]) -> dict[str, dict[str, Any]]:
    """Screen records in batches on the worker model; code verifies each verdict against its abstract."""
    deps = ctx.deps
    knowledge = deps.knowledge
    labels = ", ".join(sorted(knowledge.designs)) + ", other"
    batches = [records[i: i + SCREEN_BATCH] for i in range(0, len(records), SCREEN_BATCH)]

    def job(batch: list[PaperRecord]):
        prompt = prompts.render(
            "screening", background_checklist=knowledge.text("background_checklist"),
            profile_json=deps.profile.model_dump_json(indent=1),
            design_name=deps.design.name(knowledge, "en"), design_label=deps.design.label, design_labels=labels,
            papers="\n\n".join(paper_block(r, numbered=True) for r in batch))
        return lambda: delegate(deps.state, ctx.usage, "screen", f"screen {len(batch)} papers",
                                lambda: deps.workers.screener.run(prompt), lambda o: f"{len(o.papers)} verdicts",
                                deps.workers.model_name)

    outputs = await gather_limited([job(b) for b in batches], deps.workers.max_parallel)
    items = {item.pmid.strip(): item for out in outputs if out for item in out.papers}
    verified: dict[str, dict[str, Any]] = {}
    for record in records:
        data, flags = verify_screening(record, items.get(record.pmid))
        data["flags"] = flags
        verified[record.pmid] = data
    return verified


async def find_studies(ctx: RunContext[AgentDeps],
                       purpose: Literal["background", "evidence", "introduction"] = "background",
                       query: str = "", need: str = "") -> dict[str, Any]:
    """Search PubMed and screen the results for relevance to THIS study.

    purpose="background": the strictly similar studies for the background (uses your study definition and design;
      call once, after define_study and choose_design).
    purpose="evidence": a targeted search for numbers you need (e.g. a baseline prevalence, an SD, an event rate for
      the sample size); give `query` in PubMed syntax and `need` in plain words. Only similar papers are kept.
    purpose="introduction": sources for the problem statement (burden, epidemiology, consequences, Iranian or local
      data, guidelines, systematic reviews); give `query` and `need`. Papers on the same topic are kept even when
      their design differs.
    Returns compact verdicts (no abstracts) with paper ids P#; use extract_data on the ones you need."""
    deps = ctx.deps
    if deps.profile is None or deps.design is None:
        raise ModelRetry("Call define_study and choose_design before find_studies.")
    state, store = deps.state, deps.state.store
    now_year = deps.now.year
    step = {"background": "literature", "evidence": "evidence", "introduction": "introduction"}[purpose]
    state.log(step, "searching", purpose=purpose, query=(query or deps.profile.core_query)[:240], need=need[:140])
    if purpose == "background":
        design = deps.knowledge.design(deps.design.label)
        ranked = await asyncio.to_thread(search_and_cut, deps.profile, design.pubmed_filter, deps.pubmed, store,
                                         now_year, deps.options.screen_top)
        records = [r.record for r in ranked]
        lanes = {r.record.pmid: r.lanes for r in ranked}
    else:
        if not query.strip():
            raise ModelRetry("purpose='evidence' needs a PubMed `query`.")
        try:
            result = await asyncio.to_thread(deps.pubmed.esearch, query, 40)
            fetched = await asyncio.to_thread(deps.pubmed.efetch, result.pmids) if result.pmids else []
        except Exception as exc:
            store.log_search("agent:evidence", "evidence", query, 0, [], error=str(exc))
            return with_workspace(ctx, {"ok": False, "error": f"PubMed search failed: {exc}",
                                        "hint": "Try a simpler query or continue with what you have."})
        store.log_search("agent:evidence", "evidence", query, result.count, result.pmids)
        state.log(step, "search", purpose=purpose, query=query[:240], count=result.count)
        kept, _ = retrieval.cut(fetched, {"evidence": result.pmids}, f"{query} {need}", EVIDENCE_TOP)
        records = [r.record for r in kept]
        lanes = {r.record.pmid: r.lanes for r in kept}
    if not records:
        return with_workspace(ctx, {"ok": False, "error": "no usable PubMed records",
                                    "hint": "Broaden the query (fewer AND terms, synonyms) and try again once."})
    verdicts = await _screen(ctx, records)
    role = {"background": "background", "evidence": "methods", "introduction": "intro"}[purpose]
    candidates = []
    for record in records:
        cand = retrieval.Candidate(record=record, screening=verdicts[record.pmid], lanes=lanes.get(record.pmid, {}))
        candidates.append(cand)
    if purpose == "background":
        selection = retrieval.select(candidates, now_year, deps.options.max_papers)
        chosen = selection.selected
        if selection.warning:
            state.warn(selection.warning, "background")
    elif purpose == "evidence":
        chosen = [c for c in candidates if c.aim >= 2 and c.population >= 1][:6]
    else:  # introduction: same topic is enough (aim ≥ 1), best first
        chosen = sorted((c for c in candidates if c.aim >= 1), key=lambda c: (-c.aim, -c.population))[:8]
    out = []
    for cand in chosen:
        paper = state.add_paper(role, cand.record, lanes=list(cand.lanes), stage="selected")
        state.screen(paper.token, cand.screening, selected=True)
        out.append({"id": paper.token, **_brief(cand.record, cand.screening)})
    state.log(step, "screened", purpose=purpose, screened=len(records), kept=len(out))
    for o in out:
        state.log(step, "paper", purpose=purpose, token=o["id"], pmid=o["pmid"], title=o["title"], year=o["year"],
                  design=o.get("design"), n=o.get("n"), country=o.get("country"), aim=o.get("aim_similarity"),
                  population=o.get("population_similarity"))
    if purpose == "background":
        state.update_plan("literature", "done", f"{len(out)} similar studies kept")
    return with_workspace(ctx, {"ok": True, "purpose": purpose, "screened": len(records), "kept": out})


async def extract_data(ctx: RunContext[AgentDeps], paper_ids: list[str], quantities: list[str]) -> dict[str, Any]:
    """Extract numbers you need (rates, proportions, means, SDs, effect sizes, sample sizes) from kept papers (P#).
    The abstract is read first; when a quantity is missing and the paper is open access, its full text (results,
    methods, tables) is read too. Each value is checked in code against the sentence or table row it came from and
    stored as an evidence item E# with that text. Use E# ids in compute_sample_size and cite the paper P# in the
    text. Ask only for what you will use."""
    deps = ctx.deps
    state = deps.state
    unknown = [p for p in paper_ids if p not in state.papers]
    if unknown:
        raise ModelRetry(f"Unknown paper ids {unknown}; use P# ids from the workspace.")
    if not quantities:
        raise ModelRetry("List the quantities you need, e.g. ['prevalence of X', 'SD of Y'].")

    state.log("evidence", "extracting", papers=len(paper_ids), quantities=[q[:60] for q in quantities[:4]])

    def job(pid: str):
        record = state.papers[pid].record
        prompt = EXTRACT_PROMPT.format(quantities="\n".join(f"- {q}" for q in quantities),
                                       paper=paper_block(record, numbered=True))
        return lambda: delegate(state, ctx.usage, "extract", f"extract {len(quantities)} quantities from {pid}",
                                lambda: deps.workers.extractor.run(prompt), lambda o: f"{len(o.items)} values",
                                deps.workers.model_name)

    outputs = await gather_limited([job(p) for p in paper_ids], deps.workers.max_parallel)
    stored, rejected = [], 0
    for pid, out in zip(paper_ids, outputs, strict=True):
        if not out:
            continue
        paper = state.papers[pid]
        sentences = split_sentences(paper.record.abstract)
        for item in out.items:
            sentence = sentences[item.sentence - 1] if 1 <= item.sentence <= len(sentences) else ""
            if not sentence or not contains_number(sentence, item.value):
                rejected += 1
                continue
            ev = state.add_evidence(token=pid, pmid=paper.record.pmid, quantity=item.quantity, value=item.value,
                                    unit=item.unit, group=item.group, population=item.population,
                                    sentence=sentence, verified=True)
            stored.append({"id": ev.id, "paper": pid, "quantity": ev.quantity, "value": ev.value, "unit": ev.unit,
                           "group": ev.group, "population": ev.population})
    # second stage: full text for papers that did not give every requested quantity (AGT-14)
    found_in = {s["paper"] for s in stored}
    need_ft = [p for p in paper_ids if deps.fulltext and state.papers[p].record.pmcid
               and (p not in found_in or len([s for s in stored if s["paper"] == p]) < len(quantities))]
    ft_stored, ft_rejected, ft_read = await _extract_full_texts(ctx, need_ft, quantities)
    for s_ in stored + ft_stored:
        state.log("evidence", "value", token=s_["paper"], quantity=str(s_["quantity"])[:90], value=s_["value"],
                  unit=s_["unit"], group=str(s_.get("group") or "")[:40],
                  source="full_text" if s_.get("from") else "abstract",
                  locator=(s_.get("from") or "").removeprefix("full text "))
    if rejected + ft_rejected:
        state.log("evidence", "rejected", count=rejected + ft_rejected)
    state.update_plan("evidence", "done", f"{len(state.evidence)} values")
    return with_workspace(ctx, {"ok": True, "stored": stored + ft_stored,
                                "rejected_unverifiable": rejected + ft_rejected,
                                "full_texts_read": ft_read})


async def _fetch_full_text(deps: AgentDeps, pmcid: str) -> dict[str, Any]:
    if pmcid not in deps.fulltext_cache:
        try:
            deps.fulltext_cache[pmcid] = await asyncio.to_thread(deps.fulltext, pmcid) or {}
        except Exception as exc:  # no open access, fetch or parse error: the abstract stays the source
            deps.state.store.log_search("agent:fulltext", "fulltext", pmcid, 0, [], error=str(exc)[:200])
            deps.fulltext_cache[pmcid] = {}
    return deps.fulltext_cache[pmcid]


async def _extract_full_texts(ctx: RunContext[AgentDeps], paper_ids: list[str],
                              quantities: list[str]) -> tuple[list[dict[str, Any]], int, list[str]]:
    deps, state = ctx.deps, ctx.deps.state
    if not paper_ids or deps.workers.fulltext_extractor is None:
        return [], 0, []
    passages = {}
    for pid in paper_ids:
        ft = await _fetch_full_text(deps, state.papers[pid].record.pmcid)
        passage = build_passage(ft)
        if passage:
            passages[pid] = passage

    def job(pid: str):
        record = state.papers[pid].record
        prompt = FULLTEXT_PROMPT.format(quantities="\n".join(f"- {q}" for q in quantities), title=record.title,
                                        passage=passages[pid].text)
        return lambda: delegate(state, ctx.usage, "read_full_text", f"full text of {pid} ({record.pmcid})",
                                lambda: deps.workers.fulltext_extractor.run(prompt), lambda o: f"{len(o.items)} values",
                                deps.workers.model_name)

    ids = list(passages)
    for pid in ids:
        state.log("evidence", "full_text", token=pid, pmcid=state.papers[pid].record.pmcid,
                  tables=len({k.split(".")[0] for k in passages[pid].lines if k.startswith("T")}))
    outputs = await gather_limited([job(p) for p in ids], deps.workers.max_parallel)
    stored, rejected = [], 0
    for pid, out in zip(ids, outputs, strict=True):
        if not out:
            continue
        paper = state.papers[pid]
        for item in out.items:
            line = passages[pid].lines.get(item.locator.strip())
            if not line or not contains_number(line, item.value):
                rejected += 1
                continue
            ev = state.add_evidence(token=pid, pmid=paper.record.pmid, quantity=item.quantity, value=item.value,
                                    unit=item.unit, group=item.group, population=item.population, sentence=line,
                                    verified=True, source="full_text", locator=item.locator.strip())
            stored.append({"id": ev.id, "paper": pid, "quantity": ev.quantity, "value": ev.value, "unit": ev.unit,
                           "group": ev.group, "population": ev.population, "from": f"full text {ev.locator}"})
    return stored, rejected, ids


def get_evidence(ctx: RunContext[AgentDeps], ids: list[str]) -> dict[str, Any]:
    """Show stored evidence items (E#) with their verbatim source sentence, or kept papers (P#) with their verdict."""
    state = ctx.deps.state
    out: dict[str, Any] = {}
    for i in ids:
        if i in state.evidence:
            e = state.evidence[i]
            out[i] = {"paper": e.token, "quantity": e.quantity, "value": e.value, "unit": e.unit, "group": e.group,
                      "population": e.population, "sentence": e.sentence, "source": e.source, "locator": e.locator}
        elif i in state.papers:
            p = state.papers[i]
            out[i] = _brief(p.record, p.screening)
        else:
            out[i] = "unknown id"
    note = idle_note(ctx)
    if note:
        out["NOTE"] = note
    return out


# ---------------------------------------------------------------- sample size


class ParamValue(BaseModel):
    evidence: str | None = Field(None, description="An evidence id E# that holds this value")
    value: float | None = Field(None, description="The value, when it is a convention or an assumption")
    reason: str = Field("", description="Why this value: required for an assumption (no evidence id), and for an "
                                       "evidence value whose source population differs from this study's")


def _param_value(ctx: RunContext[AgentDeps], name: str, spec: ParamValue) -> tuple[float, str]:
    """(value, source) where source is '{P#}' for evidence, 'convention' or 'assumption'."""
    state = ctx.deps.state
    if spec.evidence:
        ev = state.evidence.get(spec.evidence)
        if ev is None:
            raise ModelRetry(f"{name}: unknown evidence id {spec.evidence}.")
        if not ev.verified:
            raise ModelRetry(f"{name}: {spec.evidence} is not verified in its source; extract it again or use an "
                             "explained assumption.")
        paper = state.papers.get(ev.token)
        similarity = int((paper.screening if paper else {}).get("population_similarity") or 0)
        if similarity < 2 and len(spec.reason.strip()) < 30:
            raise ModelRetry(f"{name}: {spec.evidence} comes from {ev.token}, whose population is not similar to this "
                             f"study (similarity {similarity}/3: {ev.population or 'population not stated'}). Use a "
                             "value from a comparable population or a pooled estimate, or explain in `reason` why "
                             "this value still applies to this study's population.")
        value = ev.value
        if name in samplesize.PROPORTION_PARAMS and (ev.unit.strip() == "%" or value > 1):
            value = value / 100
        return value, "{" + ev.token + "}"
    if spec.value is None:
        raise ModelRetry(f"{name}: give an evidence id or a value.")
    if name in samplesize.CONVENTIONS and spec.value == samplesize.CONVENTIONS[name]:
        return spec.value, "convention"
    if len(spec.reason.strip()) < MIN_ASSUMPTION_REASON:
        raise ModelRetry(f"{name}={spec.value} has no evidence id: explain in `reason` why this value is a sound "
                         "assumption (e.g. the conservative p=0.5, or a clinically important difference).")
    return spec.value, "assumption"


def compute_sample_size(ctx: RunContext[AgentDeps], formula: str, params: dict[str, ParamValue],
                        expected_recruitment: int, attrition: float | None = None,
                        design_effect: float | None = None, design_effect_reason: str = "",
                        combine_justification: str = "", reasoning: str = "") -> dict[str, Any]:
    """Compute the sample size in code. Never compute it yourself.

    - formula: one of the design's allowed formulas (from choose_design).
    - params: each parameter either from an evidence id {"evidence": "E3"} or {"value": 0.5, "reason": "..."}.
      A two-group contrast (p1/p2, or p0 with odds_ratio) should come from ONE study that compared those groups; values
      from different studies need `combine_justification`.
    - expected_recruitment: how many eligible participants the setting can realistically provide in the study
      period (your estimate); the result is checked against it.
    - attrition: expected loss (0–0.5); design_effect: only for cluster or multistage sampling (1–10), with reason.
    Returns n, the worked calculation to copy into the sample-size subsection, and any feasibility problem."""
    deps = ctx.deps
    if deps.design is None:
        raise ModelRetry("Choose the design first.")
    allowed = list(deps.knowledge.design(deps.design.label).sample_size)
    if formula not in allowed:
        raise ModelRetry(f"Formula {formula!r} is not allowed for this design; use one of {allowed}.")
    values: dict[str, float] = {}
    sources: dict[str, str] = {}
    for name, spec in params.items():
        values[name], sources[name] = _param_value(ctx, name, spec)
    pairs = [("p1", "p2"), ("p0", "odds_ratio")]  # contrasts that must come from one comparative study
    for a, b in pairs:
        sa, sb = sources.get(a, ""), sources.get(b, "")
        if sa.startswith("{") and sb.startswith("{") and sa != sb and len(combine_justification.strip()) < 30:
            raise ModelRetry(f"{a} comes from {sa} and {b} from {sb}: a contrast should come from one study that "
                             "compared those groups. Use one study, or explain in combine_justification why combining "
                             "two studies is valid for this population.")
    if design_effect and design_effect > 1 and len(design_effect_reason.strip()) < MIN_ASSUMPTION_REASON:
        raise ModelRetry("design_effect needs design_effect_reason (the cluster sampling it corrects for).")
    try:
        result = samplesize.compute(formula, values, rate=attrition, design_effect=design_effect)
    except samplesize.SampleSizeError as exc:
        raise ModelRetry(f"The calculation failed: {exc}") from exc
    worked = samplesize.worked_calculation(result, sources, deps.lang)
    feasibility = ""
    if expected_recruitment and result.final_total > expected_recruitment:
        feasibility = (f"n = {result.final_total} exceeds the expected recruitment of {expected_recruitment}. Revise "
                       "the parameters (a smaller precision is not allowed without reason), the primary outcome, the "
                       "setting or the period, or record a decision 'sample_size_feasibility' explaining how it "
                       "will be reached.")
        deps.state.warn(feasibility, "sample_size")
    deps.sample_size = {
        "formula": result.formula, "params": result.params, "sources": sources, "rate": result.rate,
        "rate_source": "assumption" if result.rate else "", "raw_n1": round(result.raw_n1, 2), "n1": result.n1,
        "n2": result.n2, "final_n1": result.final_n1, "final_n2": result.final_n2,
        "final_total": result.final_total, "extra": result.extra, "worked": worked, "reasoning": reasoning,
        "notes": [feasibility] if feasibility else [], "expected_recruitment": expected_recruitment,
        "design_effect_reason": design_effect_reason, "combine_justification": combine_justification,
    }
    deps.state.bump("sample_size")
    deps.state.log("sample_size", "computed", formula=formula,
                   formula_fa=samplesize.FORMULAS[formula].label.get("fa"), formula_en=samplesize.FORMULAS[formula].label.get("en"),
                   n=result.final_total, per_group=result.final_n1 if result.n2 is not None else None,
                   params={k: (f"{v:g}" + (f" {sources[k]}" if sources.get(k, "").startswith("{") else ""))
                           for k, v in result.params.items()},
                   feasibility=bool(feasibility))
    deps.state.update_plan("sample_size", "done" if not feasibility else "doing", f"n={result.final_total}")
    deps.state.record_decision("sample_size", f"{formula}: n={result.final_total}", reasoning,
                               [s.strip("{}") for s in sources.values() if s.startswith("{")])
    return with_workspace(ctx, {"ok": not feasibility, "final_total": result.final_total, "n_per_group": result.n1
                                if result.n2 is not None else None, "worked_calculation": worked,
                                "feasibility_problem": feasibility})


# ---------------------------------------------------------------- one-study paragraphs (cheap writer)


async def write_study_paragraphs(ctx: RunContext[AgentDeps], paper_ids: list[str],
                                 drop: list[str] | None = None) -> dict[str, Any]:
    """Write the background (پیشینه) paragraphs: one paragraph per selected study, by the worker model, each checked
    in code so that every number is in that study's abstract, and checked against the abstract for misread claims.
    Call once with the background papers you keep. `drop` removes paragraphs already written, e.g. one of two papers
    reported as possible_duplicates (the same trial or sample)."""
    deps = ctx.deps
    state = deps.state
    for token in drop or []:
        deps.background_paragraphs.pop(token, None)
    unknown = [p for p in paper_ids if p not in state.papers]
    if unknown:
        raise ModelRetry(f"Unknown paper ids {unknown}.")
    if paper_ids:
        state.log("background", "writing", papers=len(paper_ids))
    title = deps.request.title

    async def write_one(pid: str) -> dict[str, Any]:
        paper = state.papers[pid]
        record = paper.record
        screening = {k: v for k, v in paper.screening.items() if k not in ("model_main_finding", "flags")}
        prompt = prompts.render(
            "background_paragraph", study_title=title,
            background_checklist=deps.knowledge.text("background_checklist"), token="{" + pid + "}", token_id=pid,
            paper_title=record.title, first_author=record.first_author_last_name or "not given",
            year=record.year or "not given", journal=record.journal or "not given",
            publication_types=", ".join(record.publication_types) or "not given", abstract=record.abstract,
            screening_json=str(screening), language=deps.lang,
        ) + "\n\n<writing_rules>\n" + deps.knowledge.style_rules + "\n</writing_rules>"
        allowed = {str(record.year)} if record.year else set()
        if paper.screening.get("sample_size"):
            allowed.add(str(paper.screening["sample_size"]))
        source = f"{record.title}\n{record.abstract}"
        text = await delegate(state, ctx.usage, "write_study", f"paragraph for {pid}",
                              lambda: deps.workers.writer.run(prompt), lambda o: f"{len(o)} chars",
                              deps.workers.model_name)
        missing = missing_numbers(text or "", source, allowed)
        if text and missing:
            retry = prompt + "\n\n" + prompts.render("reminder_numbers", missing=", ".join(missing), previous=text)
            text = await delegate(state, ctx.usage, "write_study", f"paragraph for {pid} (numbers retry)",
                                  lambda: deps.workers.writer.run(retry), lambda o: f"{len(o)} chars",
                                  deps.workers.model_name) or text
            missing = missing_numbers(text, source, allowed)
        text = re.sub(r"\{P\d+\}", "", text or "").strip()
        flags = ["numbers_unverified"] if missing else []
        if text:  # misread claims (a prevalence as an accuracy, weekly as daily): one rewrite with the checker's reasons
            issues = await check_support(ctx, [f"{s} {{{pid}}}" for s in split_sentences(text)], f"support of {pid}")
            if issues:
                fix = prompt + "\n\nYour previous paragraph:\n" + text + "\n\nThese sentences do not match the " + \
                    "abstract; correct them and keep the rest:\n" + "\n".join(
                        f"- «{i['sentence'][:200]}»: {i['reason']}" for i in issues)
                again = await delegate(state, ctx.usage, "write_study", f"paragraph for {pid} (support fix)",
                                       lambda: deps.workers.writer.run(fix), lambda o: f"{len(o)} chars",
                                       deps.workers.model_name)
                again = re.sub(r"\{P\d+\}", "", again or "").strip()
                if again and not missing_numbers(again, source, allowed):
                    text = again
                else:
                    flags.append("support_unverified")
        return {"token": pid, "pmid": record.pmid, "text": (text + " {" + pid + "}") if text else "",
                "flags": flags, "missing_numbers": missing}

    results = await gather_limited([lambda p=p: write_one(p) for p in paper_ids], deps.workers.max_parallel)
    for r in results:
        if r["text"]:
            deps.background_paragraphs[r["token"]] = r
    state.save_draft("background", {"paragraphs": list(deps.background_paragraphs)})
    state.update_plan("background", "done", f"{len(deps.background_paragraphs)} paragraphs")
    for r in results:
        state.log("background", "paragraph", token=r["token"], words=len(r["text"].split()) if r["text"] else 0,
                  flagged=r["flags"] or None, failed=not r["text"] or None)
    duplicates = possible_duplicates(state, sorted(deps.background_paragraphs, key=lambda t: int(t[1:])))
    for a, b in duplicates:
        state.warn(f"{a} and {b} may report the same trial or sample", "background")
    return with_workspace(ctx, {"ok": True, "written": [r["token"] for r in results if r["text"]],
                                "flagged": [r["token"] for r in results if r["flags"]],
                                "failed": [r["token"] for r in results if not r["text"]],
                                "possible_duplicates": duplicates})
