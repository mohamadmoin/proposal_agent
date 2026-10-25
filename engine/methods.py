"""The methods chapter (۳-۱ … ۳-۵) and the aims.

Order: decide the design → find methodological evidence per design topic → answer the a–h checklist → sample size
(model picks, code computes) → the five sub-sections in parallel → aims, hypotheses and questions.
Follows knowledge/designs.yaml, questionnaire.md, methods_checklist.md, sample_size.md and aims_grammar.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from engine import prompts
from engine.background import BackgroundResult, paper_block, write_checked
from engine.knowledge import GENERAL, DesignChoice, Knowledge, normalise_label, resolve_design, section_instruction
from engine.llm import LLM
from engine.pubmed import PubMed
from engine.retrieval import drop_reason, profile_query_text, tfidf_scores
from engine.samplesize import FORMULAS, WORDS, SampleSizeError, compute, prepare_params, worked_calculation
from engine.schemas import (
    AimsOut,
    DesignDecisionOut,
    MethodsNotesOut,
    PaperRecord,
    ProposalRequest,
    SampleSizeChoiceOut,
    StudyProfileOut,
    TopicQueriesOut,
)
from engine.store import Store
from engine.verify import TOKEN_RE, canonical, contains_number, missing_numbers, number_set, split_sentences

TOPIC_RETMAX = 20
TOPIC_KEEP = 6
SUMMARY_MAX_WORDS = 100
STEP = "methods"


# --- 1. design ----------------------------------------------------------------------------------------------------


def decide_design(
    request: ProposalRequest, profile: StudyProfileOut, knowledge: Knowledge, llm: LLM, store: Store
) -> tuple[DesignChoice, dict[str, Any]]:
    """The user's design when given; otherwise the design router (strong model) answers the questionnaire."""
    chosen = resolve_design(knowledge, request.design, request.design_other_name)
    if chosen:
        return chosen, decision_record(knowledge, chosen, source="user")
    store.step_started("design_router", "Choosing the study design (12 questions)")
    try:
        out = llm.complete_json(
            "design_router", DesignDecisionOut, router_prompt(request, profile, knowledge), "strong"
        )
    except Exception as exc:
        fallback = resolve_design(knowledge, profile.design_guess, profile.design_guess_other_name)
        fallback = fallback or DesignChoice(GENERAL, "")
        store.warn(f"The design router failed ({exc}); the profile's design guess was used.", "design_router")
        store.step_failed("design_router", str(exc))
        return fallback, decision_record(knowledge, fallback, source="profile")
    label = normalise_label(out.label)
    if label in knowledge.designs and label != GENERAL:
        chosen = DesignChoice(label, "")
    else:  # "other" or an unknown label: never fail, use the general template with the router's name
        chosen = DesignChoice(GENERAL, out.other_name.strip() or (out.label if label != "other" else ""))
    answers = {a.number: a.answer for a in out.answers}
    record = decision_record(
        knowledge,
        chosen,
        source="router",
        answers=[{"number": n, "answer": answers.get(n, "not answered")} for n in range(1, 13)],
        rationale=" ".join(split_sentences(out.rationale)[:3]),
        missing_information=out.missing_information,
    )
    store.step_done("design_router", f"Design: {chosen.name(knowledge, 'en')}")
    return chosen, record


def router_prompt(request: ProposalRequest, profile: StudyProfileOut, knowledge: Knowledge) -> str:
    labels = ", ".join(sorted(k for k in knowledge.designs if k != GENERAL)) + ", other"
    return prompts.render(
        "design_router",
        questionnaire=knowledge.text("questionnaire"),
        title=request.title,
        purpose=request.purpose or "not given",
        profile_json=profile.model_dump_json(indent=1),
        design_labels=labels,
    )


def decision_record(knowledge: Knowledge, choice: DesignChoice, source: str, **extra: Any) -> dict[str, Any]:
    """What is stored and shown to the user about the design decision."""
    return {
        "source": source,
        "label": choice.label,
        "other_name": choice.other_name,
        "name": {"fa": choice.name(knowledge, "fa"), "en": choice.name(knowledge, "en")},
        "answers": extra.get("answers", []),
        "rationale": extra.get("rationale", ""),
        "missing_information": extra.get("missing_information", []),
    }


