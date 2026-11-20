from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from searchbench.judge import Grounded, Pair, ground, judgment_cache, lookup_judgment
from searchbench.models import Judgment, Support

SCORES: dict[Support, float] = {"yes": 1.0, "partial": 0.5, "no": 0.0}


def on_domain(url: str, domains: Sequence[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


@dataclass(frozen=True)
class Scored:
    pair: Pair
    judgment: Judgment
    grounded: Grounded

    @property
    def score(self) -> float:
        return SCORES[self.grounded.supported]

    @property
    def domain_hit(self) -> bool:
        domains = self.pair.task.gold_domains
        return any(on_domain(r.url, domains) for r in self.pair.response.results)

    @property
    def rank(self) -> int | None:
        """Position of the result the judge cited, when the verdict survived grounding."""
        if self.grounded.supported == "no":
            return None
        return self.judgment.supporting_result_index


def score_pairs(
    pairs: Sequence[Pair],
    snapshot_dir: Path,
    model: str,
    seed: int,
    temperature: float,
    *,
    reverse: bool = False,
) -> list[Scored]:
    cache = judgment_cache(snapshot_dir, model)
    scored = []
    for pair in pairs:
        results = pair.response.results
        judgment = lookup_judgment(
            cache, model, seed, temperature, pair.task, results, reverse=reverse
        )
        if judgment is None:
            raise ValueError(
                f"no {model} judgment for {pair.task.id}/{pair.vendor} in {snapshot_dir}; "
                "run `bench judge` first"
            )
        scored.append(Scored(pair, judgment, ground(judgment, results)))
    return scored
