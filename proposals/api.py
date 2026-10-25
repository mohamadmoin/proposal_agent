"""JSON API (django-ninja). Routes live under /api/v1/…; OpenAPI docs at /api/docs."""

from django.db import connection
from ninja import NinjaAPI, Router

api = NinjaAPI(title="Proposal Agent API", version="1", docs_url="/docs", urls_namespace="api")
v1 = Router()


@v1.get("/healthz", auth=None)
def healthz(request):
    """Liveness check that also proves the database answers."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return {"status": "ok"}


api.add_router("/v1", v1)
