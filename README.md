# searchbench

An independent, reproducible benchmark of web search APIs for LLM agents.

It asks: if an agent sends this query to search API X, does what comes back
contain enough grounded evidence to answer correctly, how fast, and at what
cost? It judges the results the API returns, not an agent's final answer, and
it validates its LLM judge against human labels instead of trusting it.

## Results (snapshot 2026-11-19_1b8ce7b)

60 tasks, top 5 results per query, 95% bootstrap CIs over tasks.

| vendor | support@5 | domain_hit@5 | p50 / p95 latency | USD per query |
|---|---|---|---|---|
| Exa | 0.95 [0.89, 0.99] | 0.90 [0.82, 0.97] | 1.5 s / 2.1 s | 0.0070 |
| Tavily | 0.90 [0.82, 0.97] | 0.78 [0.67, 0.88] | 1.6 s / 3.2 s | 0.0080 |
| Firecrawl | 0.73 [0.62, 0.83] | 0.82 [0.72, 0.92] | 2.0 s / 4.0 s | 0.0076 |

Exa and Tavily both score higher than Firecrawl, and those differences hold
under a paired bootstrap. Exa versus Tavily (+0.05, CI -0.03 to 0.13) is too
close to call with 60 tasks. Firecrawl's default output is short snippets
(median 156 characters per result), which leaves the judge little text to
quote; that is part of what this measures.

Read this with the judge caveat: on a second round of 40 labels, sampled to
include the judge's no and partial verdicts, the primary judge agrees with the
author on 65% of pairs and Cohen's kappa is 0.29, below the 0.6 bar. Most
disagreements are the author saying yes where the judge said partial or no.
Treat the numbers as provisional. How the labelling rounds work is in the
methodology.

Full report, per-category tables and judge validation:
[results/2026-11-19_1b8ce7b/REPORT.md](results/2026-11-19_1b8ce7b/REPORT.md).
How tasks, judging and statistics work: [docs/methodology.md](docs/methodology.md).
Vendor settings, docs and prices: [docs/vendors.md](docs/vendors.md).

## What this does not show

- Which vendor makes a better agent. Only the search results are judged.
- Differences smaller than about 0.1 in support@5. 60 tasks cannot resolve
  them.
- Paid-tier behaviour. Every vendor was called on its free tier.
- Non-default modes. Each vendor runs in its default mode (Exa with
  highlights, since its default returns no text); deeper or scraping modes
  could rank differently.
- Anything stable over time. This is one snapshot; `bench drift` compares
  snapshots.

## Reproduce

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/).

Rebuild the report from the committed snapshot, with no network and no keys:

```
uv sync
uv run bench report --snapshot 2026-11-19_1b8ce7b
git diff --exit-code results/
```

`REPORT.md` reproduces byte for byte. The plot PNG does too on the same
platform, but its bytes can differ with other fonts or matplotlib versions.

Run a new snapshot from scratch:

```
cp .env.example .env        # fill in the vendor keys
set -a; source .env; set +a
gcloud auth application-default login
gcloud auth application-default set-quota-project <your-project>
uv run bench run            # prints the snapshot id
uv run bench judge --snapshot <id>
uv run bench label --snapshot <id>    # optional: 40 human labels
uv run bench report --snapshot <id>
uv run bench drift <older-id> <id>
```

The judge runs on Gemini through Vertex AI with Application Default
Credentials; set `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` in `.env`.
Run parameters, rate limits, the spend limit and judge models are in
`config.toml`. A full run of 60 tasks against three vendors cost about 1.36
USD at list price plus a few dollars of judge calls.

## Development

```
uv run ruff format --check . && uv run ruff check .
uv run mypy
uv run pytest --cov
```

Tests never touch the network: vendor calls are mocked with respx and judge
calls with canned `google-genai` responses. Coverage is held at 100% line and
branch.

## License

Apache-2.0. Author: Vivek Gangavarapu.
