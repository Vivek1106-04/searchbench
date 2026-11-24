import json
from datetime import date
from pathlib import Path

import pytest
from google.genai import types
from pydantic import ValidationError

from searchbench.cache import JsonlCache
from searchbench.judge import (
    Grounded,
    Judge,
    JudgeSummary,
    build_prompt,
    ground,
    judge_snapshot,
    judgment_cache,
    order_bias_sample,
    snapshot_pairs,
    visible_text,
)
from searchbench.models import Judgment, Result, Task, VendorResponse
from searchbench.runner import response_cache, response_key

TASK = Task(
    id="web_v1_001",
    category="factual",
    query="postgresql default port",
    gold_answer="5432",
    gold_evidence="The TCP port the server listens on; 5432 by default.",
    gold_domains=["postgresql.org"],
    as_of=date(2026, 11, 19),
    time_sensitive=False,
)
RESULTS = [
    Result(url="https://blog.example/ports", title="Ports", content="Many databases use ports."),
    Result(
        url="https://www.postgresql.org/docs/current/runtime-config-connection.html",
        title="Connections",
        content="port (integer) The TCP port the server listens on; 5432 by default.",
    ),
    Result(url="https://tavily.com/knowledge/x", title="Other", content="Unrelated text."),
]


def reply(supported: str, index: int | None = None, quote: str | None = None) -> str:
    return json.dumps({"supported": supported, "supporting_result_index": index, "quote": quote})


class FakeModels:
    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.calls: list[tuple[str, str, types.GenerateContentConfig]] = []

    async def generate_content(
        self, *, model: str, contents: str, config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        self.calls.append((model, contents, config))
        part = types.Part(text=self.texts.pop(0))
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=[part]))]
        )


def make_judge(models: FakeModels, tmp_path: Path, model: str = "gemini-test") -> Judge:
    cache = JsonlCache(tmp_path / "judgments" / f"{model}.jsonl", Judgment)
    return Judge(models, model, seed=1, temperature=0.0, cache=cache)


def test_prompt_is_blind_and_numbered() -> None:
    prompt = build_prompt(TASK, RESULTS)

    assert "Question: postgresql default port" in prompt
    assert "Known answer: 5432" in prompt
    assert "Result 2\nTitle: Connections\nText: port (integer)" in prompt
    assert "http" not in prompt
    assert "tavily" not in prompt


@pytest.mark.parametrize(
    ("judgment", "expected"),
    [
        (
            Judgment(supported="yes", supporting_result_index=2, quote="5432 by default"),
            Grounded("yes", False),
        ),
        (
            Judgment(supported="partial", supporting_result_index=2, quote=" TCP port "),
            Grounded("partial", False),
        ),
        (Judgment(supported="no"), Grounded("no", False)),
        (
            Judgment(supported="yes", supporting_result_index=2, quote="Connections"),
            Grounded("yes", False),
        ),
        (
            Judgment(supported="yes", supporting_result_index=1, quote="Connections"),
            Grounded("no", True),
        ),
        (
            Judgment(supported="yes", supporting_result_index=2, quote="5432 is the default"),
            Grounded("no", True),
        ),
        (
            Judgment(supported="yes", supporting_result_index=1, quote="5432 by default"),
            Grounded("no", True),
        ),
        (
            Judgment(supported="yes", supporting_result_index=4, quote="5432 by default"),
            Grounded("no", True),
        ),
        (
            Judgment(supported="yes", supporting_result_index=None, quote="5432 by default"),
            Grounded("no", True),
        ),
        (Judgment(supported="yes", supporting_result_index=2, quote=None), Grounded("no", True)),
        (Judgment(supported="yes", supporting_result_index=2, quote="  "), Grounded("no", True)),
    ],
    ids=[
        "exact",
        "partial-stripped",
        "no",
        "title",
        "other-results-title",
        "fabricated",
        "wrong-result",
        "out-of-range",
        "no-index",
        "no-quote",
        "blank-quote",
    ],
)
def test_grounding_check(judgment: Judgment, expected: Grounded) -> None:
    assert ground(judgment, RESULTS) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "created by [Linus Torvalds](https://en.wikipedia.org/wiki/Linus_Torvalds) in 1991",
            "created by Linus Torvalds in 1991",
        ),
        (
            "finger [daemon](https://en.wikipedia.org/wiki/Daemon_(computing)) runs",
            "finger daemon runs",
        ),
        ("a ![logo](https://example.com/a.png) b", "a logo b"),
        ("port\n\n  5432", "port 5432"),
        ("[unclosed](https://example.com", "[unclosed](https://example.com"),
    ],
    ids=["link", "nested-parens", "image", "whitespace", "not-a-link"],
)
def test_visible_text(text: str, expected: str) -> None:
    assert visible_text(text) == expected


