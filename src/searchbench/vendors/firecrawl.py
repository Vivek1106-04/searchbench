import time

import httpx
from pydantic import BaseModel

from searchbench.models import Result, VendorResponse
from searchbench.vendors.base import normalize_content

SEARCH_URL = "https://api.firecrawl.dev/v2/search"
# 2 credits per search of up to 10 results at the Hobby plan credit price (docs/vendors.md).
COST_PER_SEARCH_USD = 0.0076


class _FirecrawlResult(BaseModel):
    url: str
    title: str
    description: str | None = None


class _FirecrawlData(BaseModel):
    web: list[_FirecrawlResult] = []


class _FirecrawlResponse(BaseModel):
    data: _FirecrawlData


class FirecrawlVendor:
    name = "firecrawl"

    def __init__(self, client: httpx.AsyncClient, api_key: str, max_chars: int) -> None:
        self._client = client
        self._api_key = api_key
        self._max_chars = max_chars

    async def search(self, query: str, k: int) -> VendorResponse:
        start = time.perf_counter()
        response = await self._client.post(
            SEARCH_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"query": query, "limit": k},
        )
        latency_ms = (time.perf_counter() - start) * 1000
        response.raise_for_status()
        raw = response.json()
        parsed = _FirecrawlResponse.model_validate(raw)
        results = [
            Result(
                url=r.url,
                title=r.title,
                content=normalize_content(r.description or "", self._max_chars),
            )
            for r in parsed.data.web[:k]
        ]
        return VendorResponse(
            results=results, latency_ms=latency_ms, cost_usd=COST_PER_SEARCH_USD, raw=raw
        )
