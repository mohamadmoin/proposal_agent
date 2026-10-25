from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from proposals.models import Proposal
from proposals.services import create_proposal
from proposals.worker import Worker, claim_next, process, requeue_stale

TITLE = "Prevalence of vitamin D deficiency among pregnant women"
pytestmark = pytest.mark.django_db


def test_claims_the_oldest_queued_job():
    first = create_proposal({"title": TITLE + " one"})
    create_proposal({"title": TITLE + " two"})
    claimed = claim_next()
    assert claimed.pk == first.pk
    claimed.refresh_from_db()
    assert claimed.status == "running" and claimed.started_at and claimed.heartbeat_at


def test_empty_queue(capsys):
    call_command("run_worker", "--once")
    assert "queue empty" in capsys.readouterr().out
    assert claim_next() is None


def test_stale_running_jobs_are_requeued():
    stale = create_proposal({"title": TITLE})
    fresh = create_proposal({"title": TITLE + " fresh"})
    old = timezone.now() - timedelta(minutes=20)
    Proposal.objects.filter(pk=stale.pk).update(status="running", heartbeat_at=old)
    Proposal.objects.filter(pk=fresh.pk).update(status="running", heartbeat_at=timezone.now())
    assert requeue_stale() == 1
    assert Proposal.objects.get(pk=stale.pk).status == "queued"
    assert Proposal.objects.get(pk=fresh.pk).status == "running"


def test_run_once_processes_a_job_in_english():
    proposal = create_proposal({"title": TITLE, "language": "en", "sections": ["background"]})
    assert Worker().run_once() == "done"
    proposal.refresh_from_db()
    assert proposal.status == "done"
    assert proposal.result["sections"]["background"]["paragraphs"]


def test_a_crash_fails_the_job_but_not_the_worker(monkeypatch):
    proposal = create_proposal({"title": TITLE})
    claimed = claim_next()

    def explode(*args, **kwargs):
        raise RuntimeError("LLM_API_KEY is not set")

    monkeypatch.setattr("proposals.worker.run", explode)
    assert process(claimed) == "failed"
    proposal.refresh_from_db()
    assert proposal.status == "failed" and "LLM_API_KEY" in proposal.error and proposal.finished_at


def test_stop_signal_ends_the_loop_after_the_current_job():
    worker = Worker(poll=0.01)
    worker.request_stop(15, None)
    worker.run_forever()  # returns at once instead of looping
    assert worker.stopping
