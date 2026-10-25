# Task: write one background paragraph about one study

Write one paragraph of the background (پیشینه) about the single study below, for a proposal titled
"{{study_title}}". Use only this study's abstract and metadata. Follow the expert's recipe:

<background_rules>
{{background_checklist}}
</background_rules>

<paper token="{{token_id}}">
Title: {{paper_title}}
First author: {{first_author}}
Year: {{year}}
Journal: {{journal}}
Publication types: {{publication_types}}
Abstract:
{{abstract}}
</paper>

<screening>
{{screening_json}}
</screening>

Output language: <output_language>{{language}}</output_language>

Write one paragraph in the output language that covers, in order and only when the abstract gives it: the English
title in quotation marks, the first author ("… and colleagues"), the year, the aim, the design, the population and
setting (country, sample size), the methods, the main results with the numbers exactly as in the abstract, and the
authors' conclusion. End the paragraph with the token {{token}} and use it nowhere else.
