# Contributing to Proposal Agent

Thank you for helping. Two kinds of contribution matter equally here: **code** (engineers) and **domain knowledge**
(clinicians, epidemiologists, methodologists). Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Setup and tests

```bash
git clone https://github.com/<owner>/proposal_agent.git && cd proposal_agent
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .          # lint
ruff format .         # format
pytest                # the whole suite: no network, no API keys
```

`mysqlclient` needs the MySQL client headers (`apt install default-libmysqlclient-dev pkg-config`, or
`brew install mysql-client pkg-config` on macOS). The tests use SQLite, the fake model and saved PubMed records, so you
can also install everything except `mysqlclient` to run them.

Run the app locally without Docker:

```bash
export DJANGO_DEBUG=1 LLM_PROVIDER=fake PUBMED_OFFLINE=1
python manage.py migrate
python manage.py runserver          # terminal 1: http://localhost:8000
python manage.py run_worker         # terminal 2
```

or with Docker: `cp .env.example .env && docker compose up --build`.

Where things live: `engine/` is plain Python (no Django) and holds the pipeline; `engine/prompts/` holds one prompt
file per step; `knowledge/` holds the clinician's rules; `proposals/` is the Django app (models, API, web pages,
worker); `tests/` mirrors the engine modules. Read [`docs/DESIGN.md`](docs/DESIGN.md) first.

## The branch → pull request flow

1. Open an issue (or comment on one) to say what you will do, so work is not duplicated.
2. Fork the repository and create a branch from `main`: `git checkout -b fix/short-description`.
3. Make small commits with clear messages. Add or update tests for every behaviour change.
4. Run `ruff check .` and `pytest` before pushing.
5. Open a pull request and fill in the template. CI runs ruff, the tests and a Docker build.
6. A maintainer reviews. Changes to `knowledge/` are also reviewed by a clinician or methodologist.

## Adding a study design

Designs live in `knowledge/designs.yaml`. To add one (for example *diagnostic accuracy*):

1. Copy an existing block under `designs:` and give it a new label (lower case, underscores).
2. Fill in `name` (fa and en), `pubmed_filter` (a PubMed filter that finds studies of this design), exactly five
   `search_topics` (methodological aspects worth searching), and `sample_size` (formula ids from
   `knowledge/sample_size.md`; add a new formula there and in `engine/samplesize.py`, with reference values and a
   test, if none fits).
3. Write the five `sections` instructions in Persian and English, following the reporting guideline of the design
   (e.g. STARD for diagnostic accuracy).
4. Run `pytest`: the knowledge tests check that every design has all five sections in both languages and only known
   formulas. Add a title for the new design to `evals/titles.yaml`.
5. In the pull request, cite the guideline or textbook your instructions follow.

The issue template "New study design" lists the same items.

## Improving a prompt or a rule — with evidence

Prompts (`engine/prompts/*.md`) and rules (`knowledge/*`) change what students receive, so a change needs evidence:

1. Describe the failure with real examples: the title, the step, what the app wrote, and what it should have written.
   The "How this was made" panel and the `LLMCall` table in the admin show exactly what each step received and
   returned.
2. Make the smallest change that fixes the class of problem, not just the one example (do not paste evaluation titles
   into prompts).
3. Show before/after on the evaluation set: run `python manage.py eval_run --language both` (real keys needed) before
   and after, and attach both reports. The automatic checks must not get worse; say what improved.
4. Keep content out of Python: rules belong in `knowledge/`, wording in `engine/prompts/`.

## Good first issues

- Self-host the Vazirmatn font instead of loading it from Google Fonts.
- Add a Persian translation of the web UI's labels (the generated text is already Persian).
- Show the background studies' screening records (aim, finding, evidence sentences) in the web UI.
- Add a "copy section" button to each tab.
- Export the reference list as RIS or BibTeX.
- Add the STARD-based *diagnostic accuracy* design (see "Adding a study design").
- Add a `--formula` option to a small CLI that prints a worked sample-size calculation.
- Document how to run the app behind nginx with HTTPS (needed for embedding with `FRAME_ANCESTORS`).