# --- 2. topic evidence --------------------------------------------------------------------------------------------


@dataclass
class TopicEvidence:
    topic: str
    query: str
    papers: list[tuple[str, PaperRecord]] = field(default_factory=list)  # (token, record)
    summary: str = ""
    flags: list[str] = field(default_factory=list)


def clean_query(query: str, max_words: int = 6) -> str:
    """At most 6 plain words: no field tags, quotes or Boolean operators."""
    plain = re.sub(r"\[[^\]]*\]|[()\"]|\b(AND|OR|NOT)\b", " ", query)
    return " ".join(plain.split()[:max_words])


def topic_queries(
    profile: StudyProfileOut, design: DesignChoice, knowledge: Knowledge, llm: LLM
) -> list[tuple[str, str]]:
    """One call turns the design's 5 search topics into 5 short PubMed queries. Returns (topic, query) pairs."""
    topics = knowledge.design(design.label).search_topics
    user = prompts.render(
        "topic_queries",
        design_name=design.name(knowledge, "en"),
        profile_json=profile.model_dump_json(indent=1),
        topics="\n".join(f"- {t}" for t in topics),
    )
    try:
        queries = llm.complete_json("topic_queries", TopicQueriesOut, user, "small").queries
    except Exception:
        queries = []
    pairs = []
    for i, topic in enumerate(topics):
        query = clean_query(queries[i]) if i < len(queries) and queries[i].strip() else ""
        pairs.append((topic, query or clean_query(f"{profile.plain_query} {topic}")))
    return pairs


def find_topic_papers(
    query: str, profile: StudyProfileOut, pubmed: PubMed, store: Store, lane: str
) -> list[PaperRecord]:
    """esearch (20) → efetch → drop unusable → TF-IDF cut to the 6 closest to the query and the study."""
    try:
        result = pubmed.esearch(query, retmax=TOPIC_RETMAX)
    except Exception as exc:
        store.log_search(STEP, lane, query, 0, [], error=str(exc))
        return []
    store.log_search(STEP, lane, query, result.count, result.pmids)
    records = [r for r in pubmed.efetch(result.pmids) if not drop_reason(r)] if result.pmids else []
    scores = tfidf_scores(
        [f"{r.title} {r.title} {r.abstract}" for r in records], f"{query} {profile_query_text(profile)}"
    )
    ranked = sorted(zip(scores, range(len(records)), records, strict=True), key=lambda x: (-x[0], x[1]))
    return [record for _, _, record in ranked[:TOPIC_KEEP]]


def summarise_topic(evidence: TopicEvidence, design_name: str, study_title: str, llm: LLM) -> TopicEvidence:
    """≤ 100 words on what the abstracts say about the topic, cited with {M#}; numbers checked."""
    if not evidence.papers:
        evidence.summary = "No studies were found for this topic."
        evidence.flags.append("no_papers")
        return evidence
    prompt = llm.prose_prompt(
        "topic_summary",
        design_name=design_name,
        study_title=study_title,
        topic=evidence.topic,
        papers="\n\n".join(paper_block(record, token) for token, record in evidence.papers),
    )
    source = "\n".join(f"{r.title}\n{r.abstract}" for _, r in evidence.papers)
    text, missing = write_checked(llm, "topic_summary", prompt, source)
    own_tokens = {token for token, _ in evidence.papers}
    text = TOKEN_RE.sub(lambda m: m.group(0) if m.group(0).strip("{}") in own_tokens else "", text)
    evidence.summary = limit_words(text, SUMMARY_MAX_WORDS)
    if missing:
        evidence.flags.append("numbers_unverified")
    return evidence


def limit_words(text: str, max_words: int) -> str:
    """Keep whole sentences up to max_words (always at least the first sentence)."""
    kept: list[str] = []
    count = 0
    for sentence in split_sentences(text):
        words = len(sentence.split())
        if kept and count + words > max_words:
            break
        kept.append(sentence)
        count += words
    return " ".join(kept)


