"""PubMed through NCBI E-utilities (esearch, efetch), a polite rate limiter, the XML parser and an offline stand-in.

NCBI's rules: identify the tool (and an e-mail), stay under 3 requests/s without a key (10 with one), and back off on
errors. The limiter is shared by every thread of the process and spaces requests at least 110 ms apart, so parallel
steps never burst.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from engine.schemas import PaperRecord

log = logging.getLogger(__name__)

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "pubmed"
EFETCH_CHUNK = 100
RETRY_STATUSES = {429, 500, 502, 503, 504}


class PubMedError(RuntimeError):
    """E-utilities answered with an error or not at all."""


@dataclass
class SearchResult:
    """What esearch returns: the total hit count and the PMIDs of the first page, in rank order."""

    count: int
    pmids: list[str] = field(default_factory=list)


class PubMed(Protocol):
    """The interface the engine needs; the live client and the offline stand-in both implement it."""

    def esearch(self, term: str, retmax: int = 50, sort: str = "relevance") -> SearchResult: ...

    def efetch(self, pmids: list[str]) -> list[PaperRecord]: ...


# --- rate limiting ------------------------------------------------------------------------------------------------


class RateLimiter:
    """Thread-safe spacing of requests: each caller waits for its own slot."""

    def __init__(self, per_second: float, min_gap: float = 0.110):
        self.interval = max(1.0 / per_second, min_gap)
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self.interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)


_limiters: dict[bool, RateLimiter] = {}
_limiters_lock = threading.Lock()


def shared_limiter(has_key: bool) -> RateLimiter:
    """One limiter per process (per key mode): 9 requests/s with an API key, 3 without."""
    with _limiters_lock:
        if has_key not in _limiters:
            _limiters[has_key] = RateLimiter(9.0 if has_key else 3.0)
        return _limiters[has_key]


# --- the live client ----------------------------------------------------------------------------------------------


class PubMedClient:
    """E-utilities over httpx with retries, chunked POST efetch and key redaction in logs."""

    def __init__(
        self,
        tool: str = "proposal_agent",
        email: str = "",
        api_key: str = "",
        http: httpx.Client | None = None,
        base_url: str = EUTILS,
        backoff: float = 1.0,
    ):
        self.tool = tool or "proposal_agent"
        self.email = email
        self.api_key = api_key
        self.base_url = base_url
        self.backoff = backoff
        self.http = http or httpx.Client(timeout=30.0)
        self.limiter = shared_limiter(bool(api_key))

    def common_params(self) -> dict[str, str]:
        params = {"tool": self.tool, "db": "pubmed"}
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def redact(self, text: str) -> str:
        """Never write the API key to a log."""
        return text.replace(self.api_key, "***") if self.api_key else text

    def request(self, endpoint: str, params: dict[str, str], post: bool = False) -> httpx.Response:
        """One E-utilities call, retried up to 3 times on 429/5xx/network errors with exponential back-off."""
        url = self.base_url + endpoint
        last_error = ""
        for attempt in range(4):
            self.limiter.wait()
            try:
                if post:
                    response = self.http.post(url, data=params)
                else:
                    response = self.http.get(url, params=params)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code not in RETRY_STATUSES:
                    if response.status_code >= 400:
                        raise PubMedError(f"{endpoint} HTTP {response.status_code}")
                    return response
                last_error = f"HTTP {response.status_code}"
            if attempt < 3:
                log.warning("PubMed %s failed (%s), retrying", endpoint, self.redact(last_error))
                time.sleep(self.backoff * (2**attempt))
        raise PubMedError(f"{endpoint} failed after retries: {self.redact(last_error)}")

    def esearch(self, term: str, retmax: int = 50, sort: str = "relevance") -> SearchResult:
        params = {**self.common_params(), "term": term, "retmax": str(retmax), "retmode": "json", "sort": sort}
        data = self.request("esearch.fcgi", params).json()
        result = data.get("esearchresult") or {}
        if "ERROR" in result or "error" in data:
            raise PubMedError(f"esearch error: {result.get('ERROR') or data.get('error')}")
        return SearchResult(count=int(result.get("count") or 0), pmids=list(result.get("idlist") or []))

    def efetch(self, pmids: list[str]) -> list[PaperRecord]:
        records: list[PaperRecord] = []
        unique = list(dict.fromkeys(p for p in pmids if p))
        for start in range(0, len(unique), EFETCH_CHUNK):
            chunk = unique[start : start + EFETCH_CHUNK]
            params = {**self.common_params(), "id": ",".join(chunk), "retmode": "xml"}
            response = self.request("efetch.fcgi", params, post=True)
            records.extend(parse_pubmed_xml(response.content))
        return records


# --- XML parsing --------------------------------------------------------------------------------------------------


def text_of(element: ET.Element | None) -> str:
    """All text inside an element (titles can contain <i>, <sup>, …), with whitespace tidied."""
    if element is None:
        return ""
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip()


def parse_pubmed_xml(xml: str | bytes) -> list[PaperRecord]:
    """Parse an efetch PubmedArticleSet: journal articles and book chapters."""
    root = ET.fromstring(xml)
    records = []
    for node in root:
        if node.tag == "PubmedArticle":
            records.append(parse_article(node))
        elif node.tag == "PubmedBookArticle":
            records.append(parse_book_article(node))
    return records


def parse_abstract(abstract: ET.Element | None) -> str:
    """Labelled sections become "LABEL: text" lines; unlabelled ones are kept as they are."""
    if abstract is None:
        return ""
    lines = []
    for part in abstract.findall("AbstractText"):
        text = text_of(part)
        if not text:
            continue
        label = (part.get("Label") or "").strip()
        lines.append(f"{label}: {text}" if label else text)
    return "\n".join(lines)


def year_from(*dates: ET.Element | None) -> int | None:
    """The first 4-digit year in PubDate/Year, MedlineDate ("2019 Jan-Feb") or ArticleDate."""
    for date in dates:
        if date is None:
            continue
        for tag in ("Year", "MedlineDate"):
            match = re.search(r"\b(1[89]\d\d|20\d\d)\b", date.findtext(tag) or "")
            if match:
                return int(match.group(1))
    return None


def parse_authors(author_list: ET.Element | None) -> list[str]:
    """ "Lastname Initials" for people, the group name for collective authors."""
    authors = []
    if author_list is None:
        return authors
    for author in author_list.findall("Author"):
        collective = text_of(author.find("CollectiveName"))
        if collective:
            authors.append(collective)
            continue
        last = text_of(author.find("LastName"))
        initials = text_of(author.find("Initials"))
        if last:
            authors.append(f"{last} {initials}".strip())
    return authors


def article_ids(node: ET.Element, path: str) -> dict[str, str]:
    ids = {}
    for item in node.findall(path):
        id_type = item.get("IdType")
        if id_type and item.text:
            ids.setdefault(id_type, item.text.strip())
    return ids


def parse_article(node: ET.Element) -> PaperRecord:
    citation = node.find("MedlineCitation")
    article = citation.find("Article") if citation is not None else None
    if citation is None or article is None:
        raise PubMedError("PubmedArticle without MedlineCitation/Article")
    journal = article.find("Journal")
    issue = journal.find("JournalIssue") if journal is not None else None
    ids = article_ids(node, "PubmedData/ArticleIdList/ArticleId")
    doi = ids.get("doi", "")
    if not doi:
        for loc in article.findall("ELocationID"):
            if loc.get("EIdType") == "doi" and loc.text:
                doi = loc.text.strip()
    pub_types = [text_of(pt) for pt in article.findall("PublicationTypeList/PublicationType")]
    retraction_in = any(c.get("RefType") == "RetractionIn" for c in citation.findall(".//CommentsCorrections"))
    return PaperRecord(
        pmid=text_of(citation.find("PMID")),
        title=text_of(article.find("ArticleTitle")),
        abstract=parse_abstract(article.find("Abstract")),
        journal=text_of(journal.find("Title")) if journal is not None else "",
        journal_abbrev=text_of(journal.find("ISOAbbreviation")) if journal is not None else "",
        year=year_from(issue.find("PubDate") if issue is not None else None, article.find("ArticleDate")),
        volume=text_of(issue.find("Volume")) if issue is not None else "",
        issue=text_of(issue.find("Issue")) if issue is not None else "",
        pages=text_of(article.find("Pagination/MedlinePgn")),
        doi=doi,
        pmcid=ids.get("pmc", ""),
        authors=parse_authors(article.find("AuthorList")),
        publication_types=pub_types,
        mesh=[text_of(m) for m in citation.findall("MeshHeadingList/MeshHeading/DescriptorName")],
        retracted="Retracted Publication" in pub_types or retraction_in,
    )


def parse_book_article(node: ET.Element) -> PaperRecord:
    doc = node.find("BookDocument")
    if doc is None:
        raise PubMedError("PubmedBookArticle without BookDocument")
    book = doc.find("Book")
    ids = article_ids(doc, "ArticleIdList/ArticleId")
    pub_types = [text_of(pt) for pt in doc.findall("PublicationType")]
    return PaperRecord(
        pmid=text_of(doc.find("PMID")),
        title=text_of(doc.find("ArticleTitle")),
        abstract=parse_abstract(doc.find("Abstract")),
        year=year_from(book.find("PubDate") if book is not None else None, doc.find("ContributionDate")),
        authors=parse_authors(doc.find("AuthorList")),
        publication_types=pub_types,
        is_book=True,
        book_title=text_of(book.find("BookTitle")) if book is not None else "",
        book_accession=ids.get("bookaccession", ""),
        book_sections=[text_of(s) for s in doc.findall("Sections/Section/SectionTitle")],
        reference_pmids=[
            a.text.strip() for a in doc.findall("ReferenceList/Reference/ArticleIdList/ArticleId")
            if a.get("IdType") == "pubmed" and a.text
        ],
        retracted="Retracted Publication" in pub_types,
    )  # fmt: skip


# --- offline stand-in ---------------------------------------------------------------------------------------------

STOP_WORDS = {"and", "or", "not", "the", "of", "in", "for", "with", "among", "between", "to", "a", "an", "on"}


class OfflinePubMed:
    """Serves PubMed from the saved XML fixtures (PUBMED_OFFLINE=1): for tests and the keyless demo.

    Searching is a deterministic word match: good enough to exercise every step, not a search engine.
    """

    def __init__(self, fixtures_dir: Path | str | None = None):
        directory = Path(fixtures_dir or os.environ.get("PUBMED_FIXTURES_DIR") or FIXTURES_DIR)
        self.records: dict[str, PaperRecord] = {}
        for path in sorted(directory.glob("*.xml")):
            for record in parse_pubmed_xml(path.read_bytes()):
                self.records.setdefault(record.pmid, record)
        if not self.records:
            raise PubMedError(f"no PubMed fixtures found in {directory}")

    def esearch(self, term: str, retmax: int = 50, sort: str = "relevance") -> SearchResult:
        books_only = "statpearls[book]" in term.lower()
        words = query_words(term)
        years = re.search(r"(\d{4}):(\d{4})\[dp\]", term)
        required = re.search(r'\)\s+AND\s+"?([^"()\[\]]+?)"?\[tiab\]\s*$', term)
        scored = []
        for record in self.records.values():
            if record.is_book != books_only:
                continue
            if years and not (record.year and int(years.group(1)) <= record.year <= int(years.group(2))):
                continue
            haystack = " ".join([record.title, record.abstract, " ".join(record.mesh)]).lower()
            if required and required.group(1).lower() not in haystack:
                continue
            score = sum(1 for w in words if w in haystack)
            if score:
                scored.append((-score, record.pmid))
        scored.sort()
        return SearchResult(count=len(scored), pmids=[pmid for _, pmid in scored[:retmax]])

    def efetch(self, pmids: list[str]) -> list[PaperRecord]:
        return [self.records[p].model_copy(deep=True) for p in dict.fromkeys(pmids) if p in self.records]


def query_words(term: str) -> list[str]:
    """Plain words of a Boolean query: field tags, quotes, operators and short words removed."""
    no_tags = re.sub(r"\[[^\]]*\]", " ", term)
    no_dates = re.sub(r"\d{4}:\d{4}", " ", no_tags)
    words = re.findall(r"[a-z][a-z\-]{2,}", no_dates.lower())
    return list(dict.fromkeys(w for w in words if w not in STOP_WORDS))


def make_pubmed(env: dict[str, str] | None = None) -> PubMed:
    """The offline stand-in when PUBMED_OFFLINE=1, else the live client configured from NCBI_* variables."""
    env = os.environ if env is None else env
    if (env.get("PUBMED_OFFLINE") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return OfflinePubMed()
    return PubMedClient(
        tool=env.get("NCBI_TOOL", "proposal_agent"),
        email=env.get("NCBI_EMAIL", ""),
        api_key=env.get("NCBI_API_KEY", ""),
    )
