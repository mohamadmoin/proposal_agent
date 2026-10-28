"""Full texts for the extractor (AGT-14): numbered sentences of the useful sections and table rows with locators.

The text comes from the platform's open-access full-text fetch (Europe PMC, then PMC; parsed JATS with tables), which
the host injects as `fetch(pmcid) -> dict` (FullText.to_dict()). Only the cheap extractor reads it; every value it
reports is checked in code against the sentence or table row its locator points to.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from engine.verify import split_sentences

FullTextFetch = Callable[[str], dict[str, Any]]
SECTION_ORDER = ("results", "methods", "conclusion", "discussion")  # where the numbers a proposal needs live
TEXT_BUDGET = 40_000  # characters of section text per paper (whole sentences; tables come on top)
MAX_TABLES, MAX_ROWS = 10, 60


@dataclass
class Passage:
    text: str  # what the extractor reads
    lines: dict[str, str] = field(default_factory=dict)  # locator → verbatim sentence or table row


def column_names(header: list[list[str]], width: int) -> list[str]:
    """One name per column from the header rows ('Deficient (n = 158) %'), repeated spans collapsed."""
    names = []
    for col in range(width):
        parts: list[str] = []
        for row in header:
            if col < len(row) and row[col] and (not parts or parts[-1] != row[col]):
                parts.append(row[col])
        names.append(" ".join(parts))
    return names


def build_passage(ft: dict[str, Any]) -> Passage | None:
    """Numbered sentences (S1…) of the results/methods/conclusion/discussion sections within TEXT_BUDGET, then every
    table row as «T2.r3: column: value | …» with the table's label, caption and footnotes. None without a body."""
    if not ft or not ft.get("body_available"):
        return None
    lines: dict[str, str] = {}
    out: list[str] = []
    used = 0
    n = 0
    sections = sorted((s for s in ft.get("sections") or [] if s.get("text")),
                      key=lambda s: SECTION_ORDER.index(s.get("kind")) if s.get("kind") in SECTION_ORDER else 9)
    for sec in sections:
        if sec.get("kind") not in SECTION_ORDER:
            continue
        sentences = split_sentences(sec["text"])
        size = sum(len(x) for x in sentences)
        if used + size > TEXT_BUDGET and out:
            continue  # whole sections only; a later, shorter section may still fit
        out.append(f"## {sec.get('title') or sec.get('kind')}")
        for sentence in sentences:
            n += 1
            lines[f"S{n}"] = sentence
            out.append(f"{{S{n}}} {sentence}")
        used += size
    for k, table in enumerate((ft.get("tables") or [])[:MAX_TABLES], start=1):
        rows = table.get("rows") or []
        width = max([len(r) for r in rows + (table.get("header") or [])] or [0])
        names = column_names(table.get("header") or [], width)
        title = " ".join(x for x in (table.get("label"), table.get("caption")) if x)
        out.append(f"## T{k}: {title}")
        for i, row in enumerate(rows[:MAX_ROWS], start=1):
            cells = [f"{names[c]}: {v}" if c < len(names) and names[c] and c > 0 else v
                     for c, v in enumerate(row) if v]
            text = " | ".join(cells)
            lines[f"T{k}.r{i}"] = f"{title} — {text}"
            out.append(f"{{T{k}.r{i}}} {text}")
        for note in table.get("footnotes") or []:
            out.append(f"(T{k} note) {note}")
    return Passage(text="\n".join(out), lines=lines) if lines else None
