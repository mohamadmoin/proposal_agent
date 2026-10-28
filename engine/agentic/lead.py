"""The lead researcher agent (AGT-5): one strong model, one run, with the working memory in `deps`.

The lead owns the plan and every hard decision, and writes aims, methods and introduction with the whole proposal in
view. It cannot submit text that breaks the rules: the submit tools check citations, numbers against the cited
papers, meta-text, the 1:1 aims grammar and the computed sample size, and send concrete fixes back (ModelRetry).
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import jdatetime
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext, Tool, ToolOutput
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models import Model

from engine.agentic import tools as T
from engine.knowledge import GENERAL, Knowledge
from engine.schemas import ProposalRequest
from engine.verify import missing_numbers, normalise_digits

INSTRUCTIONS = Path(__file__).with_name("lead_instructions.md")
TOKEN = re.compile(r"\{(P\d+)\}")
YEAR = re.compile(r"\b(?:19[5-9]\d|20[0-4]\d)\b")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
SENTENCE_END = re.compile(r"(?<=[.!?؟])\s+|\n+")
GUIDES = ("aims_grammar", "methods_checklist", "sample_size", "introduction", "background_checklist",
          "questionnaire", "style_rules")
# sentences about the sources, the tools or the model are not proposal text
META = re.compile(
    r"(منابع?\s*(ارائه|داده|موجود|در دسترس)|فصل\s+StatPearls|StatPearls|این\s+فصل|"
    r"\b(provided|given|available)\s+(sources?|abstracts?|texts?|papers?)\b|\bthe\s+sources?\s+(do|does)\s+not\b|"
    r"\bStatPearls chapter\b|\bas an AI\b|مدل\s+زبانی|هوش\s+مصنوعی|\bprompt\b|\btool\b|دستورالعمل‌های\s+داده)",
    re.I)
LANGUAGE_NAMES = {"fa": "Persian (Farsi)", "en": "English"}
ANALYTIC_DESIGNS = {"cohort", "case_control", "randomized_controlled_trial", "quasi_experimental"}
ASSOCIATION = re.compile(r"ارتباط|رابطه|مقایسه|تأثیر|تاثیر|اثر\s|association|relationship|compar|effect", re.I)
MIN_UNCITED_SHARE = 0.3  # at most 30% of the introduction's sentences may go without a citation (AGT-8)
MIN_SOURCES = 12  # distinct cited papers in the whole proposal (AGT-8; 12 since the S2 gate, AGT-17)
# author names are written only from PubMed metadata (background paragraphs); elsewhere they were seen invented
AUTHORS = re.compile(r"[\w\u0600-\u06FF\u200c-]+\s+و\s+همکاران|\b[A-Z][a-z]+\s+et\s+al\.?", re.U)
# AGT-18: an unfilled template slot («[نام بیمارستان]», [hospital name]) or XXX; a bracketed formula is not one
PLACEHOLDER = re.compile(r"\[[^\]\n]{0,60}(?:[\u0600-\u06FF]|\b(?:name|insert|TBD|hospital|cent(?:er|re))\b)[^\]\n]{0,60}\]"
                         r"|\bX{3,}\b", re.I)
# S3 gate: workspace ids written into the proposal as text, e.g. «(E17)», «[E3, E5]» or «(P12)» (a citation is {P12})
EVIDENCE_IDS = re.compile(r"[(\[]\s*([EP]\d{1,3}(?:\s*[,،]\s*[EP]\d{1,3})*)\s*[)\]]")
BARE_ID = re.compile(r"(?<![{\w])([EP]\d{1,3})(?![}\w])")  # «مطالعه P5», «مستند E21»
# AGT-18: this study's own validity or reliability check reported as already done, in an uncited sentence
VALIDATION = re.compile(r"روایی|پایایی|\bCVR\b|\bCVI\b|\bICC\b|کاپا|\bkappa\b|کرونباخ|Cronbach", re.I)
ALREADY_DONE = re.compile(r"ب(?:ه\s*\u200c?\s*)?دست\s*\u200c?\s*آمد|(?:تأیید|تایید)\s*\u200c?\s*(?:شد|گردید)|"
                          r"محاسبه\s+شد|گزارش\s+شد")
# AGT-15: a Jalali year written as a date (after «سال» or a month name, or in a range); digits normalised first
_MONTHS = "فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|آذر|دی|بهمن|اسفند"
JALALI_YEAR = re.compile(rf"(?<![\u0600-\u06FF])(?:سال(?:\s*\u200c?\s*های)?|{_MONTHS})\s*(?:ماه\s*)?(1[34]\d\d)(?!\d)"
                         r"|(?<![\d.])(1[34]\d\d)\s*(?:-|–|/|تا|الی|لغایت)\s*(?:[\u0600-\u06FF]+\s*)?(1[34]\d\d)(?!\d)")
# a past year is right for a record review or past statistics, when the sentence says so; a search range must end
# this year or later, so it is not exempt
RETROSPECTIVE = re.compile(r"گذشته\s*\u200c?\s*نگر|پرونده|سوابق|بایگانی|ثبت\s*\u200c?\s*شده|آمار|retrospective", re.I)


def jalali_today(now: datetime) -> jdatetime.date:
    return jdatetime.date.fromgregorian(date=now.date())


MONTH_YEAR = re.compile(rf"(?<![\u0600-\u06FF])({_MONTHS})\s*(?:ماه\s*)?(1[34]\d\d)(?!\d)")
MONTH_NUMBER = {m: i + 1 for i, m in enumerate(_MONTHS.split("|"))}
SEARCH = re.compile(r"جست\s*\u200c?\s*و?\s*\u200c?\s*جو|\bsearch", re.I)


def past_months(sentence: str, today: jdatetime.date) -> list[str]:
    """A study period that starts in a month of this year already gone («از تیر ۱۴۰۵ …» written in Aban 1405), or
    whose dates are all in the past. A search range may start in the past."""
    dates = [(int(y), MONTH_NUMBER[m], f"{m} {y}") for m, y in MONTH_YEAR.findall(normalise_digits(sentence))]
    if not dates:
        return []
    now = (today.year, today.month)
    if max(d[:2] for d in dates) < now:
        return [d[2] for d in dates]
    first = min(dates)
    if not SEARCH.search(sentence) and first[0] == today.year and first[:2] < now:
        return [first[2]]
    return []


def past_years(sentence: str, current_year: int, allowed: set[int] = frozenset()) -> list[int]:
    """Jalali years in a date context that are all before the current year (a range ending now is fine). `allowed`
    are years the researcher gave in the title."""
    years = {int(y) for m in JALALI_YEAR.finditer(normalise_digits(sentence)) for y in m.groups() if y} - set(allowed)
    return sorted(years) if years and max(years) < current_year else []


# ---------------------------------------------------------------- submissions


class HypothesisIn(BaseModel):
    text: str
    applicable: bool = Field(description="false for a purely descriptive aim; then text says it is not applicable")


class AimsIn(BaseModel):
    main_aim: str
    secondary_aims: list[str]
    hypotheses: list[HypothesisIn] = Field(description="exactly one per secondary aim, same order")
    questions: list[str] = Field(description="exactly one per secondary aim, same order")
    practical_aims: list[str]


class SubsectionIn(BaseModel):
    id: str = Field(description="one of the methods sub-section ids returned by choose_design")
    text: str


class VariableIn(BaseModel):
    name: str
    role: Literal["independent", "dependent", "background", "confounder", "descriptive"]
    type: Literal["quantitative continuous", "quantitative discrete", "qualitative nominal", "qualitative ordinal"]
    unit_or_categories: str = ""
    definition: str = Field(description="scientific-operational definition")
    measurement: str = Field(description="how and with what instrument it is measured")


class TimelineItem(BaseModel):
    activity: str
    start_month: int = Field(description="first month of this activity (1 = the project's first month)")
    end_month: int


class MethodsIn(BaseModel):
    subsections: list[SubsectionIn]
    variables: list[VariableIn] = Field(description="every variable the aims and the analysis need")
    ethics: list[str] = Field(description="concrete ethical commitments of THIS study (read_guide('ethics')), "
                                          "consistent with the consent and observation methods")
    duration_months: int = Field(description="the whole project duration in months")
    timeline: list[TimelineItem] = Field(description="the project's activities from approval to the final report, "
                                                     "with realistic overlapping months")


class FinishReport(BaseModel):
    summary: str = Field(description="two sentences: what the proposal plans and its key design choices")
    open_issues: list[str] = Field(description="things the researcher must complete or check, e.g. the hospital name")


def last_chance(ctx: RunContext[T.AgentDeps]) -> bool:
    """The tool's final allowed attempt: one more ModelRetry would end the whole run."""
    return getattr(ctx, "retry", 0) >= getattr(ctx, "max_retries", 1 << 30)


