import time

import httpx
from pydantic import BaseModel

from searchbench.models import Result, VendorResponse
from searchbench.vendors.base import normalize_content

SEARCH_URL = "https://api.tavily.com/search"
# Basic depth costs 1 credit; pay-as-you-go list price per credit (docs/vendors.md).
COST_PER_SEARCH_USD = 0.008


class _TavilyResult(BaseModel):
    url: str
    title: str
    content: str


class _TavilyResponse(BaseModel):
    results: list[_TavilyResult]


class TavilyVendor:
    name = "tavily"

    def __init__(self, client: httpx.AsyncClient, api_key: str, max_chars: int) -> None:
        self._client = client
        self._api_key = api_key
        self._max_chars = max_chars

    async def search(self, query: str, k: int) -> VendorResponse:
        start = time.perf_counter()
        response = await self._client.post(
            SEARCH_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"query": query, "max_results": k},
        )
        latency_ms = (time.perf_counter() - start) * 1000
        response.raise_for_status()
        raw = response.json()
        parsed = _TavilyResponse.model_validate(raw)
        results = [
            Result(
                url=r.url,
                title=r.title,
                content=normalize_content(r.content, self._max_chars),
            )
            for r in parsed.results[:k]
        ]
        return VendorResponse(
            results=results, latency_ms=latency_ms, cost_usd=COST_PER_SEARCH_USD, raw=raw
        )
