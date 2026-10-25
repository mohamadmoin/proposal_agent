# Task: methods notes (answer the checklist before any methods text is written)

The researcher's study is a {{design_name}}:
<study_profile>
{{profile_json}}
</study_profile>
<purpose>{{purpose}}</purpose>

Answer the expert's checklist from the sources below only:

<methods_checklist>
{{methods_checklist}}
</methods_checklist>

The most similar studies (cite them with their tokens):
<background_papers>
{{background_papers}}
</background_papers>

Summaries of what studies say about the design's methodological topics:
<topic_summaries>
{{topic_summaries}}
</topic_summaries>

Return the fields a–h:
- `main_outcome` (a), `inclusion_exclusion` (c), `population` (d), `instruments` (f), `statistical_analysis` (g) and
  `intervention_details` (h, or "not applicable" for designs without an intervention): short English text with the
  paper tokens of the sources, e.g. {P2}.
- `baseline_numbers` (b): one item per number useful for the sample size, with `quantity` (e.g. "prevalence of
  vitamin D deficiency"), `value` exactly as written, `unit` (e.g. "%", "ng/mL", or ""), the `token` of the paper it
  comes from (e.g. "P2") and the `sentence` it comes from, copied verbatim.
- `variables` (e): one item per variable with `name`, `role` (outcome, exposure, intervention, confounder or
  background), `type` (continuous, binary, categorical, ordinal or time-to-event) and `measurement` (how and when).

Write "not reported" when the sources do not answer an item. Never invent numbers.
