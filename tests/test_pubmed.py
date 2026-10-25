import threading
import time

import httpx
import pytest

from engine.pubmed import (
    OfflinePubMed,
    PubMedClient,
    PubMedError,
    RateLimiter,
    parse_pubmed_xml,
    query_words,
)
from tests.conftest import FIXTURES

PUBMED = FIXTURES / "pubmed"

HANDMADE = b"""<?xml version="1.0"?>
<PubmedArticleSet>
<PubmedArticle><MedlineCitation><PMID>111</PMID><Article>
  <Journal><JournalIssue><Volume>12</Volume><Issue>3</Issue><PubDate><MedlineDate>2019 Jan-Feb</MedlineDate></PubDate>
  </JournalIssue><Title>Test Journal</Title><ISOAbbreviation>Test J</ISOAbbreviation></Journal>
  <ArticleTitle>A <i>tidy</i> title.</ArticleTitle>
  <Pagination><MedlinePgn>10-20</MedlinePgn></Pagination>
  <ELocationID EIdType="doi">10.1000/xyz</ELocationID>
  <Abstract><AbstractText Label="BACKGROUND">Why.</AbstractText><AbstractText Label="RESULTS">It was 45%.</AbstractText>
  </Abstract>
  <AuthorList><Author><LastName>Smith</LastName><Initials>JA</Initials></Author>
  <Author><CollectiveName>The Study Group</CollectiveName></Author></AuthorList>
  <PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
</Article>
<CommentsCorrectionsList><CommentsCorrections RefType="RetractionIn"><PMID>999</PMID></CommentsCorrections>
</CommentsCorrectionsList>
</MedlineCitation>
<PubmedData><ArticleIdList><ArticleId IdType="pmc">PMC123</ArticleId></ArticleIdList></PubmedData>
</PubmedArticle>
</PubmedArticleSet>"""


def test_handmade_article_fields():
    (record,) = parse_pubmed_xml(HANDMADE)
    assert record.title == "A tidy title."
    assert record.year == 2019  # from MedlineDate
    assert record.abstract == "BACKGROUND: Why.\nRESULTS: It was 45%."
    assert record.authors == ["Smith JA", "The Study Group"]
    assert (record.volume, record.issue, record.pages) == ("12", "3", "10-20")
    assert record.doi == "10.1000/xyz" and record.pmcid == "PMC123"
    assert record.retracted  # RetractionIn


def test_real_articles_parse():
    records = parse_pubmed_xml((PUBMED / "articles.xml").read_bytes())
    assert len(records) == 60
    first = records[0]
    assert first.pmid == "30921001"
    assert first.journal == "Clinical obstetrics and gynecology" and first.year == 2019
    assert first.authors == ["Sullivan SA"] and "Review" in first.publication_types
    assert "Vitamin D Deficiency" in first.mesh
    labelled = next(r for r in records if r.pmid == "36461014")
    assert labelled.abstract.startswith("BACKGROUND: ")


def test_retracted_publication_flag():
    (record,) = parse_pubmed_xml((PUBMED / "retracted.xml").read_bytes())
    assert record.retracted
    assert "Retracted Publication" in record.publication_types


def test_book_article():
    books = parse_pubmed_xml((PUBMED / "statpearls_books.xml").read_bytes())
    chapter = next(b for b in books if b.pmid == "30335299")
    assert chapter.is_book and chapter.book_title == "StatPearls"
    assert chapter.title == "Vitamin D Deficiency"
    assert chapter.book_accession == "NBK532266"
    assert chapter.authors == ["Kaur J", "Khare S", "Givler A"]
    assert chapter.year == 2025
    assert "Epidemiology" in chapter.book_sections
    assert len(chapter.reference_pmids) == 49 and chapter.reference_pmids[0] == "31101452"


def test_query_words():
    words = query_words('("Vitamin D Deficiency"[mh] OR pregnant[tiab]) AND 2017:2026[dp]')
    assert words == ["vitamin", "deficiency", "pregnant"]


def test_offline_search_is_deterministic_and_typed():
    pubmed = OfflinePubMed(PUBMED)
    first = pubmed.esearch("vitamin D deficiency pregnancy prevalence", retmax=10)
    assert first == pubmed.esearch("vitamin D deficiency pregnancy prevalence", retmax=10)
    assert len(first.pmids) == 10 and first.count >= 10
    books = pubmed.esearch("(vitamin D deficiency) AND statpearls[book]", retmax=5)
    assert "30335299" in books.pmids
    assert all(r.is_book for r in pubmed.efetch(books.pmids))
    recent = pubmed.esearch("(vitamin pregnancy) AND 2022:2026[dp]", retmax=50)
    assert all(2022 <= r.year <= 2026 for r in pubmed.efetch(recent.pmids))


class FrozenClock:
    """time.monotonic() that never moves and a sleep() that only records how long it was asked to wait."""

    def __init__(self):
        self.delays = []
        self.lock = threading.Lock()

    def monotonic(self):
        return 100.0

    def sleep(self, seconds):
        with self.lock:
            self.delays.append(round(seconds, 3))


def test_rate_limiter_gives_parallel_callers_spaced_slots(monkeypatch):
    import engine.pubmed as pubmed_module

    clock = FrozenClock()
    monkeypatch.setattr(pubmed_module, "time", clock)
    limiter = RateLimiter(per_second=20, min_gap=0.05)
    threads = [threading.Thread(target=limiter.wait) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # The first caller goes at once; each later caller waits one more interval: no bursts.
    assert sorted(clock.delays) == [0.05, 0.1, 0.15, 0.2]


def test_rate_limiter_really_waits():
    limiter = RateLimiter(per_second=50, min_gap=0.05)
    start = time.monotonic()
    for _ in range(3):
        limiter.wait()
    assert time.monotonic() - start >= 0.095


def test_rate_limiter_respects_the_110_ms_floor():
    assert RateLimiter(per_second=9).interval == pytest.approx(0.111, abs=0.001)
    assert RateLimiter(per_second=3).interval == pytest.approx(0.333, abs=0.001)
    assert RateLimiter(per_second=50).interval == 0.110


def make_client(handler):
    client = PubMedClient(api_key="SECRETKEY", http=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0)
    client.limiter = RateLimiter(per_second=1000, min_gap=0)
    return client


def test_client_retries_429_then_succeeds_and_sends_identity():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(429)
        return httpx.Response(200, json={"esearchresult": {"count": "2", "idlist": ["1", "2"]}})

    result = make_client(handler).esearch("asthma", retmax=2)
    assert result.count == 2 and result.pmids == ["1", "2"]
    assert len(calls) == 3
    assert calls[-1].url.params["tool"] == "proposal_agent"
    assert calls[-1].url.params["api_key"] == "SECRETKEY"


def test_client_gives_up_after_three_retries_and_redacts_key():
    def handler(request):
        return httpx.Response(503)

    with pytest.raises(PubMedError) as info:
        make_client(handler).esearch("asthma")
    assert "SECRETKEY" not in str(info.value)


def test_efetch_posts_chunks_of_100():
    sizes = []
    xml = (PUBMED / "retracted.xml").read_bytes()

    def handler(request):
        assert request.method == "POST"
        body = dict(httpx.QueryParams(request.content.decode()))
        sizes.append(len(body["id"].split(",")))
        return httpx.Response(200, content=xml)

    make_client(handler).efetch([str(i) for i in range(250)])
    assert sizes == [100, 100, 50]
