from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from statistics import mean, median

from searchbench.config import Config
from searchbench.judge import ORDER_BIAS_SAMPLE, order_bias_sample, snapshot_pairs
from searchbench.models import Category, HumanLabel, Support, Task
from searchbench.scoring import Scored, score_pairs
from searchbench.stats import Interval, bootstrap_ci, cohen_kappa, paired_bootstrap_ci, percentile

KAPPA_FLOOR = 0.6
CATEGORIES: tuple[Category, ...] = ("factual", "technical_docs", "fresh", "multi_hop", "long_tail")
PLOT_NAME = "cost_vs_support.png"


@dataclass(frozen=True)
class VendorStats:
    vendor: str
    support: Interval
    domain_hit: Interval
    mean_rank: float | None
    ranked: int
    latency_p50: float | None
    latency_p95: float | None
    cost_per_query: float
    error_rate: float
    median_chars: float


@dataclass(frozen=True)
class Agreement:
    n: int
    kappa: float
    raw: float


def ci(interval: Interval) -> str:
    return f"{interval.estimate:.2f} [{interval.low:.2f}, {interval.high:.2f}]"


def ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}"


def vendor_stats(vendor: str, scored: Sequence[Scored]) -> VendorStats:
    ok = [s.pair.response for s in scored if s.pair.response.error is None]
    latencies = [r.latency_ms for r in ok]
    ranks = [s.rank for s in scored if s.rank is not None]
    chars = [len(r.content) for s in scored for r in s.pair.response.results]
    return VendorStats(
        vendor=vendor,
        support=bootstrap_ci([s.score for s in scored]),
        domain_hit=bootstrap_ci([float(s.domain_hit) for s in scored]),
        mean_rank=mean(ranks) if ranks else None,
        ranked=len(ranks),
        latency_p50=percentile(latencies, 50) if latencies else None,
        latency_p95=percentile(latencies, 95) if latencies else None,
        cost_per_query=mean(s.pair.response.cost_usd for s in scored),
        error_rate=1 - len(ok) / len(scored),
        median_chars=median(chars) if chars else 0.0,
    )


def agreement(a: Sequence[Support], b: Sequence[Support]) -> Agreement:
    raw = sum(x == y for x, y in zip(a, b, strict=True)) / len(a)
    return Agreement(n=len(a), kappa=cohen_kappa(a, b), raw=raw)


def by_vendor(scored: Sequence[Scored]) -> dict[str, list[Scored]]:
    groups: dict[str, list[Scored]] = {}
    for s in scored:
        groups.setdefault(s.pair.vendor, []).append(s)
    return dict(sorted(groups.items()))


def hallucination_line(model: str, scored: Sequence[Scored]) -> str:
    positive = [s for s in scored if s.judgment.supported != "no"]
    failed = sum(s.grounded.hallucinated for s in positive)
    rate = failed / len(positive) if positive else 0.0
    return f"| {model} | {failed} of {len(positive)} | {rate:.1%} |"


