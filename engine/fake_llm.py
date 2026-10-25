"""A deterministic stand-in for the language model (LLM_PROVIDER=fake).

It reads the same prompt a real model gets and answers with simple rules, copying sentences and numbers verbatim
from the sources so every code check passes. It exists for the tests and for a keyless demo: its prose is
placeholder text, not a real proposal.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

from engine import schemas
from engine.verify import split_sentences

COUNTRIES = [
    "Iran", "Iraq", "Turkey", "Saudi Arabia", "Pakistan", "India", "China", "Japan", "Korea", "Egypt", "Nigeria",
    "Ethiopia", "Kenya", "Brazil", "Mexico", "United States", "USA", "Canada", "United Kingdom", "UK", "Ireland",
    "Spain", "Italy", "Greece", "Germany", "France", "Netherlands", "Belgium", "Australia", "Bangladesh", "Qatar",
    "United Arab Emirates", "Lebanon", "Jordan", "Oman", "Kuwait", "Vietnam", "Thailand", "Malaysia", "Indonesia",
]  # fmt: skip

DEMO_PROFILE = {
    "english_title": "Prevalence of vitamin D deficiency among pregnant women",
    "population": "pregnant women",
    "setting_country": None,
    "exposure_or_intervention": "pregnancy (no intervention)",
    "comparator": "not stated",
    "outcomes": "vitamin D deficiency (serum 25-hydroxyvitamin D)",
    "design_guess": "cross_sectional",
    "design_guess_other_name": "",
    "concepts": ["vitamin D deficiency", "pregnancy", "prevalence"],
    "core_query": '("Vitamin D Deficiency"[mh] OR "vitamin D deficiency"[tiab] OR hypovitaminosis D[tiab]) AND '
    '("Pregnancy"[mh] OR pregnant[tiab] OR pregnancy[tiab]) AND ("Prevalence"[mh] OR prevalence[tiab])',
    "plain_query": "vitamin D pregnancy prevalence",
    "statpearls_terms": "vitamin D deficiency",
}

DESIGN_KEYWORDS = [
    ("systematic_review_or_meta_analysis", ["systematic review", "meta-analysis", "meta analysis", "مرور نظام", "فراتحلیل"]),
    ("randomized_controlled_trial", ["randomized", "randomised", "trial", "کارآزمایی"]),
    ("quasi_experimental", ["quasi", "before and after", "نیمه‌تجربی", "نیمه تجربی", "educational intervention", "مداخله"]),
    ("case_control", ["case-control", "case control", "مورد-شاهد", "مورد شاهد"]),
    ("cohort", ["cohort", "follow-up", "incidence", "کوهورت", "بروز"]),
    ("cross_sectional", ["prevalence", "cross-sectional", "شیوع", "مقطعی"]),
]  # fmt: skip


def tag(text: str, name: str) -> str:
    """Inner text of the first <name>…</name> in a prompt."""
    match = re.search(rf"<{name}(?:\s[^>]*)?>(.*?)</{name}>", text, flags=re.S)
    return match.group(1).strip() if match else ""


def blocks(text: str, name: str) -> list[tuple[dict[str, str], str]]:
    """All <name attr="…">body</name> blocks, with their attributes."""
    found = []
    for match in re.finditer(rf"<{name}((?:\s+\w+=\"[^\"]*\")*)\s*>(.*?)</{name}>", text, flags=re.S):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', match.group(1)))
        found.append((attrs, match.group(2).strip()))
    return found


def field(body: str, label: str) -> str:
    match = re.search(rf"^{label}:\s*(.*)$", body, flags=re.M)
    return match.group(1).strip() if match else ""


def json_tag(text: str, name: str) -> Any:
    raw = tag(text, name)
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def guess_design(text: str) -> str:
    low = text.lower()
    for label, words in DESIGN_KEYWORDS:
        if any(w in low for w in words):
            return label
    return "cross_sectional"


def stem(word: str) -> str:
    return word.lower()[:6]


def concept_hits(concepts: list[str], text: str) -> int:
    """How many concepts have at least one meaningful word in the text."""
    low = text.lower()
    hits = 0
    for concept in concepts:
        words = [w for w in re.findall(r"[a-z][a-z\-]+", concept.lower()) if len(w) >= 4 or w == "d"]
        long_words = [w for w in words if len(w) >= 4]
        if long_words and any(stem(w) in low for w in long_words):
            hits += 1
    return hits


def strip_label(sentence: str) -> str:
    return re.sub(r"^[A-Z][A-Z /&]+:\s*", "", sentence)


class FakeBackend:
    """Implements the backend interface of engine.llm with rule-based answers keyed by step name."""

    def __init__(self, config: Any = None):
        self.config = config

    def call(self, messages: list[dict[str, str]], tier: str, schema: type[BaseModel] | None, step: str = ""):
        from engine.llm import Completion  # local import: llm imports this module lazily

        user = messages[-1]["content"]
        handler = getattr(self, "step_" + step.split(":")[0], None)
        if handler is None:
            raise ValueError(f"the fake model has no answer for step {step!r}")
        result = handler(user)
        if schema is not None:
            parsed = schema.model_validate(result) if not isinstance(result, BaseModel) else result
            text = parsed.model_dump_json()
        else:
            parsed, text = None, str(result)
        model = "fake-strong" if tier == "strong" else "fake-small"
        return Completion(text, parsed, len(user) // 4, len(text) // 4, model)

    # --- structured steps ---

    def step_profile(self, user: str) -> dict[str, Any]:
        title, purpose, hint = tag(user, "title"), tag(user, "purpose"), tag(user, "design_hint")
        text = f"{title} {purpose}"
        if "vitamin" in text.lower() or "ویتامین" in text:
            profile = dict(DEMO_PROFILE)
        else:
            words = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", title)
            english = title if words else "Study title (the offline demo cannot translate)"
            concepts = words[:3] or ["study"]
            profile = {
                "english_title": english,
                "population": "not stated",
                "setting_country": None,
                "exposure_or_intervention": "not stated",
                "comparator": "not stated",
                "outcomes": "not stated",
                "design_guess": "cross_sectional",
                "design_guess_other_name": "",
                "concepts": concepts,
                "core_query": " AND ".join(f"({w}[tiab])" for w in concepts),
                "plain_query": " ".join(concepts[:4]),
                "statpearls_terms": " ".join(concepts[:2]),
            }
        low = text.lower()
        if any(k in low for k in ("tehran", "iran")) or any(k in text for k in ("تهران", "ایران", "شیراز", "مشهد")):
            profile["setting_country"] = "Iran"
        profile["design_guess"] = guess_design(f"{hint} {text}") if hint or text else profile["design_guess"]
        return profile

    def step_screening(self, user: str) -> dict[str, Any]:
        profile = json_tag(user, "study_profile")
        concepts = profile.get("concepts") or []
        country = (profile.get("setting_country") or "").lower()
        study_label = (re.search(r"\(label: ([a-z_]+)\)", user) or [None, ""])[1]
        items = []
        for attrs, body in blocks(tag(user, "papers"), "paper"):
            title = field(body, "Title")
            abstract_numbered = body.split("Abstract:", 1)[-1]
            sentences = re.findall(r"\{(\d+)\} (.*?)(?= \{\d+\} |$)", abstract_numbered, flags=re.S)
            abstract = " ".join(s for _, s in sentences)
            title_hits, all_hits = concept_hits(concepts, title), concept_hits(concepts, title + " " + abstract)
            if title_hits >= 3 or (title_hits >= 2 and all_hits >= 3):
                aim = 3
            elif title_hits >= 2 or all_hits >= 3:
                aim = 2
            else:
                aim = 1 if all_hits else 0
            population = 2 if concept_hits([profile.get("population", "")], abstract) else 1
            if country and country in abstract.lower():
                population += 1
            design = guess_design(field(body, "Publication types") + " " + title + " " + abstract)
            finding_no, finding = self.pick_finding(sentences)
            items.append(
                {
                    "pmid": attrs.get("pmid", ""),
                    "aim_similarity": aim,
                    "population_similarity": population,
                    "design": design,
                    "design_match": design == study_label,
                    "sample_size": self.sample_size(abstract),
                    "country": next((c for c in COUNTRIES if re.search(rf"\b{c}\b", abstract)), None),
                    "aim": f"To study {title.rstrip('.')}.",
                    "main_finding": finding,
                    "evidence": [finding_no] if finding_no else [],
                }
            )
        return {"papers": items}

    @staticmethod
    def pick_finding(sentences: list[tuple[str, str]]) -> tuple[int, str]:
        """The first results sentence with a percentage (or any number), copied verbatim."""
        for wanted in (r"\d\s?%", r"\d"):
            for number, sentence in sentences:
                if re.search(wanted, sentence) and not sentence.upper().startswith(("BACKGROUND", "OBJECTIVE", "AIM")):
                    return int(number), strip_label(sentence)
        if sentences:
            number, sentence = sentences[-1]
            return int(number), strip_label(sentence)
        return 0, ""

    @staticmethod
    def sample_size(abstract: str) -> int | None:
        match = re.search(
            r"(\d[\d,]*)\s+(?:pregnant women|women|participants|patients|subjects|mothers|children|individuals)",
            abstract,
        )
        if not match:
            return None
        value = int(match.group(1).replace(",", ""))
        return value if value < 10_000_000 else None

    def step_design_router(self, user: str) -> dict[str, Any]:
        text = " ".join([tag(user, "title"), tag(user, "purpose"), tag(user, "study_profile")])
        label = guess_design(text)
        answers = [
            {"number": i, "answer": f"Answered from the title and purpose (offline demo answer {i})."}
            for i in range(1, 13)
        ]
        return {
            "answers": answers,
            "label": label,
            "other_name": "",
            "rationale": f"The title describes a study best matched by the '{label}' template. "
            "This is a rule-based answer from the offline demo.",
            "missing_information": ["The recruitment setting and period", "The expected sample source"],
        }

    def step_topic_queries(self, user: str) -> dict[str, Any]:
        profile = json_tag(user, "study_profile")
        subject = (profile.get("plain_query") or "study").split()[:3]
        topics = [t.strip("- ").strip() for t in tag(user, "topics").splitlines() if t.strip()]
        return {"queries": [" ".join((subject + t.split())[:6]) for t in topics]}

    def step_methods_notes(self, user: str) -> dict[str, Any]:
        profile = json_tag(user, "study_profile")
        papers = blocks(tag(user, "background_papers"), "paper")
        tokens = [a.get("token", "") for a, _ in papers]
        first = "{" + tokens[0] + "}" if tokens else ""
        baseline = []
        for attrs, body in papers:
            for sentence in split_sentences(body.split("Abstract:", 1)[-1]):
                match = re.search(r"(\d+(?:\.\d+)?)\s?%", sentence)
                if match:
                    baseline.append(
                        {
                            "quantity": "proportion reported in a similar study",
                            "value": match.group(1),
                            "unit": "%",
                            "token": attrs.get("token", ""),
                            "sentence": strip_label(sentence),
                        }
                    )
                    break
            if len(baseline) >= 3:
                break
        population = profile.get("population") or "the study population"
        return {
            "main_outcome": f"{profile.get('outcomes') or 'not reported'} {first}".strip(),
            "baseline_numbers": baseline,
            "inclusion_exclusion": f"Similar studies included {population} and excluded people with conditions "
            f"affecting the outcome {first}".strip(),
            "population": f"{population} {first}".strip(),
            "variables": [
                {
                    "name": profile.get("outcomes") or "main outcome",
                    "role": "outcome",
                    "type": "binary",
                    "measurement": "measured once at enrolment",
                },
                {"name": "age", "role": "background", "type": "continuous", "measurement": "recorded at enrolment"},
            ],  # fmt: skip
            "instruments": "not reported",
            "statistical_analysis": f"Descriptive statistics and regression models {first}".strip(),
            "intervention_details": "not applicable",
        }

    def step_sample_size(self, user: str) -> dict[str, Any]:
        allowed = json_tag(user, "allowed_formulas") or []
        baseline = json_tag(user, "baseline_numbers") or []
        formula = allowed[0]["id"] if allowed else "single_proportion"
        params = self.default_params(formula, baseline)
        return {
            "formula": formula,
            "parameters": params,
            "nonresponse_rate": 0.1,
            "rate_source": "assumption",
            "rate_token": "",
            "reasoning": f"The main aim fits the {formula} formula (offline demo choice).",
        }

    @staticmethod
    def default_params(formula: str, baseline: list[dict[str, str]]) -> list[dict[str, Any]]:
        def param(name: str, value: float, source: str = "assumption", token: str = "") -> dict[str, Any]:
            return {"name": name, "value": value, "source": source, "token": token, "note": "offline demo value"}

        conventions = [param("alpha", 0.05, "convention")]
        power = [param("power", 0.8, "convention")]
        percent = next((b for b in baseline if b.get("unit") == "%"), None)
        if formula == "single_proportion":
            if percent:
                p = float(percent["value"]) / 100
                return [param("p", p, "paper", percent["token"]), param("d", 0.05, "convention")] + conventions
            return [param("p", 0.5, "convention"), param("d", 0.05, "convention")] + conventions
        table = {
            "single_mean": [param("sigma", 10), param("d", 2)],
            "two_proportions": [param("p1", 0.6), param("p2", 0.4)],
            "two_means": [param("sigma", 10), param("delta", 5)],
            "paired_mean": [param("sigma_d", 8), param("delta", 4)],
            "correlation": [param("r", 0.3)],
            "case_control_unmatched": [param("p0", 0.3), param("odds_ratio", 2)],
        }
        extra = [] if formula == "single_mean" else power
        return table.get(formula, []) + conventions + extra

    def step_aims(self, user: str) -> dict[str, Any]:
        profile = json_tag(user, "study_profile")
        lang = tag(user, "output_language") or "fa"
        population = profile.get("population") or "the study population"
        outcome = profile.get("outcomes") or "the main outcome"
        exposure = profile.get("exposure_or_intervention") or "the exposure"
        title = profile.get("english_title") or "the study"
        if lang == "fa":
            return {
                "main_aim": f"تعیین {title}",
                "secondary_aims": [f"تعیین وضعیت {outcome} در {population}",
                                   f"تعیین ارتباط {exposure} با {outcome} در {population}"],
                "hypotheses": [{"text": "فرضیه برای این هدف توصیفی کاربرد ندارد.", "applicable": False},
                               {"text": f"میان {exposure} و {outcome} در {population} ارتباط وجود دارد.",
                                "applicable": True}],
                "questions": [f"وضعیت {outcome} در {population} چگونه است؟",
                              f"آیا میان {exposure} و {outcome} در {population} ارتباط وجود دارد؟"],
                "practical_aims": ["استفاده پزشکان و سیاست‌گذاران سلامت از نتایج برای برنامه‌ریزی غربالگری"],
            }  # fmt: skip
        return {
            "main_aim": f"To determine the {title[0].lower() + title[1:]}",
            "secondary_aims": [f"To determine {outcome} in {population}",
                               f"To determine the association between {exposure} and {outcome} in {population}"],
            "hypotheses": [{"text": "Not applicable (descriptive aim).", "applicable": False},
                           {"text": f"There is an association between {exposure} and {outcome} in {population}.",
                            "applicable": True}],
            "questions": [f"What is the status of {outcome} in {population}?",
                          f"Is there an association between {exposure} and {outcome} in {population}?"],
            "practical_aims": ["Clinicians and health policy makers can use the results to plan screening."],
        }  # fmt: skip

    # --- prose steps ---

    def step_background_paragraph(self, user: str) -> str:
        attrs, body = (blocks(user, "paper") or [({}, "")])[0]
        token = "{" + attrs.get("token", "P0").strip("{}") + "}"
        record = json_tag(user, "screening")
        title, author, year = field(body, "Title"), field(body, "First author"), field(body, "Year")
        finding = record.get("main_finding", "")
        size = record.get("sample_size")
        if tag(user, "output_language") == "en":
            size_text = f" The study included {size} participants." if size else ""
            return (
                f'In a study by {author} and colleagues ({year}), titled "{title}", the authors examined this '
                f"question.{size_text} The main finding was reported as follows: {finding} {token}"
            )
        size_text = f" حجم نمونه این مطالعه {size} نفر بود." if size else ""
        return (
            f'در مطالعه‌ای که توسط {author} و همکاران در سال {year} با عنوان "{title}" انجام شد، '
            f"پژوهشگران این موضوع را بررسی کردند.{size_text} یافته اصلی این مطالعه چنین گزارش شد: {finding} {token}"
        )

    def step_topic_summary(self, user: str) -> str:
        parts, words = [], 0
        for attrs, body in blocks(tag(user, "papers"), "paper")[:2]:
            sentences = split_sentences(body.split("Abstract:", 1)[-1])
            if not sentences:
                continue
            sentence = strip_label(sentences[0])
            chunk = " ".join(sentence.split()[: max(0, 45 - words)])
            if chunk:
                parts.append(f"{chunk} {{{attrs.get('token', '')}}}")
                words += len(chunk.split())
        return " ".join(parts) or "The abstracts say nothing specific about this topic."

    def step_methods_section(self, user: str) -> str:
        lang = tag(user, "output_language") or "fa"
        design = field(tag(user, "study"), "Design")
        tokens = re.findall(r"\{([PM]\d+)\}", tag(user, "notes") + " " + tag(user, "topic_summaries"))
        cite = f" {{{tokens[0]}}}" if tokens else ""
        calculation = tag(user, "calculation")
        if lang == "en":
            text = (
                f"This {design} will follow the expert instruction for this sub-section, as similar studies did.{cite}"
            )
            if calculation:
                text += f" The sample size was calculated as follows. {calculation}"
            return text
        text = f"این {design} مطابق دستورالعمل این بخش و همانند مطالعات مشابه اجرا خواهد شد.{cite}"
        if calculation:
            text += f" حجم نمونه به شرح زیر محاسبه شد. {calculation}"
        return text

    def step_intro_problem(self, user: str) -> str:
        lang = tag(user, "output_language") or "fa"
        parts = []
        for i, (attrs, body) in enumerate(blocks(tag(user, "sources"), "source")[:4]):
            sentences = split_sentences(body.split("Text:", 1)[-1])
            for sentence in sentences[: 2 if i == 0 else 1]:
                parts.append(f"{strip_label(sentence)} {{{attrs.get('token', '')}}}")
        lead = "بر اساس منابع موجود: " if lang == "fa" else ""
        return lead + " ".join(parts) if parts else ""

    def step_intro_studies(self, user: str) -> str:
        lang = tag(user, "output_language") or "fa"
        parts = []
        for attrs, body in blocks(tag(user, "papers"), "paper")[:5]:
            parts.append(f"{field(body, 'Main finding')} {{{attrs.get('token', '')}}}")
        if lang == "en":
            gap = "Few studies have examined this question in the study population, so this study is needed."
            return ("Similar studies reported the following. " + " ".join(parts) + " " + gap).strip()
        gap = "مطالعات اندکی این موضوع را در جمعیت مورد نظر بررسی کرده‌اند و انجام این مطالعه ضروری است."
        return ("مطالعات مشابه چنین گزارش کرده‌اند: " + " ".join(parts) + " " + gap).strip()


# Keep the schemas import used (documentation: the fake answers these models).
ANSWERS = {
    "profile": schemas.StudyProfileOut,
    "screening": schemas.ScreeningBatchOut,
    "design_router": schemas.DesignDecisionOut,
    "topic_queries": schemas.TopicQueriesOut,
    "methods_notes": schemas.MethodsNotesOut,
    "sample_size": schemas.SampleSizeChoiceOut,
    "aims": schemas.AimsOut,
}