def reject(ctx: RunContext[T.AgentDeps], where: str, message: str) -> None:
    """Send the text back to the lead (ModelRetry). On the tool's final attempt keep it instead, with the open problems
    as a warning: a stubborn rule must never end the run and lose the section."""
    if last_chance(ctx):
        ctx.deps.state.warn(f"The {where} was kept on its last attempt with open problems: {message[:400]}", "agent")
        return
    raise ModelRetry(message)


def check_text(ctx: RunContext[T.AgentDeps], text: str, where: str, numbers_need_citation: bool = False,
               allowed: set[str] | None = None, study_period: bool = False) -> None:
    """Raise ModelRetry listing every rule the text breaks. `study_period`: the text plans this study (aims,
    methods), so an uncited date before this year is wrong unless it names records or a search range."""
    state = ctx.deps.state
    problems: list[str] = []
    today = jalali_today(ctx.deps.now)
    title_years = {int(y) for y in re.findall(r"1[34]\d\d", normalise_digits(ctx.deps.request.title))}
    leaked = sorted({m.group(0) for m in EVIDENCE_IDS.finditer(text)
                     if any(e.strip() in state.evidence or e.strip() in state.papers for e in re.split(r"[,،]", m.group(1)))}
                    | {m.group(1) for m in BARE_ID.finditer(text) if m.group(1) in state.evidence or m.group(1) in state.papers})
    if leaked:
        problems.append(f"remove the workspace ids {leaked[:3]} from the text: cite a paper only as its token, e.g. {{P3}}")
    holes = sorted({m.group(0) for m in PLACEHOLDER.finditer(text)})
    if holes:
        problems.append(f"fill or remove the placeholders {holes[:3]}: use the names the title gives, or write the "
                        "text without them (the researcher completes open details from the finish report)")
    unknown = sorted({t for t in TOKEN.findall(text) if t not in state.papers})
    if unknown:
        problems.append(f"unknown paper tokens {unknown}: cite only P# ids from the workspace")
    meta = sorted({m.group(0) for m in META.finditer(text)})
    if meta:
        problems.append(f"remove sentences about the sources, tools or model (found: {meta[:3]})")
    authors = sorted({m.group(0).strip() for m in AUTHORS.finditer(text)})
    if authors:
        problems.append(f"do not name authors ({authors[:2]}): cite the paper with its token only, e.g. «… {{P2}}»")
    for sentence in SENTENCE_END.split(text):
        cited = [t for t in TOKEN.findall(sentence) if t in state.papers]
        body = YEAR.sub(" ", TOKEN.sub(" ", sentence))
        if cited:
            source = " ".join(f"{state.papers[t].record.title} {state.papers[t].record.abstract}" for t in cited)
            source += " " + " ".join(e.sentence for e in state.evidence.values() if e.token in cited and e.verified)
            missing = missing_numbers(body, source, allowed)
            if missing:
                problems.append(f"numbers {missing} are not in the cited paper(s) {cited}: «{sentence.strip()[:120]}»")
        elif numbers_need_citation and missing_numbers(body, "", allowed):
            problems.append(f"a sentence with numbers has no citation: «{sentence.strip()[:120]}»")
        if not cited and VALIDATION.search(sentence) and ALREADY_DONE.search(sentence):
            problems.append("this study's validity or reliability check is reported as already done; write the planned "
                            f"check and its acceptance threshold in the future tense, or cite the validation study: "
                            f"«{sentence.strip()[:120]}»")
        if study_period and not cited and not RETROSPECTIVE.search(sentence):
            old = past_years(sentence, today.year, title_years) or past_months(sentence, today)
            if old:
                problems.append(f"the date(s) {old} have passed (today is {today.isoformat()} in the Jalali calendar): "
                                f"this study starts after approval, so plan it from {today.year} on; a record review or "
                                f"a search range may name past years if the sentence says so: «{sentence.strip()[:120]}»")
        if len(problems) >= 6:
            break
    if problems:
        reject(ctx, where, f"The {where} was not accepted:\n- " + "\n- ".join(problems) + "\nFix these and submit again.")


