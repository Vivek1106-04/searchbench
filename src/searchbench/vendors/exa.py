import time

import httpx
from pydantic import BaseModel

from searchbench.models import Result, VendorResponse
from searchbench.vendors.base import normalize_content

SEARCH_URL = "https://api.exa.ai/search"
# type=auto with up to 10 results; highlights add no charge (docs/vendors.md).
COST_PER_SEARCH_USD = 0.007


class _ExaResult(BaseModel):
    url: str
    title: str
    highlights: list[str] = []


class _ExaResponse(BaseModel):
    results: list[_ExaResult]


class ExaVendor:
    name = "exa"

    def __init__(self, client: httpx.AsyncClient, api_key: str, max_chars: int) -> None:
        self._client = client
        self._api_key = api_key
        self._max_chars = max_chars

    async def search(self, query: str, k: int) -> VendorResponse:
        start = time.perf_counter()
        response = await self._client.post(
            SEARCH_URL,
            headers={"x-api-key": self._api_key},
            json={
                "query": query,
                "numResults": k,
                "contents": {"highlights": {"maxCharacters": self._max_chars}},
            },
        )
        latency_ms = (time.perf_counter() - start) * 1000
        response.raise_for_status()
        raw = response.json()
        parsed = _ExaResponse.model_validate(raw)
        results = [
            Result(
                url=r.url,
                title=r.title,
                content=normalize_content(" ".join(r.highlights), self._max_chars),
            )
            for r in parsed.results[:k]
        ]
        return VendorResponse(
            results=results, latency_ms=latency_ms, cost_usd=COST_PER_SEARCH_USD, raw=raw
        )
