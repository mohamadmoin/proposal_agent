"""Data shapes shared by the engine, the API and the model calls.

Models whose name ends in `Out` are what a model call must return (OpenAI Structured Outputs). They avoid numeric
constraints on purpose: strict JSON-schema mode supports few of them, so the code checks ranges itself.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SECTION_KEYS = ("introduction", "aims", "background", "methods")
Language = Literal["fa", "en"]


# --- the request --------------------------------------------------------------------------------------------------


class ProposalRequest(BaseModel):
    """What the researcher asked for."""

    title: str
    purpose: str = ""
    design: str = ""  # a designs.yaml label, "other", a free design name, or empty (the app decides)
    design_other_name: str = ""
    language: Language = "fa"
    sections: list[str] = Field(default_factory=lambda: list(SECTION_KEYS))
    statpearls_html: str = ""

    def wants(self, section: str) -> bool:
        return section in (self.sections or SECTION_KEYS)


# --- PubMed records -----------------------------------------------------------------------------------------------


class PaperRecord(BaseModel):
    """One PubMed record (journal article or book chapter), as parsed from efetch XML."""

    pmid: str
    title: str = ""
    abstract: str = ""
    journal: str = ""
    journal_abbrev: str = ""
    year: int | None = None
    volume: str = ""
    issue: str = ""
    pages: str = ""
    doi: str = ""
    pmcid: str = ""
    authors: list[str] = Field(default_factory=list)
    publication_types: list[str] = Field(default_factory=list)
    mesh: list[str] = Field(default_factory=list)
    retracted: bool = False
    is_book: bool = False
    book_title: str = ""
    book_accession: str = ""  # NBK… for Bookshelf chapters
    book_sections: list[str] = Field(default_factory=list)
    reference_pmids: list[str] = Field(default_factory=list)

    @property
    def first_author_last_name(self) -> str:
        return self.authors[0].split(" ")[0] if self.authors else ""


# --- model outputs ------------------------------------------------------------------------------------------------


class StudyProfileOut(BaseModel):
    english_title: str
    population: str
    setting_country: str | None
    exposure_or_intervention: str
    comparator: str
    outcomes: str
    design_guess: str
    design_guess_other_name: str
    concepts: list[str]
    core_query: str
    plain_query: str
    statpearls_terms: str


class ScreeningItemOut(BaseModel):
    pmid: str
    aim_similarity: int
    population_similarity: int
    design: str
    design_match: bool
    sample_size: int | None
    country: str | None
    aim: str
    main_finding: str
    evidence: list[int]


class ScreeningBatchOut(BaseModel):
    papers: list[ScreeningItemOut]


class QuestionAnswerOut(BaseModel):
    number: int
    answer: str


class DesignDecisionOut(BaseModel):
    answers: list[QuestionAnswerOut]
    label: str
    other_name: str
    rationale: str
    missing_information: list[str]


class TopicQueriesOut(BaseModel):
    queries: list[str]


class BaselineNumberOut(BaseModel):
    quantity: str
    value: str
    unit: str
    token: str
    sentence: str


class VariableOut(BaseModel):
    name: str
    role: str
    type: str
    measurement: str


class MethodsNotesOut(BaseModel):
    main_outcome: str
    baseline_numbers: list[BaselineNumberOut]
    inclusion_exclusion: str
    population: str
    variables: list[VariableOut]
    instruments: str
    statistical_analysis: str
    intervention_details: str


class SampleSizeParamOut(BaseModel):
    name: str
    value: float
    source: Literal["paper", "convention", "assumption"]
    token: str
    note: str


class SampleSizeChoiceOut(BaseModel):
    formula: str
    parameters: list[SampleSizeParamOut]
    nonresponse_rate: float | None
    rate_source: Literal["paper", "convention", "assumption"]
    rate_token: str
    reasoning: str


class HypothesisOut(BaseModel):
    text: str
    applicable: bool


class AimsOut(BaseModel):
    main_aim: str
    secondary_aims: list[str]
    hypotheses: list[HypothesisOut]
    questions: list[str]
    practical_aims: list[str]
