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
