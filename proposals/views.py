"""Web pages: the form (/), recent proposals (/p/), live progress and results (/p/<id>), the access code page."""

from __future__ import annotations

import hmac

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods

from engine.schemas import SECTION_KEYS
from proposals import services
from proposals.models import Proposal

SECTION_LABELS = {
    "introduction": "Introduction",
    "aims": "Aims, hypotheses and questions",
    "background": "Background (پیشینه)",
    "methods": "Methods (۳-۱ … ۳-۵)",
}


def design_choices() -> list[tuple[str, str]]:
    knowledge = services.knowledge()
    choices = [
        (d.label, f"{d.name['en']} — {d.name['fa']}") for d in knowledge.designs.values() if d.label != "general"
    ]
    return choices + [("other", "Other (type its name) — سایر")]


def demo_mode() -> dict[str, bool]:
    """Tell the user when answers come from the fake model or PubMed is served from fixtures."""
    llm, pubmed = services.engine_dependencies()
    return {"fake_llm": llm.config.provider == "fake", "offline_pubmed": type(pubmed).__name__ == "OfflinePubMed"}


@require_http_methods(["GET", "POST"])
def index(request: HttpRequest) -> HttpResponse:
    """The form. On POST, queue the proposal and go to its progress page."""
    error = ""
    values: dict = {"language": "fa", "sections": list(SECTION_KEYS)}
    if request.method == "POST":
        values = {
            "title": request.POST.get("title", ""),
            "purpose": request.POST.get("purpose", ""),
            "design": request.POST.get("design", ""),
            "design_other_name": request.POST.get("design_other_name", ""),
            "language": request.POST.get("language", "fa"),
            "sections": request.POST.getlist("sections"),
        }
        try:
            values["statpearls_html"] = read_upload(request)
            if not values["sections"]:
                raise services.InputError("Choose at least one section.")
            proposal = services.create_proposal(values)
            return redirect("proposal_detail", proposal_id=proposal.id)
        except services.InputError as exc:
            error = str(exc)
    context = {
        "values": values,
        "error": error,
        "designs": design_choices(),
        "sections": [(key, SECTION_LABELS[key]) for key in SECTION_KEYS],
        "demo": demo_mode(),
    }
    return render(request, "proposals/index.html", context, status=422 if error else 200)


def read_upload(request: HttpRequest) -> str:
    """The optional saved StatPearls page (≤ 3 MB), decoded as UTF-8."""
    upload = request.FILES.get("statpearls_file")
    if not upload:
        return ""
    if upload.size > services.HTML_MAX_BYTES:
        raise services.InputError("The uploaded HTML page must be at most 3 MB.")
    return upload.read().decode("utf-8", errors="replace")


@require_GET
def proposal_list(request: HttpRequest) -> HttpResponse:
    return render(request, "proposals/list.html", {"proposals": Proposal.objects.all()[:50]})


@require_GET
def proposal_detail(request: HttpRequest, proposal_id) -> HttpResponse:
    proposal = get_object_or_404(Proposal, pk=proposal_id)
    return render(request, "proposals/detail.html", {"proposal": proposal, "demo": demo_mode()})


@require_GET
def proposal_status(request: HttpRequest, proposal_id) -> JsonResponse:
    """What the progress page polls every 2 seconds."""
    proposal = get_object_or_404(Proposal, pk=proposal_id)
    return JsonResponse(services.proposal_payload(proposal, trace=True), json_dumps_params={"ensure_ascii": False})


@require_GET
def proposal_docx(request: HttpRequest, proposal_id) -> HttpResponse:
    proposal = get_object_or_404(Proposal, pk=proposal_id)
    if proposal.status not in (Proposal.Status.DONE, Proposal.Status.DONE_WITH_ERRORS) or not proposal.result:
        raise Http404("The Word file is ready when the proposal is done.")
    response = HttpResponse(
        services.docx_bytes(proposal),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{services.docx_filename(proposal)}"'
    return response


@require_http_methods(["GET", "POST"])
def access(request: HttpRequest) -> HttpResponse:
    """Ask once per session for WEB_ACCESS_CODE (when it is set)."""
    target = request.POST.get("next") or request.GET.get("next") or "/"
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        target = "/"
    if not settings.WEB_ACCESS_CODE:
        return redirect(target)
    error = ""
    if request.method == "POST":
        code = request.POST.get("code", "")
        if hmac.compare_digest(code.encode(), settings.WEB_ACCESS_CODE.encode()):
            request.session["access_ok"] = True
            return redirect(target)
        error = "That code is not right."
    return render(request, "proposals/access.html", {"next": target, "error": error}, status=403 if error else 200)
