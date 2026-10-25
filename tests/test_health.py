import pytest


@pytest.mark.django_db
def test_healthz_needs_no_auth(client):
    response = client.get("/api/v1/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_docs_are_served(client):
    assert client.get("/api/openapi.json").status_code == 200
