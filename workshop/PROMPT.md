# The build prompt

This is the prompt given to a fresh Claude Code session, started in this repository when it held only
`knowledge/`, `docs/DESIGN.md` and `workshop/`. Version 2: it now also carries the lessons of the first live runs
(see the last section of `PROMPT_ANNOTATED.md`). `PROMPT_ANNOTATED.md` explains which lines carry medical expertise
and which carry engineering.

---

You are building **Proposal Agent**, an open-source web app that drafts research-proposal sections for medical
researchers. I am a physician who built an earlier version of this tool, which students used for real proposals. You
are the engineer. Build the whole app in this repository, working in small verified steps.

## 0. Read first

1. `knowledge/`: the domain knowledge, written by me. These files are the source of truth for how the app thinks and
   writes. Load them at run time; do not copy their content into Python code.
2. `docs/DESIGN.md`: the architecture and the decisions behind it. Follow it. If you must deviate, record why in
   `docs/DESIGN.md`.

## 1. What the app does

**Input** (web form or API):
- a study title (Persian or English, required);
- an optional purpose (free text);
- an optional study design: one of the labels in `knowledge/designs.yaml`, "other" with a name, or empty to let the
  app decide;
- the output language: `fa` (default) or `en`;
- which sections to write (default: all);
- an optional StatPearls/Bookshelf HTML page the user saved in their own browser.

**Output**:
- the introduction;
- the aims, hypotheses and research questions;
- the background (پیشینه): one paragraph per strictly similar study;
- the methods chapter (۳-۱ … ۳-۵);
- one numbered reference list;
- a Word (.docx) file of all of it.

The app also keeps a full trace: searches, papers, scores, model calls, tokens and warnings.

**Users**: medical students and researchers. The output is a draft; the researcher is the author.

## 2. The pipeline: medical rules

Each step is one small job. Steps that return data use structured outputs (Pydantic models). Every prose-writing
step appends `knowledge/style_rules.md`. Every model call sends `knowledge/persona.md` as the system message.

### 2.1 Study profile (1 call, small model)
From the title, the purpose and the design hint, return:
- `english_title`;
- the PICO-style parts: `population`, `setting_country` (or null), `exposure_or_intervention`, `comparator`,
  `outcomes`;
- `design_guess`: a label from `designs.yaml`, or "other" plus a name;
- `concepts`: 2–5 English concept names;
- `core_query`: a PubMed Boolean query of the study's subject only: the condition (or exposure) and the population,
  at most 3 blocks. Each block is written as `("MeSH term"[mh] OR synonym[tiab] OR …)` and the blocks are joined
  with AND. No filters, no dates, and never a block for what is measured or how (prevalence, associated factors,
  effect, design words): those words hide the most similar studies;
- `plain_query`: 2–4 plain words with no field tags;
- `statpearls_terms`: 1–3 words naming the disease or condition.

