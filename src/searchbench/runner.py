import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from searchbench.cache import JsonlCache, cache_key
from searchbench.config import Config
from searchbench.models import Task, VendorResponse
from searchbench.vendors.base import SearchVendor

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]

RETRYABLE_STATUS = frozenset({408, 429})
BACKOFF_BASE_S = 1.0
# A vendor asking for a longer pause than this is treated as down for this run.
MAX_RETRY_AFTER_S = 60.0


def snapshot_id(now_utc: datetime, git_sha: str) -> str:
    return f"{now_utc:%Y-%m-%d}_{git_sha[:7]}"


def response_key(vendor: str, query: str, k: int, max_chars: int) -> str:
    return cache_key(vendor, query, {"k": k, "max_chars": max_chars})


def response_cache(snapshot_dir: Path, vendor: str) -> JsonlCache[VendorResponse]:
    return JsonlCache(snapshot_dir / "responses" / f"{vendor}.jsonl", VendorResponse)


class TokenBucket:
    """Holds at most one token, so calls are spaced at least 1/rate seconds apart."""

    def __init__(self, rate: float, clock: Clock = time.monotonic, sleep: Sleep = asyncio.sleep):
        self._rate = rate
        self._clock = clock
        self._sleep = sleep
        self._tokens = 1.0
        self._updated = clock()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            self._tokens = min(1.0, self._tokens + (now - self._updated) * self._rate)
            self._updated = now
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rate
                await self._sleep(wait)
                self._updated = now + wait
                self._tokens = 1.0
            self._tokens -= 1.0


def _retry_after_s(response: httpx.Response) -> float | None:
    try:
        return min(float(response.headers["Retry-After"]), MAX_RETRY_AFTER_S)
    except (KeyError, ValueError):
        return None


def _failure(error: str) -> VendorResponse:
    return VendorResponse(results=[], latency_ms=0.0, cost_usd=0.0, raw=None, error=error)


async def search_with_retry(
    vendor: SearchVendor,
    query: str,
    k: int,
    max_attempts: int,
    sleep: Sleep,
    rng: random.Random,
) -> VendorResponse:
    attempt = 0
    while True:
        attempt += 1
        hint: float | None = None
        try:
            return await vendor.search(query, k)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            error = f"HTTP {status}"
            retryable = status in RETRYABLE_STATUS or status >= 500
            hint = _retry_after_s(exc.response)
        except httpx.TransportError as exc:
            error = type(exc).__name__
            retryable = True
        except ValueError as exc:
            error = f"invalid response: {type(exc).__name__}"
            retryable = False
        if not retryable or attempt >= max_attempts:
            return _failure(f"{error} after {attempt} attempt(s)")
        backoff = BACKOFF_BASE_S * 2 ** (attempt - 1) + rng.uniform(0, BACKOFF_BASE_S)
        await sleep(hint if hint is not None else backoff)


@dataclass
class RunSummary:
    calls: int = 0
    cached: int = 0
    errors: int = 0
    spend_usd: float = 0.0
    stopped_by_budget: bool = False


async def run_snapshot(
    tasks: Sequence[Task],
    vendors: Sequence[SearchVendor],
    config: Config,
    snapshot_dir: Path,
    *,
    sleep: Sleep = asyncio.sleep,
    clock: Clock = time.monotonic,
    rng: random.Random | None = None,
) -> RunSummary:
    """Fetch every (task, vendor) response not already cached in the snapshot.

    Spend is shared across vendors and includes responses cached by an earlier,
    interrupted run of the same snapshot. Once it reaches the limit no new call
    starts; calls already in flight finish and are recorded.
    """
    run = config.run
    rng = rng or random.Random()
    caches = {v.name: response_cache(snapshot_dir, v.name) for v in vendors}
    summary = RunSummary(
        spend_usd=sum(r.cost_usd for c in caches.values() for r in c.values()),
    )

    async def fetch(
        vendor: SearchVendor, task: Task, gate: asyncio.Semaphore, bucket: TokenBucket
    ) -> None:
        cache = caches[vendor.name]
        key = response_key(vendor.name, task.query, run.k, run.max_content_chars)
        if cache.get(key) is not None:
            summary.cached += 1
            return
        async with gate:
            if summary.spend_usd >= run.max_spend_usd:
                summary.stopped_by_budget = True
                return
            await bucket.acquire()
            response = await search_with_retry(
                vendor, task.query, run.k, run.max_attempts, sleep, rng
            )
        cache.put(key, response)
        summary.calls += 1
        summary.errors += response.error is not None
        summary.spend_usd += response.cost_usd

    async with asyncio.TaskGroup() as group:
        for vendor in vendors:
            limits = config.vendors[vendor.name]
            gate = asyncio.Semaphore(limits.concurrency)
            bucket = TokenBucket(limits.requests_per_second, clock=clock, sleep=sleep)
            for task in tasks:
                group.create_task(fetch(vendor, task, gate, bucket))
    return summary
