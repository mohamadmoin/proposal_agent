"""The agent's working memory for one proposal run (AGT-2).

Everything the lead researcher knows about its own work lives here and is passed to every tool as `deps`:
  plan       the checklist the agent keeps (todo → doing → done / blocked)
  decisions  design, primary outcome, sampling … each with its rationale and evidence ids
  papers     P#  every PubMed record the run kept, with its role and screening verdict
  evidence   E#  one extracted quantity from one paper, with the verbatim sentence it came from
  tasks      T#  every delegated sub-agent job (kind, input, status, tokens, compact result)
  drafts     sections submitted so far
Every change is mirrored to the engine Store (steps, papers, warnings), so the web page shows live progress and the
trace keeps the agent's plan and sub-tasks. `digest()` is the compact view returned with every tool result, so the
lead always knows what is done and what is next without re-reading raw data.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from engine.schemas import PaperRecord
from engine.store import Store

PlanStatus = Literal["todo", "doing", "done", "blocked"]
TaskStatus = Literal["running", "done", "failed"]
DIGEST_MAX_CHARS = 6000  # ~1.5k tokens


@dataclass
class PlanItem:
    id: str
    title: str
    status: PlanStatus = "todo"
    note: str = ""


@dataclass
class Decision:
    key: str
    value: str
    rationale: str = ""
    evidence: list[str] = field(default_factory=list)


@dataclass
class Paper:
    token: str
    role: str
    record: PaperRecord
    screening: dict[str, Any] = field(default_factory=dict)
    selected: bool = False


@dataclass
class Evidence:
    id: str
    token: str  # the paper (P#)
    pmid: str
    quantity: str  # e.g. "prevalence of vitamin D deficiency (<20 ng/mL)"
    value: float
    unit: str = ""  # "%", "proportion", "ng/mL", "OR" …
    group: str = ""  # which group the value belongs to ("cases", "mesh repair", "overall")
    population: str = ""  # who was measured
    sentence: str = ""  # verbatim sentence or table row the value was read from
    verified: bool = False  # code found `value` in `sentence`
    source: str = "abstract"  # abstract | full_text
    locator: str = ""  # full text: S<n> (sentence) or T<k>.r<i> (table row)


@dataclass
class Task:
    id: str
    kind: str  # screen | extract | write_study | review …
    summary: str
    status: TaskStatus = "running"
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    result: str = ""
    started: float = field(default_factory=time.monotonic, repr=False)


class ResearchState:
    def __init__(self, store: Store, language: str = "fa"):
        self.store = store
        self.language = language
        self.plan: list[PlanItem] = []
        self.decisions: dict[str, Decision] = {}
        self.papers: dict[str, Paper] = {}
        self.evidence: dict[str, Evidence] = {}
        self.tasks: dict[str, Task] = {}
        self.drafts: dict[str, dict[str, Any]] = {}
        self.warnings: list[str] = []
        self.version = 0  # bumped on every submission or sample-size computation
        self.section_version: dict[str, int] = {}
        self.reviews: list[dict[str, Any]] = []  # {"at": version, "issues": [...]}
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- plan
    def set_plan(self, items: list[tuple[str, str]]) -> None:
        with self._lock:
            known = {p.id: p for p in self.plan}
            self.plan = [known.get(pid) or PlanItem(pid, title) for pid, title in items]

    def update_plan(self, item_id: str, status: PlanStatus, note: str = "") -> PlanItem:
        with self._lock:
            item = next((p for p in self.plan if p.id == item_id), None)
            if item is None:
                item = PlanItem(item_id, item_id)
                self.plan.append(item)
            previous, item.status, item.note = item.status, status, note or item.note
        step = f"plan:{item_id}"
        if status == "doing" and previous != "doing":
            self.store.step_started(step, item.title)
        elif status == "done":
            self.store.step_done(step, note)
        elif status == "blocked":
            self.store.step_failed(step, note or "blocked")
        return item

    def log(self, plan_id: str, kind: str, **fields: Any) -> None:
        """A short factual event for the live proposal page, attached to a plan step. Written by code from tool
        results (S5, AGT-33): structured fields, so the page words it in its own language. A step that receives an
        event while still 'todo' starts."""
        item = {"kind": kind, "at": round(time.time(), 1),
                **{k: v for k, v in fields.items() if v not in (None, "", [], {})}}
        with self._lock:
            plan = next((p for p in self.plan if p.id == plan_id), None)
        if plan is not None and plan.status == "todo":
            self.update_plan(plan_id, "doing")
        emit = getattr(self.store, "step_event", None)
        if emit is not None:
            emit(f"plan:{plan_id}", item)

    def close_plan(self, ok: bool) -> None:
        """At the end of a run no step stays running: a step in progress is done (or failed when the run stopped),
        one never started is skipped."""
        with self._lock:
            items = list(self.plan)
        for item in items:
            if item.status == "doing":
                self.update_plan(item.id, "done" if ok else "blocked", item.note or ("" if ok else "stopped"))
            elif item.status == "todo":
                skip = getattr(self.store, "step_skipped", None)
                if skip is not None:
                    skip(f"plan:{item.id}")

    # ---------------------------------------------------------------- decisions
    def record_decision(self, key: str, value: str, rationale: str = "", evidence: list[str] | None = None) -> Decision:
        with self._lock:
            d = Decision(key, value, rationale, list(evidence or []))
            self.decisions[key] = d
        return d

    # ---------------------------------------------------------------- papers
    def add_paper(self, role: str, record: PaperRecord, **fields: Any) -> Paper:
        with self._lock:
            existing = next((p for p in self.papers.values() if p.record.pmid == record.pmid and p.role == role), None)
            if existing:
                return existing
            token = f"P{len(self.papers) + 1}"
            paper = Paper(token, role, record)
            self.papers[token] = paper
        self.store.save_paper(role, record, token=token, **fields)
        return paper

    def screen(self, token: str, screening: dict[str, Any], selected: bool) -> None:
        with self._lock:
            paper = self.papers[token]
            paper.screening, paper.selected = screening, selected
        self.store.save_paper(paper.role, paper.record, token=token, screening=screening, selected=selected,
                              stage="screened")

    def paper_by_pmid(self, pmid: str) -> Paper | None:
        return next((p for p in self.papers.values() if p.record.pmid == pmid), None)

    # ---------------------------------------------------------------- evidence
    def add_evidence(self, **fields: Any) -> Evidence:
        with self._lock:
            ev = Evidence(id=f"E{len(self.evidence) + 1}", **fields)
            self.evidence[ev.id] = ev
        return ev

    # ---------------------------------------------------------------- sub-agent tasks
    def start_task(self, kind: str, summary: str, model: str = "") -> Task:
        with self._lock:
            task = Task(id=f"T{len(self.tasks) + 1}", kind=kind, summary=summary, model=model)
            self.tasks[task.id] = task
        return task

    def finish_task(self, task: Task, result: str, usage: Any = None, failed: bool = False) -> None:
        with self._lock:
            task.status = "failed" if failed else "done"
            task.result = result[:300]
            task.seconds = round(time.monotonic() - task.started, 1)
            if usage is not None:
                task.input_tokens = getattr(usage, "input_tokens", 0) or 0
                task.output_tokens = getattr(usage, "output_tokens", 0) or 0

    # ---------------------------------------------------------------- warnings and drafts
    def warn(self, message: str, step: str = "") -> None:
        with self._lock:
            self.warnings.append(message)
        self.store.warn(message, step)

    def save_draft(self, section: str, content: dict[str, Any]) -> None:
        with self._lock:
            self.drafts[section] = content
        self.bump(section)

    def bump(self, section: str) -> int:
        """Mark a section as changed now (used to know whether a review issue was addressed afterwards)."""
        with self._lock:
            self.version += 1
            self.section_version[section] = self.version
            return self.version

    def issue_resolved(self, issue: dict[str, Any], at: int) -> bool:
        """An issue is resolved when its section changed after the review, or a decision explains it."""
        section = "methods" if issue["section"] == "sample_size" else issue["section"]
        changed = max(self.section_version.get(section, 0),
                      self.section_version.get("sample_size", 0) if issue["section"] == "sample_size" else 0)
        return changed > at or f"review:{issue['id']}" in self.decisions

    # ---------------------------------------------------------------- views
    def digest(self) -> str:
        """Compact status for the lead: plan, decisions, kept papers, evidence ids, open tasks, drafts."""
        with self._lock:
            lines = ["PLAN: " + "; ".join(f"[{p.status}] {p.id} {p.title}" + (f" ({p.note[:60]})" if p.note else "")
                                         for p in self.plan)]
            if self.decisions:
                lines.append("DECISIONS: " + "; ".join(f"{d.key}={d.value[:80]}" for d in self.decisions.values()))
            for role in ("background", "methods", "intro"):
                kept = [p for p in self.papers.values() if p.role == role]
                if kept:
                    sel = [p.token for p in kept if p.selected]
                    lines.append(f"PAPERS[{role}]: {len(kept)} kept, selected {','.join(sel) or 'none'}")
            if self.evidence:
                lines.append("EVIDENCE: " + "; ".join(
                    f"{e.id}({e.token}) {e.quantity[:50]}={e.value:g}{e.unit if e.unit != 'proportion' else ''}"
                    f"{' [full text]' if e.source == 'full_text' else ''}{'' if e.verified else ' UNVERIFIED'}"
                    for e in self.evidence.values()))
            if self.tasks:
                done = sum(t.status == "done" for t in self.tasks.values())
                open_ = [f"{t.id}:{t.kind}" for t in self.tasks.values() if t.status == "running"]
                failed = [f"{t.id}:{t.kind}" for t in self.tasks.values() if t.status == "failed"]
                lines.append(f"TASKS: {done} done" + (f"; running {','.join(open_)}" if open_ else "")
                             + (f"; failed {','.join(failed)}" if failed else ""))
            lines.append("SUBMITTED: " + (", ".join(self.drafts) or "nothing yet"))
            if self.reviews:
                last = self.reviews[-1]
                open_ = [i["id"] for i in last["issues"] if not self.issue_resolved(i, last["at"])]
                lines.append(f"REVIEW: {len(last['issues'])} issues, open: {','.join(open_) or 'none'}")
            text = "\n".join(lines)
        return text if len(text) <= DIGEST_MAX_CHARS else text[: DIGEST_MAX_CHARS - 20] + "\n…(truncated)"

    def to_json(self) -> dict[str, Any]:
        """The trace stored under result["agent"]: plan, decisions, evidence and tasks (no raw abstracts)."""
        with self._lock:
            return {
                "plan": [asdict(p) for p in self.plan],
                "decisions": [asdict(d) for d in self.decisions.values()],
                "evidence": [asdict(e) for e in self.evidence.values()],
                "tasks": [{k: v for k, v in asdict(t).items() if k != "started"} for t in self.tasks.values()],
                "papers": [{"token": p.token, "role": p.role, "pmid": p.record.pmid, "title": p.record.title,
                            "year": p.record.year, "selected": p.selected, "screening": p.screening}
                           for p in self.papers.values()],
                "warnings": list(self.warnings),
                "reviews": list(self.reviews),
            }