### 2.2 Background (پیشینه): follow `knowledge/background_checklist.md`
1. Search: see §3. Retrieve, cut to the best 40, and screen them.
2. **Screening** (small model, batches of 5 papers, in parallel). Each paper is given with its title, journal, year,
   publication types and the whole abstract, split into numbered sentences (`{1} … {2} …`). For each paper return:
   - `aim_similarity` (0–3): 0 = unrelated, 1 = same topic but a different question, 2 = similar aim, 3 = same aim
     and main outcome;
   - `population_similarity` (0–3);
   - `design` (a label or "other") and `design_match` (bool, compared with the study's design);
   - `sample_size` (int or null) and `country`;
   - `aim`: one sentence;
   - `main_finding`: one sentence, with numbers exactly as written;
   - `evidence`: the sentence numbers that support `main_finding`.
3. **Verify** in code, never trusting the model:
   - The evidence sentence numbers must exist. If they do not, cap `aim_similarity` at 1.
   - Every number in `main_finding` must appear in the abstract. Normalise first: Persian and Arabic-Indic digits to
     ASCII, "·" to ".", ".5" to "0.5", and percentages both ways. If a number is missing, replace `main_finding` with
     the evidence sentences and keep the model text for audit.
4. **Select** in code:
   - Eligible: `aim_similarity ≥ 2` AND (`design_match` OR `population_similarity ≥ 2`).
   - Score = 3·aim + 1·population + 2·design_match + recency, where recency is 2 within the last 5 years, 1 within
     10, and 0 otherwise.
   - Merge duplicates: same first author and year, and title cosine > 0.8.
   - Take the top `BACKGROUND_MAX_PAPERS` (default 8, allowed 5–10).
   - If fewer than 5 qualify, add papers with `aim_similarity ≥ 2` and mark them "partly similar". Never add papers
     with aim ≤ 1. Tell the user with a warning: "Only k strictly similar studies were found."
5. **Write** one paragraph per selected paper (small model, one call per paper, in parallel). The call gets only that
   paper's abstract, its metadata and its screening record, so findings never mix between papers. Follow the 9-item
   recipe. End with the paper's token (`{P3}`). Code makes sure the token appears exactly once, at the end.
   - Numbers in the paragraph must be in the abstract, apart from the year and the sample size. If one is not, retry
     once with a reminder, then flag the paragraph as "numbers unverified" for the user.
6. Order the paragraphs by score.

### 2.3 Methods: follow `designs.yaml`, `questionnaire.md`, `methods_checklist.md`, `sample_size.md`
1. **Design.**
   - If the user chose one, normalise it (lower case, spaces → `_`). A label that is not in the file uses the
     `general` template with the user's design name.
   - Otherwise the **design router** (strong model) answers the 12 questions of `questionnaire.md` and returns
     `answers` (12 items), `label`, `other_name`, a ≤ 3-sentence `rationale` and `missing_information`. Store all of
     it and show it to the user. Never fail on an unknown label: use `general`.
   - Every later step gets the full design name in the output language, never a fragment of it.
2. **Topic evidence.**
   - One call turns the design's 5 `search_topics` into 5 PubMed queries of ≤ 6 plain words each, combining the
     study subject and the topic.
   - For each query: esearch (20 results), efetch, TF-IDF cut to 6 papers.
   - Then one call per topic writes a summary of ≤ 100 words of what those abstracts say about the topic, citing
     them with tokens `{M1}`, `{M2}`, … Numbers are verified against the abstracts as in §2.2.
3. **Methods notes** (1 call). Answer the a–h checklist of `methods_checklist.md` from the 5 best background papers
   (with their `{P#}` tokens) and the topic summaries. Item b (baseline numbers) is a list of
   `{quantity, value, unit, token, sentence}`. Drop any number that does not appear in the cited paper.
4. **Sample size** (1 call + code), exactly as in `sample_size.md`.
   - The model picks a formula from the design's list and fills the parameters, each with a source: `paper` (with a
     token), `convention` or `assumption`. It may also give a non-response or drop-out rate.
   - Each baseline number carries how similar its paper's population is to the study's. The model must choose
     values from the most similar population (then the same country, then the most recent).
   - Code then:
     - validates the parameter names for the formula;
     - flags, in the worked calculation, a value taken from a paper whose population may differ;
     - adds a note when the total exceeds 2,000, asking the researcher to check the smallest clinically important
       difference;
     - turns paper-sourced values that cannot be found in that paper into assumptions;
     - computes, rounds up, inflates for the rate;
     - writes a worked calculation (formula, substituted values, result) in the output language.
   - A design with no formulas (systematic review) skips this step.
   - Tests must reproduce the reference values in `sample_size.md`.
5. **Sub-sections ۳-۱ … ۳-۵** (5 calls in parallel). Each call gets:
   - the section title and that section's instruction from `designs.yaml`, in the output language;
   - the design name, the study profile and the purpose;
   - the notes and the topic summaries;
   - for ۳-۳, the worked calculation.
   - Check that the final sample size appears in ۳-۳ (after digit normalisation). If it does not, retry once, then
     flag it.
6. **Aims** (1 call): follow `aims_grammar.md` and return the structured aims. Check in code that the counts match
   one to one. If they do not, retry once, then flag.