def submit_aims(ctx: RunContext[T.AgentDeps], aims: AimsIn) -> dict[str, Any]:
    """Submit aims, hypotheses and research questions (read_guide('aims_grammar') first). One hypothesis and one
    question per secondary aim, in the same order."""
    n = len(aims.secondary_aims)
    if not aims.main_aim.strip() or n == 0:
        raise ModelRetry("Give a main aim and at least one secondary aim.")
    if len(aims.hypotheses) != n or len(aims.questions) != n:
        raise ModelRetry(f"{n} secondary aims but {len(aims.hypotheses)} hypotheses and {len(aims.questions)} "
                         "questions: give exactly one of each per aim, in the same order (hypothesis applicable=false "
                         "for descriptive aims).")
    label = ctx.deps.design.label if ctx.deps.design else ""
    analytic = label in ANALYTIC_DESIGNS or any(ASSOCIATION.search(a) for a in aims.secondary_aims)
    if analytic and not any(h.applicable for h in aims.hypotheses):
        raise ModelRetry("The aims test associations or effects: give a directional or null hypothesis for each "
                         "analytic aim (applicable=true); keep applicable=false only for purely descriptive aims.")
    text = "\n".join([aims.main_aim, *aims.secondary_aims, *(h.text for h in aims.hypotheses), *aims.questions,
                      *aims.practical_aims])
    check_text(ctx, text, "aims", study_period=True)
    ctx.deps.state.save_draft("aims", aims.model_dump())
    ctx.deps.state.update_plan("aims", "done", f"{n} aims")  # automatic: the lead need not mark it
    return T.with_workspace(ctx, {"ok": True})


