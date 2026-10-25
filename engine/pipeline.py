"""The whole run: profile → design → background → methods → aims → introduction → references.

Each section runs inside its own try/except: a failed step marks its section failed with the reason and the run
continues. The job ends "done", "done_with_errors" or "failed", and a section is never "done" while empty.
"""

from __future__ import annotations

import logging
import os
import threading
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from engine.background import BackgroundResult, run_background
from engine.introduction import run_introduction
from engine.knowledge import DesignChoice, Knowledge
from engine.llm import LLM
from engine.methods import decide_design, run_methods, write_aims
from engine.profile import build_profile
from engine.pubmed import PubMed
from engine.references import number_document
from engine.schemas import SECTION_KEYS, PaperRecord, ProposalRequest, StudyProfileOut
from engine.store import Store
from engine.titles import title

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunOptions:
    """Tunable limits (BACKGROUND_MAX_PAPERS 5–10, BACKGROUND_SCREEN_TOP)."""

    max_papers: int = 8
    screen_top: int = 40

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> RunOptions:
        env = os.environ if env is None else env
        max_papers = int(env.get("BACKGROUND_MAX_PAPERS") or 8)
        return cls(
            max_papers=min(10, max(5, max_papers)),
            screen_top=max(5, int(env.get("BACKGROUND_SCREEN_TOP") or 40)),
        )


class UsageMeter:
    """Adds up tokens and cost of every model call while passing the record on to the store."""

    def __init__(self, store: Store):
        self.store = store
        self.lock = threading.Lock()
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost: float | None = None

    def __call__(self, record: dict[str, Any]) -> None:
        with self.lock:
            self.calls += 1
            self.prompt_tokens += record.get("prompt_tokens") or 0
            self.completion_tokens += record.get("completion_tokens") or 0
            if record.get("cost_usd") is not None:
                self.cost = (self.cost or 0.0) + record["cost_usd"]
        self.store.log_call(record)

    def totals(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "cost_usd": round(self.cost, 4) if self.cost is not None else None,
        }


def failed(key: str, lang: str, reason: str) -> dict[str, Any]:
    return {"status": "failed", "title": title(key, lang), "error": reason}


def run(
    request: ProposalRequest,
    store: Store,
    llm: LLM,
    pubmed: PubMed,
    now: datetime,
    options: RunOptions | None = None,
    knowledge: Knowledge | None = None,
) -> dict[str, Any]:
    """Run the selected sections and return the result (also saved through `store.save_result`)."""
    options = options or RunOptions.from_env()
    knowledge = knowledge or llm.knowledge
    meter = UsageMeter(store)
    llm = llm.with_recorder(meter)
    lang = request.language
    wanted = [key for key in SECTION_KEYS if request.wants(key)]
    sections: dict[str, dict[str, Any]] = {}

    def publish(key: str, section: dict[str, Any]) -> None:
        """Keep a finished (or failed) section and show it to the store straight away."""
        sections[key] = section
        store.save_section(key, section)

    def guarded(key: str, step: str, fn: Any, requested: bool = True) -> Any:
        """Run one section; on failure, record why and keep going."""
        try:
            return fn()
        except Exception as exc:
            log.warning("section %s failed: %s\n%s", key, exc, traceback.format_exc())
            store.step_failed(step, str(exc))
            store.warn(f"The {key} step failed: {exc}", step)
            if requested:
                publish(key, failed(key, lang, str(exc)))
            return None

    # 1. Profile: everything depends on it.
    store.step_started("profile", "Reading the title and building the search plan")
    try:
        profile = build_profile(request, knowledge, llm)
    except Exception as exc:
        store.step_failed("profile", str(exc))
        for key in wanted:
            publish(key, failed(key, lang, f"the study profile could not be built: {exc}"))
        return finish(request, store, sections, {}, meter, now, profile=None, design=None)
    store.save_profile(profile.model_dump())
    store.step_done("profile", profile.english_title)

    # 2. Design (the user's, or the router's).
    design, decision = decide_design(request, profile, knowledge, llm, store)
    store.save_design(decision)

    # 3. Background search and selection (also needed by the introduction and the methods).
    background: BackgroundResult | None = None
    if any(k in wanted for k in ("background", "methods", "introduction")):
        background = guarded(
            "background",
            "background",
            lambda: run_background(
                profile, design, knowledge, llm, pubmed, store, now.year, lang, request.title,
                options.max_papers, options.screen_top, write="background" in wanted,
            ),
            requested="background" in wanted,  # when not requested, a failure only shows as a warning
        )  # fmt: skip
        if background is not None and "background" in wanted:
            publish("background", background_section(background, lang))

    # 4. Methods and 5. aims.
    methods = None
    if "methods" in wanted:
        methods = guarded(
            "methods",
            "methods",
            lambda: run_methods(request, profile, design, knowledge, llm, pubmed, store, background),
        )
        if methods is not None:
            publish("methods", methods_section(methods, decision, lang, knowledge))
    if "aims" in wanted:
        store.step_started("aims", "Writing the aims, hypotheses and questions")
        aims = guarded(
            "aims", "aims", lambda: write_aims(request, profile, design, knowledge, llm, (methods or {}).get("notes"))
        )
        if aims is not None:
            store.step_done("aims")
            if "aims_count_mismatch" in aims["flags"]:
                store.warn("The numbers of aims, hypotheses and questions do not match one to one.", "aims")
            publish("aims", {"status": "done", "title": title("aims", lang), **aims})

    # 6. Introduction.
    intro = None
    if "introduction" in wanted:
        intro = guarded(
            "introduction",
            "introduction",
            lambda: run_introduction(request, profile, design, knowledge, llm, pubmed, store, background),
        )
        if intro is not None:
            publish("introduction", {"status": "done", "title": title("introduction", lang), **without_records(intro)})

    sources = collect_sources(background, methods, intro)
    return finish(request, store, sections, sources, meter, now, profile=profile, design=(design, decision))


