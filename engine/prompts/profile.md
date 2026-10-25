# Task: build the study profile

A researcher gave the title of their study (Persian or English), and optionally its purpose and a design. Turn it
into a structured profile that the rest of the pipeline uses to search PubMed.

<title>{{title}}</title>
<purpose>{{purpose}}</purpose>
<design_hint>{{design_hint}}</design_hint>

Allowed design labels (from the app's design templates): {{design_labels}}

Return:
- `english_title`: the title in English (translate if needed, keep its meaning exactly).
- `population`, `exposure_or_intervention`, `comparator`, `outcomes`: the PICO-style parts in English. Write
  "not stated" for a part the title and purpose do not give.
- `setting_country`: the country in English if the title or purpose names a place, otherwise null.
- `design_guess`: one of the allowed labels, or "other". If the design hint is given, follow it.
- `design_guess_other_name`: the design's name when `design_guess` is "other", otherwise an empty string.
- `concepts`: 2–5 English concept names that a search must combine (the condition, the population, the exposure
  or intervention, the outcome).
- `core_query`: a PubMed Boolean query of the study's **subject only**: the condition (or exposure/intervention)
  and the population, at most 3 concept blocks. Write each block as `("MeSH term"[mh] OR synonym[tiab] OR …)`
  using real MeSH headings and close synonyms, and join the blocks with AND. Never add a block for what is measured
  or how (prevalence, incidence, associated factors, risk factors, effect, efficacy, comparison, cross-sectional,
  trial): those words hide similar studies, and the design is searched separately. Keep each block as specific as
  the title: a block for "female university students" may include `student*[tiab]` and `"Students"[mh]` but not a
  broader group such as `women[tiab]`.
- `plain_query`: 2–4 plain English words with no field tags naming the condition and the population, e.g.
  `vitamin D pregnant women`.
- `statpearls_terms`: 1–3 English words naming the disease or condition, e.g. `vitamin D deficiency`.
