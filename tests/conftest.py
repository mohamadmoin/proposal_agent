"""Shared fixtures: the knowledge, the fake model, offline PubMed and a fixed clock. No network, no keys."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine.knowledge import get_knowledge
from engine.llm import LLM, LLMConfig
from engine.pubmed import OfflinePubMed

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any real HTTP request in a test is a bug: block httpx's transport."""
    import httpx

    def refuse(self, request):
        raise RuntimeError(f"network access attempted in a test: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture(scope="session")
def knowledge():
    return get_knowledge()


@pytest.fixture
def fake_llm(knowledge):
    llm = LLM(LLMConfig(provider="fake", model_small="fake-small", model_strong="fake-strong"), knowledge)
    llm.retry_sleep = 0
    return llm


@pytest.fixture(scope="session")
def offline_pubmed():
    return OfflinePubMed(FIXTURES / "pubmed")


@pytest.fixture
def now():
    return datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
