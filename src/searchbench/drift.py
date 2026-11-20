from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from searchbench.config import Config
from searchbench.judge import snapshot_pairs
from searchbench.models import Task
from searchbench.scoring import Scored, score_pairs
from searchbench.stats import Interval, paired_bootstrap_ci

FRESH = "fresh"


@dataclass(frozen=True)
class Flip:
    task_id: str
    before: str
    after: str


@dataclass(frozen=True)
class VendorDrift:
    vendor: str
    group: str
    n: int
    change: Interval
    flips: list[Flip]


def _primary(
    snapshot_dir: Path, tasks: Sequence[Task], config: Config
) -> dict[tuple[str, str], Scored]:
    judge = config.judge
    pairs = snapshot_pairs(
        tasks, list(config.vendors), snapshot_dir, config.run.k, config.run.max_content_chars
    )
    scored = score_pairs(pairs, snapshot_dir, judge.primary_model, judge.seed, judge.temperature)
    return {(s.pair.vendor, s.pair.task.id): s for s in scored}


def compare(dir_a: Path, dir_b: Path, tasks: Sequence[Task], config: Config) -> list[VendorDrift]:
    """Per vendor, support@5 change from a to b on tasks present in both snapshots."""
    a, b = _primary(dir_a, tasks, config), _primary(dir_b, tasks, config)
    rows = []
    for vendor in sorted({v for v, _ in a} & {v for v, _ in b}):
        for group in ("other", FRESH):
            common = [
                t
                for t in tasks
                if (vendor, t.id) in a
                and (vendor, t.id) in b
                and (t.category == FRESH) == (group == FRESH)
            ]
            if not common:
                continue
            before = [a[(vendor, t.id)] for t in common]
            after = [b[(vendor, t.id)] for t in common]
            change = paired_bootstrap_ci([s.score for s in after], [s.score for s in before])
            flips = [
                Flip(x.pair.task.id, x.grounded.supported, y.grounded.supported)
                for x, y in zip(before, after, strict=True)
                if {x.grounded.supported, y.grounded.supported} == {"yes", "no"}
            ]
            rows.append(VendorDrift(vendor, group, len(common), change, flips))
    return rows


def render(snap_a: str, snap_b: str, rows: Sequence[VendorDrift]) -> str:
    lines = [
        f"# Drift: {snap_a} -> {snap_b}",
        "",
        "| vendor | tasks | n | support@5 change | flagged | yes <-> no flips |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        flips = ", ".join(f"{f.task_id} ({f.before} -> {f.after})" for f in row.flips) or "-"
        flagged = "yes" if row.change.excludes_zero else "no"
        c = row.change
        lines.append(
            f"| {row.vendor} | {row.group} | {row.n} | {c.estimate:+.2f} [{c.low:+.2f}, "
            f"{c.high:+.2f}] | {flagged} | {flips} |"
        )
    lines += [
        "",
        "A vendor is flagged when the 95% paired bootstrap CI of the change excludes 0. "
        "`fresh` tasks are reported separately: drift there may be the world changing "
        "(new releases, edited pages), not the vendor.",
        "",
    ]
    return "\n".join(lines)
