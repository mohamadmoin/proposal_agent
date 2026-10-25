# From Expertise to Product — workshop outline (75 minutes)

**Audience:** medical students who are curious about technology and AI. Most have never shipped software.
**Promise of the session:** you will see every step a real product takes, from a doctor's know-how to a working,
open-source tool, and leave with a map of what to learn next and a project you can contribute to.
**The case:** *Proposal Agent*, an app that writes the پیشینه (literature review), aims and methods of a research
proposal. Cheap models write it well because an expert's know-how is built into its steps.

**Format:** slides, no code run in class. The "an agent builds it from the prompt" moment is a short screen recording.
The finished agent runs live inside DrNext Academy at the end.
**Deck style:**
- assertion-evidence: each slide title is a full-sentence claim, and the body is the evidence (a diagram, real code, a real output);
- one idea per slide, minimal text;
- the same visual code throughout: teal = model step, slate = code step, amber = you decide;
- every concept slide ends with 1–2 "learn more" links, and all links are also on the final resources slide and in `workshop/RESOURCES.md`.

| Time | Part | Slides | Purpose |
|---|---|---|---|
| 0–5 | Opening | 1–3 | Hook, the case, agenda |
| 5–12 | The product mindset | 4–7 | Problem → user → smallest useful product; your edge is domain knowledge |
| 12–25 | Expertise → pipeline | 8–14 | How an expert writes a پیشینه and a methods section, turned into steps a cheap model can do |
| 25–45 | The stack, one concept at a time | 15–27 | Just enough of each tool to know why it is there |
| 45–58 | The prompt, broken down | 28–33 | The build prompt line by line: which lines are medicine, which are engineering |
| 58–63 | Quality and responsibility | 34–36 | Evaluation, citations, authorship, licences |
| 63–69 | GitHub and open source | 37–39 | How to run it, read it, and contribute |
| 69–75 | DrNext Academy | 40–42 | Live demo, early announcement, Q&A |

## Slides

### Opening (0–5)
1. **Title.** "From expertise to product: building a research-proposal agent." Presenter, date, repo QR.
2. **A cheap model wrote this proposal section in two minutes, for a few cents.** Real output: a Persian پیشینه paragraph with [n] citations, plus the reference list. The question: how?
3. **Today: one real product, every step.** Agenda as a 7-step path. That path recurs as a progress bar on later slides.

### The product mindset (5–12)
4. **Every product starts with a painful, frequent task.** Writing a proposal: finding similar studies, choosing a design, sample size, the university form. Who suffers, how often, what it costs.
5. **The first version should be the smallest thing that is truly useful.** MVP: background + aims + methods, not a whole thesis. *Learn more:* YC Startup School; "The Mom Test" (customer interviews).
6. **Your unfair advantage is what you know as a clinician.** Generic chatbots write generic proposals. The details an expert knows (what "similar study" means, which design answers which question) are the product.
7. **The plan: decide by evidence, build in small verified steps.** Simple loop: idea → spec → build → measure → improve.

### Expertise → pipeline (12–25)
8. **An expert's background section has a recipe.** Search widely → screen → keep strictly similar recent studies → summarise each the same way → cite precisely.
9. **Each step of the recipe becomes one small job.** Pipeline diagram: phrases → PubMed → TF-IDF screen → rank by title → one paper per call → code adds citations.
10. **Small jobs let small models do expert work.** Why it works: one job per call, short inputs, structured outputs, near-zero temperature. *Learn more:* OpenAI prompt-engineering guide; Anthropic prompt-engineering overview.
11. **Facts that must be exact are owned by code, not by the model.** Citation numbers, references and sample-size arithmetic come from code. The model never invents a reference.
12. **The methods section is decided by the study design.** A 12-question design questionnaire leads to one of six design labels, which picks the design-specific search topics and writing instructions (STROBE/CONSORT/PRISMA in disguise). *Learn more:* EQUATOR Network.
13. **Aims follow a grammar.** Fixed Persian sentence frames per variable; hypotheses and questions are transformations of each aim, so they always match.
14. **The whole flow on one page.** End-to-end diagram: title → design → literature → per-paper notes → sample size → sections → Word file.

