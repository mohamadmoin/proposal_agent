"""Citation numbering and the Vancouver reference list (code only, docs/DESIGN.md D8).

The model writes tokens such as {P3}, {S1} or {M2}. Here they become numbers in order of first appearance through
the whole document (Introduction → Aims → Background → Methods); adjacent citations share one bracket; unknown
tokens are removed and logged. Every reference in the list is cited, and every citation has a reference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from engine.schemas import PaperRecord

TOKEN_RUN = re.compile(r"[ \t]*(\{[A-Z]\d+\}(?:[\s,;،؛]*\{[A-Z]\d+\})*)")
SINGLE = re.compile(r"\{([A-Z]\d+)\}")
# models sometimes put several tokens in one pair of braces: {S1, S2, S3} → {S1}{S2}{S3}
GROUPED = re.compile(r"\{\s*([A-Z]\d+(?:\s*[,،;؛]\s*[A-Z]\d+)+)\s*\}")
BOOKSHELF = "https://www.ncbi.nlm.nih.gov/books/"
PUBMED = "https://pubmed.ncbi.nlm.nih.gov/"


@dataclass
class Numbering:
    """The result of numbering a document."""

    texts: list[str]
    references: list[dict] = field(default_factory=list)  # {number, text, pmid, url, tokens}
    unknown_tokens: list[str] = field(default_factory=list)


def source_key(record: PaperRecord) -> str:
    """One reference per real source, even when two tokens point to it (e.g. a paper found twice)."""
    if record.pmid:
        return f"pmid:{record.pmid}"
    if record.book_accession:
        return f"nbk:{record.book_accession}"
    return f"title:{record.title.lower()}"


def split_grouped(text: str) -> str:
    return GROUPED.sub(lambda m: "".join("{" + t + "}" for t in re.split(r"\s*[,،;؛]\s*", m.group(1))), text)


def number_document(texts: list[str], sources: dict[str, PaperRecord]) -> Numbering:
    """Replace tokens with [n] in order of first appearance across `texts` (already in document order)."""
    numbers: dict[str, int] = {}  # source key → number
    records: dict[int, PaperRecord] = {}
    tokens_of: dict[int, list[str]] = {}
    unknown: list[str] = []

    def replace(match: re.Match[str]) -> str:
        cited: list[int] = []
        for token in SINGLE.findall(match.group(1)):
            record = sources.get(token)
            if record is None:
                if token not in unknown:
                    unknown.append(token)
                continue
            key = source_key(record)
            if key not in numbers:
                numbers[key] = len(numbers) + 1
                records[numbers[key]] = record
            number = numbers[key]
            tokens_of.setdefault(number, [])
            if token not in tokens_of[number]:
                tokens_of[number].append(token)
            if number not in cited:
                cited.append(number)
        if not cited:
            return ""
        return " [" + ", ".join(str(n) for n in sorted(cited)) + "]"

    out = [tidy(TOKEN_RUN.sub(replace, split_grouped(text))) for text in texts]
    references = [
        {
            "number": number,
            "text": vancouver(record),
            "pmid": record.pmid,
            "url": reference_url(record),
            "tokens": tokens_of[number],
        }
        for number, record in sorted(records.items())
    ]
    return Numbering(texts=out, references=references, unknown_tokens=unknown)


def tidy(text: str) -> str:
    """Clean spaces left by removed tokens: no space before punctuation, single spaces, no leading space."""
    text = re.sub(r"[ \t]+([.,;:!?،؛])", r"\1", text)
    text = re.sub(r"([.!?؟])\s*(\[\d+(?:, \d+)*\])(?=\s|$)", r"\1 \2", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return "\n".join(line.strip() for line in text.split("\n"))


def reference_url(record: PaperRecord) -> str:
    if record.is_book and record.book_accession:
        return f"{BOOKSHELF}{record.book_accession}/"
    if record.pmid:
        return f"{PUBMED}{record.pmid}/"
    return ""


def authors_text(authors: list[str]) -> str:
    """Up to 6 authors, then "et al."."""
    if len(authors) > 6:
        return ", ".join(authors[:6]) + ", et al."
    return ", ".join(authors)


def with_period(text: str) -> str:
    text = text.strip()
    return text if not text or text.endswith((".", "?", "!")) else text + "."


def vancouver(record: PaperRecord) -> str:
    """Vancouver style: Authors. Title. Journal. Year;Volume(Issue):Pages. doi:… PMID: …"""
    parts = []
    authors = authors_text(record.authors)
    if authors:
        parts.append(with_period(authors))
    parts.append(with_period(record.title))
    if record.is_book:
        year = record.year or ""
        url = reference_url(record)
        text = " ".join(parts) + " In: StatPearls [Internet]. Treasure Island (FL): StatPearls Publishing;"
        text += f" {year}." if year else ""
        if url:
            text += f" Available from: {url}"
        return text
    journal = record.journal_abbrev or record.journal
    if journal:
        parts.append(with_period(journal))
    source = str(record.year or "")
    if record.volume:
        source += f";{record.volume}"
        if record.issue:
            source += f"({record.issue})"
    if record.pages:
        source += f":{record.pages}"
    if source:
        parts.append(with_period(source))
    if record.doi:
        parts.append(f"doi:{record.doi}.")
    if record.pmid:
        parts.append(f"PMID: {record.pmid}.")
    return " ".join(parts)
