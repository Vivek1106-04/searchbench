import json

import httpx
import pytest
import respx
from pydantic import ValidationError

from searchbench.vendors.base import SearchVendor
from searchbench.vendors.exa import COST_PER_SEARCH_USD, SEARCH_URL, ExaVendor

PAYLOAD = {
    "requestId": "r1",
    "results": [
        {
            "url": "https://www.postgresql.org/docs/current/runtime-config-connection.html",
            "title": "Connections",
            "highlights": ["The TCP port the server listens on;", "5432   by default."],
        },
        {"url": "https://b.example", "title": "B", "highlights": ["y" * 50]},
        {"url": "https://c.example", "title": "C"},
    ],
    "costDollars": {"total": 0.007},
}


@respx.mock
async def test_search_requests_highlights_and_joins_them() -> None:
    route = respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json=PAYLOAD))

    async with httpx.AsyncClient() as client:
        vendor: SearchVendor = ExaVendor(client, "exa-key", max_chars=40)
        response = await vendor.search("postgresql default port", k=5)

    request = route.calls.last.request
    assert request.headers["x-api-key"] == "exa-key"
    assert json.loads(request.content) == {
        "query": "postgresql default port",
        "numResults": 5,
        "contents": {"highlights": {"maxCharacters": 40}},
    }
    assert response.results[0].content == "The TCP port the server listens on; 5432"
    assert response.results[1].content == "y" * 40
    assert response.cost_usd == COST_PER_SEARCH_USD
    assert response.latency_ms >= 0
    assert response.raw == PAYLOAD


@respx.mock
async def test_result_without_highlights_has_empty_content() -> None:
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json=PAYLOAD))

    async with httpx.AsyncClient() as client:
        response = await ExaVendor(client, "k", max_chars=100).search("q", k=5)

    assert response.results[2].url == "https://c.example"
    assert response.results[2].content == ""


@respx.mock
async def test_search_keeps_at_most_k_results() -> None:
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json=PAYLOAD))

    async with httpx.AsyncClient() as client:
        response = await ExaVendor(client, "k", max_chars=100).search("q", k=2)

    assert len(response.results) == 2


@pytest.mark.parametrize("status", [401, 402, 429, 503])
@respx.mock
async def test_http_errors_propagate_for_the_runner(status: int) -> None:
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(status, json={"error": "e"}))

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError) as exc:
            await ExaVendor(client, "k", max_chars=100).search("q", k=5)

    assert exc.value.response.status_code == status


@respx.mock
async def test_malformed_payload_raises() -> None:
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": [{"url": 1}]}))

    async with httpx.AsyncClient() as client:
        with pytest.raises(ValidationError):
            await ExaVendor(client, "k", max_chars=100).search("q", k=5)