def topic_evidence(
    profile: StudyProfileOut,
    design: DesignChoice,
    knowledge: Knowledge,
    llm: LLM,
    pubmed: PubMed,
    store: Store,
    study_title: str,
) -> list[TopicEvidence]:
    """Search each design topic, assign {M#} tokens, and summarise each topic in parallel."""
    evidence: list[TopicEvidence] = []
    tokens: dict[str, str] = {}
    for i, (topic, query) in enumerate(topic_queries(profile, design, knowledge, llm), start=1):
        item = TopicEvidence(topic=topic, query=query)
        for record in find_topic_papers(query, profile, pubmed, store, f"topic {i}"):
            tokens.setdefault(record.pmid, f"M{len(tokens) + 1}")
            item.papers.append((tokens[record.pmid], record))
        evidence.append(item)
    design_name = design.name(knowledge, "en")
    results = llm.map(lambda e: summarise_topic(e, design_name, study_title, llm), evidence)
    for item, result in zip(evidence, results, strict=True):
        if isinstance(result, Exception):
            item.summary, item.flags = "The summary of this topic could not be written.", ["failed"]
            store.warn(f"Methods topic '{item.topic}': summary failed ({result}).", STEP)
        elif "numbers_unverified" in item.flags:
            store.warn(f"Methods topic '{item.topic}': numbers unverified in the summary.", STEP)
    return evidence


def tokens_to_pmids(text: str, papers: list[tuple[str, PaperRecord]]) -> str:
    """For the stored trace: {M3} → (PMID 123). These summaries are notes, not proposal text, so they are not
    numbered (numbering them would add references the document never cites)."""
    pmids = {token: record.pmid for token, record in papers}
    return TOKEN_RE.sub(lambda m: f"(PMID {pmids[m.group(0)[1:-1]]})" if m.group(0)[1:-1] in pmids else "", text)


def summaries_text(evidence: list[TopicEvidence]) -> str:
    return "\n".join(f"- {e.topic}: {e.summary}" for e in evidence) or "none"


# --- 3. methods notes ---------------------------------------------------------------------------------------------


def methods_notes(
    profile: StudyProfileOut,
    request: ProposalRequest,
    design: DesignChoice,
    knowledge: Knowledge,
    llm: LLM,
    background: BackgroundResult | None,
    evidence: list[TopicEvidence],
    sources: dict[str, PaperRecord],
) -> dict[str, Any]:
    """Answer the a–h checklist from the 5 best background papers and the topic summaries."""
    best = background.selected[:5] if background else []
    user = prompts.render(
        "methods_notes",
        design_name=design.name(knowledge, "en"),
        profile_json=profile.model_dump_json(indent=1),
        purpose=request.purpose or "not given",
        methods_checklist=knowledge.text("methods_checklist"),
        background_papers="\n\n".join(paper_block(c.record, c.token) for c in best) or "none",
        topic_summaries=summaries_text(evidence),
    )
    notes = llm.complete_json("methods_notes", MethodsNotesOut, user, "small").model_dump()
    kept, dropped = [], []
    for number in notes["baseline_numbers"]:
        token = number["token"].strip("{} ")
        record = sources.get(token)
        if record and not missing_numbers(number["value"], record.abstract):
            kept.append({**number, "token": token, **population_of(token, record, background)})
        else:
            dropped.append(number)
    notes["baseline_numbers"] = kept
    notes["dropped_baseline_numbers"] = dropped
    return notes


def population_of(token: str, record: PaperRecord, background: BackgroundResult | None) -> dict[str, Any]:
    """Who the number was measured in: a sample size needs a parameter from a population like the study's."""
    candidate = background.by_token().get(token) if background else None
    screening = candidate.screening if candidate else {}
    return {
        "paper_title": record.title,
        "paper_country": screening.get("country") or "",
        "population_similarity": screening.get("population_similarity"),
    }


# --- 4. sample size -----------------------------------------------------------------------------------------------


def allowed_formulas_json(formula_ids: list[str]) -> str:
    items = []
    for fid in formula_ids:
        formula = FORMULAS[fid]
        items.append(
            {
                "id": fid,
                "when": formula.label["en"],
                "required_parameters": list(formula.required),
                "optional_parameters": list(formula.optional),
                "formula": formula.expression,
            }
        )
    return json.dumps(items, ensure_ascii=False, indent=1)


def value_in_paper(value: float, record: PaperRecord | None) -> bool:
    """Is this value written in the paper's abstract (percentages both ways)?"""
    if record is None:
        return False
    return canonical(f"{value:.6f}") in number_set(record.abstract)