def submit_methods(ctx: RunContext[T.AgentDeps], methods: MethodsIn) -> dict[str, Any]:
    """Submit every methods sub-section (ids from choose_design) and the variables. Keep them consistent with your
    decisions, the aims and the sample size; copy the worked calculation into the sample-size sub-section."""
    deps = ctx.deps
    if deps.design is None:
        raise ModelRetry("Choose the design first.")
    ids = deps.knowledge.section_ids()
    given = [s.id for s in methods.subsections]
    if sorted(given) != sorted(ids):
        raise ModelRetry(f"Submit exactly these sub-section ids once each: {ids} (got {given}).")
    allowed: set[str] = set()
    needs_n = bool(deps.knowledge.design(deps.design.label).sample_size) and deps.design.label != GENERAL
    if deps.sample_size:
        ss = deps.sample_size
        allowed = {str(v) for v in [ss["final_total"], ss["n1"], ss.get("n2"), ss.get("final_n1"), ss.get("final_n2"),
                                   *ss["params"].values()] if v is not None}
        # the worked calculation cites its source paper, but its Z values, constants and steps are the study's own
        allowed |= set(NUMBER.findall(normalise_digits(ss.get("worked") or "")))
        sub = next(s for s in methods.subsections if s.id == "sample_size")
        if str(ss["final_total"]) not in normalise_digits(sub.text):
            raise ModelRetry(f"The sample-size sub-section must state the computed final n = {ss['final_total']} and "
                             "include the worked calculation.")
    elif needs_n:
        raise ModelRetry("Call compute_sample_size before submitting the methods.")
    if not methods.variables:
        raise ModelRetry("List the study variables (every variable the aims and the analysis need).")
    if len([e for e in methods.ethics if e.strip()]) < 3:
        raise ModelRetry("Give the ethics part: at least three concrete commitments (read_guide('ethics')).")
    if not 3 <= methods.duration_months <= 48:
        raise ModelRetry("duration_months must be between 3 and 48.")
    bad = [t.activity for t in methods.timeline if not 1 <= t.start_month <= t.end_month <= methods.duration_months]
    if len(methods.timeline) < 3 or bad:
        raise ModelRetry(f"Give at least three timeline activities within 1..{methods.duration_months} months with "
                         f"start ≤ end (problems: {bad[:3]}).")
    for sub in methods.subsections:
        # only cited sentences are checked against papers; uncited numbers describe this study's own plan
        check_text(ctx, sub.text, f"methods sub-section {sub.id}", allowed=allowed, study_period=True)
    deps.state.save_draft("methods", methods.model_dump())
    deps.state.update_plan("methods", "done", f"{len(methods.variables)} variables")
    return T.with_workspace(ctx, {"ok": True})


def own_numbers(ctx: RunContext[T.AgentDeps]) -> set[str]:
    """Numbers the closing sentences may state without a source: the title's, and this or the next years."""
    year = jalali_today(ctx.deps.now).year
    return set(NUMBER.findall(normalise_digits(ctx.deps.request.title))) | {str(y) for y in range(year, year + 4)}


