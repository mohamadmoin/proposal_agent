# Task: write the aims, hypotheses and research questions

Follow the expert's grammar exactly:

<aims_grammar>
{{aims_grammar}}
</aims_grammar>

<study>
Title: {{study_title}}
English title: {{english_title}}
Design: {{design_name}}
Purpose: {{purpose}}
</study>
<study_profile>
{{profile_json}}
</study_profile>

The study's variables (from the methods notes):
<variables>
{{variables}}
</variables>

Output language: <output_language>{{language}}</output_language>

Return, in the output language:
- `main_aim`: the title rewritten as an aim.
- `secondary_aims`: one sentence per variable of interest, using the sentence frames.
- `hypotheses`: one item per secondary aim, in the same order. For a descriptive aim, set `applicable` to false and
  write in `text` that a hypothesis is not applicable (the question covers it).
- `questions`: one question per secondary aim, in the same order.
- `practical_aims`: who can use the results and how (1–3 items).

The three lists `secondary_aims`, `hypotheses` and `questions` must have the same length.
