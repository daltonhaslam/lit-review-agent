# Search Log

_This is an illustrative demo review — the counts below are small placeholder numbers chosen to
be internally consistent with `state.json`'s `prisma_counts`, not a real search record._

## Database Search — 2026-06-24

- Databases searched: PubMed, OpenAlex, Europe PMC (see `protocol.md` §4–5 for the full query
  strategy and `state.json` for the stepwise PubMed query history)
- Records identified: 120
- Duplicate records removed: 25
- Records screened (title/abstract): 95
- Records excluded at screening: 81
- Reports sought for full-text retrieval: 15 (14 from database screening below, plus 1 merged
  in from the surviving citation-chased candidate — see Citation Chasing section)
- Reports assessed for eligibility: 15
- Articles excluded at full-text: 9
- **Included from database searching: 5**

## Citation Chasing — 2026-06-25

- Backward (CrossRef references) + forward (Semantic Scholar citations) candidates identified: 15
- Screened after deduplication: 10
- Excluded at citation-chase screening: 9
- 1 surviving candidate merges into full-text assessment above (eligibility/assessed = 15)
- **New includes from citation chasing: 1**
- PRISMA `citation_chasing_added`: 1

## Total Included: 6

_Note: `retrieval_list.csv` in this folder lists only the 6 included articles' download status,
not all 15 articles sought for full-text — per-article artifacts are trimmed to the included set
for brevity in this demo (see `protocol.md`)._