def background_section(background: BackgroundResult, lang: str) -> dict[str, Any]:
    papers = []
    for candidate in background.selected:
        papers.append(
            {
                "token": candidate.token,
                "pmid": candidate.record.pmid,
                "title": candidate.record.title,
                "year": candidate.record.year,
                "journal": candidate.record.journal,
                "score": candidate.score,
                "aim_similarity": candidate.aim,
                "population_similarity": candidate.population,
                "design_match": candidate.design_match,
                "flags": candidate.flags,
            }
        )
    return {"status": "done", "title": title("background", lang), "paragraphs": background.paragraphs,
            "papers": papers}  # fmt: skip


def methods_section(methods: dict[str, Any], decision: dict[str, Any], lang: str, knowledge: Knowledge) -> dict:
    flags = ["incomplete"] if any("failed" in s["flags"] for s in methods["subsections"]) else []
    return {"status": "done", "title": title("methods", lang), "design": decision, "flags": flags,
            **without_records(methods)}  # fmt: skip


def collect_sources(background: BackgroundResult | None, *payloads: dict[str, Any] | None) -> dict[str, PaperRecord]:
    """Token → record for every source the document may cite ({P#}, {M#}, {S#})."""
    sources: dict[str, PaperRecord] = {}
    if background:
        sources.update({c.token: c.record for c in background.selected})
    for payload in payloads:
        sources.update((payload or {}).get("source_records", {}))
    return sources


def without_records(payload: dict[str, Any]) -> dict[str, Any]:
    """The section payload without the in-memory records (they are not JSON)."""
    return {k: v for k, v in payload.items() if k != "source_records"}


def ordered_texts(sections: dict[str, dict[str, Any]]) -> list[tuple[tuple[str, ...], str]]:
    """Every citable text with its location, in document order: Introduction → Aims → Background → Methods."""
    items: list[tuple[tuple[str, ...], str]] = []
    intro = sections.get("introduction")
    if intro and intro["status"] == "done":
        items.append((("introduction", "text"), intro["text"]))
    aims = sections.get("aims")
    if aims and aims["status"] == "done":
        items.append((("aims", "main_aim"), aims["main_aim"]))
        for i, text in enumerate(aims["secondary_aims"]):
            items.append((("aims", "secondary_aims", str(i)), text))
        for i, hypothesis in enumerate(aims["hypotheses"]):
            items.append((("aims", "hypotheses", str(i)), hypothesis["text"]))
        for key in ("questions", "practical_aims"):
            for i, text in enumerate(aims[key]):
                items.append((("aims", key, str(i)), text))
    background = sections.get("background")
    if background and background["status"] == "done":
        for i, paragraph in enumerate(background["paragraphs"]):
            items.append((("background", "paragraphs", str(i)), paragraph["text"]))
    methods = sections.get("methods")
    if methods and methods["status"] == "done":
        for i, sub in enumerate(methods["subsections"]):
            items.append((("methods", "subsections", str(i)), sub["text"]))
    return items


def put_back(sections: dict[str, dict[str, Any]], location: tuple[str, ...], text: str) -> None:
    section, key, *index = location
    if not index:
        sections[section][key] = text
        return
    target = sections[section][key][int(index[0])]
    if isinstance(target, dict):
        target["text"] = text
    else:
        sections[section][key][int(index[0])] = text


def section_is_empty(section: dict[str, Any], key: str) -> bool:
    if key == "introduction":
        return not section.get("text", "").strip()
    if key == "aims":
        return not section.get("main_aim", "").strip()
    if key == "background":
        return not section.get("paragraphs")
    if key == "methods":
        return not any(s.get("text") for s in section.get("subsections", []))
    return False


def finish(
    request: ProposalRequest,
    store: Store,
    sections: dict[str, dict[str, Any]],
    sources: dict[str, PaperRecord],
    meter: UsageMeter,
    now: datetime,
    profile: StudyProfileOut | None,
    design: tuple[DesignChoice, dict[str, Any]] | None,
) -> dict[str, Any]:
    """Number the citations, enforce "never done and empty", compute the job status and save the result."""
    lang = request.language
    for key, section in list(sections.items()):
        if section["status"] == "done" and section_is_empty(section, key):
            sections[key] = failed(key, lang, "the section came out empty")
            store.warn(f"The {key} section came out empty and was marked failed.", key)
    items = ordered_texts(sections)
    numbering = number_document([text for _, text in items], sources)
    for (location, _), text in zip(items, numbering.texts, strict=True):
        put_back(sections, location, text)
    if numbering.unknown_tokens:
        store.warn(f"Unknown citation tokens were removed: {', '.join(numbering.unknown_tokens)}.", "references")
    wanted = [key for key in SECTION_KEYS if request.wants(key)]
    statuses = [sections.get(key, {}).get("status", "failed") for key in wanted]
    incomplete = any("incomplete" in sections.get(key, {}).get("flags", []) for key in wanted)
    if wanted and all(s == "failed" for s in statuses):
        status = "failed"
    elif any(s == "failed" for s in statuses) or incomplete:
        status = "done_with_errors"
    else:
        status = "done"
    result = {
        "status": status,
        "title": request.title,
        "language": lang,
        "generated_at": now.isoformat(),
        "profile": profile.model_dump() if profile else None,
        "design": design[1] if design else None,
        "sections": {key: sections[key] for key in SECTION_KEYS if key in sections},
        "references": numbering.references,
        "unknown_tokens": numbering.unknown_tokens,
        "usage": meter.totals(),
    }
    store.save_result(result)
    return result
