# PubMed fixtures

Real records saved once (2026-10-25) from the public NCBI E-utilities API (no key, under 2 requests/s). They feed
the parser tests, the offline demo (`PUBMED_OFFLINE=1`) and the end-to-end test. Article reference lists were
stripped to keep the files small; book chapters keep theirs (the introduction uses them).

| File | Query / source |
|---|---|
| `articles.xml` | 60 records of `("Vitamin D Deficiency"[mh] OR "vitamin D deficiency"[tiab]) AND ("Pregnancy"[mh] OR pregnant[tiab]) AND (prevalence[tiab] OR "Cross-Sectional Studies"[mh])`, sort = relevance |
| `statpearls_books.xml` | `vitamin D deficiency AND statpearls[book]` (5 StatPearls chapters, `PubmedBookArticle`) |
| `statpearls_cited.xml` | the PubMed-indexed references of the chapter "Vitamin D Deficiency" (NBK532266) |
| `retracted.xml` | one record of `vitamin D AND "Retracted Publication"[pt]` |

PubMed metadata and abstracts are provided by the U.S. National Library of Medicine; abstracts remain under their
publishers' copyright and are included here only as test data.
