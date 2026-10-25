"""StatPearls as the introduction's source: through the official PubMed API only, or a page the user saved.

Bookshelf pages answer scripts with a bot check and the StatPearls site does not allow AI input, so the app never
fetches those pages (docs/DESIGN.md, D2). It uses what E-utilities returns for the chapter (summary, section titles,
reference list) or parses an HTML file the user saved in their own browser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from engine.pubmed import PubMed
from engine.retrieval import drop_reason, tfidf_scores
from engine.schemas import PaperRecord
from engine.store import Store

CHAPTER_RETMAX = 5
MAX_CITED = 8
MAX_CITED_FETCH = 100
MAX_CHAPTER_CHARS = 30_000
REMOVE_TAGS = ["script", "style", "nav", "header", "footer", "aside", "form", "noscript", "button", "svg"]
PMID_PATTERNS = [
    re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d{1,9})"),
    re.compile(r"ncbi\.nlm\.nih\.gov/pubmed/(\d{1,9})"),
    re.compile(r"\bPMID:?\s*(\d{1,9})", re.I),
]
STEP = "introduction"


@dataclass
class UploadedPage:
    """A StatPearls/Bookshelf page the user saved and uploaded, parsed locally."""

    title: str
    sections: list[tuple[str, str]] = field(default_factory=list)  # (heading, text)
    reference_pmids: list[str] = field(default_factory=list)
    nbk: str = ""

    def text(self) -> str:
        parts = [f"{heading}\n{body}" if heading else body for heading, body in self.sections if body]
        return "\n\n".join(parts)[:MAX_CHAPTER_CHARS]


@dataclass
class IntroSource:
    """One source of the introduction: {S1} is the chapter, {S2}… are the papers it cites."""

    token: str
    record: PaperRecord
    text: str
    kind: str  # "chapter" or "cited"


# --- the uploaded page --------------------------------------------------------------------------------------------


def is_references_heading(text: str) -> bool:
    return bool(re.fullmatch(r"\s*(references?|bibliography|منابع)\s*:?\s*", text, flags=re.I))


def pmids_in(element) -> list[str]:
    """PMIDs from links (pubmed.ncbi.nlm.nih.gov/123) or from "PMID: 123" text."""
    found: list[str] = []
    haystacks = [a.get("href", "") for a in element.find_all("a")] + [element.get_text(" ")]
    for text in haystacks:
        for pattern in PMID_PATTERNS:
            found.extend(pattern.findall(text))
    return list(dict.fromkeys(found))


def parse_uploaded_html(html: str) -> UploadedPage:
    """Headings with their paragraphs become sections; the list after "References" gives the cited PMIDs."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(REMOVE_TAGS):
        tag.decompose()
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else (soup.title.get_text(" ", strip=True) if soup.title else "")
    nbk_match = re.search(r"\bNBK\d+\b", html)
    page = UploadedPage(
        title=re.sub(r"\s*-\s*StatPearls.*$", "", title).strip(), nbk=nbk_match.group(0) if nbk_match else ""
    )
    heading, chunks, in_references = "", [], False
    for element in soup.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        if element.name in {"h1", "h2", "h3", "h4"}:
            if chunks and not in_references:
                page.sections.append((heading, " ".join(chunks)))
            heading, chunks = element.get_text(" ", strip=True), []
            in_references = is_references_heading(heading)
            continue
        if in_references:
            if element.name == "li" or element.find_parent(["ol", "ul"]) is None:
                page.reference_pmids.extend(p for p in pmids_in(element) if p not in page.reference_pmids)
            continue
        if element.name == "li" and element.find("p"):
            continue  # its paragraphs are read on their own
        text = element.get_text(" ", strip=True)
        if text and (not chunks or chunks[-1] != text):
            chunks.append(text)
    if chunks and not in_references:
        page.sections.append((heading, " ".join(chunks)))
    return page


# --- the API chapter ----------------------------------------------------------------------------------------------


def find_chapter(pubmed: PubMed, terms: str, profile_text: str, store: Store) -> PaperRecord | None:
    """Search `(<terms>) AND statpearls[book]`, then pick the chapter whose title is most like the profile."""
    query = f"({terms.strip()}) AND statpearls[book]"
    try:
        result = pubmed.esearch(query, retmax=CHAPTER_RETMAX)
    except Exception as exc:
        store.log_search(STEP, "statpearls", query, 0, [], error=str(exc))
        return None
    store.log_search(STEP, "statpearls", query, result.count, result.pmids)
    chapters = [r for r in pubmed.efetch(result.pmids) if r.is_book] if result.pmids else []
    if not chapters:
        return None
    scores = tfidf_scores([c.title for c in chapters], profile_text)
    best = max(range(len(chapters)), key=lambda i: (scores[i], -i))
    return chapters[best]


def cited_papers(pubmed: PubMed, pmids: list[str], profile_text: str, store: Store) -> list[PaperRecord]:
    """Fetch the chapter's cited papers and keep the 8 with abstracts most similar to the profile."""
    pmids = list(dict.fromkeys(pmids))[:MAX_CITED_FETCH]
    if not pmids:
        return []
    try:
        records = [r for r in pubmed.efetch(pmids) if not r.is_book and not drop_reason(r)]
    except Exception as exc:
        store.warn(f"The chapter's cited papers could not be fetched: {exc}", STEP)
        return []
    store.log_search(STEP, "statpearls_cited", f"{len(pmids)} cited PMIDs", len(records), [r.pmid for r in records])
    scores = tfidf_scores([f"{r.title} {r.abstract}" for r in records], profile_text)
    ranked = sorted(zip(scores, range(len(records)), records, strict=True), key=lambda x: (-x[0], x[1]))
    return [record for _, _, record in ranked[:MAX_CITED]]


def chapter_from_upload(page: UploadedPage, api_chapter: PaperRecord | None) -> PaperRecord:
    """Use the API record's metadata when it is the same chapter; otherwise build a record from the page."""
    if api_chapter and (
        (page.nbk and page.nbk == api_chapter.book_accession) or page.title.lower() == api_chapter.title.lower()
    ):
        return api_chapter
    return PaperRecord(
        pmid="",
        title=page.title or "StatPearls chapter",
        is_book=True,
        book_title="StatPearls",
        book_accession=page.nbk,
        reference_pmids=page.reference_pmids,
    )


def gather_sources(
    pubmed: PubMed, terms: str, profile_text: str, uploaded_html: str, store: Store
) -> tuple[list[IntroSource], str]:
    """The introduction's sources and where the chapter came from ("api", "upload" or "none")."""
    api_chapter = find_chapter(pubmed, terms, profile_text, store) if terms.strip() else None
    if uploaded_html.strip():
        page = parse_uploaded_html(uploaded_html)
        chapter = chapter_from_upload(page, api_chapter)
        chapter_text = page.text() or chapter.abstract
        reference_pmids = page.reference_pmids or chapter.reference_pmids
        origin = "upload"
    elif api_chapter:
        chapter = api_chapter
        sections = ", ".join(s for s in chapter.book_sections if s.lower() not in {"references", "review questions"})
        chapter_text = f"{chapter.abstract}\nSections of the chapter: {sections}"
        reference_pmids = chapter.reference_pmids
        origin = "api"
    else:
        return [], "none"
    sources = [IntroSource("S1", chapter, chapter_text, "chapter")]
    for number, record in enumerate(cited_papers(pubmed, reference_pmids, profile_text, store), start=2):
        sources.append(IntroSource(f"S{number}", record, record.abstract, "cited"))
    return sources, origin
