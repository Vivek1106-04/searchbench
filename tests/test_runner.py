import asyncio
import random
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from searchbench.config import Config, JudgeConfig, RunConfig, VendorLimits
from searchbench.models import Result, Task, VendorResponse
from searchbench.runner import (
    MAX_RETRY_AFTER_S,
    TokenBucket,
    response_cache,
    response_key,
    run_snapshot,
    search_with_retry,
    snapshot_id,
)


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def ok(cost: float = 0.01) -> VendorResponse:
    return VendorResponse(
        results=[Result(url="https://a.example", title="A", content="text")],
        latency_ms=5.0,
        cost_usd=cost,
        raw={},
    )


def status_error(code: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://vendor.example/search")
    response = httpx.Response(code, headers=headers, request=request)
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


class ScriptedVendor:
    """Plays back a script of responses or exceptions, then answers ok()."""

    def __init__(
        self,
        name: str,
        script: list[VendorResponse | BaseException] | None = None,
        hold_s: float = 0.0,
    ) -> None:
        self.name = name
        self._hold_s = hold_s
        self.queries: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._script = list(script or [])

    async def search(self, query: str, k: int) -> VendorResponse:
        self.queries.append(query)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(self._hold_s)
        self.in_flight -= 1
        item = self._script.pop(0) if self._script else ok()
        if isinstance(item, BaseException):
            raise item
        return item


def make_config(
    vendors: dict[str, VendorLimits] | None = None, max_spend_usd: float = 10.0
) -> Config:
    return Config(
        run=RunConfig(
            k=5,
            max_content_chars=2000,
            max_spend_usd=max_spend_usd,
            max_attempts=3,
            request_timeout_s=60.0,
        ),
        vendors=vendors or {"a": VendorLimits(concurrency=1, requests_per_second=100.0)},
        judge=JudgeConfig(
            primary_model="p", second_model="s", seed=1, temperature=0.0, concurrency=1
        ),
    )


def make_tasks(n: int) -> list[Task]:
    return [
        Task(
            id=f"t{i}",
            category="factual",
            query=f"query {i}",
            gold_answer="x",
            gold_evidence="x",
            gold_domains=["a.example"],
            as_of=datetime(2026, 11, 19, tzinfo=UTC).date(),
            time_sensitive=False,
        )
        for i in range(n)
    ]


async def retry(vendor: ScriptedVendor, time: FakeTime) -> VendorResponse:
    return await search_with_retry(vendor, "q", 5, 3, time.sleep, random.Random(0))


def test_snapshot_id_is_utc_date_and_short_sha() -> None:
    now = datetime(2026, 11, 20, 1, 2, tzinfo=UTC)

    assert snapshot_id(now, "ab12cd3ef") == "2026-11-20_ab12cd3"


async def test_429_is_retried_with_backoff_then_succeeds() -> None:
    vendor, time = ScriptedVendor("a", [status_error(429)]), FakeTime()

    response = await retry(vendor, time)

    assert response.error is None
    assert len(vendor.queries) == 2
    assert len(time.sleeps) == 1
    assert 1.0 <= time.sleeps[0] < 2.0


@pytest.mark.parametrize(
    ("header", "expected"),
    [("7", 7.0), ("100000", MAX_RETRY_AFTER_S)],
)
async def test_retry_after_header_sets_the_wait(header: str, expected: float) -> None:
    vendor, time = ScriptedVendor("a", [status_error(429, {"Retry-After": header})]), FakeTime()

    await retry(vendor, time)

    assert time.sleeps == [expected]


async def test_unparseable_retry_after_falls_back_to_backoff() -> None:
    script: list[VendorResponse | BaseException] = [
        status_error(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    ]
    vendor, time = ScriptedVendor("a", script), FakeTime()

    await retry(vendor, time)

    assert 1.0 <= time.sleeps[0] < 2.0


async def test_5xx_on_every_attempt_becomes_a_recorded_failure() -> None:
    script: list[VendorResponse | BaseException] = [status_error(503)] * 3
    vendor, time = ScriptedVendor("a", script), FakeTime()

    response = await retry(vendor, time)

    assert response.error == "HTTP 503 after 3 attempt(s)"
    assert response.results == []
    assert response.cost_usd == 0.0
    assert len(vendor.queries) == 3
    assert 1.0 <= time.sleeps[0] < 2.0
    assert 2.0 <= time.sleeps[1] < 3.0


@pytest.mark.parametrize(
    "exc", [status_error(408), httpx.ReadTimeout("slow"), httpx.ConnectError("down")]
)
async def test_timeouts_and_transport_errors_are_retried(exc: BaseException) -> None:
    vendor, time = ScriptedVendor("a", [exc]), FakeTime()

    response = await retry(vendor, time)

    assert response.error is None
    assert len(vendor.queries) == 2


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (status_error(401), "HTTP 401 after 1 attempt(s)"),
        (status_error(402), "HTTP 402 after 1 attempt(s)"),
        (ValueError("bad json"), "invalid response: ValueError after 1 attempt(s)"),
    ],
)
async def test_client_errors_and_bad_payloads_are_not_retried(
    exc: BaseException, error: str
) -> None:
    vendor, time = ScriptedVendor("a", [exc]), FakeTime()

    response = await retry(vendor, time)

    assert response.error == error
    assert len(vendor.queries) == 1
    assert time.sleeps == []


