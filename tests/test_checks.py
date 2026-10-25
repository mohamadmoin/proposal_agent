import copy

import yaml
from django.core.management import call_command

from engine.checks import check_aims, check_citations, check_sample_size, run_checks
from engine.pipeline import RunOptions, run
from engine.schemas import SECTION_KEYS, ProposalRequest
from engine.store import MemoryStore
from tests.test_pipeline import FA_TITLE


def fake_run(fake_llm, offline_pubmed, now):
    store = MemoryStore()
    result = run(ProposalRequest(title=FA_TITLE), store, fake_llm, offline_pubmed, now, RunOptions())
    selected = [{"pmid": e["paper"].pmid, "abstract": e["paper"].abstract, "screening": e.get("screening")}
                for e in store.papers_for("background") if e.get("selected")]  # fmt: skip
    return result, selected


def test_all_checks_pass_on_a_fake_run(fake_llm, offline_pubmed, now):
    result, selected = fake_run(fake_llm, offline_pubmed, now)
    checks = run_checks(result, selected, list(SECTION_KEYS))
    assert [c.passed for c in checks] == [True, True, True, True, True], [c.detail for c in checks]


def test_checks_catch_problems(fake_llm, offline_pubmed, now):
    result, _ = fake_run(fake_llm, offline_pubmed, now)
    broken = copy.deepcopy(result)
    broken["references"].append({"number": 999, "text": "x", "pmid": "", "url": "", "tokens": []})
    broken["sections"]["background"]["paragraphs"][0]["text"] += " {P77}"
    assert not check_citations(broken).passed
    broken["sections"]["methods"]["subsections"][2]["text"] = "No number."
    assert not check_sample_size(broken).passed
    broken["sections"]["aims"]["questions"].append("extra?")
    assert not check_aims(broken).passed


def test_eval_set_has_one_title_per_design(knowledge):
    items = yaml.safe_load(open("evals/titles.yaml", encoding="utf-8"))["titles"]
    designs = {i["design"] for i in items}
    assert len(items) == 6 and designs == {d for d in knowledge.designs if d != "general"}
    assert all(i["fa"] and i["en"] for i in items)


def test_eval_run_dry_run(tmp_path, capsys):
    call_command("eval_run", "--allow-fake", "--only", "cross_sectional", "--out", str(tmp_path))
    report = next(tmp_path.glob("eval_*.md")).read_text(encoding="utf-8")
    assert "Expert rating" in report and "| cross_sectional | fa |" in report
    assert "FAIL" not in report.split("## Check details")[0]
