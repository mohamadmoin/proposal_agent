"""Search lanes, the TF-IDF + rank-fusion cut, and the code-only selection rules of the background.

Why lanes: one Boolean query misses studies that are phrased differently. Several cheap searches (the core query,
the core query restricted to the same design, to the last 10 years, to the country, and a plain-words query) are
merged, then a TF-IDF score and reciprocal-rank fusion keep the best 40 for the model to screen
(docs/DESIGN.md, D1).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer

from engine.schemas import PaperRecord, StudyProfileOut

RRF_K = 60
MIN_ABSTRACT_CHARS = 200
LANE_RETMAX = 50
WIDE_RETMAX = 100
MIN_UNION = 20
MIN_PAPERS = 5


@dataclass(frozen=True)
class Lane:
    """One PubMed search of the background."""

    name: str
    query: str
    retmax: int = LANE_RETMAX


def build_lanes(profile: StudyProfileOut, pubmed_filter: str, now_year: int) -> list[Lane]:
    """The background lanes, built in code from the profile (never by the model)."""
    core = profile.core_query.strip()
    lanes = [Lane("core", core)]
    if pubmed_filter.strip():
        lanes.append(Lane("design", f"({core}) AND ({pubmed_filter.strip()})"))
    lanes.append(Lane("recent", f"({core}) AND {now_year - 9}:{now_year}[dp]"))
    if profile.plain_query.strip():
        lanes.append(Lane("plain", profile.plain_query.strip()))
    country = (profile.setting_country or "").strip()
    if country and country.lower() not in {"null", "none", "not stated"}:
        term = f'"{country}"' if " " in country else country
        lanes.append(Lane("country", f"({core}) AND {term}[tiab]"))
    return lanes


# --- the cut ------------------------------------------------------------------------------------------------------


@dataclass
class Ranked:
    """A record that survived the filters, with its scores."""

    record: PaperRecord
    lanes: dict[str, int] = field(default_factory=dict)  # lane name → rank in that lane (1-based)
    tfidf: float = 0.0
    tfidf_rank: int = 0
    rrf: float = 0.0


def profile_query_text(profile: StudyProfileOut) -> str:
    """What the TF-IDF compares papers against: the English title, PICO parts and concepts."""
    parts = [profile.english_title, profile.population, profile.exposure_or_intervention, profile.outcomes]
    parts.extend(profile.concepts)
    return " ".join(p for p in parts if p and p.lower() != "not stated")


def drop_reason(record: PaperRecord) -> str:
    """Why a record cannot be screened, or "" when it can."""
    if record.retracted:
        return "retracted"
    if len(record.abstract) < MIN_ABSTRACT_CHARS:
        return "short_abstract"
    return ""


def tfidf_scores(documents: list[str], query: str) -> list[float]:
    """Cosine similarity of each document to the query (scikit-learn TF-IDF, 1–2-grams, sublinear tf)."""
    if not documents:
        return []
    vectorizer = TfidfVectorizer(stop_words="english", sublinear_tf=True, ngram_range=(1, 2))
    try:
        matrix = vectorizer.fit_transform(documents)
    except ValueError:  # every word was a stop word
        return [0.0] * len(documents)
    query_vector = vectorizer.transform([query])
    return [float(v) for v in (matrix @ query_vector.T).toarray().ravel()]


def ranks_from_scores(scores: list[float]) -> list[int]:
    """1 for the highest score; ties keep the input order."""
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    ranks = [0] * len(scores)
    for rank, index in enumerate(order, start=1):
        ranks[index] = rank
    return ranks


def rrf_score(tfidf_rank: int, lane_ranks: dict[str, int]) -> float:
    """Reciprocal-rank fusion: 1/(60 + tfidf rank) + Σ over lanes 1/(60 + rank in lane)."""
    return 1 / (RRF_K + tfidf_rank) + sum(1 / (RRF_K + r) for r in lane_ranks.values())


def cut(
    records: list[PaperRecord], lane_hits: dict[str, list[str]], query: str, top: int
) -> tuple[list[Ranked], list[tuple[PaperRecord, str]]]:
    """Drop unusable records, score the rest and keep the `top` best. Returns (kept, dropped with reasons)."""
    dropped: list[tuple[PaperRecord, str]] = []
    usable: list[Ranked] = []
    for record in records:
        reason = drop_reason(record)
        if reason:
            dropped.append((record, reason))
            continue
        lanes = {name: pmids.index(record.pmid) + 1 for name, pmids in lane_hits.items() if record.pmid in pmids}
        usable.append(Ranked(record=record, lanes=lanes))
    scores = tfidf_scores([f"{r.record.title} {r.record.title} {r.record.abstract}" for r in usable], query)
    for item, score, rank in zip(usable, scores, ranks_from_scores(scores), strict=True):
        item.tfidf, item.tfidf_rank = score, rank
        item.rrf = rrf_score(rank, item.lanes)
    usable.sort(key=lambda r: (-r.rrf, r.tfidf_rank))
    for item in usable[top:]:
        dropped.append((item.record, "below_cut"))
    return usable[:top], dropped


# --- selection ----------------------------------------------------------------------------------------------------


@dataclass
class Candidate:
    """A screened paper: the verified screening record plus everything selection needs."""

    record: PaperRecord
    screening: dict[str, Any]
    lanes: dict[str, int] = field(default_factory=dict)
    tfidf: float = 0.0
    rrf: float = 0.0
    score: float = 0.0
    flags: list[str] = field(default_factory=list)
    selected: bool = False
    token: str = ""

    @property
    def aim(self) -> int:
        return int(self.screening.get("aim_similarity") or 0)

    @property
    def population(self) -> int:
        return int(self.screening.get("population_similarity") or 0)

    @property
    def design_match(self) -> bool:
        return bool(self.screening.get("design_match"))


def recency_points(year: int | None, now_year: int) -> int:
    """2 within the last 5 years, 1 within the last 10, otherwise 0."""
    if not year:
        return 0
    age = now_year - year
    if age <= 4:
        return 2
    if age <= 9:
        return 1
    return 0


def similarity_score(candidate: Candidate, now_year: int) -> float:
    """Score = 3·aim + 1·population + 2·design_match + recency."""
    return (
        3 * candidate.aim
        + candidate.population
        + 2 * int(candidate.design_match)
        + recency_points(candidate.record.year, now_year)
    )


def is_eligible(candidate: Candidate) -> bool:
    """Strictly similar: aim ≥ 2 AND (same design OR population ≥ 2)."""
    return candidate.aim >= 2 and (candidate.design_match or candidate.population >= 2)


def title_cosine(a: str, b: str) -> float:
    """Cosine of word counts of two titles (1.0 = the same words)."""
    wa, wb = Counter(re.findall(r"\w+", a.lower())), Counter(re.findall(r"\w+", b.lower()))
    dot = sum(wa[w] * wb[w] for w in wa)
    norm = math.sqrt(sum(v * v for v in wa.values())) * math.sqrt(sum(v * v for v in wb.values()))
    return dot / norm if norm else 0.0


def is_duplicate(a: PaperRecord, b: PaperRecord) -> bool:
    """The same study published twice: same first author and year, and title cosine > 0.8."""
    return (
        bool(a.first_author_last_name)
        and a.first_author_last_name.lower() == b.first_author_last_name.lower()
        and a.year == b.year
        and title_cosine(a.title, b.title) > 0.8
    )


def merge_duplicates(candidates: list[Candidate]) -> list[Candidate]:
    """Keep the best-scored copy of each duplicate group; flag the others."""
    kept: list[Candidate] = []
    for candidate in sorted(candidates, key=sort_key):
        twin = next((k for k in kept if is_duplicate(k.record, candidate.record)), None)
        if twin:
            candidate.flags.append(f"duplicate_of:{twin.record.pmid}")
        else:
            kept.append(candidate)
    return kept


def sort_key(candidate: Candidate) -> tuple[float, int, str]:
    return (-candidate.score, -(candidate.record.year or 0), candidate.record.pmid)


@dataclass
class Selection:
    selected: list[Candidate]
    strict_count: int
    warning: str = ""


def select(candidates: list[Candidate], now_year: int, max_papers: int) -> Selection:
    """The background's studies: strictly similar first, topped up to 5 with "partly similar" ones (aim ≥ 2)."""
    max_papers = min(10, max(MIN_PAPERS, max_papers))
    for candidate in candidates:
        candidate.score = similarity_score(candidate, now_year)
    unique = merge_duplicates(candidates)
    strict = [c for c in unique if is_eligible(c)][:max_papers]
    chosen = list(strict)
    if len(chosen) < MIN_PAPERS:
        extras = [c for c in unique if c not in chosen and c.aim >= 2]
        for extra in extras[: MIN_PAPERS - len(chosen)]:
            extra.flags.append("partly_similar")
            chosen.append(extra)
    chosen.sort(key=sort_key)
    for candidate in chosen:
        candidate.selected = True
    warning = ""
    if len(strict) < MIN_PAPERS:
        warning = f"Only {len(strict)} strictly similar studies were found."
    return Selection(selected=chosen, strict_count=len(strict), warning=warning)