async def test_token_bucket_spaces_calls_at_the_rate() -> None:
    time = FakeTime()
    bucket = TokenBucket(2.0, clock=time.clock, sleep=time.sleep)

    for _ in range(3):
        await bucket.acquire()
    time.now += 10.0
    await bucket.acquire()

    assert time.sleeps == [0.5, 0.5]


async def test_run_fetches_every_pair_and_caches_it(tmp_path: Path) -> None:
    config = make_config(
        {
            "a": VendorLimits(concurrency=1, requests_per_second=100.0),
            "b": VendorLimits(concurrency=1, requests_per_second=100.0),
        }
    )
    a, b, time = ScriptedVendor("a"), ScriptedVendor("b"), FakeTime()

    summary = await run_snapshot(
        make_tasks(3), [a, b], config, tmp_path, sleep=time.sleep, clock=time.clock
    )

    assert (summary.calls, summary.cached, summary.errors) == (6, 0, 0)
    assert summary.spend_usd == pytest.approx(0.06)
    assert not summary.stopped_by_budget
    cache = response_cache(tmp_path, "a")
    assert cache.get(response_key("a", "query 2", 5, 2000)) == ok()
    assert len(response_cache(tmp_path, "b")) == 3


async def test_final_failure_is_recorded_not_dropped(tmp_path: Path) -> None:
    script: list[VendorResponse | BaseException] = [status_error(500)] * 3
    vendor, time = ScriptedVendor("a", script), FakeTime()

    summary = await run_snapshot(
        make_tasks(2), [vendor], make_config(), tmp_path, sleep=time.sleep, clock=time.clock
    )

    assert (summary.calls, summary.errors) == (2, 1)
    failed = response_cache(tmp_path, "a").get(response_key("a", "query 0", 5, 2000))
    assert failed is not None
    assert failed.error == "HTTP 500 after 3 attempt(s)"


async def test_rerun_after_crash_only_fetches_missing_pairs(tmp_path: Path) -> None:
    tasks, time = make_tasks(4), FakeTime()
    crashing = ScriptedVendor("a", [ok(), ok(), RuntimeError("process killed")])

    with pytest.raises(ExceptionGroup):
        await run_snapshot(
            tasks, [crashing], make_config(), tmp_path, sleep=time.sleep, clock=time.clock
        )
    resumed = ScriptedVendor("a")
    summary = await run_snapshot(
        tasks, [resumed], make_config(), tmp_path, sleep=time.sleep, clock=time.clock
    )

    assert resumed.queries == ["query 2", "query 3"]
    assert (summary.calls, summary.cached) == (2, 2)
    assert summary.spend_usd == pytest.approx(0.04)


async def test_spend_kill_switch_stops_new_calls(tmp_path: Path) -> None:
    vendor, time = ScriptedVendor("a"), FakeTime()

    summary = await run_snapshot(
        make_tasks(5),
        [vendor],
        make_config(max_spend_usd=0.025),
        tmp_path,
        sleep=time.sleep,
        clock=time.clock,
    )

    assert summary.stopped_by_budget
    assert summary.calls == 3
    assert len(vendor.queries) == 3
    assert len(response_cache(tmp_path, "a")) == 3


async def test_spend_from_an_earlier_run_counts_toward_the_limit(tmp_path: Path) -> None:
    tasks, time = make_tasks(3), FakeTime()
    cache = response_cache(tmp_path, "a")
    cache.put(response_key("a", "query 0", 5, 2000), ok(cost=1.0))

    vendor = ScriptedVendor("a")
    summary = await run_snapshot(
        tasks, [vendor], make_config(max_spend_usd=1.0), tmp_path, sleep=time.sleep
    )

    assert vendor.queries == []
    assert summary.cached == 1
    assert summary.stopped_by_budget


async def test_concurrency_limit_is_respected(tmp_path: Path) -> None:
    config = make_config({"a": VendorLimits(concurrency=2, requests_per_second=1000.0)})
    vendor = ScriptedVendor("a", hold_s=0.01)

    await run_snapshot(make_tasks(6), [vendor], config, tmp_path)

    assert vendor.max_in_flight == 2
