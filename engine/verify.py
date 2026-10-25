"""Checks the code makes instead of trusting the model: sentences, digits and numbers.

Why: cheap models sometimes cite the wrong sentence or change a number. Each claim the app keeps must be traceable to
a sentence of its source, and every number in a summary must appear in that source.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# Persian (U+06F0–06F9) and Arabic-Indic (U+0660–0669) digits → ASCII.
DIGIT_MAP = {ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")}
DIGIT_MAP.update({ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")})
DIGIT_MAP.update({ord("٫"): ".", ord("·"): ".", ord("٬"): ",", ord("٪"): "%", ord("−"): "-"})

ABBREVIATIONS = {
    "al", "vs", "e.g", "i.e", "eg", "ie", "fig", "figs", "approx", "dr", "mr", "mrs", "ms", "prof", "no", "nos",
    "ca", "cf", "etc", "st", "vol", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov",
    "dec", "resp", "incl", "min", "max", "mo", "yr", "yrs", "wk", "wks", "hr", "hrs", "sd", "se", "inc", "ltd", "co",
    "u.s", "u.k", "ref", "refs", "est", "suppl", "dept", "univ",
}  # fmt: skip

TOKEN_RE = re.compile(r"\{[A-Z]\d+\}")
NUMBER_RE = re.compile(r"(?<![\w.])(\d+(?:,\d{3})+(?:\.\d+)?|\d*\.\d+|\d+)(\s?%)?")
BOUNDARY_RE = re.compile(r"[.!?؟]+[\"')\]»”]*\s+")


def normalise_digits(text: str) -> str:
    """Persian/Arabic digits to ASCII, '٫' and '·' to '.', so numbers compare across scripts."""
    return text.translate(DIGIT_MAP)


# --- sentences ----------------------------------------------------------------------------------------------------


def split_sentences(text: str) -> list[str]:
    """Split an abstract into sentences without breaking "p < 0.05", "1.5 mg", "et al." or "vs.".

    Labelled abstracts arrive as "LABEL: text" lines; each line is split on its own.
    """
    sentences: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            sentences.extend(split_line(line))
    return sentences


def split_line(line: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for match in BOUNDARY_RE.finditer(line):
        end = match.end()
        if is_real_boundary(line, match.start(), end):
            piece = line[start:end].strip()
            if piece:
                parts.append(piece)
            start = end
    tail = line[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def is_real_boundary(line: str, dot_pos: int, next_pos: int) -> bool:
    """A full stop ends a sentence unless it follows an abbreviation/initial or the next word is lower case."""
    word_match = re.search(r"([\w.]+)$", line[:dot_pos])
    word = (word_match.group(1) if word_match else "").lower().rstrip(".")
    if line[dot_pos] == "." and (word in ABBREVIATIONS or (len(word) == 1 and word.isalpha())):
        return False
    if next_pos >= len(line):
        return True
    nxt = line[next_pos]
    return not nxt.islower()


def numbered_sentences(text: str) -> tuple[str, list[str]]:
    """Return ("{1} First. {2} Second.", [sentences]) for the screening prompt."""
    sentences = split_sentences(text)
    joined = " ".join(f"{{{i}}} {s}" for i, s in enumerate(sentences, start=1))
    return joined, sentences


# --- numbers ------------------------------------------------------------------------------------------------------


def canonical(number: str | Decimal) -> str:
    """'0.50' → '0.5', '.5' → '0.5', '1,234' → '1234', '12.0' → '12'."""
    try:
        value = Decimal(str(number).replace(",", ""))
    except InvalidOperation:
        return str(number)
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def extract_numbers(text: str) -> list[tuple[str, bool]]:
    """All numbers in a text as (canonical, has_percent). Citation tokens like {P3} are ignored."""
    clean = TOKEN_RE.sub(" ", normalise_digits(text))
    found = []
    for match in NUMBER_RE.finditer(clean):
        found.append((canonical(match.group(1)), bool(match.group(2))))
    return found


def number_set(source: str) -> set[str]:
    """Numbers of a source, with percentages added both ways (45% ↔ 0.45)."""
    numbers: set[str] = set()
    for value, has_percent in extract_numbers(source):
        numbers.add(value)
        dec = Decimal(value)
        if has_percent:
            numbers.add(canonical(dec / 100))
        if 0 < dec <= 1:
            numbers.add(canonical(dec * 100))
    return numbers


def missing_numbers(text: str, source: str, allowed: set[str] | None = None) -> list[str]:
    """Numbers in `text` that do not appear in `source` (or in `allowed`, e.g. the year and the sample size)."""
    available = number_set(source) | {canonical(a) for a in (allowed or set())}
    missing = []
    for value, has_percent in extract_numbers(text):
        candidates = {value}
        if has_percent:
            candidates.add(canonical(Decimal(value) / 100))
        if not candidates & available and value not in missing:
            missing.append(value)
    return missing


def contains_number(text: str, number: int | float | str) -> bool:
    """Is this number written in the text (after digit normalisation)?"""
    return canonical(str(number)) in {value for value, _ in extract_numbers(text)}


# --- screening records --------------------------------------------------------------------------------------------


def valid_evidence(evidence: list[int], sentence_count: int) -> bool:
    """Evidence must name at least one sentence, and every number must exist."""
    return bool(evidence) and all(1 <= i <= sentence_count for i in evidence)


def evidence_text(evidence: list[int], sentences: list[str]) -> str:
    """The evidence sentences, verbatim, in the order given."""
    return " ".join(sentences[i - 1] for i in evidence if 1 <= i <= len(sentences))


def citation_tokens(text: str) -> list[str]:
    """Tokens such as P3 or S1 found in a text, in order."""
    return [t.strip("{}") for t in TOKEN_RE.findall(text)]