def sample_size(
    profile: StudyProfileOut,
    request: ProposalRequest,
    design: DesignChoice,
    knowledge: Knowledge,
    llm: LLM,
    notes: dict[str, Any],
    sources: dict[str, PaperRecord],
    lang: str,
) -> dict[str, Any] | None:
    """The model chooses the formula and parameters; code validates, computes and writes the worked calculation.

    Returns None for designs without a formula (systematic reviews). Raises SampleSizeError when no number can be
    computed.
    """
    formula_ids = knowledge.design(design.label).sample_size
    if not formula_ids:
        return None
    user = prompts.render(
        "sample_size",
        sample_size_rules=knowledge.text("sample_size"),
        design_name=design.name(knowledge, "en"),
        profile_json=profile.model_dump_json(indent=1),
        purpose=request.purpose or "not given",
        allowed_formulas=allowed_formulas_json(formula_ids),
        baseline_numbers=json.dumps(notes.get("baseline_numbers", []), ensure_ascii=False, indent=1),
    )
    choice = llm.complete_json("sample_size", SampleSizeChoiceOut, user, "small")
    if choice.formula not in formula_ids:
        raise SampleSizeError(f"formula {choice.formula!r} is not allowed for this design ({', '.join(formula_ids)})")
    values: dict[str, float] = {}
    param_sources: dict[str, str] = {}
    changes: list[str] = []
    for param in choice.parameters:
        if param.name in values:
            continue
        values[param.name] = param.value
        param_sources[param.name] = checked_source(param.source, param.token, param.value, sources, changes, param.name)
    similarity = {n["token"]: n.get("population_similarity") for n in notes.get("baseline_numbers", [])}
    for param in choice.parameters:
        if param_sources.get(param.name, "").startswith("{") and (similarity.get(param.token.strip("{} ")) or 0) < 2:
            changes.append(DIFFERENT_POPULATION[lang].format(name=param.name))
    params, notes_from_code = prepare_params(choice.formula, values)
    for name in params:
        param_sources.setdefault(name, "convention")
    rate = choice.nonresponse_rate
    rate_source = checked_source(choice.rate_source, choice.rate_token, rate, sources, changes, "rate") if rate else ""
    result = compute(choice.formula, params, rate)
    worked = worked_calculation(result, param_sources, lang)
    for line in changes:
        if line.startswith(DIFFERENT_POPULATION[lang][:12]):
            worked += "\n" + line
    if result.rate:
        worked += "\n" + rate_source_line(rate_source, lang)
    if result.final_total > LARGE_SAMPLE:
        worked += "\n" + LARGE_SAMPLE_NOTE[lang]
    return {
        "formula": choice.formula,
        "params": result.params,
        "sources": param_sources,
        "rate": result.rate,
        "rate_source": rate_source,
        "raw_n1": round(result.raw_n1, 2),
        "n1": result.n1,
        "n2": result.n2,
        "final_n1": result.final_n1,
        "final_n2": result.final_n2,
        "final_total": result.final_total,
        "extra": result.extra,
        "worked": worked,
        "reasoning": choice.reasoning,
        "notes": notes_from_code + changes,
    }


LARGE_SAMPLE = 2000
LARGE_SAMPLE_NOTE = {
    "fa": "توجه: حجم نمونه بزرگ است، معمولاً چون تفاوت یا دقت مورد انتظار کوچک انتخاب شده است. پژوهشگر باید بررسی کند که آیا کوچک‌ترین تفاوت دارای اهمیت بالینی یا دقت قابل قبول، مقدار بزرگ‌تری است.",
    "en": "Note: the sample is large, usually because the expected difference or precision is small. The researcher should check whether the smallest clinically important difference, or an acceptable precision, is larger.",
}

DIFFERENT_POPULATION = {
    "fa": "توجه: مقدار {name} از مطالعه‌ای گرفته شده است که جمعیت آن ممکن است با جمعیت این پژوهش متفاوت باشد؛ پژوهشگر باید آن را بررسی و تأیید کند.",
    "en": "Note: the value of {name} comes from a study whose population may differ from this study's; the researcher must check and confirm it.",
}