async def submit_introduction(ctx: RunContext[T.AgentDeps], text: str) -> dict[str, Any]:
    """Submit the introduction (read_guide('introduction') first): the problem and its burden, what is known, the
    gap, and why this study in this setting. Every number must be cited to a kept paper, and every cited claim must be
    what that paper reports (an independent check reads the cited abstracts)."""
    if len(text.split()) < 150:
        raise ModelRetry("The introduction is too short; write at least four substantial paragraphs.")
    check_text(ctx, text, "introduction", numbers_need_citation=True, allowed=own_numbers(ctx))
    sentences = [x.strip() for x in SENTENCE_END.split(text) if len(x.strip().split()) >= 6]
    body = sentences[:-2]  # the closing sentences state this study's aim and need no source
    uncited = [x for x in body if not TOKEN.search(x)]
    if body and len(uncited) / len(body) > MIN_UNCITED_SHARE:
        reject(ctx, "introduction", f"{len(uncited)} of {len(body)} sentences of the problem statement have no source. "
               "Cite the factual claims (find sources with find_studies(purpose='introduction')) or remove them, e.g.: «"
               + "» «".join(u[:100] for u in uncited[:3]) + "»")
    deps = ctx.deps
    deps.state.save_draft("introduction", {"text": text})
    deps.state.update_plan("introduction", "done")
    # AGT-16: the claim check advises; its findings join the methodology review as major issues to fix or explain
    deps.claim_issues = await T.check_support(ctx, [x.strip() for x in SENTENCE_END.split(text) if TOKEN.search(x)],
                                              "support of the introduction's cited claims")
    out: dict[str, Any] = {"ok": True}
    if deps.claim_issues:
        out["claims_to_check"] = [f"«{f['sentence'][:160]}» {f['tokens']}: {f['verdict']}: {f['reason']}"
                                  for f in deps.claim_issues[:8]]
        out["note"] = ("An independent check of the cited abstracts doubts these claims. Correct each to what the paper "
                       "reports, cite a paper that states it, or remove it, and submit again; otherwise they are raised "
                       "in review_proposal.")
    return T.with_workspace(ctx, out)


GuideTopic = Literal[
    "aims_grammar", "methods_checklist", "sample_size", "introduction", "background_checklist", "questionnaire",
    "style_rules", "design_cross_sectional", "design_cohort", "design_case_control", "design_rct",
    "design_quasi_experimental", "design_diagnostic", "design_systematic_review", "design_qualitative",
    "sample_size_principles", "ethics", "consistency_checklist"]
GUIDES_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "guides"
DESIGN_GUIDE = T.DESIGN_GUIDES


def read_guide(ctx: RunContext[T.AgentDeps], topic: GuideTopic) -> str:
    """Read expert rules before you decide or write a part: the design guide of your design (design_*) before the
    methods; sample_size_principles before compute_sample_size; ethics before the methods; consistency_checklist
    before finishing; aims_grammar before the aims; introduction before the introduction."""
    if topic in ctx.deps.knowledge.texts:
        text = ctx.deps.knowledge.text(topic)
    else:
        path = GUIDES_DIR / f"{topic}.md"
        if not path.exists():
            raise ModelRetry(f"No guide {topic!r}.")
        text = path.read_text(encoding="utf-8")
    note = T.idle_note(ctx)
    return text + (f"\n\nNOTE: {note}" if note else "")


# ---------------------------------------------------------------- context economy

KEEP_RECENT_REQUESTS = 1  # tool results of the last request stay complete (AGT-12)
SUMMARY_KEYS = ("ok", "error", "purpose", "screened", "final_total", "n_per_group", "feasibility_problem",
                "rejected_unverifiable", "written", "flagged", "failed", "design", "allowed_sample_size_formulas")


def compact_result(name: str, content: Any) -> Any:
    """An older tool result, shortened: what happened and the ids/numbers, without the bulk. The full data stays in
    the working memory (get_workspace, get_evidence)."""
    if name == "read_guide":
        return "(guide read earlier; read it again if you need the details)"
    if name == "get_workspace" or not isinstance(content, dict):
        return "(older result; see get_workspace for the current state)"
    if "note" in content:  # already shortened in an earlier pass: the processor runs before every request
        return content
    short = {k: content[k] for k in SUMMARY_KEYS if k in content}
    if "kept" in content:
        short["kept"] = [k.get("id") if isinstance(k, dict) else k for k in content["kept"]]
    if "stored" in content:
        short["stored"] = [f"{s['id']}={s['value']}{s.get('unit', '')}" if isinstance(s, dict) else s
                           for s in content["stored"]]
    if "methods_subsections" in content:
        short["methods_subsections"] = list(content["methods_subsections"])
    if isinstance(short.get("allowed_sample_size_formulas"), dict):
        short["allowed_sample_size_formulas"] = list(short["allowed_sample_size_formulas"])
    short["note"] = "older result, shortened; current state via get_workspace"
    return short


SUBMIT_TOOLS = {"submit_aims", "submit_methods", "submit_introduction"}
SUBMITTED_NOTE = "(submitted text, stored in the workspace; not repeated here)"


