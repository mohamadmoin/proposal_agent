"""Step 1: the study profile (English title, PICO parts, design guess and the PubMed queries)."""

from __future__ import annotations

import re

from engine import prompts
from engine.knowledge import Knowledge, normalise_label
from engine.llm import LLM
from engine.schemas import ProposalRequest, StudyProfileOut


def build_profile(request: ProposalRequest, knowledge: Knowledge, llm: LLM) -> StudyProfileOut:
    """One small-model call, then code tidies what the later steps rely on."""
    labels = ", ".join(sorted(k for k in knowledge.designs if k != "general")) + ", other"
    hint = request.design_other_name or request.design
    user = prompts.render(
        "profile",
        title=request.title,
        purpose=request.purpose or "not given",
        design_hint=hint or "not given",
        design_labels=labels,
    )
    profile = llm.complete_json("profile", StudyProfileOut, user, "small")
    return tidy_profile(profile, knowledge)


def tidy_profile(profile: StudyProfileOut, knowledge: Knowledge) -> StudyProfileOut:
    """Enforce the profile's limits in code: 2–5 concepts, 2–4 plain words, 1–3 StatPearls words, a known label."""
    data = profile.model_dump()
    data["concepts"] = [c.strip() for c in profile.concepts if c.strip()][:5]
    plain = re.sub(r"\[[^\]]*\]|[()\"]|\b(AND|OR|NOT)\b", " ", profile.plain_query)
    data["plain_query"] = " ".join(plain.split()[:4])
    data["statpearls_terms"] = " ".join(re.sub(r"[()\"\[\]]", " ", profile.statpearls_terms).split()[:3])
    label = normalise_label(profile.design_guess)
    if label not in knowledge.designs and label != "other":
        data["design_guess_other_name"] = profile.design_guess_other_name or profile.design_guess
        label = "other"
    data["design_guess"] = label or "other"
    country = (profile.setting_country or "").strip()
    data["setting_country"] = None if country.lower() in {"", "null", "none", "not stated"} else country
    if not data["core_query"].strip():
        data["core_query"] = data["plain_query"]
    return StudyProfileOut(**data)
