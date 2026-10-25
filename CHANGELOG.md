# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
