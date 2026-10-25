# Proposal Agent

**Proposal Agent drafts the sections of a medical research proposal from a study title.** A researcher types a title
(Persian or English); the app searches PubMed for strictly similar studies and drafts:

- the **introduction** (the problem, the possible solution, similar studies, the gap);
- the **aims, hypotheses and research questions**, matched one to one;
- the **background (پیشینه)**: one paragraph per strictly similar study;
- the **methods chapter (۳-۱ … ۳-۵)**, with the sample size computed by code;
- one numbered **reference list** (Vancouver), where every citation points to a real PubMed record;
- a **Word (.docx)** file of all of it, right-to-left for Persian.

It keeps a full trace: every search, paper, score, model call, token and warning.

**Who it is for:** medical students and researchers who write proposals, especially in Iranian universities (the
methods skeleton follows the Iranian proposal form), and teachers of research methods. **The output is a draft. The
researcher is the author** and must check every sentence, number and reference.

## Quick start

You need [Docker](https://docs.docker.com/get-docker/) with Compose. No API key is needed for the demo.

```bash
git clone https://github.com/<owner>/proposal_agent.git && cd proposal_agent
cp .env.example .env
docker compose up --build
```

Open <http://localhost:8000> (if port 8000 is busy, set `WEB_PORT=8077` in `.env` and open
<http://localhost:8077>). The defaults run in **fake mode**: a built-in, rule-based model and PubMed records saved in
`tests/fixtures/`, so the pipeline runs end to end without keys or network. Fake-mode text is placeholder text; try
the title `شیوع کمبود ویتامین D در زنان باردار مراجعه‌کننده به مراکز بهداشتی تهران`, which matches the saved records.

For real proposals, edit `.env`: set `LLM_PROVIDER=openai` (or `gemini`), `LLM_API_KEY=…`, `PUBMED_OFFLINE=0`,
`NCBI_EMAIL=…` (and ideally `NCBI_API_KEY`), a real `DJANGO_SECRET_KEY` and `API_TOKENS`, then
`docker compose up -d`. More workers: `docker compose up -d --scale worker=3`.

## Configuration

Everything is set through environment variables (`.env`, documented in `.env.example`).

| Variable | Default | What it does |
|---|---|---|
| `DJANGO_SECRET_KEY` | demo value in `.env.example` | Django's signing key. Required when `DJANGO_DEBUG=0`; use a long random string. |
| `DJANGO_DEBUG` | `0` | `1` shows detailed error pages (development only) and opens the API when no tokens are set. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Host names the app answers to. |
| `CSRF_TRUSTED_ORIGINS` | empty | Full origins allowed to post forms through a proxy or another domain. |
| `DJANGO_BEHIND_HTTPS_PROXY` | `0` | `1` when a proxy terminates HTTPS and sends `X-Forwarded-Proto`. |
| `LOG_LEVEL` | `INFO` | Logging level. |
| `DATABASE_URL` | SQLite in `var/` | e.g. `mysql://user:pass@db:3306/proposal_agent`. Compose uses MySQL 8.4. |
| `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD` | demo values | Used by the compose `db` service; must match `DATABASE_URL`. |
| `WEB_PORT` | `8000` | Host port of the web app in compose. |
| `LLM_PROVIDER` | `openai` in code, `fake` in `.env.example` | `openai`, `gemini` (OpenAI-compatible endpoint) or `fake` (offline). |
| `LLM_API_KEY` | empty | The provider's key. |
| `LLM_BASE_URL` | provider default | Any OpenAI-compatible endpoint. |
| `LLM_MODEL`, `LLM_MODEL_STRONG` | `gpt-5.4-mini` / `gpt-5.4-mini` (openai); `gemini-3.1-flash-lite` / `gemini-3.8-flash` (gemini) | The small model (most steps) and the strong model (design router, introduction parts 1–2). |
| `LLM_TEMPERATURE` | empty (not sent) | Reasoning models reject a temperature; set it only for models that accept one. |
| `LLM_REASONING_EFFORT`, `LLM_REASONING_EFFORT_STRONG` | `none` / `low` | `reasoning_effort` for each tier; an empty value means it is not sent. |
| `LLM_TIMEOUT` | `90` | Seconds per model call. |
| `LLM_MAX_PARALLEL` | `4` | Parallel model calls (screening batches, paragraphs, sub-sections). |
| `LLM_PRICE_INPUT_PER_M`, `LLM_PRICE_OUTPUT_PER_M` | empty | US$ per million tokens; when both are set, the cost is shown. |
| `NCBI_TOOL` | `proposal_agent` | Tool name sent to NCBI E-utilities. |
| `NCBI_EMAIL` | empty | Your e-mail, sent to NCBI as its usage rules ask. |
| `NCBI_API_KEY` | empty | Raises the rate limit from 3 to 9 requests per second. Redacted from logs. |
| `PUBMED_OFFLINE` | `0` in code, `1` in `.env.example` | `1` serves PubMed from `tests/fixtures/pubmed/`. |
| `API_TOKENS` | empty | Comma-separated bearer tokens for the JSON API. Empty means the API answers 403 unless `DJANGO_DEBUG=1`. |
| `FRAME_ANCESTORS` | `'self'` | Space-separated origins allowed to embed the pages (CSP `frame-ancestors`). Other origins need HTTPS. |
| `WEB_ACCESS_CODE` | empty | When set, the web UI asks for it once per session. |
| `BACKGROUND_MAX_PAPERS` | `8` | Studies kept in the background (5–10). |
| `BACKGROUND_SCREEN_TOP` | `40` | Records screened by the model after the cut. |

## How it works

The design principles are in [`docs/DESIGN.md`](docs/DESIGN.md); the clinician's rules are plain text and YAML in
[`knowledge/`](knowledge/README.md), read at run time. In short: **one small job per model call, structured outputs,
code owns the facts, and code verifies what the model says.**

1. **Study profile** (1 small call). English title, PICO parts, a design guess, a PubMed Boolean query, plain words
   and StatPearls terms. *Why:* every later search is built from it in code.
2. **Design.** Your choice, or the **design router** (strong model) answers the clinician's 12 design questions
   (`knowledge/questionnaire.md`) and names a design; unknown designs use a general template. *Why:* the design
   decides the search filters, the methods topics, the allowed sample-size formulas and every methods instruction.
3. **Background search.** Up to five PubMed lanes built in code (core query; core + design filter; core + last 10
   years; plain words; core + country), merged, retracted and abstract-less records dropped, then a **TF-IDF score
   fused with the lane ranks** (reciprocal-rank fusion) keeps the best 40. *Why:* one query misses differently
   phrased studies; ranking titles and abstracts beats ranking titles alone.
4. **Screening** (small model, 5 papers per call, in parallel). Aim, population and design similarity, sample size,
   country, aim, main finding and the **numbers of the abstract sentences** that support it. **Code then checks**
   that the sentences exist and that every number of the finding is in the abstract (Persian digits, "·", ".5" and
   percentages normalised); a failed check caps the score or replaces the finding by the evidence sentences.
5. **Selection** (code). Strictly similar = aim ≥ 2 and (same design or population ≥ 2); score = 3·aim + population
   + 2·design + recency; duplicates merged; 5–10 kept. Fewer than 5? You are told, and only "partly similar" studies
   with a similar aim are added — never loosely related ones.
6. **Background paragraphs** (one call per study, in parallel), from that study's abstract only, following the
   9-item recipe; numbers are checked (one retry, then flagged).
7. **Methods.** Five methodological topics per design become short PubMed searches and 100-word summaries; one call
   answers the a–h checklist (`knowledge/methods_checklist.md`) from the best background studies; the model picks a
   sample-size formula and its parameters, each with a source, and **code computes** n, rounds up, inflates for
   non-response and writes the worked calculation; five calls write ۳-۱ … ۳-۵, and code checks that ۳-۳ states the
   computed number.
8. **Aims** follow `knowledge/aims_grammar.md`; code checks that aims, hypotheses and questions match one to one.
9. **Introduction.** A StatPearls chapter is found **through the official PubMed API only** (summary, section titles,
   reference list), plus up to 8 abstracts of the papers it cites; or you upload a page you saved in your browser.
   Parts 1–2 use only those sources (strong model); parts 3–4 use the selected background studies.
10. **References** (code). Tokens such as `{P3}` become numbers in order of first appearance (Introduction → Aims →
    Background → Methods), adjacent citations share a bracket, unknown tokens are removed and logged, and the list is
    formatted in Vancouver style. Every reference is cited and every citation has a reference.
11. **Word export** with python-docx: right-to-left Persian (B Nazanin, Vazirmatn as alternative), left-to-right
    references.

A typical run makes about 30–37 model calls, about 90% on the small model.

**Architecture:** Django 5.2 (web pages + django-ninja JSON API) → MySQL ← a database-polled worker
(`manage.py run_worker`, `SELECT … FOR UPDATE SKIP LOCKED`) → `engine/` (plain Python, no Django). The API is
documented at `/api/docs` (create, poll, download .docx, list by `client_ref`, designs, health check).

```bash
curl -X POST http://localhost:8000/api/v1/proposals -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" -d '{"title": "…", "language": "fa"}'
```

## Limitations and responsible use

- **AI disclosure.** The text is drafted by a language model. Say so where your university or journal asks, and
  follow its rules on AI assistance. The Word file's title page states that it is a generated draft.
- **Check every claim.** The code checks that cited sentences exist and that numbers appear in their sources, but it
  cannot check that a sentence is a fair summary, that a study is the right one, or that a methods choice suits your
  setting. Read every cited abstract. Sample-size parameters marked "assumption" must be confirmed by you.
- **Coverage.** Only PubMed is searched; studies outside it (including many Persian-language studies) are missed.
  Fewer than 5 similar studies is reported, not hidden.
- **Source licences.** PubMed abstracts remain under their publishers' copyright. StatPearls chapters are CC BY-NC-ND
  4.0: the app uses only what the PubMed API returns, never scrapes NCBI Bookshelf or statpearls.com (they answer
  scripts with a bot check, and StatPearls does not allow AI input), and an uploaded page is used only for that
  proposal. Respect NCBI's E-utilities usage policy (identify yourself, stay within the rate limits).
- **Privacy.** Titles, purposes and outputs are stored in your database and sent to the model provider you choose.
  Do not enter patient data.
- **Fonts.** The web UI loads Vazirmatn from Google Fonts; self-host it if that matters for your users.

## Contributing

Contributions from developers **and methodologists** are welcome: see [`CONTRIBUTING.md`](CONTRIBUTING.md) (setup,
tests, how to add a study design or improve a prompt with evidence, good first issues) and the
[Code of Conduct](CODE_OF_CONDUCT.md). Security issues: [`SECURITY.md`](SECURITY.md). Evaluation:
[`docs/EVALUATION.md`](docs/EVALUATION.md).

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt   # mysqlclient needs MySQL client headers; tests use SQLite
ruff check . && pytest                # no network, no keys
```

## Licences

- Code and `knowledge/`: [MIT](LICENSE), © 2026 the Proposal Agent contributors.
- `docs/` and `workshop/`: [CC BY 4.0](LICENSE-docs).

## How to cite

See [`CITATION.cff`](CITATION.cff) (GitHub shows a "Cite this repository" button). For example:
*Proposal Agent contributors. Proposal Agent: drafting research-proposal sections from PubMed evidence [software].
Version 0.1.0; 2026.*

## Acknowledgements

- The **NCBI E-utilities** and PubMed, from the U.S. National Library of Medicine, which make the literature
  searchable by software.
- **StatPearls** (StatPearls Publishing, NCBI Bookshelf) for chapters indexed in PubMed.
- The **original pipeline**: a clinician's tool used by students for real proposals in 2024–2025, whose expertise is
  distilled in `knowledge/`; and DrNext's fast PubMed retrieval pipeline, which inspired the search design.
