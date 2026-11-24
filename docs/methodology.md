# Methodology

searchbench asks one question: if an agent sends this query to search API X,
does what comes back contain enough grounded evidence to answer correctly, how
fast, and at what cost?

It judges the search results themselves, not an agent's final answer. A
downstream agent adds its own reasoning and failure modes; leaving it out
isolates the search API. Result URLs are never fetched: only the text the API
returns is judged, because that is what an agent sees.

## Task design

The suite `suites/web_v1/tasks.jsonl` has 60 hand-written tasks, 12 in each of
five categories. Each category exists to test a different way search can fail.

- `factual`: stable facts with one authoritative source, such as the default
  PostgreSQL port. This is the floor. A vendor that misses these has a basic
  retrieval or extraction problem.
- `technical_docs`: library and API details an agent needs while coding, such
  as the default timeout in httpx. These live deep in documentation pages and
  are often outranked by blog posts and Q&A threads.
- `fresh`: facts from the 60 days before the suite was written (releases and
  announcements from October and November 2026). They test index freshness and
  are the category where drift is expected, so it is reported separately.
- `multi_hop`: answers that need two facts combined, such as the license of the
  library FastAPI uses for validation. One query must surface evidence for
  both hops, which is harder than either hop alone.
- `long_tail`: niche facts with few sources, such as specific RFC sections or
  small open source projects. They test coverage beyond popular pages.

Authoring rules:

- Every gold answer was checked against a page on one of the task's
  `gold_domains`, and each task records that date in `as_of`. The
  `gold_evidence` field quotes or closely follows the page text.
- A task was dropped when two reasonable answers exist. Examples dropped
  during authoring: the urllib3 default retry count (the `Retry()` default and
  the pool default differ) and the Elasticsearch HTTP port (documented as the
  range 9200-9300).
- No query contains its answer.
- Query styles are mixed: 28 keyword-style and 32 natural-language queries.

The suite was committed on its own before any vendor was queried, so the git
history shows the tasks were not tuned to results. Tasks are never edited
after the first run; corrections go into a new suite. One task (`web_v1_045`)
went in without its gold page fetched, which is noted here rather than
changed.

## Running vendors

Every vendor gets the identical query string and the same parameters: k = 5
results, and result text cut to 2,000 characters after whitespace is
collapsed. Each vendor runs in its default mode unless that mode returns no
text. Exa returns no text by default, so it is asked for query-relevant
highlights, the closest match to the snippets other vendors return by default.
The per-vendor choices, doc links and prices are in `docs/vendors.md`.

Calls are paced per vendor with a concurrency limit and a minimum gap between
calls. 408, 429, 5xx and timeouts are retried with exponential backoff and
jitter, up to 3 attempts, honouring `Retry-After`. A call that still fails is
stored as an error result and counts toward the error rate; it is never
dropped. Every response is stored with its raw JSON, keyed by vendor, query
and parameters, so an interrupted run resumes without paying twice. A spend
limit stops new calls once the estimated list-price spend reaches it.

## Judging

One judgment is made per (task, vendor) pair: do the vendor's top 5 results
contain evidence that supports the gold answer?

The judge prompt contains the query, the gold answer, the gold evidence and
the 5 results as title and text. URLs and vendor names are left out, so the
judge is blind to the vendor (some result URLs name the vendor). The model
returns structured JSON: `supported` (yes, partial or no), the number of the
supporting result, and a quote copied from it.

Grounding check: a yes or partial verdict counts only if the quote appears
verbatim in the title or text of the result it cites. Otherwise it is scored as
no and counted as a judge hallucination. The first version of the check looked
only at the text; titles were added after the first snapshot was judged and
before any report was written, because the judge is shown titles and so is an
agent. Grounding is computed when the report is built, so no judgment changed.

