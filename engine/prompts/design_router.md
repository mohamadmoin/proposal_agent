# Task: choose the study design

Answer the clinician's twelve design questions about the researcher's study, then name the design.

<questionnaire>
{{questionnaire}}
</questionnaire>

The researcher's study:
<title>{{title}}</title>
<purpose>{{purpose}}</purpose>
<study_profile>
{{profile_json}}
</study_profile>

Supported design labels: {{design_labels}}

Return:
- `answers`: twelve items, `number` 1 to 12, each `answer` one or two sentences about this study. When the title and
  purpose do not tell you, say so in the answer.
- `label`: one of the supported labels, or "other" when none fits (for example a qualitative or diagnostic-accuracy
  study).
- `other_name`: the design's full name when `label` is "other", otherwise an empty string.
- `rationale`: at most three sentences explaining the choice from the answers.
- `missing_information`: the details the researcher should add to make the choice certain (may be empty).
