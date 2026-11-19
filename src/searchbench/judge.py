import asyncio
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from google.genai import types

from searchbench.cache import JsonlCache, cache_key
from searchbench.models import Judgment, Result, Support, Task, VendorResponse
from searchbench.runner import response_cache, response_key

ORDER_BIAS_SAMPLE = 30

PROMPT = """You check whether web search results contain evidence for a known answer.

Question: {query}
Known answer: {gold_answer}
Example of supporting evidence: {gold_evidence}

Search results:
{results}

Decide whether the search results above support the known answer.
- "yes": one result states the known answer, or states something that directly implies it.
- "partial": a result gives part of what is needed (for example one of two facts) or
  states the answer with less precision than asked, but not the full answer.
- "no": no result supports the answer, or results only mention the topic.
Judge only the text of the results. Do not use your own knowledge to fill gaps.

If "yes" or "partial", give supporting_result_index (the result number) and quote: a short
span copied character for character from that result that shows the evidence. Do not
paraphrase, fix typos or join text from different places. If "no", set both to null."""


class GenerateContent(Protocol):
    async def generate_content(
        self, *, model: str, contents: str, config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse: ...


@dataclass(frozen=True)
class Grounded:
    supported: Support
    hallucinated: bool


def build_prompt(task: Task, results: Sequence[Result]) -> str:
    blocks = "\n\n".join(
        f"Result {i}\nTitle: {r.title}\nText: {r.content}" for i, r in enumerate(results, start=1)
    )
    return PROMPT.format(
        query=task.query,
        gold_answer=task.gold_answer,
        gold_evidence=task.gold_evidence,
        results=blocks,
    )


def ground(judgment: Judgment, results: Sequence[Result]) -> Grounded:
    """Keep a positive verdict only if its quote is literally in the cited result.

    The title counts as part of the result: the judge is shown it, and so is an agent.
    """
    if judgment.supported == "no":
        return Grounded("no", hallucinated=False)
    index, quote = judgment.supporting_result_index, (judgment.quote or "").strip()
    if (
        index is not None
        and index <= len(results)
        and quote
        and (quote in results[index - 1].content or quote in results[index - 1].title)
    ):
        return Grounded(judgment.supported, hallucinated=False)
    return Grounded("no", hallucinated=True)


def _unreverse(judgment: Judgment, n: int) -> Judgment:
    index = judgment.supporting_result_index
    if index is None or index > n:
        return judgment
    return judgment.model_copy(update={"supporting_result_index": n + 1 - index})


class Judge:
    def __init__(
        self,
        models: GenerateContent,
        model: str,
        seed: int,
        temperature: float,
        cache: JsonlCache[Judgment],
    ) -> None:
        self.model = model
        self._models = models
        self._seed = seed
        self._temperature = temperature
        self._cache = cache
        self._config = types.GenerateContentConfig(
            temperature=temperature,
            seed=seed,
            response_mime_type="application/json",
            response_schema=Judgment,
        )

    def lookup(
        self, task: Task, results: Sequence[Result], *, reverse: bool = False
    ) -> Judgment | None:
        """The cached judgment, if any; never calls the model."""
        if not results:
            return Judgment(supported="no")
        shown = self._order(results, reverse)
        judgment = self._cache.get(self._key(task, shown))
        return None if judgment is None else self._restore(judgment, len(shown), reverse)

    async def judge(
        self, task: Task, results: Sequence[Result], *, reverse: bool = False
    ) -> Judgment:
        """Judge the results; with reverse=True they are shown last-first, and the
        returned index still refers to the original order."""
        cached = self.lookup(task, results, reverse=reverse)
        if cached is not None:
            return cached
        shown = self._order(results, reverse)
        response = await self._models.generate_content(
            model=self.model, contents=build_prompt(task, shown), config=self._config
        )
        judgment = Judgment.model_validate_json(response.text or "")
        self._cache.put(self._key(task, shown), judgment)
        return self._restore(judgment, len(shown), reverse)

    def _key(self, task: Task, shown: Sequence[Result]) -> str:
        return cache_key(self.model, self._seed, self._temperature, build_prompt(task, shown))

    @staticmethod
    def _order(results: Sequence[Result], reverse: bool) -> list[Result]:
        return list(reversed(results)) if reverse else list(results)

    @staticmethod
    def _restore(judgment: Judgment, n: int, reverse: bool) -> Judgment:
        return _unreverse(judgment, n) if reverse else judgment


def judgment_cache(snapshot_dir: Path, model: str) -> JsonlCache[Judgment]:
    return JsonlCache(snapshot_dir / "judgments" / f"{model}.jsonl", Judgment)


@dataclass(frozen=True)
class Pair:
    task: Task
    vendor: str
    response: VendorResponse


def snapshot_pairs(
    tasks: Sequence[Task], vendors: Sequence[str], snapshot_dir: Path, k: int, max_chars: int
) -> list[Pair]:
    """Every (task, vendor) pair with a stored response, in a fixed order."""
    pairs = []
    for vendor in sorted(vendors):
        cache = response_cache(snapshot_dir, vendor)
        for task in tasks:
            response = cache.get(response_key(vendor, task.query, k, max_chars))
            if response is not None:
                pairs.append(Pair(task, vendor, response))
    return pairs


def order_bias_sample(pairs: Sequence[Pair], n: int, seed: int) -> list[Pair]:
    eligible = [p for p in pairs if len(p.response.results) >= 2]
    return random.Random(seed).sample(eligible, min(n, len(eligible)))


@dataclass(frozen=True)
class JudgeSummary:
    pairs: int
    reversed: int


async def judge_snapshot(
    pairs: Sequence[Pair], primary: Judge, second: Judge, concurrency: int, seed: int
) -> JudgeSummary:
    """Both judges see every pair; the primary also re-judges a sample in reverse order."""
    sample = order_bias_sample(pairs, ORDER_BIAS_SAMPLE, seed)
    gate = asyncio.Semaphore(concurrency)

    async def one(judge: Judge, pair: Pair, reverse: bool) -> None:
        async with gate:
            await judge.judge(pair.task, pair.response.results, reverse=reverse)

    async with asyncio.TaskGroup() as group:
        for pair in pairs:
            group.create_task(one(primary, pair, False))
            group.create_task(one(second, pair, False))
        for pair in sample:
            group.create_task(one(primary, pair, True))
    return JudgeSummary(pairs=len(pairs), reversed=len(sample))
