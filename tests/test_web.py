import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings

from proposals.models import Proposal
from tests.conftest import FIXTURES

TITLE = "شیوع کمبود ویتامین D در زنان باردار مراجعه‌کننده به مراکز بهداشتی تهران"
pytestmark = pytest.mark.django_db


def test_form_has_every_input(client):
    html = client.get("/").content.decode()
    for name in ('name="title"', 'name="purpose"', 'name="design"', 'name="design_other_name"', 'name="language"',
                 'name="sections"', 'name="statpearls_file"'):  # fmt: skip
        assert name in html
    assert '<label for="title">' in html and "Demo mode" in html


def test_post_creates_and_redirects(client):
    upload = SimpleUploadedFile("page.html", (FIXTURES / "statpearls_page.html").read_bytes(), "text/html")
    response = client.post("/", {"title": TITLE, "language": "fa", "sections": ["introduction", "background"],
                                 "statpearls_file": upload})  # fmt: skip
    proposal = Proposal.objects.get()
    assert response.status_code == 302 and response["Location"] == f"/p/{proposal.id}"
    assert proposal.sections == ["introduction", "background"]
    assert "Vitamin D deficiency is a common condition" in proposal.uploaded_html


def test_post_errors_are_shown(client):
    response = client.post("/", {"title": "abc", "sections": ["aims"]})
    assert response.status_code == 422 and "title must be" in response.content.decode()
    big = SimpleUploadedFile("big.html", b"a" * (3 * 1024 * 1024 + 1), "text/html")
    response = client.post("/", {"title": TITLE, "sections": ["aims"], "statpearls_file": big})
    assert response.status_code == 422 and "3 MB" in response.content.decode()
    response = client.post("/", {"title": TITLE})
    assert response.status_code == 422 and "at least one section" in response.content.decode()


def test_detail_status_and_docx_pages(client):
    client.post("/", {"title": TITLE, "language": "fa", "sections": ["background", "aims", "methods", "introduction"]})
    proposal = Proposal.objects.get()
    page = client.get(f"/p/{proposal.id}")
    assert page.status_code == 200 and "detail.js" in page.content.decode()
    assert client.get(f"/p/{proposal.id}/status.json").json()["status"] == "queued"
    assert client.get(f"/p/{proposal.id}/docx").status_code == 404
    call_command("run_worker", "--once")
    status = client.get(f"/p/{proposal.id}/status.json").json()
    assert status["status"] == "done"
    assert status["trace"]["calls"] and status["trace"]["searches"]
    assert client.get(f"/p/{proposal.id}/docx").status_code == 200
    listing = client.get("/p/").content.decode()
    assert TITLE in listing


def test_frame_ancestors_header(client):
    assert client.get("/").headers["Content-Security-Policy"] == "frame-ancestors 'self'"
    with override_settings(FRAME_ANCESTORS="'self' https://academy.example.org"):
        csp = client.get("/").headers["Content-Security-Policy"]
        assert csp == "frame-ancestors 'self' https://academy.example.org"
    assert "X-Frame-Options" not in client.get("/").headers


@override_settings(WEB_ACCESS_CODE="open-sesame")
def test_access_code_is_asked_once_per_session(client):
    response = client.get("/p/")
    assert response.status_code == 302 and response["Location"].startswith("/access?next=/p/")
    assert client.get("/api/v1/healthz").status_code == 200  # the API has its own auth
    assert client.post("/access", {"code": "wrong", "next": "/p/"}).status_code == 403
    response = client.post("/access", {"code": "open-sesame", "next": "/p/"})
    assert response.status_code == 302 and response["Location"] == "/p/"
    assert client.get("/p/").status_code == 200
    assert client.post("/access", {"code": "open-sesame", "next": "https://evil.example"})["Location"] == "/"


def test_frame_ancestors_setting_restores_csp_quotes():
    from config.settings import normalise_frame_ancestors

    assert normalise_frame_ancestors("self https://a.org") == "'self' https://a.org"
    assert normalise_frame_ancestors("") == "'self'"
