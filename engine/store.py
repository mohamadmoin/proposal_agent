"""How the engine reports progress and saves results, without knowing about Django.

`Store` is the interface; proposals/store.py implements it on the database, and `MemoryStore` keeps everything in
lists (tests, smoke_live). Keeping the engine free of Django means it can be tested with fakes and imported by
another platform.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

from engine.schemas import PaperRecord


class Store(Protocol):
    def step_started(self, step: str, label: str = "") -> None: ...

    def step_done(self, step: str, detail: str = "") -> None: ...

    def step_failed(self, step: str, reason: str) -> None: ...

    def warn(self, message: str, step: str = "") -> None: ...

    def log_search(self, step: str, lane: str, query: str, count: int, pmids: list[str], error: str = "") -> None: ...

    def save_paper(self, role: str, paper: PaperRecord, **fields: Any) -> None: ...

    def save_profile(self, profile: dict[str, Any]) -> None: ...

    def save_design(self, decision: dict[str, Any]) -> None: ...

    def save_section(self, key: str, data: dict[str, Any]) -> None: ...

    def save_result(self, result: dict[str, Any]) -> None: ...

    def log_call(self, record: dict[str, Any]) -> None: ...


@dataclass
class MemoryStore:
    """Keeps every event in memory. Thread-safe, because parallel steps report at the same time."""

    steps: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    searches: list[dict[str, Any]] = field(default_factory=list)
    papers: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    sections: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    profile: dict[str, Any] = field(default_factory=dict)
    design: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def step_started(self, step: str, label: str = "") -> None:
        with self._lock:
            self.steps.append({"step": step, "status": "started", "message": label})

    def step_done(self, step: str, detail: str = "") -> None:
        with self._lock:
            self.steps.append({"step": step, "status": "done", "message": detail})

    def step_failed(self, step: str, reason: str) -> None:
        with self._lock:
            self.steps.append({"step": step, "status": "failed", "message": reason})

    def warn(self, message: str, step: str = "") -> None:
        with self._lock:
            self.warnings.append({"step": step, "message": message})

    def log_search(self, step: str, lane: str, query: str, count: int, pmids: list[str], error: str = "") -> None:
        with self._lock:
            self.searches.append(
                {"step": step, "lane": lane, "query": query, "count": count, "pmids": list(pmids), "error": error}
            )

    def save_paper(self, role: str, paper: PaperRecord, **fields: Any) -> None:
        with self._lock:
            entry = self.papers.setdefault((role, paper.pmid), {"role": role, "paper": paper})
            entry["paper"] = paper
            entry.update(fields)

    def save_profile(self, profile: dict[str, Any]) -> None:
        self.profile = dict(profile)

    def save_design(self, decision: dict[str, Any]) -> None:
        self.design = dict(decision)

    def save_section(self, key: str, data: dict[str, Any]) -> None:
        with self._lock:
            self.sections[key] = dict(data)

    def save_result(self, result: dict[str, Any]) -> None:
        self.result = dict(result)

    def log_call(self, record: dict[str, Any]) -> None:
        with self._lock:
            self.calls.append(dict(record))

    def papers_for(self, role: str) -> list[dict[str, Any]]:
        return [entry for (r, _), entry in self.papers.items() if r == role]