def rate_source_line(source: str, lang: str) -> str:
    """Where the non-response rate comes from, in the output language."""
    words = WORDS[lang]
    label = "Source of the non-response or drop-out rate" if lang == "en" else "منبع نرخ ریزش یا عدم پاسخ‌دهی"
    text = f"{words['paper']} {source}" if source.startswith("{") else words.get(source, source)
    return f"{label}: {text}"


def checked_source(
    source: str, token: str, value: float | None, sources: dict[str, PaperRecord], changes: list[str], name: str
) -> str:
    """A paper-sourced value must be found in that paper; otherwise it becomes an assumption."""
    if source != "paper":
        return source
    token = token.strip("{} ")
    if value is not None and value_in_paper(value, sources.get(token)):
        return "{" + token + "}"
    changes.append(f"{name} = {value}: not found in {token or 'the cited paper'}; treated as an assumption")
    return "assumption"


# --- 5. sub-sections ----------------------------------------------------------------------------------------------


def calculation_block(size: dict[str, Any] | None, size_error: str, lang: str) -> str:
    if size:
        return (
            "The sample-size calculation, computed by code (copy the formula, the values with their sources and the "
            f"result; state the final sample size exactly as {size['final_total']}):\n"
            f"<calculation>\n{size['worked']}\n</calculation>"
        )
    if size_error:
        return (
            "No sample-size calculation could be made "
            f"({size_error}). Explain which information is needed to calculate it, and do not give a number."
        )
    return ""


def write_subsection(
    section_id: str,
    design: DesignChoice,
    knowledge: Knowledge,
    llm: LLM,
    request: ProposalRequest,
    profile: StudyProfileOut,
    notes: dict[str, Any],
    evidence: list[TopicEvidence],
    size: dict[str, Any] | None,
    size_error: str,
) -> dict[str, Any]:
    """One methods sub-section. ۳-۳ must contain the computed final sample size (one retry, then a flag)."""
    lang = request.language
    title = knowledge.section_title(section_id, lang)
    prompt = llm.prose_prompt(
        "methods_section",
        section_title=title,
        study_title=request.title,
        design_name=design.name(knowledge, lang),
        purpose=request.purpose or "not given",
        profile_json=profile.model_dump_json(indent=1),
        instruction=section_instruction(knowledge, design, section_id, lang),
        notes_json=json.dumps({k: v for k, v in notes.items() if k != "dropped_baseline_numbers"}, ensure_ascii=False,
                              indent=1),
        topic_summaries=summaries_text(evidence),
        calculation_block=calculation_block(size, size_error, lang) if section_id == "sample_size" else "",
        language=lang,
    )  # fmt: skip
    text = llm.complete_text(f"methods_section:{section_id}", prompt)
    flags: list[str] = []
    if section_id == "sample_size" and size:
        if not contains_number(text, size["final_total"]):
            reminder = prompts.render("reminder_sample_size", final_n=size["final_total"], previous=text)
            text = llm.complete_text(f"methods_section:{section_id}:retry", prompt + "\n\n" + reminder)
            if not contains_number(text, size["final_total"]):
                flags.append("sample_size_missing")
    return {"id": section_id, "title": title, "text": text.strip(), "flags": flags}