def build_report(
    snapshot_id: str,
    snapshot_dir: Path,
    tasks: Sequence[Task],
    config: Config,
    labels: Sequence[HumanLabel],
) -> tuple[str, list[VendorStats]]:
    """REPORT.md for one snapshot; a pure function of the stored files."""
    judge = config.judge
    pairs = snapshot_pairs(
        tasks, list(config.vendors), snapshot_dir, config.run.k, config.run.max_content_chars
    )
    if not pairs:
        raise ValueError(f"no stored responses in {snapshot_dir}")
    primary = score_pairs(pairs, snapshot_dir, judge.primary_model, judge.seed, judge.temperature)
    second = score_pairs(pairs, snapshot_dir, judge.second_model, judge.seed, judge.temperature)
    sample = order_bias_sample(pairs, ORDER_BIAS_SAMPLE, judge.seed)
    forward = score_pairs(sample, snapshot_dir, judge.primary_model, judge.seed, judge.temperature)
    backward = score_pairs(
        sample, snapshot_dir, judge.primary_model, judge.seed, judge.temperature, reverse=True
    )
    groups = by_vendor(primary)
    stats = [vendor_stats(v, s) for v, s in groups.items()]

    index = {(s.pair.task.id, s.pair.vendor): i for i, s in enumerate(primary)}
    mine = [label for label in labels if label.snapshot == snapshot_id]
    missing = [(lb.task_id, lb.vendor) for lb in mine if (lb.task_id, lb.vendor) not in index]
    if missing:
        raise ValueError(f"human labels for pairs not in {snapshot_dir}: {missing}")
    labelled = [index[(label.task_id, label.vendor)] for label in mine]
    human = [label.supported for label in mine]
    vs_human = agreement(human, [primary[i].grounded.supported for i in labelled]) if mine else None
    second_vs_human = (
        agreement(human, [second[i].grounded.supported for i in labelled]) if mine else None
    )
    between = agreement(
        [s.grounded.supported for s in primary], [s.grounded.supported for s in second]
    )
    flips = sum(
        f.grounded.supported != b.grounded.supported for f, b in zip(forward, backward, strict=True)
    )

    lines = [f"# searchbench report: {snapshot_id}", ""]
    if vs_human is None or vs_human.kappa < KAPPA_FLOOR:
        shown = "no human labels" if vs_human is None else f"kappa = {vs_human.kappa:.2f}"
        lines += [
            f"> **Warning: judge agreement with human labels is below {KAPPA_FLOOR} "
            f"({shown}).** Treat support@5 differences as provisional. See Judge validation.",
            "",
        ]
    lines += [
        f"{len(tasks)} tasks, {len(groups)} vendors, k = {config.run.k}, content cut to "
        f"{config.run.max_content_chars} characters. Primary judge `{judge.primary_model}`, "
        f"second judge `{judge.second_model}`. 95% CIs by bootstrap over tasks.",
        "",
        "## Headline",
        "",
        "| vendor | support@5 | domain_hit@5 | cited rank (n) | p50 ms | p95 ms "
        "| USD / query | error rate | median chars |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for st in stats:
        rank = "n/a" if st.mean_rank is None else f"{st.mean_rank:.2f} ({st.ranked})"
        lines.append(
            f"| {st.vendor} | {ci(st.support)} | {ci(st.domain_hit)} | {rank} "
            f"| {ms(st.latency_p50)} | {ms(st.latency_p95)} | {st.cost_per_query:.4f} "
            f"| {st.error_rate:.1%} | {st.median_chars:.0f} |"
        )
    lines += [
        "",
        f"![cost vs support]({PLOT_NAME})",
        "",
        "## Pairwise support@5 (paired bootstrap)",
        "",
        "| comparison | difference | verdict |",
        "|---|---|---|",
    ]
    for a, b in combinations(groups, 2):
        by_task_b = {s.pair.task.id: s.score for s in groups[b]}
        common = [s for s in groups[a] if s.pair.task.id in by_task_b]
        diff = paired_bootstrap_ci(
            [s.score for s in common], [by_task_b[s.pair.task.id] for s in common]
        )
        verdict = "significant" if diff.excludes_zero else "too close to call"
        lines.append(f"| {a} - {b} | {ci(diff)} | {verdict} |")
    lines += [
        "",
        f"With {len(tasks)} tasks, a difference is called significant only when its 95% CI "
        "excludes 0. Rows marked too close to call cannot be resolved by this suite.",
        "",
        "## support@5 by category",
        "",
        "| category | " + " | ".join(groups) + " |",
        "|---|" + "---|" * len(groups),
    ]
    for category in CATEGORIES:
        cells = []
        for scored in groups.values():
            values = [s.score for s in scored if s.pair.task.category == category]
            cells.append(ci(bootstrap_ci(values)) if values else "n/a")
        lines.append(f"| {category} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "Each cell has at most 12 tasks, so these intervals are wide. Changes in `fresh` "
        "can reflect the world changing, not only the vendor.",
        "",
        "## Judge validation",
        "",
        "| comparison | n | Cohen's kappa | raw agreement |",
        "|---|---|---|---|",
    ]
    for name, result in (
        (f"{judge.primary_model} vs human", vs_human),
        (f"{judge.second_model} vs human", second_vs_human),
        (f"{judge.primary_model} vs {judge.second_model}", between),
    ):
        if result is not None:
            lines.append(f"| {name} | {result.n} | {result.kappa:.2f} | {result.raw:.0%} |")
    yes_share = human.count("yes") / len(human) if human else 0.0
    lines += [
        "",
        f"Human labels: {len(human)} pairs, stratified by category, {yes_share:.0%} labelled yes. "
        "When one label dominates, chance agreement is high and kappa stays low even with "
        "high raw agreement, so both are shown.",
        "",
        "| judge | quotes not found in the cited result | hallucination rate |",
        "|---|---|---|",
        hallucination_line(judge.primary_model, primary),
        hallucination_line(judge.second_model, second),
        "",
        "A yes or partial verdict counts only if its quote appears verbatim in the title or "
        "text of the result it cites; otherwise it is scored as no.",
        "",
        f"Order bias: {flips} of {len(sample)} sampled pairs ({flips / len(sample):.0%}) changed "
        f"verdict when the results were shown in reverse order to `{judge.primary_model}`."
        if sample
        else "Order bias: no pairs with two or more results to test.",
        "",
        "## Threats to validity",
        "",
        f"- {len(tasks)} tasks is small: most differences between vendors are within noise.",
        "- One judge family: Gemini judges results that may include Google-indexed content.",
        "- Gold answers can go stale; each task records the date it was verified (`as_of`).",
        "- Free-tier access may differ from paid tiers in ranking, freshness or latency.",
        f"- Content is cut to {config.run.max_content_chars} characters, and each vendor is "
        "judged on its default output: vendors that return short snippets give the judge less "
        "text to quote (see median chars).",
        "- Cited rank is the result the judge quoted, which is not always the first result "
        "that supports the answer.",
        "- The primary judge is a thinking model whose wording can change between runs; "
        "judgments are read from the committed cache, never re-run.",
        "- One snapshot in time: results change as indexes and the web change.",
        "",
    ]
    return "\n".join(lines), stats


def write_plot(stats: Sequence[VendorStats], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6, 4), dpi=100)
    for st in stats:
        ax.errorbar(
            st.cost_per_query,
            st.support.estimate,
            yerr=[[st.support.estimate - st.support.low], [st.support.high - st.support.estimate]],
            fmt="o",
            capsize=4,
        )
        ax.annotate(
            st.vendor,
            (st.cost_per_query, st.support.estimate),
            xytext=(6, 6),
            textcoords="offset points",
        )
    ax.set_xlabel("USD per query (list price)")
    ax.set_ylabel("support@5 (95% CI)")
    ax.set_ylim(0, 1.05)
    ax.margins(x=0.2)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, format="png", metadata={"Software": None})
    plt.close(fig)
