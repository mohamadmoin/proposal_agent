# From Expertise to Product — workshop outline (75 minutes)

**Audience:** medical students who are curious about technology and AI. Most have never shipped software.
**Promise of the session:** you walk the real path of a real product, step by step, the way its developer did: from a
doctor's know-how, through a repository and a build prompt, to a working open-source tool inside DrNext Academy. You
leave with a map of what to learn next and a project you can contribute to.
**The case:** *Proposal Agent*, an app that writes the پیشینه (literature review), aims, methods and introduction of a
research proposal. Cheap models write it well because an expert's know-how is built into its steps.

**Format:** a workshop that follows the developer's steps. No code runs in class; the "agent builds it" moment is a
short screen recording, and the finished agent runs live inside DrNext Academy.

**Teaching tactics used throughout:**
- assertion-evidence slides: every title is a full-sentence claim; the body is the evidence (real code, real output);
- one idea per slide, with the same colour code all session: teal = a model does it, slate = code does it, amber = a human decides;
- the build prompt is the spine: a "prompt view" slide shows the whole prompt with the current section highlighted,
  and the highlight moves (magic-move transition) as we go back and forth between the prompt and the concepts behind it;
- "lab notebook" slides for every assumption that proved wrong in testing, in four steps revealed one by one:
  *we assumed → the run showed → why it was wrong → the fix*;
- every concept slide ends with a "Learn more" line, with a free video (▶) where a good one exists; slide 65 gathers
  them into a learning path, and `RESOURCES.md` lists everything.

| Time | Station | Slides | What happens |
|---|---|---|---|
| 0–4 | Opening | 1–3 | A real output, and the route we will walk |
| 4–8 | Step 0 · The idea | 4–6 | A painful task, the smallest useful product, your clinical edge |
| 8–12:30 | Step 1 · The repository | 7–10 | Public repo + licence; the first commit is the expertise; design principles |
| 12:30–52:15 | Step 2 · The build prompt | 11–48 | The prompt section by section, each concept behind it, and the mistakes it absorbed |
| 52:15–55 | Step 3 · The agent builds it | 49–51 | Recording of the build, what came out |
| 55–59:30 | Step 4 · Real runs, a clinician's eye | 52–56 | Twelve live-run findings, the pattern, measurement, authorship |
| 59:30–65 | Step 5 · Ship | 57–60 | One engine, two hosts; the live timeline; live demo in DrNext Academy |
| 65–75 | Your turn | 61–66 | Git and GitHub, run it, contribute, the Academy announcement, learning path, questions |

## Slides (titles as on the deck)

### Opening
1. From expertise to product
2. A small model wrote this in under two minutes, for less than 20 US cents
3. We follow the developer's real path, step by step

### Step 0 · The idea
4. Every product starts with a painful, frequent task
5. Version 1 is the smallest thing that is truly useful
6. Your unfair advantage is what you know as a clinician

### Step 1 · The repository
7. *(divider)* Start with a repository
8. Create a public repository with a licence, then clone it
9. The first commit holds no code: it holds the expertise (`knowledge/`)
10. Seven principles decided before any code was written (`docs/DESIGN.md`)

### Step 2 · The build prompt (`workshop/PROMPT.md`)
11. *(divider)* Write the build prompt
12. A specification, not a wish: ten parts, about 400 lines *(prompt view starts)*
13. §0 tells the agent where the medicine lives
14. §1 defines inputs, outputs and the user — like an aim
15. §2.1 turns a Persian title into PICO and a PubMed query
16. **Lab notebook 1:** "Put every concept in the query" hid the most similar study — why "prevalence" left the core
    query (it was never a rule, nor in the 2024 platform's prompts: prompt v1 AND-ed aim words as blocks), and where it
    is still searched
17. §2.2 encodes how an expert picks "strictly similar" studies
18. An expert's background section follows a five-step recipe
19. Each step of the recipe becomes one small job
20. Small jobs let small models do expert work
21. The model can point at sentences, but cannot invent them
22. §2.3 makes the study design drive the whole methods chapter
23. Twelve questions choose the design; the design chooses everything else
24. The model chooses and justifies; Python does the arithmetic
25. **Lab notebook 2:** A prevalence from pregnant women sized a study of students
26. **Lab notebook 3:** A correct calculation asked a student for 10,386 women
27. Aims follow a grammar, so hypotheses and questions always match
28. §2.4 writes the introduction from a textbook chapter — legally
29. Parsing reads pages as data; licences decide what you may take
30. **Lab notebook 4–6:** Three small assumptions that quietly lowered quality (archived chapter, "percent", English in Persian)
31. §2.5–2.6 deliver what a university actually receives
32. §3 is search engineering: many cheap searches, one fair cut
33. PubMed has a free, programmable door: E-utilities
34. TF-IDF ranks text by shared rare words — fast, free, explainable
35. We measured three search designs before choosing one
36. §4 is the engineering: stack, layout, data, API, Docker, tests
37. Python is the glue — and much of the code is medicine
38. An LLM API is a paid function you call over the internet
39. Structured outputs turn the model's answer into data
40. A Pydantic class is a data-extraction form for one paper
41. Django gives a web app, an admin and a database layer in one
42. The database keeps every step, so each sentence can be traced
43. A two-minute job runs beside the request, and streams its progress (worker vs. thread + stream; why no Celery)
44. Docker packages the app so it runs the same everywhere
45. Settings live in the environment, never in the code
46. 151 tests check every step, with no internet and no keys
47. **Lab notebook 7–10:** Four outside-world assumptions no fake could break (strong model down, free-tier limit,
    demo default in real runs, grouped citation tokens)
48. §5–6 tell the agent how to work, and when it is finished

### Step 3 · The agent builds it
49. *(divider)* Let the agent build it
50. An AI coding agent built the app from the prompt in about an hour *(screen recording)*
51. Title in; a cited draft and a Word file out

### Step 4 · Real runs, a clinician's eye
52. *(divider)* Run it for real, and read it like a clinician
53. Real runs found what tests could not, five of them medical (all twelve findings)
54. Every assumption is a hypothesis until a real run tests it
55. You measure quality, you don't assume it
56. The tool drafts. The researcher is the author.

### Step 5 · Ship
57. *(divider)* Ship it where people already are
58. A separate service? The product owner said no. (one engine, two hosts: the open-source app, and the same engine
    inside the Academy's own backend — no new container, database or secret)
59. Two minutes feel short when you can watch the agent think (the live timeline, ideas borrowed from DrNext's chat trace)
60. The same agent, inside DrNext Academy *(live demo)*

### Your turn
61. Git saves the history; GitHub is where people build together
62. Run it on your laptop in three commands, no key needed
63. Help make research tools better: clinicians are the scarce contributors
64. The Academy will open to apps built by you *(early announcement)*
65. Every concept from today, in a free video *(learning path)*
66. Questions, and where to go next *(repo QR)*

Speaker notes and exact timings are in each slide's `<aside>` in `deck/project/slides/`.