def run_methods(
    request: ProposalRequest,
    profile: StudyProfileOut,
    design: DesignChoice,
    knowledge: Knowledge,
    llm: LLM,
    pubmed: PubMed,
    store: Store,
    background: BackgroundResult | None,
) -> dict[str, Any]:
    """Evidence → notes → sample size → the five sub-sections. Returns the methods payload."""
    store.step_started("methods_evidence", "Searching methodological evidence for the design topics")
    evidence = topic_evidence(profile, design, knowledge, llm, pubmed, store, request.title)
    sources: dict[str, PaperRecord] = {c.token: c.record for c in (background.selected if background else [])}
    for item in evidence:
        for token, record in item.papers:
            sources[token] = record
            store.save_paper("methods", record, token=token, lanes=[item.topic], selected=True, stage="selected")
    store.step_done("methods_evidence", f"{len(evidence)} topics summarised")

    store.step_started("methods_notes", "Answering the methods checklist (a–h)")
    try:
        notes = methods_notes(profile, request, design, knowledge, llm, background, evidence, sources)
        store.step_done("methods_notes", f"{len(notes['baseline_numbers'])} baseline numbers kept")
        if notes["dropped_baseline_numbers"]:
            store.warn(f"{len(notes['dropped_baseline_numbers'])} baseline number(s) were dropped because they do "
                       "not appear in the cited paper.", STEP)  # fmt: skip
    except Exception as exc:
        notes = {}
        store.step_failed("methods_notes", str(exc))
        store.warn(f"The methods notes could not be written ({exc}); the sub-sections use the topic summaries only.")

    size, size_error = None, ""
    if knowledge.design(design.label).sample_size:
        store.step_started("sample_size", "Choosing the formula; code computes the sample size")
        try:
            size = sample_size(profile, request, design, knowledge, llm, notes, sources, request.language)
            store.step_done("sample_size", f"Final sample size: {size['final_total']}")
            for change in size["notes"]:
                if "assumption" in change:
                    store.warn(f"Sample size: {change}.", "sample_size")
        except Exception as exc:
            size_error = str(exc)
            store.step_failed("sample_size", size_error)
            store.warn(f"The sample size could not be computed: {size_error}", "sample_size")

    store.step_started("methods_writing", "Writing ۳-۱ … ۳-۵")
    section_ids = knowledge.section_ids()
    outputs = llm.map(
        lambda sid: write_subsection(sid, design, knowledge, llm, request, profile, notes, evidence, size, size_error),
        section_ids,
    )
    subsections = []
    for section_id, output in zip(section_ids, outputs, strict=True):
        title = knowledge.section_title(section_id, request.language)
        if isinstance(output, Exception) or not output["text"]:
            reason = str(output) if isinstance(output, Exception) else "empty answer"
            store.warn(f"Methods sub-section {title} failed: {reason}", STEP)
            subsections.append({"id": section_id, "title": title, "text": "", "flags": ["failed"], "error": reason})
            continue
        if "sample_size_missing" in output["flags"]:
            store.warn(f"Section {title} does not state the computed sample size ({size['final_total']}).", STEP)
        subsections.append(output)
    store.step_done("methods_writing", f"{sum(1 for s in subsections if s['text'])} of 5 sub-sections written")
    return {
        "topics": [
            {
                "topic": e.topic,
                "query": e.query,
                "summary": tokens_to_pmids(e.summary, e.papers),
                "flags": e.flags,
                "papers": [{"token": t, "pmid": r.pmid, "title": r.title} for t, r in e.papers],
            }
            for e in evidence
        ],  # fmt: skip
        "notes": notes,
        "sample_size": size,
        "sample_size_error": size_error,
        "subsections": subsections,
        "source_records": {t: r for e in evidence for t, r in e.papers},
    }


# --- 6. aims ------------------------------------------------------------------------------------------------------


def write_aims(
    request: ProposalRequest,
    profile: StudyProfileOut,
    design: DesignChoice,
    knowledge: Knowledge,
    llm: LLM,
    notes: dict[str, Any] | None,
) -> dict[str, Any]:
    """Aims, hypotheses and questions by aims_grammar.md; code checks the 1:1 counts (one retry, then a flag)."""
    variables = (notes or {}).get("variables") or []
    prompt = llm.prose_prompt(
        "aims",
        aims_grammar=knowledge.text("aims_grammar"),
        study_title=request.title,
        english_title=profile.english_title,
        design_name=design.name(knowledge, request.language),
        purpose=request.purpose or "not given",
        profile_json=profile.model_dump_json(indent=1),
        variables=json.dumps(variables, ensure_ascii=False, indent=1) if variables else "not available",
        language=request.language,
    )
    aims = llm.complete_json("aims", AimsOut, prompt)
    flags: list[str] = []
    if not counts_match(aims):
        reminder = prompts.render(
            "reminder_aims",
            n_aims=len(aims.secondary_aims),
            n_hypotheses=len(aims.hypotheses),
            n_questions=len(aims.questions),
        )
        aims = llm.complete_json("aims:retry", AimsOut, prompt + "\n\n" + reminder)
        if not counts_match(aims):
            flags.append("aims_count_mismatch")
    return {**aims.model_dump(), "flags": flags}


def counts_match(aims: AimsOut) -> bool:
    return len(aims.secondary_aims) == len(aims.hypotheses) == len(aims.questions) and bool(aims.secondary_aims)
