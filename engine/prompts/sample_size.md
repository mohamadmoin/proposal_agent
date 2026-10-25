# Task: choose the sample-size formula and its parameters

You do not calculate anything: code computes the sample size from your choice. Follow the expert's rules:

<sample_size_rules>
{{sample_size_rules}}
</sample_size_rules>

The researcher's study is a {{design_name}}:
<study_profile>
{{profile_json}}
</study_profile>
<purpose>{{purpose}}</purpose>

Formulas allowed for this design, with their parameter names:
<allowed_formulas>
{{allowed_formulas}}
</allowed_formulas>

Baseline numbers found in similar studies (each with its paper token, source sentence, the paper's title and country, and how similar its population is to this study's, 0–3; null = not rated):
<baseline_numbers>
{{baseline_numbers}}
</baseline_numbers>

Return:
- `formula`: one id from the allowed list, the one that fits the study's main aim.
- `parameters`: one item per parameter, using exactly the parameter names listed for that formula. Give `value` as a
  number (proportions as decimals, e.g. 0.3). `source` is "paper" (then `token` is the paper token, e.g. "P2", and the
  value must be written in that paper's sentence above), "convention" (α = 0.05, power = 0.80, d = 0.05 for a
  prevalence of 0.5) or "assumption" (no source; the researcher must confirm it). `token` is "" unless the source is
  "paper". `note` says briefly why this value.
- Choose each paper-sourced value from the study whose **population is most like this study's** (highest
  population similarity, then the same country, then the most recent). A prevalence measured in a different
  population (e.g. pregnant women for a study of university students) is a weak basis: use it only when nothing
  closer exists, and say so in `note`.
- `nonresponse_rate`: the expected non-response or drop-out rate as a decimal (e.g. 0.1), or null. `rate_source` and
  `rate_token` as for the parameters.
- `reasoning`: one or two sentences on why this formula fits.
