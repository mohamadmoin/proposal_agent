# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-10-29

### Added
- The agentic engine (`PROPOSAL_ENGINE=agentic`, `engine/agentic/`): a lead researcher agent (pydantic-ai) that
  plans, decides and writes with the whole proposal in view through typed tools; cheap worker agents for screening,
  extraction and one-study paragraphs; working memory (plan, decisions, evidence with source sentences, drafts);
  submission checks with rejection reasons returned to the agent; a methodology critic and a claim-support check;
  budgets on requests, tool calls and tokens; Gemini with an OpenAI fallback.
- Diagnostic-accuracy design with Buderer's sample-size formula; design effect in the sample size; variables, ethics
  and timeline in the Word file.
- The command line `./proposal-agent` (`python -m engine.cli`): `check` and `run`, live steps, `--fake` for a keyless
  demo, outputs `proposal.docx`, `result.json` and `trace.json`. It runs the agentic engine by default.

### Fixed
- `jdatetime` was missing from `requirements.txt` (the agentic lead checks study periods in the Persian calendar).

## [0.1.0] - 2026-10-25

### Added
- The engine: study profile, design router (12-question questionnaire), background search lanes with TF-IDF and
  reciprocal-rank fusion, screening with verified evidence sentences and numbers, code selection, one paragraph per
  study, methods evidence and notes, sample size computed by code with a worked calculation, the five methods
  sub-sections, aims with the one-to-one check, the StatPearls-based introduction (PubMed API or a user-saved page),
  Vancouver references numbered in document order, and the Word export with right-to-left Persian.
- Providers: OpenAI, Gemini (OpenAI-compatible endpoint), any OpenAI-compatible URL, and an offline fake model.
- Django app: models with a full trace, admin, a database-polled worker, the JSON API with token auth and OpenAPI
  docs, and the web UI with live progress and a "How this was made" panel.
- Docker (MySQL 8.4, web, worker), CI, the evaluation set and `eval_run`, `smoke_live`, and the open-source files.
