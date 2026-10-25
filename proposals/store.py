"""`engine.Store` on the database: every progress event, paper, model call and result is saved for one proposal.

Database writes happen only on the thread that runs the pipeline. Model calls made from the engine's parallel
threads are queued and written by that thread at its next event, so no extra database connections are opened and
writes never race.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from django.db import transaction
from django.utils import timezone

from engine.schemas import PaperRecord
from proposals.models import LLMCall, Paper, Proposal, Reference, StepLog

PAPER_METADATA = ("journal_abbrev", "volume", "issue", "pages", "mesh", "retracted", "is_book", "book_title",
                  "book_accession", "book_sections", "reference_pmids")  # fmt: skip


class DjangoStore:
    """Saves one proposal's run. Create it on the thread that will call `engine.pipeline.run`."""

    def __init__(self, proposal: Proposal):
        self.proposal = proposal
        self.owner = threading.get_ident()
        self.pending: list[tuple[Callable[..., None], tuple[Any, ...], dict[str, Any]]] = []
        self.lock = threading.Lock()
        self.warnings: list[dict[str, str]] = list(proposal.warnings or [])
        self.searches: list[dict[str, Any]] = list(proposal.searches or [])
        self.partial_sections: dict[str, Any] = {}

    # -- thread routing --

    def _do(self, fn: Callable[..., None], *args: Any, **kwargs: Any) -> None:
        """Run a write now on the owner thread, or queue it when called from a worker thread."""
        if threading.get_ident() != self.owner:
            with self.lock:
                self.pending.append((fn, args, kwargs))
            return
        self.flush()
        fn(*args, **kwargs)

    def flush(self) -> None:
        with self.lock:
            pending, self.pending = self.pending, []
        for fn, args, kwargs in pending:
            fn(*args, **kwargs)

    def _touch(self, step: str | None = None) -> None:
        fields: dict[str, Any] = {"heartbeat_at": timezone.now()}
        if step:
            fields["current_step"] = step
        Proposal.objects.filter(pk=self.proposal.pk).update(**fields)

    # -- engine.Store --

    def step_started(self, step: str, label: str = "") -> None:
        self._do(self._log_step, step, "started", label)

    def step_done(self, step: str, detail: str = "") -> None:
        self._do(self._log_step, step, "done", detail)

    def step_failed(self, step: str, reason: str) -> None:
        self._do(self._log_step, step, "failed", reason)

    def warn(self, message: str, step: str = "") -> None:
        self._do(self._warn, message, step)

    def log_search(self, step: str, lane: str, query: str, count: int, pmids: list[str], error: str = "") -> None:
        entry = {"step": step, "lane": lane, "query": query, "count": count, "pmids": list(pmids), "error": error}
        self._do(self._search, entry)

    def save_paper(self, role: str, paper: PaperRecord, **fields: Any) -> None:
        self._do(self._paper, role, paper, fields)

    def save_profile(self, profile: dict[str, Any]) -> None:
        self._do(self._update, profile=profile)

    def save_design(self, decision: dict[str, Any]) -> None:
        self._do(self._update, design_decision=decision)

    def save_section(self, key: str, data: dict[str, Any]) -> None:
        self._do(self._section, key, data)

    def save_result(self, result: dict[str, Any]) -> None:
        self._do(self._result, result)

    def log_call(self, record: dict[str, Any]) -> None:
        self._do(self._call, record)

    # -- writers (owner thread only) --

    def _log_step(self, step: str, status: str, message: str) -> None:
        StepLog.objects.create(proposal_id=self.proposal.pk, step=step, status=status, message=message[:5000])
        self._touch(step if status == "started" else None)

    def _warn(self, message: str, step: str) -> None:
        self.warnings.append({"step": step, "message": message})
        StepLog.objects.create(proposal_id=self.proposal.pk, step=step or "warning", status="warning",
                               message=message[:5000])  # fmt: skip
        Proposal.objects.filter(pk=self.proposal.pk).update(warnings=self.warnings, heartbeat_at=timezone.now())

    def _search(self, entry: dict[str, Any]) -> None:
        self.searches.append(entry)
        Proposal.objects.filter(pk=self.proposal.pk).update(searches=self.searches, heartbeat_at=timezone.now())

    def _paper(self, role: str, paper: PaperRecord, fields: dict[str, Any]) -> None:
        lookup: dict[str, Any] = {"proposal_id": self.proposal.pk, "role": role}
        if paper.pmid:
            lookup["pmid"] = paper.pmid
        else:
            lookup["token"] = fields.get("token", "")
        defaults = {
            "pmid": paper.pmid,
            "doi": paper.doi[:255],
            "pmcid": paper.pmcid[:20],
            "title": paper.title,
            "abstract": paper.abstract,
            "journal": paper.journal[:300],
            "year": paper.year,
            "authors": paper.authors,
            "publication_types": paper.publication_types,
            "metadata": {key: getattr(paper, key) for key in PAPER_METADATA},
        }
        for key in ("token", "lanes", "scores", "screening", "stage", "selected", "paragraph", "flags"):
            if key in fields:
                defaults[key] = fields[key] if fields[key] is not None else defaults.get(key)
        Paper.objects.update_or_create(**lookup, defaults=defaults)

    def _update(self, **fields: Any) -> None:
        Proposal.objects.filter(pk=self.proposal.pk).update(heartbeat_at=timezone.now(), **fields)

    def _section(self, key: str, data: dict[str, Any]) -> None:
        self.partial_sections[key] = data
        self._update(result={"partial": True, "sections": self.partial_sections})

    def _call(self, record: dict[str, Any]) -> None:
        LLMCall.objects.create(
            proposal_id=self.proposal.pk,
            step=record.get("step", "")[:100],
            tier=record.get("tier", ""),
            model=record.get("model", "")[:100],
            messages=record.get("messages", []),
            response=record.get("response", ""),
            prompt_tokens=record.get("prompt_tokens") or 0,
            completion_tokens=record.get("completion_tokens") or 0,
            latency_ms=record.get("latency_ms") or 0,
            status=record.get("status", "ok"),
            error=record.get("error", ""),
            attempt=record.get("attempt", 1),
            cost_usd=record.get("cost_usd"),
        )
        self._touch()

    def _result(self, result: dict[str, Any]) -> None:
        with transaction.atomic():
            Reference.objects.filter(proposal_id=self.proposal.pk).delete()
            Reference.objects.bulk_create(
                [
                    Reference(
                        proposal_id=self.proposal.pk,
                        number=r["number"],
                        token=", ".join(r["tokens"])[:100],
                        text=r["text"],
                        pmid=r.get("pmid", ""),
                        url=r.get("url", "")[:300],
                    )
                    for r in result.get("references", [])
                ]  # fmt: skip
            )
            self._update(result=result, usage=result.get("usage", {}))