### 2.4 Introduction: follow `knowledge/introduction.md`
1. **StatPearls through the API only.**
   - Search `(<statpearls_terms>) AND statpearls[book]` (5 results) and efetch the records (`PubmedBookArticle`).
   - Pick the chapter whose title is most similar (TF-IDF) to the profile. A current chapter always wins over one
     marked "(Archived)".
   - Keep the chapter summary (abstract), its section titles, its reference list with PMIDs, its NBK accession and
     its date.
   - Fetch the abstracts of up to 8 of its cited PMIDs, ranked by similarity to the profile.
   - Sources: `{S1}` = the chapter, `{S2}` … = the cited papers.
2. If the user uploaded an HTML page, parse it with BeautifulSoup:
   - drop scripts, styles and navigation;
   - turn headings plus their paragraphs into sections;
   - find the references list (the list after a "References" heading) and pull PMIDs out of links or "PMID:" text.
   - Use the chapter text as `{S1}` instead of the summary, and add the cited papers as above.
   - Never fetch NCBI Bookshelf or statpearls.com pages yourself: they answer scripts with a bot check, and the
     StatPearls site does not allow AI input.
3. **Parts 1–2** (strong model): the problem and the possible solution, from the sources only, cited with
   `{S#}` tokens.
4. **Parts 3–4** (small model): the similar studies and the gap and rationale, from the selected background papers'
   aims and findings, cited with `{P#}` tokens.

### 2.5 References and numbering (code only)
- Replace the tokens with numbers in order of first appearance through the document. The order is Introduction →
  Aims → Background → Methods.
- Merge adjacent citations into one bracket (`[1, 2]`). Accept several tokens written inside one pair of braces
  (`{S1, S2}`). Remove unknown tokens and log them.
- Vancouver style:
  - authors: up to 6, then "et al.";
  - then title, journal, `Year;Volume(Issue):Pages`, `doi:` and `PMID:`;
  - a StatPearls chapter: `Authors. Title. In: StatPearls [Internet]. Treasure Island (FL): StatPearls Publishing;
    Year. Available from: https://www.ncbi.nlm.nih.gov/books/NBK…/`.

### 2.6 Word export (python-docx)
- Content, in order: a title page with the title, the date and the line "Draft generated by Proposal Agent. The
  researcher is responsible for checking all content.", then Introduction, Aims (with hypotheses and questions),
  Background, Methods ۳-۱ … ۳-۵, References.
- For `fa`: every paragraph and run is right-to-left (`w:bidi`, `w:rtl`). The complex-script font is "B Nazanin"
  with "Vazirmatn" as an alternative, the Latin font is Times New Roman, and text is justified. References are
  left-to-right.
- Section titles come from `designs.yaml` and from a small titles table for the other sections, in both languages.

## 3. Search engineering

- **PubMed E-utilities** (`esearch`, `efetch`) through `httpx`. Always send `tool` (`NCBI_TOOL`), plus `email`
  (`NCBI_EMAIL`) and `api_key` (`NCBI_API_KEY`) when set.
  - Rate limiter: one per process, thread-safe; at most 3 requests/s without a key and 9/s with one, at least 110 ms
    apart, so there are no bursts.
  - Retry 429 and 5xx 3 times with back-off. Redact the key from logs.
  - efetch in POST chunks of ≤ 100 PMIDs.
- **Parse** with `xml.etree`:
  - `PubmedArticle` and `PubmedBookArticle`;
  - labelled abstract sections joined as "LABEL: text";
  - year from `PubDate`, `MedlineDate` or `ArticleDate`;
  - authors as "Lastname Initials" (or `CollectiveName`), journal, volume, issue, pages, DOI, PMCID, publication
    types and MeSH;
  - a retraction flag (publication type "Retracted Publication" or `RetractionIn`).
- **Background lanes**, built in code from the profile (esearch, sort = relevance, 50 per lane):
  1. `core`;
  2. `(core) AND (<design pubmed_filter>)`, when the filter is not empty;
  3. `(core) AND <Y−9>:<Y>[dp]`;
  4. `plain_query`;
  5. `(core) AND <country>[tiab]`, when there is a country.

  If `core` returns an error or 0 results, log it and keep going with the other lanes. If the union has fewer than
  20 records, add `plain_query` with 100 results.
