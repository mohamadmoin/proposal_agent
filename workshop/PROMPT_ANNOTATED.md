# The build prompt, annotated

`PROMPT.md` is about 350 lines. Read it with three highlighters:

- 🩺 **medicine**: research-methods knowledge a clinician brings. No engineer would write these lines unprompted.
- 🔎 **search engineering**: how to find the right papers cheaply and reliably.
- 🛠 **software engineering**: how to build, run, test and share the app.

The slides use the same three colours. The point to take away: the 🩺 lines decide how good the output is. The 🛠
lines decide whether it runs. An AI coding agent can fill in many 🛠 details by itself. It cannot invent the 🩺 ones.

## §0 Read first: the knowledge pack 🩺🛠

| Line | Colour | Why it is there |
|---|---|---|
| "`knowledge/` … the source of truth … load them at run time; do not copy their content into Python" | 🛠 | The expert's content stays editable by the expert: change a template, not code. |
| The files themselves (`questionnaire.md`, `designs.yaml`, …) | 🩺 | Twelve design questions, six design templates (each with STROBE/CONSORT/PRISMA-style topics), the paragraph recipe, the aims grammar, the sample-size rules. This is the product. |

## §1 What the app does 🩺

| Line | Colour | Why |
|---|---|---|
| Inputs: title, optional purpose, optional design, language | 🩺 | The old tool used the title twice ("title" and "purpose"). A real purpose field and a design hint fix that. |
| "the output is a draft; the researcher is the author" | 🩺 | Research-ethics position, repeated in the Word file. |

## §2 The pipeline: medical rules

| Line | Colour | Why |
|---|---|---|
| "Each step is one small job … structured outputs" | 🛠 | Why cheap models work: short inputs, short outputs, nothing to parse. |
| Study profile: population, exposure, comparator, outcomes (PICO) | 🩺 | How a clinician frames a study. It drives the search. |
| `core_query` with MeSH and `[tiab]` synonyms | 🔎🩺 | MeSH is the controlled vocabulary of medicine. Knowing it exists is domain knowledge. |
| Screening scores: "1 = same topic but a different question, 2 = similar aim, 3 = same aim and main outcome" | 🩺 | "Strictly similar" made measurable. |
| "`design_match`" and "+2 for design match" | 🩺 | A background section compares studies of the same design: a cross-sectional study cites cross-sectional studies. |
| "recency: 2 within the last 5 years, 1 within 10" | 🩺 | "Newer is better" — the old prompt's rule, now in code. |
| "5 to 10 studies … never add papers with aim ≤ 1 … warn the user" | 🩺 | Never pad a literature review. |
| "evidence = sentence numbers … code checks they exist" | 🔎🛠 | A quote cannot be invented if the model can only point at numbered sentences. |
| "every number in `main_finding` must appear in the abstract" | 🩺🛠 | Numbers are where hallucinations hurt most in medicine. |
| "one call per paper … findings never mix between papers" | 🩺🛠 | One source per paragraph: no blended findings. |
| The 9-item recipe (title untranslated, first author, year, aim, design, population, methods, results, conclusion) | 🩺 | How an experienced researcher summarises a study. |
| Design router "answers the 12 questions … never fail on an unknown label" | 🩺🛠 | The questionnaire is a clinician's chain of thought. The fallback fixes an old crash. |
| "every later step gets the full design name … never a fragment" | 🛠 | Fixes the old bug where the writers saw "C" instead of "Cohort". |
| Topic evidence: 5 design-specific topics, ≤ 100-word summaries | 🩺 | Reporting-guideline items turned into searches. |
| a–h methods checklist | 🩺 | What a methodologist checks before writing methods. |
| "The model picks a formula … code computes … rounds up … inflates for drop-out" | 🩺🛠 | Statistics belongs in code. The model's job is to choose and justify. |
| "parameters each with a source: paper / convention / assumption" | 🩺 | An honest sample-size section shows where every number came from. |
| "the final sample size appears in ۳-۳" | 🛠 | Verification instead of trust. |
| Aims grammar and the 1:1 count check | 🩺🛠 | The aims, hypotheses and questions always line up. |
| Introduction: StatPearls "through the API only" | 🔎🛠 | Respecting site rules (bot checks, AI-input signals) is part of engineering. |
| "Parts 1–2 strong model, parts 3–4 small model" | 🛠 | Spend on the hardest synthesis only. |
| References: tokens → numbers by first appearance; Vancouver | 🩺🛠 | Citation style is domain knowledge; numbering is code. |
| Word: RTL Persian, B Nazanin, references LTR | 🩺🛠 | What an Iranian university expects to receive. |

## §3 Search engineering 🔎

