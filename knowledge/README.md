# knowledge/: the expert's part of the product

Everything a clinician-researcher knows about writing a proposal lives here, as plain text and YAML. **No code.**
The engine reads these files at run time. To change how the app writes, you edit a file here, not the Python.

| File | What it holds | Used by |
|---|---|---|
| `persona.md` | the system prompt sent with every model call (epidemiologist, reporting guidelines, no invention) | every step |
| `style_rules.md` | writing rules for every prose section (narrative, numbers as written, citation tokens, Persian typography) | every writer |
| `background_checklist.md` | which studies belong in the background and the 9-item paragraph recipe | background search and writer |
| `questionnaire.md` | the 12 design questions answered before a design is named | design router |
| `designs.yaml` | per design: PubMed design filter, 5 methodological search topics, the allowed sample-size formulas, and instructions for each methods sub-section in Persian and English | background search, methods |
| `methods_checklist.md` | the a–h evidence questions answered before the methods are written | methods notes |
| `sample_size.md` | the formulas computed by code, the parameter-source rule and the reference values | sample-size step, tests |
| `aims_grammar.md` | sentence frames for aims, and the 1:1 rule for hypotheses and questions | aims writer |
| `introduction.md` | the four parts of an introduction and the StatPearls source rules | introduction writer |

These files were distilled from a pipeline a clinician built and used for real proposals (2024–2025), improved where
the old outputs showed weaknesses. Contributions from methodologists are very welcome: see `CONTRIBUTING.md`.