- **Cut**:
  - drop retracted records and abstracts shorter than 200 characters;
  - TF-IDF (scikit-learn, English stop words, sublinear tf, 1–2-grams) over `title + title + abstract`, against a
    query made of the English title, population, exposure, outcomes and concepts;
  - reciprocal-rank fusion: `1/(60 + tfidf_rank) + Σ over lanes 1/(60 + rank in lane)`;
  - keep the top `BACKGROUND_SCREEN_TOP` (default 40).
- **Sentence splitting** must not break on decimals ("p < 0.05", "1.5 mg") or abbreviations ("et al.", "vs.").

## 4. Software

### 4.1 Stack
- Python 3.12 and Django 5.2 LTS, with django-ninja for the JSON API and Pydantic v2.
- MySQL 8.4 (via `DATABASE_URL`; SQLite in tests), using `dj-database-url` and `mysqlclient`.
- The `openai` SDK for every model call, `httpx`, scikit-learn, BeautifulSoup4 (with `lxml`), python-docx, PyYAML
  and gunicorn. Use no other runtime dependency without a clear reason.
- Tooling: pytest, pytest-django and ruff. Pin versions in `requirements.txt` (runtime) and `requirements-dev.txt`.

### 4.2 Layout
```
config/          Django settings (all from env), urls, wsgi
proposals/       Django app: models, admin, api.py (ninja), views + templates + static (web UI),
                 store.py (implements engine.Store), worker.py, management/commands/{run_worker,smoke_live,eval_run}.py
engine/          plain Python, NO Django imports:
                 knowledge.py (load + validate knowledge/), schemas.py (Pydantic), llm.py, pubmed.py, retrieval.py
                 (lanes, cut, select), verify.py (sentences, numbers, digits), background.py, methods.py,
                 samplesize.py, statpearls.py, introduction.py, references.py, docx_export.py, pipeline.py,
                 prompts/*.md (one file per step, named placeholders)
knowledge/       (exists — read only)
evals/           titles.yaml (6 titles, one per design, fa and en)
tests/           unit tests per engine module, fixtures/, API + worker + end-to-end tests
```

### 4.3 LLM client (`engine/llm.py`)
- `complete_json(step, Schema, user, tier="small")` returns a Pydantic object through
  `client.chat.completions.parse`. `complete_text(step, user, tier)` returns a string.
- Providers (`LLM_PROVIDER`):
  - `openai` (default): models `gpt-5.4-mini` and `gpt-5.4-mini`;
  - `gemini`: base URL `https://generativelanguage.googleapis.com/v1beta/openai/`, models `gemini-3.1-flash-lite`
    and `gemini-3.8-flash`;
  - `fake`: deterministic canned outputs, used for tests and for an offline demo.
  - Override with `LLM_BASE_URL`, `LLM_MODEL` and `LLM_MODEL_STRONG`. The key is in `LLM_API_KEY`.
- Current models are reasoning models: do **not** send `temperature` unless `LLM_TEMPERATURE` is set. Send
  `reasoning_effort` from `LLM_REASONING_EFFORT` (default `none`) and `LLM_REASONING_EFFORT_STRONG` (default `low`);
  an empty value means it is not sent.