A quote matches if it is in the raw text, or if its visible form is in the
visible form of the text: markdown links and images reduced to their link text,
whitespace collapsed. Judges quote either form. Before this, a judge quoting
"created by Linus Torvalds in 1991" from
`created by [Linus Torvalds](https://...) in 1991` was scored as a
hallucination. This was found while reviewing round 2 label disagreements, so
round 2 is not fully held out from this fix. It turned 4 of the 6 primary
hallucinations into supported verdicts, all on Firecrawl, whose output is
markdown. Quotes with changed punctuation or garbled characters still fail.

The primary judge is `gemini-3.1-pro-preview` on Vertex AI, the strongest Pro
model available to the project. Every call sends seed 1 and temperature 0.
This model is a thinking model, and in testing its reruns kept the same
verdict but sometimes quoted a different span. Judgments are therefore cached,
keyed by model, seed, temperature and prompt, committed with the snapshot, and
the report is built from that cache only.

The judge is validated, not trusted:

- Human labels: the author labels pairs with the same blind view the judge had
  (`bench label`), in rounds. Round 1 was 40 pairs, 8 per category; the
  labelling screen did not show the judge's rubric, and 37 of the 40 labels
  were yes. On review, 4 of the primary judge's 6 disagreements were
  multi-hop pairs where the label relied on knowledge outside the results,
  which the rubric forbids. Round 1 labels are kept as recorded. Later rounds
  (`bench label --round 2`) show the rubric, are stratified by category and by
  the primary judge's verdict so that no and partial pairs are labelled too,
  and skip pairs labelled in other rounds. The rubric was not changed to fit
  round 1, so a later round is a held-out check. The report gives Cohen's
  kappa and raw agreement per round, and puts a warning at the top when the
  latest round's kappa is below 0.6.
- Second judge: `gemini-3.5-flash-lite` judges every pair, and the report gives
  agreement between the two judges. It was also the weaker judge: it often
  copied the gold evidence from the prompt as its quote instead of quoting a
  result, which the grounding check catches.
- Order bias: the primary judge re-judges a seeded sample of 30 pairs (each
  with at least two results) with the results in reverse order. The report
  gives how many verdicts changed.

## Metrics

Per vendor, overall and per category:

- `support@5`: yes = 1, partial = 0.5, no = 0, from the primary judge after
  the grounding check. This is the primary metric.
- `domain_hit@5`: whether any result URL is on a gold domain or one of its
  subdomains.
- Cited rank: the position of the result the judge quoted, averaged over
  supported pairs. It is not always the first supporting result.
- Latency p50 and p95 over successful calls, cost per query at list price, and
  error rate.

95% confidence intervals come from a percentile bootstrap over tasks with
10,000 resamples and a fixed seed. Vendors are compared with a paired bootstrap
on per-task differences, and a difference is called significant only when its
interval excludes 0. With 60 tasks many differences cannot be resolved, and
the report says so for each pair.

## Drift

Each `bench run` writes a new snapshot named by UTC date and git commit, and
old snapshots stay committed. `bench drift A B` compares support@5 per vendor
on the tasks both snapshots share, using the same paired bootstrap, and lists
tasks that flipped between yes and no. The `fresh` category is reported
separately, because a change there may be the world changing (a new release, an
edited page) rather than the vendor.

## Independence and validity

- No vendor-specific query rewriting; every vendor gets identical parameters.
- The judge is blind to vendor identity.
- The suite was committed before the first run.
- Every raw response and judgment is committed. No API key appears in them.

Threats to validity:

- 60 tasks is small. Most differences between vendors are within noise.
- One judge family: Gemini judges results that may include Google-indexed
  content.
- Judge agreement with the human labels is low by kappa (0.39 on round 2, see
  the report). The judge is stricter than the author: most disagreements are
  the author saying yes where the judge said partial or no. Treat support@5
  differences as provisional.
- Gold answers can go stale; `as_of` records when each was checked.
- Free tiers may differ from paid tiers in ranking, freshness or latency.
- Content is cut to 2,000 characters and each vendor is judged on its default
  output. Vendors that return short snippets give the judge less to quote.
- Results are one snapshot in time.
