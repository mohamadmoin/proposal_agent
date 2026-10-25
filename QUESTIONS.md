# Questions for the clinician (decisions taken without you)

Each item says what was unclear, what I chose (the most conservative reading I could find), and where it lives in
the code, so you can confirm or change it. Nothing in `knowledge/` was edited.

## Models and providers

1. **Default OpenAI models.** The brief lists `gpt-5.4-mini` for both the small and the strong tier. I used exactly
   that (`engine/llm.py › PROVIDERS`); if the strong tier was meant to be a larger model, set `LLM_MODEL_STRONG` or
   tell me which model to make the default.

## Background (پیشینه)

2. **Recency windows.** "Within the last 5 years" = publication year ≥ current year − 4 (2 points); "within 10" =
   ≥ current year − 9 (1 point), matching the `<Y−9>:<Y>[dp]` lane (`engine/retrieval.py › recency_points`).
3. **The "only k" warning.** k is the number of *strictly* similar studies. The warning is shown whenever k < 5, even
   when "partly similar" studies top the list up to 5. If no study has aim ≥ 2, the background is empty and is marked
   failed (never padded).
4. **Numbers in paragraphs.** Besides the year and the sample size, numbers that appear in the paper's **title** are
   accepted (the recipe requires the title, and titles often contain numbers such as "type 2"). See DESIGN.md D13.
5. **Percentages "both ways".** A finding's "45%" matches "0.45" in the abstract and vice versa; any decimal ≤ 1 in the
   source also counts as its percentage (`engine/verify.py › number_set`).
6. **Empty evidence.** An empty evidence list counts as invalid evidence, so aim_similarity is capped at 1.
7. **Duplicates.** "Title cosine" is computed on word counts (no IDF weighting), threshold > 0.8, plus same first-author
   surname and year.
8. **Design match for "other" designs.** When the study uses the general template (e.g. a qualitative study), few
   papers will be judged "same design", so eligibility relies on population similarity ≥ 2. Is that acceptable?

## Design and methods

9. **When the router runs.** With no design from the user, the design router (strong model) runs once, before the
   background, because the background's design filter and design match need it. If it fails, the profile's design
   guess is used, with a warning.
10. **Which number must appear in ۳-۳.** The final **total** after non-response/drop-out inflation (the sum of both
    groups for two-group formulas). Rounding is up, per group, and inflation is applied per group:
    ⌈⌈n⌉ / (1 − rate)⌉.
11. **Parameter clean-up.** Proportions given as percentages (60) are read as 0.60; a non-response rate between 1 and
    100 is read as a percentage; a rate of 90% or more is ignored; missing α and power are filled with the
    conventions 0.05 and 0.80. A missing substantive parameter (p, σ, Δ, …) is **not** invented: the calculation
    fails, ۳-۳ is written without a number and you get a warning (DESIGN.md D15).
12. **Sample size for qualitative or other designs.** `designs.yaml › general` allows numeric formulas, so a
    qualitative study may receive a computed sample size. Should the general template skip the calculation when the
    design is qualitative?
13. **Topic summaries** (methods evidence) are written in English as internal notes and are not cited in the final
    document (DESIGN.md D14). Should they be in the output language and visible?
14. **Aims without methods.** When the methods section is not requested, the aims are written from the profile only
    (no variable list from the methods notes).

## Introduction

15. **Numbers in parts 1–2** are checked against the sources and flagged with a warning (no retry); the brief did not
    specify a retry for the introduction.
16. **An uploaded page plus the API.** With an upload, the API search still runs to recover the chapter's authors,
    year and NBK number when the page's NBK or title matches; otherwise the reference is built from the page alone.
    The chapter text sent to the model is capped at 30,000 characters; at most 100 cited PMIDs are fetched and the 8
    most similar abstracts kept.

## Output and documents

17. **Persian section titles** not in `designs.yaml` were chosen by me: «مقدمه»، «اهداف، فرضیه‌ها و سؤالات پژوهش»،
    «پیشینه پژوهش»، «روش اجرای پژوهش»، «منابع» (`engine/titles.py`). Please confirm the wording your university uses.
18. **The disclaimer line** on the title page is the English sentence from the brief, in both languages. Do you want
    a Persian version for Persian documents?
19. **Web UI language.** The interface labels are English; the generated content is Persian (RTL, Vazirmatn) or
    English. A Persian interface is listed as a good first issue.
20. **Order of citations** follows the brief: Introduction → Aims → Background → Methods, so the StatPearls chapter is
    usually reference 1.

## Open-source files

21. **LICENSE-docs** is a CC BY 4.0 notice that links to the official legal code instead of reproducing the full legal
    text from memory (to avoid an inaccurate copy). Replace it with the verbatim legal code if you prefer.
22. **Contacts.** No maintainer e-mail was given, so the Code of Conduct and SECURITY.md point to GitHub's private
    reporting. The README's clone URL uses `<owner>`, and `CITATION.cff` lists "The Proposal Agent contributors";
    please fill in names and the repository owner.
