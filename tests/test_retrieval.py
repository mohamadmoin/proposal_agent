import pytest

from engine.retrieval import (
    Candidate,
    build_lanes,
    cut,
    is_duplicate,
    is_eligible,
    recency_points,
    rrf_score,
    select,
    similarity_score,
    title_cosine,
)
from engine.schemas import PaperRecord, StudyProfileOut

LONG = "x " * 150


def profile(**changes):
    data = {
        "english_title": "Prevalence of vitamin D deficiency among pregnant women",
        "population": "pregnant women",
        "setting_country": "Iran",
        "exposure_or_intervention": "pregnancy",
        "comparator": "not stated",
        "outcomes": "vitamin D deficiency",
        "design_guess": "cross_sectional",
        "design_guess_other_name": "",
        "concepts": ["vitamin D deficiency", "pregnancy"],
        "core_query": '("Vitamin D Deficiency"[mh]) AND ("Pregnancy"[mh])',
        "plain_query": "vitamin D pregnancy",
        "statpearls_terms": "vitamin D deficiency",
    }
    data.update(changes)
    return StudyProfileOut(**data)


def test_lanes_are_built_in_code():
    lanes = build_lanes(profile(), "cross-sectional[tiab]", 2026)
    assert [lane.name for lane in lanes] == ["core", "design", "recent", "plain", "country"]
    core = '("Vitamin D Deficiency"[mh]) AND ("Pregnancy"[mh])'
    assert lanes[0].query == core and lanes[0].retmax == 50
    assert lanes[1].query == f"({core}) AND (cross-sectional[tiab])"
    assert lanes[2].query == f"({core}) AND 2017:2026[dp]"
    assert lanes[3].query == "vitamin D pregnancy"
    assert lanes[4].query == f"({core}) AND Iran[tiab]"


def test_optional_lanes_are_skipped():
    lanes = build_lanes(profile(setting_country=None), "", 2026)
    assert [lane.name for lane in lanes] == ["core", "recent", "plain"]
    spaced = build_lanes(profile(setting_country="Saudi Arabia"), "", 2026)
    assert spaced[-1].query.endswith('"Saudi Arabia"[tiab]')


def record(pmid, title="Vitamin D in pregnancy", abstract=LONG, **kw):
    return PaperRecord(pmid=pmid, title=title, abstract=abstract, **kw)


def test_cut_drops_retracted_and_short_and_keeps_top():
    records = [
        record("1", "Vitamin D deficiency in pregnant women", "vitamin D deficiency pregnant women " * 10),
        record("2", "Unrelated cardiology", "heart failure drugs " * 20),
        record("3", retracted=True),
        record("4", abstract="too short"),
        record("5", "Vitamin D and pregnancy outcomes", "vitamin D pregnancy outcomes " * 10),
    ]
    hits = {"core": ["1", "2", "5"], "plain": ["5", "1"]}
    kept, dropped = cut(records, hits, "vitamin D deficiency pregnant women", top=2)
    assert [k.record.pmid for k in kept] == ["1", "5"]
    reasons = {r.pmid: why for r, why in dropped}
    assert reasons == {"3": "retracted", "4": "short_abstract", "2": "below_cut"}
    first = kept[0]
    assert first.lanes == {"core": 1, "plain": 2}
    assert first.rrf == pytest.approx(1 / 61 + 1 / 61 + 1 / 62)


def test_rrf_formula():
    assert rrf_score(1, {}) == pytest.approx(1 / 61)
    assert rrf_score(3, {"core": 2, "plain": 10}) == pytest.approx(1 / 63 + 1 / 62 + 1 / 70)


def cand(pmid, aim, pop, match, year=2024, title=None, author="Smith A"):
    rec = PaperRecord(pmid=pmid, title=title or f"Study {pmid} on topic {pmid}", year=year, authors=[author],
                      abstract=LONG)  # fmt: skip
    return Candidate(rec, {"aim_similarity": aim, "population_similarity": pop, "design_match": match})


def test_eligibility_rule():
    assert is_eligible(cand("1", 2, 0, True))
    assert is_eligible(cand("2", 2, 2, False))
    assert not is_eligible(cand("3", 2, 1, False))
    assert not is_eligible(cand("4", 1, 3, True))


def test_score_and_recency():
    assert recency_points(2026, 2026) == 2 and recency_points(2022, 2026) == 2
    assert recency_points(2021, 2026) == 1 and recency_points(2017, 2026) == 1
    assert recency_points(2016, 2026) == 0 and recency_points(None, 2026) == 0
    assert similarity_score(cand("1", 3, 2, True, 2024), 2026) == 9 + 2 + 2 + 2


def test_duplicates_merge():
    a = PaperRecord(pmid="1", title="Vitamin D deficiency in pregnant women of Tehran", year=2020, authors=["Ali R"])
    b = PaperRecord(pmid="2", title="Vitamin D deficiency in pregnant women in Tehran", year=2020, authors=["Ali M"])
    c = PaperRecord(pmid="3", title="Vitamin D deficiency in pregnant women of Tehran", year=2021, authors=["Ali R"])
    assert title_cosine(a.title, b.title) > 0.8
    assert is_duplicate(a, b)
    assert not is_duplicate(a, c)  # different year


def test_selection_takes_top_and_merges_duplicates():
    same_title = "Vitamin D deficiency among pregnant women in Shiraz"
    cands = [cand(str(i), 3, 2, True, 2024) for i in range(1, 12)]
    cands.append(cand("dup", 3, 2, True, 2024, title=cands[0].record.title))
    cands.append(cand("old", 3, 3, True, 2000, title=same_title))
    selection = select(cands, 2026, max_papers=8)
    assert len(selection.selected) == 8 and selection.warning == ""
    assert all(c.selected for c in selection.selected)
    dup = next(c for c in cands if c.record.pmid == "dup")
    assert not dup.selected and dup.flags == ["duplicate_of:1"]


def test_selection_tops_up_with_partly_similar_and_warns():
    cands = [cand("a", 3, 2, True), cand("b", 2, 3, False), cand("c", 2, 0, False), cand("d", 1, 3, True),
             cand("e", 2, 1, False), cand("f", 0, 0, False)]  # fmt: skip
    selection = select(cands, 2026, max_papers=8)
    chosen = {c.record.pmid for c in selection.selected}
    assert chosen == {"a", "b", "c", "e"}  # never aim ≤ 1
    assert selection.strict_count == 2
    assert selection.warning == "Only 2 strictly similar studies were found."
    assert "partly_similar" in next(c for c in cands if c.record.pmid == "c").flags


def test_max_papers_is_clamped_to_5_10():
    cands = [cand(str(i), 3, 3, True) for i in range(20)]
    assert len(select(cands, 2026, max_papers=50).selected) == 10
    cands = [cand(str(i), 3, 3, True) for i in range(20)]
    assert len(select(cands, 2026, max_papers=2).selected) == 5
