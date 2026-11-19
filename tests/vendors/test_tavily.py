import json

import httpx
import pytest
import respx
from pydantic import ValidationError

from searchbench.vendors.base import SearchVendor
from searchbench.vendors.tavily import COST_PER_SEARCH_USD, SEARCH_URL, TavilyVendor

PAYLOAD = {
    "query": "postgresql default port",
    "results": [
        {
            "url": "https://www.postgresql.org/docs/current/runtime-config-connection.html",
            "title": "Connections",
            "content": "The TCP port   the server listens on;\n5432 by default.",
            "score": 0.9,
        },
        {"url": "https://b.example", "title": "B", "content": "x" * 50, "score": 0.5},
    ],
    "response_time": 1.2,
}


@respx.mock
async def test_search_maps_results_and_sends_identical_params() -> None:
    route = respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json=PAYLOAD))

    async with httpx.AsyncClient() as client:
        vendor: SearchVendor = TavilyVendor(client, "tvly-key", max_chars=20)
        response = await vendor.search("postgresql default port", k=5)

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer tvly-key"
    assert json.loads(request.content) == {"query": "postgresql default port", "max_results": 5}
    assert [r.url for r in response.results] == [
        "https://www.postgresql.org/docs/current/runtime-config-connection.html",
        "https://b.example",
    ]
    assert response.results[0].content == "The TCP port the ser"
    assert response.results[1].content == "x" * 20
    assert response.cost_usd == COST_PER_SEARCH_USD
    assert response.latency_ms >= 0
    assert response.raw == PAYLOAD
    assert response.error is None


@respx.mock
async def test_search_keeps_at_most_k_results() -> None:
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json=PAYLOAD))

    async with httpx.AsyncClient() as client:
        response = await TavilyVendor(client, "k", max_chars=100).search("q", k=1)

    assert len(response.results) == 1


@respx.mock
async def test_empty_results_are_not_an_error() -> None:
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": []}))

    async with httpx.AsyncClient() as client:
        response = await TavilyVendor(client, "k", max_chars=100).search("q", k=5)

    assert response.results == []


@pytest.mark.parametrize("status", [401, 429, 432, 500])
@respx.mock
async def test_http_errors_propagate_for_the_runner(status: int) -> None:
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(status, json={"detail": {}}))

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError) as exc:
            await TavilyVendor(client, "k", max_chars=100).search("q", k=5)

    assert exc.value.response.status_code == status


@respx.mock
async def test_malformed_payload_raises() -> None:
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": [{"url": 1}]}))

    async with httpx.AsyncClient() as client:
        with pytest.raises(ValidationError):
            await TavilyVendor(client, "k", max_chars=100).search("q", k=5)