| Line | Why |
|---|---|
| "Always send `tool`, `email`, `api_key`; 3 requests/s without a key, 9/s with one; ≥ 110 ms apart" | NCBI's published rules. Spacing the requests, not just counting them, avoids 429 errors (measured at DrNext). |
| Five lanes: core, design filter, last 10 years, plain words, country | Many cheap searches beat one clever search. Plain words guard against a wrong MeSH term. |
| TF-IDF over title + title + abstract, then reciprocal-rank fusion | A free, fast cut before any model reads anything; papers that several lanes agree on rise. |
| "Sentence splitting must not break on decimals or 'et al.'" | Small details like this decide whether the evidence check works. |

## §4 Software 🛠

| Line | Why |
|---|---|
| Django + django-ninja + Pydantic, MySQL, Docker Compose | One web framework with an admin and ORM; Pydantic schemas shared by the API and the model calls; the database DrNext uses. |
| `engine/` with no Django imports | Pure Python is easy to test and reuse. The web part stays thin. |
| `prompts/*.md` with named placeholders | Prompts are content: reviewed like code, edited without touching Python. |
| "do not send `temperature`; send `reasoning_effort`" | Current models are reasoning models. Old habits (temperature 0.01) now fail. |
| `LLM_PROVIDER = openai | gemini | fake` | Students can use any key they have, or none. |
| DB-polled worker with `SKIP LOCKED` | A real background queue without extra services. |
| Token auth, constant-time comparison, input limits | Security basics for an API. |
| `FRAME_ANCESTORS` | Lets DrNext Academy embed the app safely. |
| Tests with no network and no keys; fixtures | Anyone can run them, including CI. |
| Open-source files, CI, image publishing | What makes a repository a project others can join. |

## §5–6 How to work, and "done means" 🛠

| Line | Why |
|---|---|
| Milestones M1–M8, tests and a commit after each | Small verified steps: the single most useful instruction for an AI coding agent. |
| "Never push" | Humans decide what becomes public. |
| "Do not edit `knowledge/`; write questions in `QUESTIONS.md`" | The agent must not silently change medical content. |
| "Done means …" with concrete checks | A definition of done that can be verified, not a feeling. |

## What happened when we ran it

**The build (2026-10-25).** A fresh Claude Code agent got only this repository (the knowledge pack, DESIGN.md and
PROMPT.md v1). In about an hour it built all eight milestones, each with tests and a local commit:
- 143 tests, run with no network and no keys;
- a Docker setup that goes from the web form to a finished Word file in fake mode.

It wrote 22 decisions it had to take alone into `QUESTIONS.md`. That file is the agent's way of saying "a
clinician should confirm this".

**What only real runs could show.** Fakes test the plumbing, not the judgement. Real proposals on OpenAI and Gemini
found nine problems:

| Found in a live run | Kind | Fix (code or prompt line) |
|---|---|---|
| The model put "prevalence" and "associated factors" into the PubMed query, which hid the closest studies (female medical students in Tehran were missed) | 🩺🔎 | Prompt: the core query holds only the condition and the population |
| The sample size used a prevalence measured in pregnant women for a study of university students | 🩺 | Each number carries its population similarity; the closest population wins; a dissimilar source is flagged |
| An "(Archived)" StatPearls chapter was chosen | 🩺 | A current chapter always wins |
| A 2-point difference between two observed proportions gave a sample size of 10,386 | 🩺 | Above 2,000, the researcher is asked to check the smallest clinically important difference |
| A provider's strong model was down and stalled a run for 9 minutes | 🛠 | The small model takes over the step, with a warning |
| A free-tier key hit "15 requests per minute" | 🛠 | Wait as long as the provider asks (up to 45 s) |
| The model wrote `{S1, S2, S3}` in one pair of braces, so the citation stayed in the text | 🛠 | The numbering accepts grouped tokens |
| A raw JSON error dump appeared in the user's warnings | 🛠 | Short reasons for users; full errors in the trace |
| Another service could not reach the API (host not allowed) | 🛠 | Deployment note: allow the internal hostname |

Four of the nine are medical judgement. No test written before the runs would have caught them; a clinician reading
the output did. Each became a line in PROMPT.md v2, so the next build starts with it.

**Measured after the fixes (OpenAI `gpt-5.4-mini`, six Persian titles, one per design, design chosen by the app):**
- the design router picked the right design 6 of 6 times;
- every automatic check passed on every title: citations resolve, evidence is verbatim, the ۳-۳ number equals the
  computed one, aims match one to one, no empty section;
- 82–103 seconds and about 96–145 thousand tokens per proposal.

The expert's blind rating column is still empty: that is the next evaluation step.