def test_quote_of_rendered_markdown_is_grounded() -> None:
    results = [
        Result(
            url="https://en.wikipedia.org/wiki/Linux",
            title="Linux",
            content="The kernel was created by [Linus Torvalds](https://w.org/LT) in 1991.",
        )
    ]
    rendered = Judgment(
        supported="yes", supporting_result_index=1, quote="created by Linus Torvalds in 1991"
    )
    raw = Judgment(supported="yes", supporting_result_index=1, quote="created by [Linus Torvalds]")
    absent = Judgment(supported="yes", supporting_result_index=1, quote="created by Linus in 1990")

    assert ground(rendered, results) == Grounded("yes", hallucinated=False)
    assert ground(raw, results) == Grounded("yes", hallucinated=False)
    assert ground(absent, results) == Grounded("no", hallucinated=True)


async def test_fabricated_quote_from_the_model_is_downgraded(tmp_path: Path) -> None:
    models = FakeModels(reply("yes", 2, "PostgreSQL always uses port 5432"))

    judgment = await make_judge(models, tmp_path).judge(TASK, RESULTS)

    assert judgment.supported == "yes"
    assert ground(judgment, RESULTS) == Grounded("no", hallucinated=True)


async def test_judge_sends_fixed_seed_temperature_and_schema(tmp_path: Path) -> None:
    models = FakeModels(reply("yes", 2, "5432 by default"))

    judgment = await make_judge(models, tmp_path).judge(TASK, RESULTS)

    model, contents, config = models.calls[0]
    assert model == "gemini-test"
    assert contents == build_prompt(TASK, RESULTS)
    assert (config.seed, config.temperature) == (1, 0.0)
    assert config.response_mime_type == "application/json"
    assert config.response_schema is Judgment
    assert judgment == Judgment(supported="yes", supporting_result_index=2, quote="5432 by default")


async def test_cached_judgment_is_reused_across_instances(tmp_path: Path) -> None:
    first = FakeModels(reply("yes", 2, "5432 by default"))
    await make_judge(first, tmp_path).judge(TASK, RESULTS)

    second = FakeModels()
    judgment = await make_judge(second, tmp_path).judge(TASK, RESULTS)

    assert second.calls == []
    assert judgment.supported == "yes"


async def test_cache_is_per_model(tmp_path: Path) -> None:
    await make_judge(FakeModels(reply("no")), tmp_path, "model-a").judge(TASK, RESULTS)
    other = FakeModels(reply("yes", 2, "5432 by default"))

    judgment = await make_judge(other, tmp_path, "model-b").judge(TASK, RESULTS)

    assert len(other.calls) == 1
    assert judgment.supported == "yes"


async def test_reversed_order_maps_index_back(tmp_path: Path) -> None:
    models = FakeModels(reply("yes", 2, "5432 by default"), reply("yes", 1, "x"))
    judge = make_judge(models, tmp_path)

    reversed_judgment = await judge.judge(TASK, RESULTS, reverse=True)

    assert models.calls[0][1] == build_prompt(TASK, list(reversed(RESULTS)))
    assert reversed_judgment.supporting_result_index == 2
    assert ground(reversed_judgment, RESULTS) == Grounded("yes", False)


@pytest.mark.parametrize("index", [None, 9])
async def test_reversed_order_keeps_missing_or_invalid_index(
    tmp_path: Path, index: int | None
) -> None:
    models = FakeModels(reply("yes", index, "5432 by default"))

    judgment = await make_judge(models, tmp_path).judge(TASK, RESULTS, reverse=True)

    assert judgment.supporting_result_index == index


async def test_no_results_is_no_without_a_model_call(tmp_path: Path) -> None:
    models = FakeModels()

    judgment = await make_judge(models, tmp_path).judge(TASK, [])

    assert judgment == Judgment(supported="no")
    assert models.calls == []


