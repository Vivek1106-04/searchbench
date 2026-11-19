# Vendors

Every vendor gets the identical query string, k = 5 results, and result content
cut to 2,000 characters after whitespace normalization. Each vendor runs in its
default search mode unless that mode returns no content text.

Cost per call is computed from the list price below, not from the free tier, so
that vendors are compared at what an agent would actually pay.

## Tavily

- Docs read 2026-11-19:
  - https://docs.tavily.com/documentation/api-reference/endpoint/search
  - https://docs.tavily.com/documentation/api-credits
  - https://docs.tavily.com/documentation/rate-limits
  - https://www.tavily.com/pricing
- Endpoint: `POST https://api.tavily.com/search`, header
  `Authorization: Bearer <TAVILY_API_KEY>`.
- Request: `{"query": ..., "max_results": 5}`. Everything else is left at its
  default: `search_depth` = `basic`, `topic` = `general`,
  `include_raw_content` = false, `chunks_per_source` = 3.
- Mode choice: the default `basic` depth already returns a `content` field per
  result (up to 3 chunks of about 500 characters each), so no mode change is
  needed. Content is the vendor's extracted snippet, not the full page.
- Cost: `basic` is 1 credit per search. Pay-as-you-go list price is 0.008 USD
  per credit, so 0.008 USD per call.
- Free tier: 1,000 credits per month, no card. One full run (60 tasks) uses 60
  credits.
- Rate limit: 100 requests per minute on the free plan, shared across all keys
  of the account. `config.toml` uses 1.5 requests per second.
- Errors: 429 (rate limit, may carry `Retry-After`) and 5xx are retried; 401,
  432 (plan limit), 433 (pay-as-you-go limit) and 400/422 are not.
- Smoke run 2026-11-19 (5 tasks): every call returned 5 results with 455 to
  1,480 characters of content each, so the 2,000-character cut did not bite.
  Two calls took 12 to 15 seconds and one exceeded a 30 second read timeout,
  while later calls reported a server `response_time` under 3 seconds. Timeouts
  are therefore retried like 429 and 5xx.

## Exa

- Docs read 2026-11-19:
  - https://docs.exa.ai/reference/search
  - https://exa.ai/docs/search/quickstart.md
  - https://exa.ai/docs/admin/billing.md
  - https://exa.ai/docs/admin/error-codes.md
  - https://exa.ai/pricing
- Endpoint: `POST https://api.exa.ai/search`, header `x-api-key: <EXA_API_KEY>`.
- Request: `{"query": ..., "numResults": 5, "contents": {"highlights":
  {"maxCharacters": 2000}}}`. `type` is left at its default, `auto`.
- Mode choice: the default returns no content text, only URLs and titles, so
  content has to be requested. Text and highlights cost the same (nothing extra
  on `/search`). Highlights were chosen because they are query-relevant
  excerpts, the closest match to the snippets other vendors return by default.
  The first 2,000 characters of full page text are often navigation and would
  measure our truncation rather than Exa's retrieval. Highlights of one result
  are joined with a space before normalization.
- Cost: `auto` is 7 USD per 1,000 searches for up to 10 results, so 0.007 USD
  per call. The response also carries `costDollars`, an estimate that is kept
  in `raw` for audit but not used.
- Free tier: 10 USD of credit on signup.
- Rate limit: 10 queries per second for `/search`, shared across team keys.
- Errors: 429 and 5xx (including 503 overloaded, which is not billed) are
  retried; 400, 401, 402 (out of credit or over budget) and 403 are not.
- Smoke run 2026-11-19 (5 tasks, 0.035 USD reported by `costDollars`, matching
  the table): every call returned 5 results, a gold domain was in the top 5 for
  all 5 tasks. Highlight length ranged from about 200 characters (fresh task)
  to the 2,000 cap. The first call took 26 seconds; the rest took 2 to 4. One
  result had an empty title, which is accepted.

## Firecrawl

- Docs read 2026-11-19:
  - https://docs.firecrawl.dev/api-reference/endpoint/search
  - https://docs.firecrawl.dev/features/search.md
  - https://www.firecrawl.dev/pricing
- Endpoint: `POST https://api.firecrawl.dev/v2/search`, header
  `Authorization: Bearer <FIRECRAWL_API_KEY>`.
- Request: `{"query": ..., "limit": 5}`. Everything else is left at its
  default: web source only, `highlights` = true, no `scrapeOptions`.
- Mode choice: the default returns a `description` per result, which with the
  default `highlights` = true is query-relevant highlight text. That is content,
  so no scraping is requested. Scraping full pages would cost 1 extra credit
  per result and would again measure our truncation rather than retrieval.
- Cost: 2 credits per search of up to 10 results. The free plan has no
  pay-as-you-go price, so the cheapest paid list price is used: Hobby at 19 USD
  for 5,000 credits, 0.0038 USD per credit, so 0.0076 USD per call.
- Free tier: 1,000 credits per month (500 searches). One full run uses 120.
- Rate limit: 10 requests per minute and 2 concurrent requests on the free
  plan. `config.toml` uses 0.15 requests per second.
- Errors: the docs only document 400, 408 and 500. 408, 429 and 5xx are
  retried; other 4xx are not.
- Smoke run 2026-11-19 (5 tasks, 10 credits as reported by `creditsUsed`): the
  live response uses `data.web[]` as in the API reference. Every call returned
  5 results in about 2 seconds. Descriptions are short, mostly 100 to 200
  characters and as little as 22 for the top python.org result on the fresh
  task, so Firecrawl can rank the right page first and still give the judge
  too little text to quote. This is a cost of judging default output and is
  listed as a threat to validity.
