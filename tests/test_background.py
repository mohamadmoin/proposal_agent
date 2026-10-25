from engine.background import keep_only_token_at_end, run_background, verify_screening, write_checked
from engine.knowledge import resolve_design
from engine.llm import LLM, Completion, LLMConfig
from engine.profile import build_profile
from engine.schemas import PaperRecord, ProposalRequest, ScreeningItemOut
from engine.store import MemoryStore

ABSTRACT = (
    "BACKGROUND: Vitamin D deficiency is common in pregnancy.\n"
    "RESULTS: Of 400 women, 45.6% were deficient. The mean level was 14.2 ng/mL.\n"
    "CONCLUSIONS: Screening is advised."
)
RECORD = PaperRecord(pmid="9", title="Vitamin D in 2 cities", abstract=ABSTRACT, year=2021, authors=["Ali R"])


def item(**changes):
    data = {
        "pmid": "9",
        "aim_similarity": 3,
        "population_similarity": 2,
        "design": "cross_sectional",
        "design_match": True,
        "sample_size": 400,
        "country": "Iran",
        "aim": "To estimate deficiency.",
        "main_finding": "45.6% were deficient.",
        "evidence": [2],
    }
    data.update(changes)
    return ScreeningItemOut(**data)


def test_valid_screening_is_kept():
    data, flags = verify_screening(RECORD, item())
    assert flags == [] and data["aim_similarity"] == 3
    assert data["evidence_sentences"] == "RESULTS: Of 400 women, 45.6% were deficient."


def test_missing_evidence_caps_aim_at_one():
    data, flags = verify_screening(RECORD, item(evidence=[12]))
    assert "evidence_invalid" in flags and data["aim_similarity"] == 1
    data, flags = verify_screening(RECORD, item(evidence=[]))
    assert data["aim_similarity"] == 1


def test_invented_number_replaces_finding_with_evidence():
    data, flags = verify_screening(RECORD, item(main_finding="52% were deficient.", evidence=[2, 3]))
    assert "finding_replaced" in flags
    assert data["main_finding"] == "RESULTS: Of 400 women, 45.6% were deficient. The mean level was 14.2 ng/mL."
    assert data["model_main_finding"] == "52% were deficient."


def test_out_of_range_scores_are_clamped():
    data, _ = verify_screening(RECORD, item(aim_similarity=7, population_similarity=-2))
    assert data["aim_similarity"] == 3 and data["population_similarity"] == 0


def test_unscreened_paper():
    data, flags = verify_screening(RECORD, None)
    assert flags == ["not_screened"] and data["aim_similarity"] == 0


def test_token_kept_once_at_the_end():
    text = "In a study {P3} by Ali {P1}, deficiency was common {P3}."
    assert keep_only_token_at_end(text, "P3") == "In a study by Ali, deficiency was common. {P3}"


class Scripted:
    def __init__(self, texts):
        self.texts = list(texts)
        self.prompts = []

    def call(self, messages, tier, schema, step=""):
        self.prompts.append((step, messages[-1]["content"]))
        return Completion(self.texts.pop(0), None, 1, 1, "m")


def test_write_checked_retries_once_then_flags(knowledge):
    backend = Scripted(["Deficiency was 52%.", "Deficiency was 45.6%."])
    llm = LLM(LLMConfig(provider="openai", api_key="x"), knowledge, backend)
    text, missing = write_checked(llm, "step", "prompt", ABSTRACT)
    assert text == "Deficiency was 45.6%." and missing == []
    assert backend.prompts[1][0] == "step:retry" and "52" in backend.prompts[1][1]

    backend = Scripted(["It was 52%.", "It was 61%."])
    llm = LLM(LLMConfig(provider="openai", api_key="x"), knowledge, backend)
    _, missing = write_checked(llm, "step", "prompt", ABSTRACT)
    assert missing == ["61"]


def test_full_background_with_fake_model(knowledge, fake_llm, offline_pubmed, now):
    request = ProposalRequest(title="شیوع کمبود ویتامین D در زنان باردار مراجعه‌کننده به مراکز بهداشتی تهران")
    profile = build_profile(request, knowledge, fake_llm)
    assert profile.setting_country == "Iran" and profile.design_guess == "cross_sectional"
    store = MemoryStore()
    design = resolve_design(knowledge, "cross_sectional")
    result = run_background(profile, design, knowledge, fake_llm, offline_pubmed, store, now.year, "fa",
                            request.title)  # fmt: skip
    assert 5 <= len(result.selected) <= 8
    assert [c.token for c in result.selected] == [f"P{i}" for i in range(1, len(result.selected) + 1)]
    scores = [c.score for c in result.selected]
    assert scores == sorted(scores, reverse=True)
    assert len(result.paragraphs) == len(result.selected)
    for paragraph in result.paragraphs:
        assert paragraph["text"].endswith("{" + paragraph["token"] + "}")
        assert paragraph["text"].count("{") == 1
    lanes = {s["lane"] for s in store.searches}
    assert {"core", "design", "recent", "plain", "country"} <= lanes
    stages = {entry["stage"] for entry in store.papers.values()}
    assert {"retrieved", "screened", "selected"} <= stages
    assert len(store.papers_for("background")) == len({p["paper"].pmid for p in store.papers.values()})
