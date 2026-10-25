"""URL map: the JSON API under /api/v1 (OpenAPI docs at /api/docs), the web pages at /, /p/ and /p/<id>, the admin."""

from django.contrib import admin
from django.urls import include, path

from proposals.api import api

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),  # routes are added under /v1 inside proposals/api.py
    path("", include("proposals.urls")),
]