- Timeout `LLM_TIMEOUT` (90 s). Two retries on 429, 5xx and timeouts; one retry on a refusal or an invalid parse.
  On a 429, wait as long as the provider asks (`Retry-After`, or Gemini's "retry in Ns"), up to 45 s.
- When the strong model times out or is overloaded, the small model writes that step and the user gets a warning.
  A strong model must never stall a job for minutes.
- Warnings shown to users give a short reason ("the provider's rate limit was reached"), never a raw error dump.
  The full error stays in the call log.
  Parallel calls through a thread pool of `LLM_MAX_PARALLEL` (4).
- Every call is reported to the store: step, model, messages, response, tokens, latency, status. The cost is shown
  only if `LLM_PRICE_INPUT_PER_M` and `LLM_PRICE_OUTPUT_PER_M` are set.

### 4.4 Engine ↔ Django
- `engine.pipeline.run(request, store, llm, pubmed, now)` runs the selected sections and reports progress through
  the store (`step_started`, `step_done`, `warn`, `save_paper`, `save_section`, `log_call`, …).
- A failed step marks its section failed with the reason, and the run continues with the other sections. The job
  ends `done`, `done_with_errors` or `failed`, never "done" with an empty section.

### 4.5 Data model
- `Proposal`:
  - identity and request: UUID id, `client_ref`, title, purpose, design input, language, sections, uploaded HTML;
  - job state: status, timestamps, heartbeat, error;
  - results: profile, design decision, result JSON, warnings, usage totals.
- `Paper`: proposal, role (background | methods | intro), token, PMID, DOI, PMCID, title, abstract, journal, year,
  authors, publication types, lanes, scores, screening record, selected, paragraph, flags.
- `Reference`: proposal, number, token, text, PMID, URL.
- `LLMCall` and `StepLog`, as in DESIGN.md.
- Register everything in the Django admin, read-only for the logs.

### 4.6 Worker
- `manage.py run_worker [--once] [--poll 2]` claims the oldest queued proposal inside a transaction with
  `select_for_update(skip_locked=True)`, marks it running, runs the pipeline and updates the heartbeat on every step.
- On start, it re-queues jobs that are running with a heartbeat older than 15 minutes.
- On SIGTERM it finishes the current job, then exits.
- Scale with `docker compose up --scale worker=N`.

### 4.7 API (`/api/v1`, OpenAPI at `/api/docs`)
- Endpoints:
  - `POST /proposals` creates a proposal: 201, and the job is queued;
  - `GET /proposals/{id}` returns the status, the step list, the warnings, and when finished the structured
    sections, references, selected papers and usage;
  - `GET /proposals/{id}/docx`;
  - `GET /proposals?client_ref=` lists proposals;
  - `GET /designs`;
  - `GET /healthz`, which needs no auth and checks the database.
- Auth: `Authorization: Bearer <token>`, checked against `API_TOKENS` (comma-separated, compared in constant time).
  If no tokens are set, the API is open only when `DJANGO_DEBUG=1`; otherwise it returns 403.
- Validate inputs with limits: title 5–300 characters, purpose ≤ 2,000, HTML ≤ 3 MB.

### 4.8 Web UI (Django templates, vanilla JS, no build step)
- `/`: the form, with all inputs.
- `/p/<id>`: live progress (polls every 2 s). When done:
  - one tab per section, rendered RTL for Persian;
  - citations link to the reference list;
  - the background tab lists the selected papers with their scores and PubMed links;
  - the methods tab shows the design decision, the questionnaire answers and the sample-size calculation;
  - a "How this was made" panel shows the queries with hit counts, the papers at each stage, the model calls,
    tokens and cost;
  - a download button for the .docx.
- `/p/`: recent proposals.
- Clean, calm and readable on a phone. Persian text uses the Vazirmatn font. Keep it accessible: labels, focus,
  contrast.
- `FRAME_ANCESTORS` (a space-separated list of origins) sets the CSP `frame-ancestors`, so another platform can embed
  the app. The default is `'self'`.
- Optional `WEB_ACCESS_CODE`: when set, the web UI asks for it once per session.

### 4.9 Configuration
Everything comes from the environment. Commit an `.env.example` that documents every variable with a safe default:
- Django: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` (include the internal hostname other services
  use to reach the API), `CSRF_TRUSTED_ORIGINS`;
- database: `DATABASE_URL` (plus the MySQL variables for compose);
- model: the `LLM_*` variables;
- PubMed: the `NCBI_*` variables;
- app: `API_TOKENS`, `FRAME_ANCESTORS`, `WEB_ACCESS_CODE`, `BACKGROUND_MAX_PAPERS`, `BACKGROUND_SCREEN_TOP`;
- offline demo: `PUBMED_OFFLINE=1` serves PubMed from the test fixtures.

### 4.10 Docker
- A multi-stage `Dockerfile` (build wheels, then a slim runtime): non-root user, `collectstatic` at build time,
  gunicorn.
- `compose.yaml` with three services:
  - `db`: MySQL 8.4, with a health check and a named volume;
  - `web`: runs migrations, then gunicorn on port 8000;
  - `worker`: the same image running `run_worker`.
- `docker compose up` with only `.env` copied from `.env.example` must start a working app in fake mode, so students
  can try it without any keys.

### 4.11 Tests (no network, no keys)
- Unit tests for:
  - knowledge validation (every design has all 5 sections in both languages, and valid formula ids);
  - lanes, TF-IDF cut, RRF, the selection rules and duplicates;
  - sentence splitting, number and digit normalisation, evidence and number verification;
  - every sample-size reference value;
  - reference formatting and token numbering, including merged brackets and unknown tokens;
  - the PubMed XML parser (articles, book articles, retractions, labelled abstracts);
  - the StatPearls HTML parser (a small hand-made fixture);
  - the docx (opens, RTL set, references LTR).
- Integration tests for: the API (auth, validation, create, get, docx); the worker (`--once`); a full pipeline run
  with the fake LLM and offline PubMed producing every section.
- Fixtures: you may download a few real records once from E-utilities (public, no key, respect the rate limit) and
  save them under `tests/fixtures/`.
- `smoke_live --title "…"` runs one real proposal with real keys and writes a JSON report under `var/`. It is never
  run in CI.

### 4.12 Open source
- `README.md` with these parts:
  - what it is and who it is for;
  - a 3-command quick start;
  - the configuration table;
  - how it works (the pipeline, and why each step exists);
  - limitations and responsible use: AI disclosure, checking every claim, source licences;
  - contributing, licences, how to cite, acknowledgements (NCBI E-utilities; the original pipeline).
- `LICENSE` (MIT, "Copyright (c) 2026 the Proposal Agent contributors") covers the code and `knowledge/`.
  `LICENSE-docs` (CC BY 4.0) covers `docs/` and `workshop/`.
- `CONTRIBUTING.md` covers:
  - setup and tests;
  - the branch → pull request flow;
  - how to add a study design or improve a prompt with evidence;
  - a list of good first issues.
- Also add:
  - `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `SECURITY.md`, `CITATION.cff`, `CHANGELOG.md`;
  - `.github/`: issue templates (bug, feature, new study design), a PR template, `dependabot.yml`;
  - workflows: `ci.yml` (ruff + pytest on push and PR, plus a Docker build) and `docker-publish.yml` (push the image
    to `ghcr.io/<owner>/proposal_agent` on `v*` tags).
- `docs/EVALUATION.md` and `manage.py eval_run`: run `evals/titles.yaml` live and write a report with the automatic
  checks. The checks: citations resolve, evidence is verbatim, the ۳-۳ number equals the computed one, aims match
  one to one, and no section is empty. Leave a column for an expert's blind rating.

## 5. How to work
- Build in milestones, in order:
  - M1 skeleton + Docker + CI;
  - M2 engine foundations;
  - M3 background;
  - M4 methods;
  - M5 introduction, references and docx;
  - M6 Django app, worker and API;
  - M7 web UI;
  - M8 open-source files, evals and final checks.
- After each milestone, run ruff and the tests, fix what fails, and make a local commit with a clear message. **Never
  push.**
- No real API keys are available to you. Build and test everything with the fake provider and the fixtures. I will
  run `smoke_live` myself.
- Never commit secrets. `.env` is git-ignored.
- Do not edit the content of `knowledge/`. If something there is unclear or contradictory, write it in `QUESTIONS.md`
  and choose the most conservative reading.
- Write code a medical student can read: small functions, type hints, docstrings that say *why*, and no clever
  metaprogramming.
- Respect NCBI's usage rules. Never scrape websites and never try to get around a bot check.

## 6. Done means
1. `ruff check .` and `pytest` pass, with no network.
2. `cp .env.example .env && docker compose up --build` serves the app at http://localhost:8000. A proposal created in
   fake mode reaches `done`, shows every section and downloads a .docx that opens in Word with correct RTL Persian.
3. All sample-size reference values pass.
4. Every citation in the output resolves to a reference, and every reference is cited.
5. The README quick start works exactly as written.
6. `QUESTIONS.md` lists anything you had to decide without me.