@pytest.mark.parametrize("text", ["not json", reply("maybe")])
async def test_malformed_model_output_raises_and_is_not_cached(tmp_path: Path, text: str) -> None:
    judge = make_judge(FakeModels(text), tmp_path)

    with pytest.raises(ValidationError):
        await judge.judge(TASK, RESULTS)

    assert not (tmp_path / "judgments" / "gemini-test.jsonl").exists()


async def test_lookup_reads_cache_only(tmp_path: Path) -> None:
    models = FakeModels(reply("yes", 2, "5432 by default"))
    judge = make_judge(models, tmp_path)

    assert judge.lookup(TASK, RESULTS) is None
    await judge.judge(TASK, RESULTS, reverse=True)

    assert judge.lookup(TASK, RESULTS) is None
    assert judge.lookup(TASK, RESULTS, reverse=True) == Judgment(
        supported="yes", supporting_result_index=2, quote="5432 by default"
    )
    assert judge.lookup(TASK, []) == Judgment(supported="no")
    assert len(models.calls) == 1


class AlwaysYes:
    """Answers every prompt with a grounded yes on the first result's first word."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def generate_content(
        self, *, model: str, contents: str, config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        self.calls.append((model, contents))
        part = types.Part(text=reply("yes", 1, "x"))
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=[part]))]
        )


def store_responses(snapshot_dir: Path, vendor: str, tasks: list[Task], n_results: int) -> None:
    cache = response_cache(snapshot_dir, vendor)
    for task in tasks:
        results = [
            Result(url=f"https://{i}.example", title=f"r{i}", content=f"x {i}")
            for i in range(n_results)
        ]
        response = VendorResponse(results=results, latency_ms=1.0, cost_usd=0.01, raw={})
        cache.put(response_key(vendor, task.query, 5, 2000), response)


def tasks(n: int) -> list[Task]:
    return [TASK.model_copy(update={"id": f"t{i}", "query": f"q{i}"}) for i in range(n)]


def test_snapshot_pairs_skips_missing_and_orders_by_vendor(tmp_path: Path) -> None:
    suite = tasks(3)
    store_responses(tmp_path, "zeta", suite[:2], 5)
    store_responses(tmp_path, "alpha", suite[1:], 5)

    pairs = snapshot_pairs(suite, ["zeta", "alpha"], tmp_path, 5, 2000)

    assert [(p.vendor, p.task.id) for p in pairs] == [
        ("alpha", "t1"),
        ("alpha", "t2"),
        ("zeta", "t0"),
        ("zeta", "t1"),
    ]


def test_order_bias_sample_is_seeded_and_needs_two_results(tmp_path: Path) -> None:
    suite = tasks(40)
    store_responses(tmp_path, "a", suite[:35], 5)
    store_responses(tmp_path, "b", suite, 1)
    pairs = snapshot_pairs(suite, ["a", "b"], tmp_path, 5, 2000)

    sample = order_bias_sample(pairs, 30, seed=1)

    assert len(sample) == 30
    assert all(p.vendor == "a" for p in sample)
    assert sample == order_bias_sample(pairs, 30, seed=1)
    assert len(order_bias_sample(pairs[:10], 30, seed=1)) == 10


async def test_judge_snapshot_runs_both_judges_and_reverse_sample(tmp_path: Path) -> None:
    suite = tasks(4)
    store_responses(tmp_path, "a", suite, 3)
    pairs = snapshot_pairs(suite, ["a"], tmp_path, 5, 2000)
    models = AlwaysYes()
    primary = Judge(models, "pro", 1, 0.0, judgment_cache(tmp_path, "pro"))
    second = Judge(models, "lite", 1, 0.0, judgment_cache(tmp_path, "lite"))

    summary = await judge_snapshot(pairs, primary, second, concurrency=2, seed=1)

    assert summary == JudgeSummary(pairs=4, reversed=4)
    assert sorted(m for m, _ in models.calls) == ["lite"] * 4 + ["pro"] * 8
    assert len(judgment_cache(tmp_path, "pro")) == 8
    assert all(primary.lookup(p.task, p.response.results, reverse=True) for p in pairs)
