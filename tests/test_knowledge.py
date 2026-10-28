import copy

import pytest
import yaml

from engine.knowledge import (
    KNOWLEDGE_DIR,
    KnowledgeError,
    normalise_label,
    parse_designs,
    resolve_design,
    section_instruction,
)
from engine.samplesize import FORMULAS


def raw_designs():
    return yaml.safe_load((KNOWLEDGE_DIR / "designs.yaml").read_text(encoding="utf-8"))


def test_every_design_has_five_sections_in_both_languages(knowledge):
    assert knowledge.section_ids() == ["conduct", "population", "sample_size", "data_collection", "analysis"]
    for design in knowledge.designs.values():
        assert set(design.sections) == set(knowledge.section_ids())
        for section in design.sections.values():
            assert section["fa"].strip() and section["en"].strip()
        assert design.name["fa"] and design.name["en"]
        assert len(design.search_topics) == 5


def test_every_formula_id_is_known(knowledge):
    for design in knowledge.designs.values():
        assert all(f in FORMULAS for f in design.sample_size)
    assert knowledge.design("systematic_review_or_meta_analysis").sample_size == []


def test_missing_language_is_rejected():
    raw = copy.deepcopy(raw_designs())
    del raw["designs"]["cohort"]["sections"]["analysis"]["fa"]
    with pytest.raises(KnowledgeError, match="cohort"):
        parse_designs(raw)


def test_unknown_formula_is_rejected():
    raw = copy.deepcopy(raw_designs())
    raw["designs"]["cohort"]["sample_size"].append("magic_formula")
    with pytest.raises(KnowledgeError, match="magic_formula"):
        parse_designs(raw)


def test_persona_is_the_part_after_the_rule(knowledge):
    assert knowledge.persona.startswith("You are a professional epidemiologist")
    assert "System prompt for every model call" not in knowledge.persona


def test_normalise_label():
    assert normalise_label("Cross Sectional") == "cross_sectional"
    assert normalise_label("  Randomized Controlled Trial ") == "randomized_controlled_trial"
    assert normalise_label("") == ""


def test_resolve_design_known_unknown_and_other(knowledge):
    assert resolve_design(knowledge, "Cohort").label == "cohort"
    assert resolve_design(knowledge, "Diagnostic accuracy").label == "diagnostic_accuracy"
    unknown = resolve_design(knowledge, "Case series")
    assert unknown.label == "general" and unknown.other_name == "Case series"
    assert unknown.name(knowledge, "fa") == "Case series"
    other = resolve_design(knowledge, "other", "Qualitative study")
    assert other.label == "general" and other.name(knowledge, "en") == "Qualitative study"
    assert resolve_design(knowledge, "") is None


def test_general_template_gets_the_full_design_name(knowledge):
    choice = resolve_design(knowledge, "other", "Case series")
    text = section_instruction(knowledge, choice, "conduct", "en")
    assert "Case series" in text and "{design_name}" not in text
