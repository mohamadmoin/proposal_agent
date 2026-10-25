"""Load and validate `knowledge/`, the clinician's part of the product.

The engine reads these files at run time, so changing how the app writes means editing a text file, not Python.
Validation runs at load time so a broken edit (a missing section, an unknown formula) fails loudly and early.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from engine.samplesize import FORMULAS

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"
LANGUAGES = ("fa", "en")
GENERAL = "general"
OTHER = "other"

# knowledge/*.md files the engine needs, by short name.
TEXT_FILES = {
    "persona": "persona.md",
    "style_rules": "style_rules.md",
    "background_checklist": "background_checklist.md",
    "questionnaire": "questionnaire.md",
    "methods_checklist": "methods_checklist.md",
    "sample_size": "sample_size.md",
    "aims_grammar": "aims_grammar.md",
    "introduction": "introduction.md",
}


class KnowledgeError(ValueError):
    """Raised when a file in knowledge/ is missing or inconsistent."""


@dataclass(frozen=True)
class Design:
    """One study-design template from designs.yaml."""

    label: str
    name: dict[str, str]
    pubmed_filter: str
    search_topics: list[str]
    sample_size: list[str]
    sections: dict[str, dict[str, str]]


@dataclass(frozen=True)
class Knowledge:
    """Everything in knowledge/, loaded once."""

    texts: dict[str, str]
    section_skeleton: list[dict[str, str]]
    designs: dict[str, Design] = field(default_factory=dict)

    def text(self, name: str) -> str:
        return self.texts[name]

    @property
    def persona(self) -> str:
        """The system message: the part of persona.md after the `---` line."""
        return body_after_rule(self.texts["persona"])

    @property
    def style_rules(self) -> str:
        return self.texts["style_rules"]

    def design(self, label: str) -> Design:
        return self.designs.get(label) or self.designs[GENERAL]

    def section_ids(self) -> list[str]:
        return [s["id"] for s in self.section_skeleton]

    def section_title(self, section_id: str, lang: str) -> str:
        for section in self.section_skeleton:
            if section["id"] == section_id:
                return section[lang]
        raise KeyError(section_id)


def body_after_rule(text: str) -> str:
    """Return the text after the first `---` line (the part meant for the model), or all of it."""
    parts = re.split(r"^---\s*$", text, maxsplit=1, flags=re.M)
    return parts[1].strip() if len(parts) == 2 else text.strip()


def normalise_label(label: str | None) -> str:
    """'Cross Sectional' → 'cross_sectional'; empty stays empty."""
    if not label:
        return ""
    return re.sub(r"[\s\-/]+", "_", label.strip().lower()).strip("_")


def load_knowledge(directory: Path | None = None) -> Knowledge:
    """Read and validate every knowledge file. Raises KnowledgeError on any problem."""
    directory = Path(directory or os.environ.get("KNOWLEDGE_DIR") or KNOWLEDGE_DIR)
    texts = {}
    for key, filename in TEXT_FILES.items():
        path = directory / filename
        if not path.exists():
            raise KnowledgeError(f"knowledge file missing: {path}")
        texts[key] = path.read_text(encoding="utf-8")
    raw = yaml.safe_load((directory / "designs.yaml").read_text(encoding="utf-8"))
    skeleton, designs = parse_designs(raw)
    return Knowledge(texts=texts, section_skeleton=skeleton, designs=designs)


@lru_cache(maxsize=1)
def get_knowledge() -> Knowledge:
    """The default knowledge, loaded once per process."""
    return load_knowledge()


def parse_designs(raw: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Design]]:
    """Turn designs.yaml into Design objects, checking every rule the engine relies on."""
    skeleton = raw.get("sections") or []
    section_ids = [s.get("id") for s in skeleton]
    if len(section_ids) != 5:
        raise KnowledgeError("designs.yaml › sections must list the 5 methods sub-sections")
    for section in skeleton:
        for lang in LANGUAGES:
            if not section.get(lang):
                raise KnowledgeError(f"designs.yaml › sections › {section.get('id')} has no {lang} title")
    designs: dict[str, Design] = {}
    for label, block in (raw.get("designs") or {}).items():
        designs[label] = parse_one_design(label, block or {}, section_ids)
    if GENERAL not in designs:
        raise KnowledgeError("designs.yaml must define the 'general' fallback design")
    return skeleton, designs


def parse_one_design(label: str, block: dict[str, Any], section_ids: list[str]) -> Design:
    name = block.get("name") or {}
    for lang in LANGUAGES:
        if not name.get(lang):
            raise KnowledgeError(f"design {label}: name.{lang} is missing")
    topics = block.get("search_topics") or []
    if len(topics) != 5:
        raise KnowledgeError(f"design {label}: needs exactly 5 search_topics, found {len(topics)}")
    formulas = list(block.get("sample_size") or [])
    unknown = [f for f in formulas if f not in FORMULAS]
    if unknown:
        raise KnowledgeError(f"design {label}: unknown sample-size formula(s) {unknown}")
    sections = block.get("sections") or {}
    for section_id in section_ids:
        for lang in LANGUAGES:
            if not (sections.get(section_id) or {}).get(lang):
                raise KnowledgeError(f"design {label}: section {section_id} has no {lang} instruction")
    return Design(
        label=label,
        name={lang: name[lang] for lang in LANGUAGES},
        pubmed_filter=(block.get("pubmed_filter") or "").strip(),
        search_topics=[str(t) for t in topics],
        sample_size=formulas,
        sections={sid: {lang: sections[sid][lang] for lang in LANGUAGES} for sid in section_ids},
    )


@dataclass(frozen=True)
class DesignChoice:
    """The design the proposal uses: the template label plus the full name shown to the writer."""

    label: str  # a key of designs.yaml (unknown designs use "general")
    other_name: str  # the user's or the router's design name when label is "general"

    def name(self, knowledge: Knowledge, lang: str) -> str:
        """The full design name in the output language (never a fragment)."""
        if self.label == GENERAL and self.other_name:
            return self.other_name
        return knowledge.design(self.label).name[lang]


def resolve_design(knowledge: Knowledge, label: str | None, other_name: str | None = None) -> DesignChoice | None:
    """Map a user or model label to a template. Unknown labels use 'general' with their own name.

    Returns None when no label was given (the design router decides then).
    """
    norm = normalise_label(label)
    other = (other_name or "").strip()
    if not norm:
        return None
    if norm in knowledge.designs and norm != GENERAL:
        return DesignChoice(label=norm, other_name="")
    if norm in (OTHER, GENERAL):
        return DesignChoice(label=GENERAL, other_name=other)
    # A design name that is not in the file, e.g. "diagnostic accuracy": keep the user's words as the name.
    return DesignChoice(label=GENERAL, other_name=other or label.strip())


def section_instruction(knowledge: Knowledge, choice: DesignChoice, section_id: str, lang: str) -> str:
    """The writer's instruction for one methods sub-section, with {design_name} filled in."""
    template = knowledge.design(choice.label).sections[section_id][lang]
    return template.replace("{design_name}", choice.name(knowledge, lang))
