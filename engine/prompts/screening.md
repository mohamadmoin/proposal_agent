# Task: screen papers for the background section

Decide how similar each paper below is to the researcher's study. Follow the expert's rules:

<background_rules>
{{background_checklist}}
</background_rules>

The researcher's study:
<study_profile>
{{profile_json}}
</study_profile>
<study_design>{{design_name}} (label: {{design_label}})</study_design>
Design labels you may use: {{design_labels}}

Each paper's abstract is split into numbered sentences `{1} … {2} …`.

<papers>
{{papers}}
</papers>

Return one item per paper, with its `pmid`:
- `aim_similarity` (0–3): 0 = unrelated; 1 = same topic but a different question; 2 = similar aim; 3 = same aim and
  same main outcome.
- `population_similarity` (0–3): 0 = different population; 3 = the same population and setting.
- `design`: the paper's design as one of the labels, or "other". `design_match`: true when it is the same design as
  the study.
- `sample_size`: the number of participants (or included studies for a review) as an integer, or null when the
  abstract does not give it. `country`: where the study was done, or null.
- `aim`: one sentence stating the paper's aim.
- `main_finding`: one sentence with the main result. Copy every number exactly as written in the abstract.
- `evidence`: the numbers of the sentences that support `main_finding` (for example [5, 6]).

Judge only from the text given. Do not guess numbers.
