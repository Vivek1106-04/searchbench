from collections.abc import Sequence
from datetime import date
from pathlib import Path

from searchbench.config import Config, JudgeConfig, RunConfig, VendorLimits
from searchbench.judge import judgment_cache, judgment_key
from searchbench.models import Category, Judgment, Result, Support, Task, VendorResponse
from searchbench.runner import response_cache, response_key

PRIMARY, SECOND = "judge-pro", "judge-lite"


def config(vendors: Sequence[str] = ("alpha", "beta")) -> Config:
    return Config(
        run=RunConfig(
            k=5, max_content_chars=2000, max_spend_usd=10.0, max_attempts=3, request_timeout_s=60.0
        ),
        vendors={v: VendorLimits(concurrency=1, requests_per_second=1.0) for v in vendors},
        judge=JudgeConfig(
            primary_model=PRIMARY, second_model=SECOND, seed=1, temperature=0.0, concurrency=1
        ),
    )


def task(i: int, category: Category = "factual") -> Task:
    return Task(
        id=f"t{i:02d}",
        category=category,
        query=f"question {i}",
        gold_answer=f"answer {i}",
        gold_evidence=f"evidence {i}",
        gold_domains=["gold.example"],
        as_of=date(2026, 11, 19),
        time_sensitive=category == "fresh",
    )


def results_for(t: Task, vendor: str, n: int = 2) -> list[Result]:
    return [
        Result(
            url="https://docs.gold.example/page" if i == 0 else f"https://other{i}.example",
            title=f"Title {i}",
            content=f"result {i} says answer {t.id} via {vendor}",
        )
        for i in range(n)
    ]


def verdict(supported: Support, t: Task, index: int = 1, quote: str | None = None) -> Judgment:
    if supported == "no":
        return Judgment(supported="no")
    return Judgment(
        supported=supported,
        supporting_result_index=index,
        quote=quote if quote is not None else f"result {index - 1} says answer {t.id}",
    )


def write_snapshot(
    snapshot_dir: Path,
    tasks: Sequence[Task],
    primary: dict[tuple[str, str], Judgment],
    second: dict[tuple[str, str], Judgment] | None = None,
    *,
    n_results: int = 2,
    errors: Sequence[tuple[str, str]] = (),
    cfg: Config | None = None,
    latency_ms: float = 100.0,
) -> None:
    """Store a response plus both judges' forward and reversed verdicts for each pair."""
    cfg = cfg or config()
    judge = cfg.judge
    second = second or primary
    for (vendor, task_id), judgment in primary.items():
        t = next(x for x in tasks if x.id == task_id)
        failed = (vendor, task_id) in errors
        results = [] if failed else results_for(t, vendor, n_results)
        response = VendorResponse(
            results=results,
            latency_ms=0.0 if failed else latency_ms,
            cost_usd=0.0 if failed else 0.01,
            raw=None,
            error="HTTP 500 after 3 attempt(s)" if failed else None,
        )
        response_cache(snapshot_dir, vendor).put(
            response_key(vendor, t.query, cfg.run.k, cfg.run.max_content_chars), response
        )
        if failed:
            continue
        for model, chosen in (
            (judge.primary_model, judgment),
            (judge.second_model, second[(vendor, task_id)]),
        ):
            cache = judgment_cache(snapshot_dir, model)
            cache.put(judgment_key(model, judge.seed, judge.temperature, t, results), chosen)
            flipped = chosen
            if chosen.supporting_result_index is not None:
                flipped = chosen.model_copy(
                    update={
                        "supporting_result_index": len(results) + 1 - chosen.supporting_result_index
                    }
                )
            reversed_results = list(reversed(results))
            cache.put(
                judgment_key(model, judge.seed, judge.temperature, t, reversed_results), flipped
            )