def compact_args(part: ToolCallPart) -> ToolCallPart:
    """An older submission: the long text it carried is stored in the workspace, so the call keeps only a note.
    The part keeps its id and any provider metadata (Gemini's thought signature); only the arguments shrink."""
    if part.tool_name not in SUBMIT_TOOLS or part.args == {"submitted": SUBMITTED_NOTE}:
        return part
    return dataclasses.replace(part, args={"submitted": SUBMITTED_NOTE})


def compact_history(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Shorten what is older than the last KEEP_RECENT_REQUESTS requests: tool results become summaries, and the long
    arguments of past section submissions become a note (the text is stored in the workspace). Nothing is removed,
    so every tool call keeps its tool return."""
    request_idx = [i for i, m in enumerate(messages) if isinstance(m, ModelRequest)]
    cutoff = request_idx[-KEEP_RECENT_REQUESTS] if len(request_idx) >= KEEP_RECENT_REQUESTS else 0
    out: list[ModelMessage] = []
    for i, msg in enumerate(messages):
        if i < cutoff and isinstance(msg, ModelRequest) and any(isinstance(p, ToolReturnPart) for p in msg.parts):
            parts = [dataclasses.replace(p, content=compact_result(p.tool_name, p.content))
                     if isinstance(p, ToolReturnPart) else p for p in msg.parts]
            msg = dataclasses.replace(msg, parts=parts)
        elif i < cutoff and isinstance(msg, ModelResponse) and any(
                isinstance(p, ToolCallPart) and p.tool_name in SUBMIT_TOOLS for p in msg.parts):
            parts = [compact_args(p) if isinstance(p, ToolCallPart) else p for p in msg.parts]
            msg = dataclasses.replace(msg, parts=parts)
        out.append(msg)
    return out


# ---------------------------------------------------------------- the consistency critic (AGT-11)


class ReviewIssue(BaseModel):
    id: str = Field(description="R1, R2, …")
    section: Literal["aims", "methods", "sample_size", "introduction", "background"]
    severity: Literal["major", "minor"]
    problem: str = Field(description="the contradiction or methodological flaw, quoting the text")
    fix: str = Field(description="the concrete change that resolves it")


class ReviewOut(BaseModel):
    issues: list[ReviewIssue]


CRITIC_INSTRUCTIONS = """You are a strict methodologist on a university research ethics and methods committee. You review
a research proposal for internal consistency and methodological soundness, using the checklist and the design guide
below. Report only real problems a committee would raise (contradictions between parts, wrong outcome wording for the
design, exclusions that remove analysed factors, matching vs analysis, clustering ignored, ITT vs per-protocol, consent
vs observation, unrepresentative or infeasible sample-size parameters, missing pieces). Do not report style or
wording preferences. Each issue: the section, major or minor, the problem quoting the text, and the concrete fix.
Return an empty list when the proposal is sound.

Check the sample size especially (the evidence table lists every verified value the researcher holds):
- the formula serves the primary aim and outcome (not a secondary comparison), and fits the design (diagnostic
  sensitivity needs diagnostic_accuracy, which divides by prevalence; never a single proportion on the total sample);
- each parameter comes from a population and setting like this study's (an emergency-only, neonatal, or single
  small cohort is not representative of a general one) and measures the same quantity (prevalence is not incidence;
  a cut-off of 30 is not one of 20);
- an effect much larger than the other estimates in the evidence table makes the study underpowered: say which
  estimate is more defensible;
