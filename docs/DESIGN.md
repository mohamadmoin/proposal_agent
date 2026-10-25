# Proposal Agent: design

## What it does

A researcher types a study title (Persian or English), and optionally a purpose and a design. The app writes a
first draft of four proposal sections:

- the **introduction**;
- the **aims, hypotheses and questions**;
- the **background (پیشینه)**: 5–10 strictly similar studies, one paragraph each;
- the **methods chapter (۳-۱ … ۳-۵)**, with a sample size computed by code.

Every citation points to a real PubMed record, and the app exports a Word file. Persian is the default output
language; English can be chosen.

It is a drafting tool. The researcher is the author and checks every sentence.

## Principles (why it works on cheap models)

1. **One small job per model call.** Plan the search, screen five abstracts, write one paragraph: each call has short
   inputs and a short output.
2. **Structured outputs.** Every call that returns data returns a Pydantic model (OpenAI Structured Outputs), so the
   app never parses free text.
3. **Code owns the facts.** Citation numbers, the reference list, the sample-size arithmetic and the design templates
   are code or data. The model writes prose around them and never invents a reference or a number.
4. **Verify what the model says.** Evidence sentences must exist in the abstract. Numbers in a summary must appear in
   its source. The sample size in ۳-۳ must equal the computed one.
5. **The expert's knowledge is data.** `knowledge/` holds the questionnaire, the design templates, the checklists and
   the style rules. Changing how the app writes means editing a text file.
6. **Keep everything.** Every search, paper, score, model call and draft is stored, so each sentence can be traced
   back to its source.
7. **Fail honestly.** A step that fails is marked failed and named. A section is never "done" and empty.

## Architecture

```
browser ──► Django (web pages + JSON API, django-ninja) ──► MySQL
                 │ creates a job row                          ▲
                 ▼                                            │
            worker process (same image, `manage.py run_worker`) — picks queued jobs from MySQL
                 │
                 ▼
            engine/ (plain Python, no Django)
              pipeline ─► background ─► methods ─► introduction ─► references ─► docx
                 │             │            │            │
                 ▼             ▼            ▼            ▼
               llm.py      pubmed.py    samplesize.py  statpearls.py
         (OpenAI-compatible) (NCBI E-utilities)        (PubMed book XML + optional saved HTML)
```

- **engine/** has no Django import. It talks to storage through a small `Store` interface, so it can be tested with
  fakes and reused elsewhere (e.g. imported by another platform).
- **proposals/** is the Django app: models, admin, API, web pages, the worker command and the `Store` implementation.
- **knowledge/** is read at run time.

## Decisions

| # | Decision | Why | Rejected |
|---|---|---|---|
| D1 | Retrieval = **plan → search lanes → TF-IDF cut with rank fusion → LLM screening with verified evidence → code selection** (adapted from DrNext's fast PubMed pipeline, re-aimed at "studies like mine") | On DrNext's 41-question clinical benchmark (2026-10) this design found 40% of the reference papers, against 31% for the Academy research loop and 16% for the old 12-phrase/title-TF-IDF method, at about 95% precision. The old method ranked titles only. | Old 12-phrase funnel (low recall); Europe PMC research loop (slower, tuned to clinical questions, returns about 30 papers) |
| D2 | StatPearls via the **official PubMed API** (summary, sections, reference list) plus an **optional user-uploaded HTML** page | Bookshelf pages answer scripts with a bot check, and the StatPearls site does not allow AI input. The app never scrapes past such checks. | Scraping Bookshelf or StatPearls pages (blocked, against the site's signals); a static 9,370-title spreadsheet (stale) |
| D3 | Background jobs run in a **database-polled worker** (`SELECT … FOR UPDATE SKIP LOCKED`), not Celery | One fewer service (no Redis) and about 60 lines of code; enough for the expected load. Celery is the documented next step. | Celery + Redis (more moving parts for students); threads in the web process (lost on restart) |
| D4 | **django-ninja** for the API | Pydantic-native, with automatic OpenAPI docs at `/api/docs`; the same schemas as the engine | Django REST framework (a second schema system) |
| D5 | **Provider-agnostic LLM client** on the OpenAI SDK: `LLM_PROVIDER=openai` (default) or `gemini` (OpenAI-compatible endpoint) or a custom `LLM_BASE_URL` | Students can use whichever key they have; DrNext runs it on Gemini | Separate SDK per provider |
| D6 | **MySQL** in Docker Compose (SQLite for tests) | Matches DrNext production practice; a real relational database to learn | PostgreSQL (fine, but not the stack students will meet at DrNext) |
| D7 | Prompts live in `engine/prompts/*.md` with named placeholders | Prompts are content: readable and reviewable like code | Prompt strings inside Python |
| D8 | Citation **tokens** (`{P3}`, `{S2}`, `{M4}`) in model text, replaced by code | Numbers are assigned once, in order of first appearance across the whole document | Letting the model number citations (the old intro bug) |

## Pipeline and model calls (typical full run)

| Step | Calls | Model tier |
|---|---|---|
| Study profile (English title, PICO, design guess, PubMed query) | 1 | small |
| Background screening (40 papers, 5 per call, in parallel) | 8 | small |
| Background paragraphs (one per selected paper, in parallel) | 5–10 | small |
| Design router with the questionnaire (skipped if the user gives a design) | 0–1 | strong |
| Methods evidence: topic queries, then 5 topic summaries | 1 + 5 | small |
| Methods notes (a–h checklist) | 1 | small |
| Sample-size parameters (code computes) | 1 | small |
| Methods sub-sections ۳-۱ … ۳-۵ (in parallel) | 5 | small |
| Aims, hypotheses, questions | 1 | small |
| Introduction parts 1–2 (StatPearls) and parts 3–4 (background) | 2 | strong, small |
| **Total** | **≈ 30–37** | ≈ 90% small |

With `gpt-5.4-mini` or `gemini-3.1-flash-lite` this costs a few US cents and takes about 1–3 minutes.

## Data model

`Proposal` (the job and its results) has these related tables:
- `Paper`: every retrieved record, with its lanes, scores, screening record, role and selection;
- `Reference`: the final numbered list;
- `LLMCall`: step, model, prompt, response, tokens, latency, status;
- `StepLog`: progress events shown to the user.

## Integration

- **REST API** (token auth): create a proposal, poll its status, fetch the result and the Word file. OpenAPI at
  `/api/docs`.
- **Embedding**: the web pages can be framed by allowed origins (`FRAME_ANCESTORS`), so a platform such as DrNext
  Academy can open the app directly or call it as a tool.

## Evaluation

`docs/EVALUATION.md` describes the evaluation:
- a fixed set of titles, one per design;
- automatic checks: every citation resolves, evidence sentences are verbatim, the ۳-۳ number equals the computed
  one, and aims, hypotheses and questions match one to one;
- a methodologist reads a sample blind, with a rubric.
