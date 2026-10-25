"""JSON API (django-ninja). Routes live under /api/v1/…; OpenAPI docs at /api/docs.

Auth: `Authorization: Bearer <token>`, checked in constant time against API_TOKENS. When no tokens are configured
the API is open only in debug mode; otherwise it answers 403, so a forgotten setting never exposes it.
"""

from __future__ import annotations

import hmac
from typing import Literal
from uuid import UUID

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from ninja import NinjaAPI, Router, Schema, Status
from ninja.errors import HttpError
from ninja.security import HttpBearer
from pydantic import Field

from proposals import services
from proposals.models import Proposal


class BearerAuth(HttpBearer):
    """Bearer-token check with the 'no tokens → 403 unless DEBUG' rule."""

    def __call__(self, request: HttpRequest):
        tokens = list(settings.API_TOKENS)
        if not tokens:
            if settings.DEBUG:
                return "debug-open"
            raise HttpError(403, "The API is disabled: set API_TOKENS (or DJANGO_DEBUG=1 for local development).")
        return super().__call__(request)

    def authenticate(self, request: HttpRequest, token: str):
        for allowed in settings.API_TOKENS:
            if hmac.compare_digest(token.encode(), allowed.encode()):
                return token
        return None


api = NinjaAPI(
    title="Proposal Agent API",
    version="1",
    description="Draft research-proposal sections from PubMed evidence. The output is a draft; the researcher is "
    "the author.",
    docs_url="/docs",
    urls_namespace="api",
    auth=BearerAuth(),
)
v1 = Router()

SectionKey = Literal["introduction", "aims", "background", "methods"]


class ProposalIn(Schema):
    title: str = Field(description="Study title, Persian or English (5–300 characters).")
    purpose: str = Field("", description="Optional purpose (≤ 2,000 characters).")
    design: str = Field("", description="A label from GET /designs, 'other' (then give design_other_name), or empty.")
    design_other_name: str = ""
    language: Literal["fa", "en"] = "fa"
    sections: list[SectionKey] = Field(default_factory=list, description="Empty means all sections.")
    statpearls_html: str = Field("", description="Optional StatPearls/Bookshelf page saved by the user (≤ 3 MB).")
    client_ref: str = Field("", description="Your own reference, to find proposals later.")


class ProposalCreated(Schema):
    id: UUID
    status: str
    url: str
    docx_url: str


class ProposalSummary(Schema):
    id: UUID
    title: str
    status: str
    language: str
    client_ref: str
    created_at: str


@v1.post("/proposals", response={201: ProposalCreated})
def create_proposal(request, payload: ProposalIn):
    """Create a proposal. It is queued at once; poll GET /proposals/{id} until it is finished."""
    try:
        proposal = services.create_proposal(payload.model_dump())
    except services.InputError as exc:
        raise HttpError(422, str(exc)) from exc
    created = {
        "id": proposal.id,
        "status": proposal.status,
        "url": f"/api/v1/proposals/{proposal.id}",
        "docx_url": f"/api/v1/proposals/{proposal.id}/docx",
    }
    return Status(201, created)


@v1.get("/proposals/{proposal_id}")
def get_proposal(request, proposal_id: UUID, trace: bool = False):
    """Status, steps and warnings; when finished also the sections, references, selected papers and usage."""
    proposal = get_object_or_404(Proposal, pk=proposal_id)
    return services.proposal_payload(proposal, trace=trace)


@v1.get("/proposals/{proposal_id}/docx")
def get_docx(request, proposal_id: UUID):
    """The Word file of a finished proposal."""
    proposal = get_object_or_404(Proposal, pk=proposal_id)
    if proposal.status not in (Proposal.Status.DONE, Proposal.Status.DONE_WITH_ERRORS) or not proposal.result:
        raise HttpError(409, f"The proposal is {proposal.status}; the Word file is ready when it is done.")
    response = HttpResponse(
        services.docx_bytes(proposal),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{services.docx_filename(proposal)}"'
    return response


@v1.get("/proposals", response=list[ProposalSummary])
def list_proposals(request, client_ref: str = ""):
    """The 50 most recent proposals, optionally only those with this client_ref."""
    queryset = Proposal.objects.all()
    if client_ref:
        queryset = queryset.filter(client_ref=client_ref)
    return [
        {"id": p.id, "title": p.title, "status": p.status, "language": p.language, "client_ref": p.client_ref,
         "created_at": p.created_at.isoformat()}
        for p in queryset[:50]
    ]  # fmt: skip


@v1.get("/designs")
def list_designs(request):
    """The study designs the app knows (labels for the `design` field)."""
    knowledge = services.knowledge()
    return [
        {"label": d.label, "name": d.name, "sample_size_formulas": d.sample_size}
        for d in knowledge.designs.values()
        if d.label != "general"
    ] + [{"label": "other", "name": {"fa": "سایر", "en": "Other (give its name)"}, "sample_size_formulas": []}]


@v1.get("/healthz", auth=None)
def healthz(request):
    """Liveness check that also proves the database answers. Needs no auth."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return {"status": "ok"}


api.add_router("/v1", v1)
