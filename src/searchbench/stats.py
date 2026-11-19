from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

RESAMPLES = 10_000
SEED = 20261119
CONFIDENCE = 0.95


@dataclass(frozen=True)
class Interval:
    estimate: float
    low: float
    high: float

    @property
    def excludes_zero(self) -> bool:
        return self.low > 0 or self.high < 0


def bootstrap_ci(values: Sequence[float], resamples: int = RESAMPLES, seed: int = SEED) -> Interval:
    """Percentile bootstrap CI of the mean, resampling tasks with replacement."""
    if not values:
        raise ValueError("bootstrap needs at least one value")
    data = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    means = data[rng.integers(0, len(data), size=(resamples, len(data)))].mean(axis=1)
    tail = (1 - CONFIDENCE) / 2 * 100
    low, high = np.percentile(means, [tail, 100 - tail])
    return Interval(float(data.mean()), float(low), float(high))


def paired_bootstrap_ci(
    a: Sequence[float], b: Sequence[float], resamples: int = RESAMPLES, seed: int = SEED
) -> Interval:
    """CI of mean(a - b) over the same tasks; the difference is significant if it excludes 0."""
    if len(a) != len(b):
        raise ValueError(f"paired samples differ in length: {len(a)} != {len(b)}")
    return bootstrap_ci([x - y for x, y in zip(a, b, strict=True)], resamples, seed)


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    """Unweighted Cohen's kappa between two raters labelling the same items."""
    if len(a) != len(b):
        raise ValueError(f"rater label lists differ in length: {len(a)} != {len(b)}")
    if not a:
        raise ValueError("kappa needs at least one item")
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    expected = sum((a.count(c) / n) * (b.count(c) / n) for c in set(a) | set(b))
    if expected == 1:
        return 1.0
    return (observed - expected) / (1 - expected)


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("percentile needs at least one value")
    return float(np.percentile(np.asarray(values, dtype=float), q))
