# Evaluation

How we know whether Proposal Agent writes good drafts, and how to tell whether a change made it better.

## 1. The title set

`evals/titles.yaml` holds one title per supported design (cross-sectional, case-control, cohort, randomised trial,
quasi-experimental, systematic review), each in Persian and English, with a short purpose and the **expected design**.
By default the design is not given to the app, so the design router is evaluated too (`--give-design` passes it).

## 2. Running it

```bash
# real provider and PubMed: set LLM_PROVIDER, LLM_API_KEY, PUBMED_OFFLINE=0, NCBI_EMAIL in the environment
python manage.py eval_run --language both            # all 6 titles × 2 languages
python manage.py eval_run --only cohort --language fa
python manage.py eval_run --allow-fake               # dry run with the fake model (checks the harness only)
```

Each run writes `var/eval/eval_<timestamp>.md` (the table below plus details) and `.json` (every result, warning and
check). It does not touch the database. A full live run costs a few US cents per title.

## 3. Automatic checks

| Check | Passes when | Why it matters |
|---|---|---|
| `citations_resolve` | every `[n]` in the document has a reference, every reference is cited, and no raw token (`{P3}`) is left | a proposal with a dangling or invented citation is unusable |
| `evidence_verbatim` | for every selected background study, the evidence sentence numbers exist, the stored evidence text is exactly those abstract sentences, and every number of the main finding is in the abstract | the background must say only what the cited abstract says |
| `sample_size_matches` | the final sample size computed by code is stated in ۳-۳, and recomputing it from the stored parameters gives the same number | the arithmetic is code's job; the text must not drift from it |
| `aims_one_to_one` | as many hypotheses (including "not applicable") and questions as secondary aims | the aims grammar requires a 1:1 match |
| `no_empty_section` | every requested section is written (status `done`) | the app must fail honestly, never return an empty section |

The report also shows whether the router chose the expected design, the number of references, tokens, cost (when
prices are configured) and time.

## 4. Expert blind rating

The automatic checks cannot judge relevance, accuracy of summaries or methodological soundness. For that, a
methodologist rates a sample **blind** (without knowing the model, the prompt version or whether a draft came before or
after a change). The report leaves an empty column, **Expert rating (1–5, blind)**. Suggested rubric, each 1–5:

1. **Background relevance**: are the selected studies strictly similar (aim, design, population)? Any important study
   missing?
2. **Faithfulness**: does each paragraph say only what its abstract says, with numbers as written?
3. **Methods soundness**: are the design, sampling, instruments and analysis right for the study? Are the sample-size
   parameters sensible and their sources honest?
4. **Introduction**: does it state the problem, the possible solution, similar studies and the gap, using the sources?
5. **Usability**: how much editing would a student need before submitting it to a research committee?

Give the rater the drafts in a random order, with model and version information removed; record the ratings in the
JSON report (`expert_rating`) or a spreadsheet keyed by `id` and `language`.

## 5. Comparing versions

To evaluate a change (prompt, rule, model): run the full set before and after with the same provider settings, keep
both reports, and compare check pass rates, warnings ("numbers unverified", "only k strictly similar studies") and
blind ratings. A change should not make any automatic check worse. Do not put evaluation titles into prompts.
