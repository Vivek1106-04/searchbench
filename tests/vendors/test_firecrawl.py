import json

import httpx
import pytest
import respx
from pydantic import ValidationError

from searchbench.vendors.base import SearchVendor
from searchbench.vendors.firecrawl import COST_PER_SEARCH_USD, SEARCH_URL, FirecrawlVendor

PAYLOAD = {
    "success": True,
    "data": {
        "web": [
            {
                "url": "https://www.postgresql.org/docs/current/runtime-config-connection.html",
                "title": "Connections",
                "description": "The TCP port the server   listens on; 5432 by default.",
                "position": 1,
            },
            {"url": "https://b.example", "title": "B", "description": "z" * 50},
            {"url": "https://c.example", "title": "C", "description": None},
        ]
    },
    "creditsUsed": 2,
}


@respx.mock
async def test_search_maps_descriptions_and_sends_default_params() -> None:
    route = respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json=PAYLOAD))

    async with httpx.AsyncClient() as client:
        vendor: SearchVendor = FirecrawlVendor(client, "fc-key", max_chars=30)
        response = await vendor.search("postgresql default port", k=5)

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer fc-key"
    assert json.loads(request.content) == {"query": "postgresql default port", "limit": 5}
    assert response.results[0].content == "The TCP port the server listen"
    assert response.results[1].content == "z" * 30
    assert response.results[2].content == ""
    assert response.cost_usd == COST_PER_SEARCH_USD
    assert response.latency_ms >= 0
    assert response.raw == PAYLOAD


@respx.mock
async def test_search_keeps_at_most_k_results() -> None:
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json=PAYLOAD))

    async with httpx.AsyncClient() as client:
        response = await FirecrawlVendor(client, "k", max_chars=100).search("q", k=1)

    assert len(response.results) == 1


@respx.mock
async def test_missing_web_list_means_no_results() -> None:
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": {}}))

    async with httpx.AsyncClient() as client:
        response = await FirecrawlVendor(client, "k", max_chars=100).search("q", k=5)

    assert response.results == []


@pytest.mark.parametrize("status", [401, 402, 408, 429, 500])
@respx.mock
async def test_http_errors_propagate_for_the_runner(status: int) -> None:
    respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(status, json={"success": False, "error": "e"})
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError) as exc:
            await FirecrawlVendor(client, "k", max_chars=100).search("q", k=5)

    assert exc.value.response.status_code == status


@respx.mock
async def test_malformed_payload_raises() -> None:
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": [{"url": 1}]}))

    async with httpx.AsyncClient() as client:
        with pytest.raises(ValidationError):
            await FirecrawlVendor(client, "k", max_chars=100).search("q", k=5)