- equal groups are assumed only when the sampling can deliver them; otherwise an allocation ratio r is needed.
Check coverage: every outcome or exposure in the variables table, and every planned analysis (a cut-off from a ROC
curve, a regression of predictors, a subgroup comparison), has a matching aim, and every aim has its analysis."""


EVIDENCE_ROWS = 40  # the critic sees at most this many verified values (enough to spot an outlier effect)


def proposal_digest(deps: T.AgentDeps) -> str:
    """The submitted proposal as compact text for the critic (no abstracts)."""
    d = deps.state.drafts
    parts = [f"TITLE: {deps.request.title}",
             "DECISIONS: " + "; ".join(f"{x.key}={x.value} ({x.rationale[:120]})" for x in deps.state.decisions.values())]
    if "aims" in d:
        a = d["aims"]
        parts.append("AIMS: main: " + a["main_aim"] + "\n" + "\n".join(
            f"- aim {i + 1}: {x} | H: {h['text']} ({'applicable' if h['applicable'] else 'n/a'}) | Q: {q}"
            for i, (x, h, q) in enumerate(zip(a["secondary_aims"], a["hypotheses"], a["questions"], strict=False))))
    if "methods" in d:
        m = d["methods"]
        parts.append("METHODS:\n" + "\n".join(f"[{x['id']}] {x['text']}" for x in m["subsections"]))
        parts.append("VARIABLES: " + "; ".join(f"{v['name']} ({v['role']}, {v['type']}): {v['measurement']}"
                                              for v in m["variables"]))
        parts.append("ETHICS: " + " | ".join(m["ethics"]))
        parts.append(f"TIMELINE ({m['duration_months']} months): " + "; ".join(
            f"{t['activity']} m{t['start_month']}-{t['end_month']}" for t in m["timeline"]))
    if deps.sample_size:
        ss = deps.sample_size
        parts.append(f"SAMPLE SIZE: {ss['formula']} params={ss['params']} sources={ss['sources']} "
                     f"final_total={ss['final_total']} expected_recruitment={ss.get('expected_recruitment')} "
                     f"reasoning={ss.get('reasoning', '')[:300]}")
        for name, src in ss["sources"].items():
            if src.startswith("{"):
                p = deps.state.papers.get(src.strip("{}"))
                if p:
                    flags = ", ".join(p.screening.get("flags") or []) or "none"
                    parts.append(f"  source of {name}: {p.record.title} (population similarity "
                                 f"{p.screening.get('population_similarity')}/3, design {p.screening.get('design')}, "
                                 f"country {p.screening.get('country')}, n {p.screening.get('sample_size')}, "
                                 f"flags: {flags})")
        evidence = [e for e in deps.state.evidence.values() if e.verified][:EVIDENCE_ROWS]
        if evidence:
            parts.append("EVIDENCE TABLE (verified values): " + "; ".join(
                f"{e.id} {e.token} {e.quantity} = {e.value}{e.unit} [{e.group}; {e.population}]" for e in evidence))
    if "introduction" in d:
        parts.append("INTRODUCTION:\n" + d["introduction"]["text"])
    return "\n\n".join(parts)


async def review_proposal(ctx: RunContext[T.AgentDeps]) -> dict[str, Any]:
    """Have an independent methodologist review the whole proposal for contradictions and flaws. Call after all
    sections are submitted. Then fix each issue by submitting the corrected section (or recomputing the sample size),
    or, if an issue does not apply, record_decision(key='review:<id>', ...) explaining why. finish requires this."""
    deps = ctx.deps
    missing = [k for k in ("aims", "methods", "introduction") if deps.request.wants(k) and k not in deps.state.drafts]
    if missing:
        raise ModelRetry(f"Submit {missing} before the review.")
    if deps.critic is None:
        return T.with_workspace(ctx, {"ok": True, "issues": [], "note": "no critic configured"})
    label = deps.design.label if deps.design else ""
    guide = DESIGN_GUIDE.get(label)
    rules = (GUIDES_DIR / "consistency_checklist.md").read_text(encoding="utf-8")
    if guide:
        rules += "\n\n" + (GUIDES_DIR / f"{guide}.md").read_text(encoding="utf-8")
    prompt = f"<rules>\n{rules}\n</rules>\n\n<proposal>\n{proposal_digest(deps)}\n</proposal>"
    out = await T.delegate(deps.state, ctx.usage, "review", "methodology review of the whole proposal",
                           lambda: deps.critic.run(prompt), lambda o: f"{len(o.issues)} issues",
                           deps.critic_name or "critic")
    if out is None:
        return T.with_workspace(ctx, {"ok": False, "error": "the review failed; continue and finish"})
    issues = [i.model_dump() for i in out.issues]
    for n, f in enumerate(deps.claim_issues, 1):  # AGT-16: cited claims the abstracts do not support
        issues.append({"id": f"C{n}", "section": "introduction", "severity": "major",
                       "problem": f"«{f['sentence'][:200]}» — {f['verdict']} by {f['tokens']}: {f['reason']}",
                       "fix": "Correct the claim to what the cited paper reports, cite a paper that states it, or remove "
                              "it, then submit the introduction again."})
    deps.state.reviews.append({"at": deps.state.version, "issues": issues})
    deps.state.update_plan("review", "done" if not issues else "doing", f"{len(issues)} issues")
    return T.with_workspace(ctx, {"ok": True, "issues": issues})


def build_critic(model: Model) -> Agent[None, ReviewOut]:
    return Agent(model, output_type=ReviewOut, instructions=CRITIC_INSTRUCTIONS, retries=1, name="critic")


# ---------------------------------------------------------------- the agent


def draft_texts(deps: T.AgentDeps) -> list[str]:
    d = deps.state.drafts
    texts = [p["text"] for p in deps.background_paragraphs.values()]
    if "introduction" in d:
        texts.append(d["introduction"]["text"])
    if "methods" in d:
        texts += [x["text"] for x in d["methods"]["subsections"]]
    if "aims" in d:
        texts += [d["aims"]["main_aim"], *d["aims"]["secondary_aims"]]
    return texts


def instructions(request: ProposalRequest, knowledge: Knowledge, now: datetime | None = None) -> str:
    labels = ", ".join(sorted(k for k in knowledge.designs if k != GENERAL)) + ", other"
    now = now or datetime.now()
    today = jalali_today(now)
    return INSTRUCTIONS.read_text(encoding="utf-8").format(
        today_jalali=f"{today.day} {today.j_months_fa[today.month - 1]} {today.year}", today_iso=now.date().isoformat(),
        jalali_year=today.year,
        design_labels=labels, design_guides=", ".join(sorted(set(DESIGN_GUIDE.values()))),
        language_name=LANGUAGE_NAMES.get(request.language, request.language),
        title=request.title, purpose=request.purpose or "not given", design=request.design or "not given")


def build_lead(model: Model, request: ProposalRequest, knowledge: Knowledge,
               now: datetime | None = None) -> Agent[T.AgentDeps, FinishReport]:
    agent = Agent(
        model,
        deps_type=T.AgentDeps,
        output_type=ToolOutput(FinishReport, name="finish",
                               description="Call when every requested section is submitted and checked."),
        instructions=instructions(request, knowledge, now) + "\n\n# Persona\n" + knowledge.persona,
        tools=[
            Tool(T.get_workspace, takes_ctx=True),
            Tool(T.update_plan, takes_ctx=True),
            Tool(T.record_decision, takes_ctx=True, max_retries=2),
            Tool(read_guide, takes_ctx=True),
            Tool(T.define_study, takes_ctx=True, max_retries=2),
            Tool(T.choose_design, takes_ctx=True, max_retries=2),
            Tool(T.find_studies, takes_ctx=True, max_retries=2, timeout=300),
            Tool(T.extract_data, takes_ctx=True, max_retries=2, timeout=240),
            Tool(T.get_evidence, takes_ctx=True),
            Tool(T.compute_sample_size, takes_ctx=True, max_retries=4),
            Tool(T.write_study_paragraphs, takes_ctx=True, max_retries=2, timeout=300),
            Tool(submit_aims, takes_ctx=True, max_retries=4),
            Tool(submit_methods, takes_ctx=True, max_retries=4),
            Tool(submit_introduction, takes_ctx=True, max_retries=4),
            Tool(review_proposal, takes_ctx=True, max_retries=1, timeout=240),
        ],
        retries=2,
        end_strategy="early",
        name="proposal-lead",
        capabilities=[ProcessHistory(compact_history)],
    )

    @agent.output_validator
    def all_sections_submitted(ctx: RunContext[T.AgentDeps], report: FinishReport) -> FinishReport:
        deps = ctx.deps
        missing = []
        if deps.request.wants("aims") and "aims" not in deps.state.drafts:
            missing.append("aims (submit_aims)")
        if deps.request.wants("methods") and "methods" not in deps.state.drafts:
            missing.append("methods (submit_methods)")
        if deps.request.wants("introduction") and "introduction" not in deps.state.drafts:
            missing.append("introduction (submit_introduction)")
        if deps.request.wants("background") and not deps.background_paragraphs:
            missing.append("background (write_study_paragraphs)")
        if missing:
            raise ModelRetry(f"Not finished: still missing {missing}.")
        if deps.critic is not None:
            reviews = deps.state.reviews
            if not reviews:
                raise ModelRetry("Call review_proposal before finishing.")
            last = reviews[-1]
            open_ = [i["id"] for i in last["issues"]
                     if i["severity"] == "major" and not deps.state.issue_resolved(i, last["at"])]
            if open_:
                raise ModelRetry(f"Major review issues still open: {open_}. Fix each (resubmit the section) or "
                                 "record_decision(key='review:<id>') explaining why it does not apply.")
        cited = set()
        for text in draft_texts(deps):
            cited |= {deps.state.papers[t].record.pmid for t in TOKEN.findall(text) if t in deps.state.papers}
        if len(cited) < MIN_SOURCES:  # distinct PMIDs: one paper found by two searches has two tokens
            raise ModelRetry(f"Only {len(cited)} different papers are cited. Strengthen the problem statement with more "
                             "sources (find_studies(purpose='introduction')) and submit it again.")
        return report

    return agent
