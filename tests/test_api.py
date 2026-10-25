import io

import pytest
from django.core.management import call_command
from django.test import override_settings
from docx import Document

from proposals.models import LLMCall, Paper, Proposal, Reference, StepLog

AUTH = {"HTTP_AUTHORIZATION": "Bearer test-token"}
TITLE = "شیوع کمبود ویتامین D در زنان باردار مراجعه‌کننده به مراکز بهداشتی تهران"
pytestmark = pytest.mark.django_db


def create(client, **payload):
    body = {"title": TITLE, **payload}
    return client.post("/api/v1/proposals", body, content_type="application/json", **AUTH)


def test_auth_is_required(client):
    assert client.get("/api/v1/proposals").status_code == 401
    assert client.get("/api/v1/proposals", HTTP_AUTHORIZATION="Bearer wrong").status_code == 401
    assert client.get("/api/v1/proposals", **AUTH).status_code == 200


@override_settings(API_TOKENS=[], DEBUG=False)
def test_no_tokens_means_closed_unless_debug(client):
    assert client.get("/api/v1/proposals").status_code == 403


@override_settings(API_TOKENS=[], DEBUG=True)
def test_no_tokens_in_debug_is_open(client):
    assert client.get("/api/v1/proposals").status_code == 200


def test_healthz_is_open(client):
    with override_settings(API_TOKENS=[], DEBUG=False):
        assert client.get("/api/v1/healthz").status_code == 200


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"title": "abc"}, "title"),
        ({"title": "x" * 301}, "title"),
        ({"purpose": "p" * 2001}, "purpose"),
        ({"statpearls_html": "a" * (3 * 1024 * 1024 + 1)}, "3 MB"),
        ({"design": "other"}, "design"),
    ],
)
def test_validation_limits(client, payload, message):
    response = create(client, **payload)
    assert response.status_code == 422
    assert message in response.content.decode()


def test_unknown_section_and_language_are_rejected(client):
    assert create(client, sections=["discussion"]).status_code == 422
    assert create(client, language="de").status_code == 422


def test_create_queues_a_job(client):
    response = create(client, purpose="Screening", client_ref="student-7", sections=["background", "aims"])
    assert response.status_code == 201
    data = response.json()
    proposal = Proposal.objects.get(pk=data["id"])
    assert proposal.status == "queued" and proposal.client_ref == "student-7"
    assert proposal.sections == ["aims", "background"]
    detail = client.get(data["url"], **AUTH).json()
    assert detail["status"] == "queued" and "sections" not in detail
    listed = client.get("/api/v1/proposals?client_ref=student-7", **AUTH).json()
    assert [p["id"] for p in listed] == [data["id"]]
    assert client.get("/api/v1/proposals?client_ref=nobody", **AUTH).json() == []


def test_docx_before_finishing_is_409(client):
    data = create(client).json()
    assert client.get(data["docx_url"], **AUTH).status_code == 409


def test_designs(client):
    labels = [d["label"] for d in client.get("/api/v1/designs", **AUTH).json()]
    assert "cross_sectional" in labels and "other" in labels and "general" not in labels


def test_worker_once_then_get_and_docx(client):
    data = create(client).json()
    call_command("run_worker", "--once")
    proposal = Proposal.objects.get(pk=data["id"])
    assert proposal.status == "done", proposal.error
    assert proposal.finished_at and proposal.heartbeat_at
    detail = client.get(data["url"] + "?trace=true", **AUTH).json()
    assert set(detail["sections"]) == {"introduction", "aims", "background", "methods"}
    assert detail["references"] and detail["papers"] and detail["usage"]["calls"] > 20
    assert detail["trace"]["searches"] and detail["trace"]["stages"]["selected"] >= 5
    assert any(s["status"] == "done" for s in detail["steps"])
    assert Reference.objects.filter(proposal=proposal).count() == len(detail["references"])
    assert LLMCall.objects.filter(proposal=proposal).count() == detail["usage"]["calls"]
    assert Paper.objects.filter(proposal=proposal, role="background", selected=True).count() >= 5
    assert StepLog.objects.filter(proposal=proposal).exists()
    response = client.get(data["docx_url"], **AUTH)
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")
    assert Document(io.BytesIO(response.content)).paragraphs[0].text == TITLE
