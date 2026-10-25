"""The database-polled worker (docs/DESIGN.md, D3): claim the oldest queued proposal, run it, repeat.

`SELECT … FOR UPDATE SKIP LOCKED` lets several workers poll the same table without taking the same job.
"""

from __future__ import annotations

import logging
import signal
import time
import traceback
from datetime import timedelta
from typing import Any

from django.db import close_old_connections, transaction
from django.utils import timezone

from engine.pipeline import RunOptions, run
from proposals.models import Proposal
from proposals.services import engine_dependencies, engine_request
from proposals.store import DjangoStore

log = logging.getLogger(__name__)
STALE_AFTER = timedelta(minutes=15)


def requeue_stale(now: Any = None) -> int:
    """Put back jobs whose worker died: running, with no heartbeat for 15 minutes."""
    cutoff = (now or timezone.now()) - STALE_AFTER
    stale = Proposal.objects.filter(status=Proposal.Status.RUNNING, heartbeat_at__lt=cutoff)
    return stale.update(status=Proposal.Status.QUEUED, current_step="requeued after a stale heartbeat")


def claim_next() -> Proposal | None:
    """Take the oldest queued proposal inside a transaction, skipping rows another worker has locked."""
    with transaction.atomic():
        proposal = (
            Proposal.objects.select_for_update(skip_locked=True)
            .filter(status=Proposal.Status.QUEUED)
            .order_by("created_at")
            .first()
        )
        if proposal is None:
            return None
        now = timezone.now()
        proposal.status = Proposal.Status.RUNNING
        proposal.started_at = now
        proposal.heartbeat_at = now
        proposal.error = ""
        proposal.save(update_fields=["status", "started_at", "heartbeat_at", "error"])
    return proposal


def process(proposal: Proposal) -> str:
    """Run the pipeline for one proposal and store the final status. Never raises."""
    store = DjangoStore(proposal)
    try:
        llm, pubmed = engine_dependencies()
        result = run(engine_request(proposal), store, llm, pubmed, timezone.now(), RunOptions.from_env())
        status, error = result["status"], ""
        if status == "failed":
            error = "; ".join(s.get("error", "") for s in result["sections"].values() if s.get("error"))
    except Exception as exc:  # a bug or a configuration error: fail this job, keep the worker alive
        log.error("proposal %s crashed: %s\n%s", proposal.pk, exc, traceback.format_exc())
        store.flush()
        store.step_failed("pipeline", str(exc))
        status, error = "failed", str(exc)
    store.flush()
    Proposal.objects.filter(pk=proposal.pk).update(
        status=status, error=error[:5000], finished_at=timezone.now(), heartbeat_at=timezone.now(), current_step=""
    )
    return status


class Worker:
    """The loop behind `manage.py run_worker`. SIGTERM/SIGINT: finish the current job, then exit."""

    def __init__(self, poll: float = 2.0):
        self.poll = poll
        self.stopping = False

    def request_stop(self, signum: int, frame: Any) -> None:
        log.info("signal %s received: finishing the current job, then exiting", signum)
        self.stopping = True

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)

    def run_once(self) -> str | None:
        """Process one job if there is one. Returns its final status, or None when the queue was empty."""
        close_old_connections()
        proposal = claim_next()
        if proposal is None:
            return None
        log.info("processing proposal %s", proposal.pk)
        return process(proposal)

    def run_forever(self) -> None:
        requeued = requeue_stale()
        if requeued:
            log.warning("requeued %s stale job(s)", requeued)
        while not self.stopping:
            if self.run_once() is None:
                self.sleep()

    def sleep(self) -> None:
        """Sleep in small slices so a stop signal is noticed quickly."""
        end = time.monotonic() + self.poll
        while not self.stopping and time.monotonic() < end:
            time.sleep(min(0.2, self.poll))
