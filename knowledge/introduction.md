# How to write the introduction

An introduction has four parts, written as one continuous text without sub-headings:

1. **The problem**: what the disease or condition is, how common and how serious it is, and why it matters (about
   400 words). Source: a StatPearls chapter on the topic and the papers it cites.
2. **The possible solution**: the approach the study title points to and what is known about it (about 250 words).
   Same source.
3. **Similar studies**: a short synthesis of what the background studies found (about 250 words). Source: the
   selected background papers, cited with their tokens.
4. **The gap and the rationale**: what is still unknown, why this study is needed, and its possible benefits (about
   150 words).

Rules:
- Parts 1 and 2 use only the source text given (the chapter summary, its section list and the abstracts of the
  chapter's cited papers). Prefer sentences that can be cited, and cite with the tokens given (`{S1}`, `{S2}`, …).
- Part 3 cites the background papers with their tokens (`{P1}`, …) and never adds studies that were not given.
- If the source does not cover something (e.g. there is no "solution" in it), write less rather than inventing.

## Where the StatPearls source comes from (and what we may not do)

StatPearls chapters (NCBI Bookshelf, CC BY-NC-ND 4.0) are indexed in PubMed. The app finds a chapter with the
official E-utilities API (`<terms> AND statpearls[book]`) and uses what the API returns: the chapter summary, its
section titles and its reference list. It then fetches the abstracts of the cited papers that have PMIDs.

The Bookshelf web pages answer automated requests with a bot check, and the StatPearls website does not allow AI
input. So the app never scrapes them and never tries to get around a bot check. A user who wants the full chapter
text can save the page from their own browser and upload the HTML file. The app parses it locally (BeautifulSoup),
keeps the text and the reference list for this proposal only, and attributes the source.