### The stack, one concept at a time (25–45)
Each slide has a one-sentence definition, the role in this app, and 1–2 links. About 90 seconds each.

15. **Python is the glue.** Tutorial, virtual environments.
16. **An LLM API is a function you call over the internet.** Request/response, tokens, temperature, cost.
17. **Structured outputs turn text into data.** A Pydantic model as the contract; the app never parses free text.
18. **Pydantic checks every piece of data at the border.** Types, validation, schemas.
19. **PubMed has a free programmable door: E-utilities.** ESearch → EFetch, API key, the 3 or 10 requests/second rule, short queries.
20. **TF-IDF is a fast, free way to rank text by overlap.** The idea in one picture, and the 0.3 threshold.
21. **HTML parsing reads web pages as data, and licences decide what you may keep.** BeautifulSoup; StatPearls and NCBI Bookshelf terms (CC BY-NC-ND).
22. **Django gives you a web app, an admin and a database layer in one.** Models, views, admin, migrations.
23. **MySQL stores jobs and every intermediate result, so each sentence is traceable.** Tables for proposal, papers, notes and sections.
24. **Long jobs run in the background.** A worker process picks queued jobs from the database (`SELECT … FOR UPDATE SKIP LOCKED`); the page polls for progress. Celery + Redis is the usual next step when a project grows.
25. **Docker packages the app so it runs the same everywhere.** Image, container, Compose: one command starts everything.
26. **Configuration lives in the environment, never in the code.** `.env`, secrets, 12-factor.
27. **Tests prove each step still works.** pytest; recorded API responses, so tests need no keys.

### The prompt, broken down (45–58)
28. **The build prompt is a specification, not a wish.** Its structure: goal → users → stack → steps → rules → acceptance checks.
29. **These lines are medicine.** Highlighted excerpts: "strictly similar in aims and design", "newer is better", "5–10 papers", the 10-item per-paper checklist, the design questionnaire, the ۳-۱ … ۳-۵ form, the aims frames, "write narratively, not as bullet points".
30. **These lines are search engineering.** 12 phrases of at most 6 words, 200 results each, TF-IDF > 0.3 on titles, dedupe by PMID, rank only titles plus dates.
31. **These lines are software engineering.** Stack, repo layout, Pydantic schemas, Docker Compose, env vars, tests, "work in small verified steps".
32. **An AI coding agent built it from the prompt in about an hour.** Screen recording (time-lapse): eight milestones, 143 tests, a commit after each, and a `QUESTIONS.md` of 22 decisions it wants a clinician to confirm.
33. **What came out.** Repo tree and the running app (form → live progress → sections → Word file).

### Quality and responsibility (58–63)
34. **Real runs found what tests could not: four of nine problems were medical judgement.** The table from `PROMPT_ANNOTATED.md` (query hid the closest studies, prevalence from the wrong population, archived chapter, 10,386-person sample) and how each became a prompt line.
35. **You measure quality, you don't assume it.** A small evaluation set across designs, a rubric (reporting items, traceable numbers, correct sample size, valid references), and an expert read.
36. **The tool drafts; the researcher is the author.** University rules on AI disclosure, plagiarism, checking every claim; licences of sources.

### GitHub and open source (63–69)
37. **Git saves history; GitHub is where code lives together.** Commit, branch, pull request, issue. *Learn more:* Pro Git; GitHub Skills.
38. **Run it in three commands.** `git clone` → copy `.env.example` → `docker compose up`.
39. **Help make research tools better.** How to contribute (CONTRIBUTING.md, good-first-issues): new study designs, other sections, better evaluation sets, translations.

### DrNext Academy (69–75)
40. **The same agent, inside DrNext Academy.** Live demo: open it from the Academy home and generate a section; then ask Fandogh, which calls it as a tool.
41. **The Academy will be open to apps built by you.** Early announcement: a draft app standard exists; the developer programme comes after the beta. Build something useful and talk to us.
42. **Questions, and where to go next.** Resources slide (all links), repo QR, contact.

**Backup slides** (not presented): cost and latency table, data model, full pipeline with call counts, sample-size formulas.
