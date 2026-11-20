from pathlib import Path

import pytest

from searchbench.models import HumanLabel, Judgment, Task
from searchbench.report import KAPPA_FLOOR, build_report, write_plot
from tests.snapshots import config, task, verdict, write_snapshot

SNAP = "2026-11-20_abc1234"


def tasks() -> list[Task]:
    return [task(i) for i in range(6)] + [task(10 + i, "fresh") for i in range(4)]


def alpha_yes_beta_half(ts: list[Task]) -> dict[tuple[str, str], Judgment]:
    judgments = {("alpha", t.id): verdict("yes", t) for t in ts}
    judgments |= {("beta", t.id): verdict("yes" if i % 2 else "no", t) for i, t in enumerate(ts)}
    return judgments


def labels_matching(ts: list[Task]) -> list[HumanLabel]:
    return [
        HumanLabel(snapshot=SNAP, task_id=t.id, vendor="beta", supported="yes" if i % 2 else "no")
        for i, t in enumerate(ts)
    ]


def test_headline_pairwise_and_category_tables(tmp_path: Path) -> None:
    ts = tasks()
    write_snapshot(tmp_path, ts, alpha_yes_beta_half(ts))

    text, stats = build_report(SNAP, tmp_path, ts, config(), labels_matching(ts))

    assert text.startswith(f"# searchbench report: {SNAP}\n")
    assert "Warning" not in text
    assert (
        "| alpha | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 (10) "
        "| 100 | 100 | 0.0100 | 0.0% | 34 |" in text
    )
    assert "| alpha - beta | 0.50 [" in text
    assert "| significant |" in text
    assert "| fresh | 1.00 [1.00, 1.00] | 0.50 [" in text
    assert "| multi_hop | n/a | n/a |" in text
    assert "| judge-pro vs human | 10 | 1.00 | 100% |" in text
    assert "Order bias: 0 of 20 sampled pairs (0%)" in text
    assert [s.vendor for s in stats] == ["alpha", "beta"]


def test_report_is_byte_identical_on_rebuild(tmp_path: Path) -> None:
    ts = tasks()
    write_snapshot(tmp_path, ts, alpha_yes_beta_half(ts))

    first, _ = build_report(SNAP, tmp_path, ts, config(), labels_matching(ts))
    second, _ = build_report(SNAP, tmp_path, ts, config(), labels_matching(ts))

    assert first == second


def test_low_kappa_puts_a_warning_at_the_top(tmp_path: Path) -> None:
    ts = tasks()
    write_snapshot(tmp_path, ts, alpha_yes_beta_half(ts))
    disagreeing = [
        label.model_copy(update={"supported": "partial"}) for label in labels_matching(ts)
    ]

    text, _ = build_report(SNAP, tmp_path, ts, config(), disagreeing)

    assert text.splitlines()[2].startswith(
        f"> **Warning: judge agreement with human labels is below {KAPPA_FLOOR}"
    )


def test_missing_labels_also_warn_and_skip_human_rows(tmp_path: Path) -> None:
    ts = tasks()
    write_snapshot(tmp_path, ts, alpha_yes_beta_half(ts))
    other_snapshot = [
        label.model_copy(update={"snapshot": "other"}) for label in labels_matching(ts)
    ]

    text, _ = build_report(SNAP, tmp_path, ts, config(), other_snapshot)

    assert "(no human labels)" in text
    assert "vs human" not in text
    assert "| judge-pro vs judge-lite | 20 |" in text


def test_hallucinations_and_errors_are_reported(tmp_path: Path) -> None:
    ts = tasks()[:4]
    judgments = {("alpha", t.id): verdict("yes", t, quote="invented") for t in ts}
    judgments |= {("beta", t.id): verdict("yes", t) for t in ts}
    write_snapshot(tmp_path, ts, judgments, errors=[("beta", "t00"), ("beta", "t01")])

    text, stats = build_report(SNAP, tmp_path, ts, config(), [])

    assert "| judge-pro | 4 of 6 | 66.7% |" in text
    beta = next(s for s in stats if s.vendor == "beta")
    assert beta.error_rate == 0.5
    assert beta.support.estimate == 0.5


def test_vendor_with_only_errors_and_no_support(tmp_path: Path) -> None:
    ts = tasks()[:2]
    judgments = {("alpha", t.id): verdict("no", t) for t in ts}
    judgments |= {("beta", t.id): verdict("no", t) for t in ts}
    write_snapshot(tmp_path, ts, judgments, errors=[("beta", t.id) for t in ts], n_results=1)

    text, stats = build_report(SNAP, tmp_path, ts, config(), [])

    assert (
        "| beta | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] | n/a | n/a | n/a | 0.0000 | 100.0% | 0 |"
        in text
    )
    assert "Order bias: no pairs with two or more results to test." in text
    assert all(s.mean_rank is None for s in stats)


def test_labels_for_unknown_pairs_are_rejected(tmp_path: Path) -> None:
    ts = tasks()[:2]
    write_snapshot(tmp_path, ts, alpha_yes_beta_half(ts))
    stray = [HumanLabel(snapshot=SNAP, task_id="t99", vendor="alpha", supported="yes")]

    with pytest.raises(ValueError, match="human labels for pairs not in"):
        build_report(SNAP, tmp_path, ts, config(), stray)


def test_empty_snapshot_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no stored responses"):
        build_report(SNAP, tmp_path, tasks(), config(), [])


def test_plot_is_a_reproducible_png(tmp_path: Path) -> None:
    ts = tasks()
    write_snapshot(tmp_path, ts, alpha_yes_beta_half(ts))
    _, stats = build_report(SNAP, tmp_path, ts, config(), [])

    write_plot(stats, tmp_path / "a.png")
    write_plot(stats, tmp_path / "b.png")

    data = (tmp_path / "a.png").read_bytes()
    assert data.startswith(b"\x89PNG")
    assert data == (tmp_path / "b.png").read_bytes()
