"""Small middlewares for embedding (CSP frame-ancestors) and the optional web access code."""

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

# Paths that never need the web access code: the JSON API has its own token auth, and the access page itself.
OPEN_PREFIXES = ("/api/", "/access", "/static/", "/admin/")


class FrameAncestorsMiddleware:
    """Send `Content-Security-Policy: frame-ancestors …` so only allowed sites can embed the pages.

    Why CSP and not X-Frame-Options: X-Frame-Options can only say DENY or SAMEORIGIN, while a platform such as
    DrNext Academy needs to embed the app from another origin.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", f"frame-ancestors {settings.FRAME_ANCESTORS}")
        return response


class AccessCodeMiddleware:
    """When WEB_ACCESS_CODE is set, ask for it once per browser session before showing the web UI."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        code = settings.WEB_ACCESS_CODE
        if code and not request.path.startswith(OPEN_PREFIXES) and not request.session.get("access_ok"):
            return redirect(f"/access?next={request.get_full_path()}")
        return self.get_response(request)
