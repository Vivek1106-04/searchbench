from pathlib import Path

from searchbench.drift import Flip, compare, render
from tests.snapshots import config, task, verdict, write_snapshot


def test_drift_flags_changes_and_separates_fresh(tmp_path: Path) -> None:
    ts = [task(i) for i in range(8)] + [task(20 + i, "fresh") for i in range(4)]
    cfg = config(("alpha", "beta", "gamma"))
    before = {(v, t.id): verdict("yes", t) for v in ("alpha", "beta") for t in ts}
    after = {("alpha", t.id): verdict("no" if t.category == "factual" else "yes", t) for t in ts}
    after |= {("beta", t.id): verdict("yes", t) for t in ts}
    after |= {("gamma", t.id): verdict("yes", t) for t in ts}
    write_snapshot(tmp_path / "a", ts, before, cfg=cfg)
    write_snapshot(tmp_path / "b", ts, after, cfg=cfg)

    rows = compare(tmp_path / "a", tmp_path / "b", ts, cfg)

    assert [(r.vendor, r.group, r.n) for r in rows] == [
        ("alpha", "other", 8),
        ("alpha", "fresh", 4),
        ("beta", "other", 8),
        ("beta", "fresh", 4),
    ]
    alpha_other = rows[0]
    assert alpha_other.change.estimate == -1.0
    assert alpha_other.change.excludes_zero
    assert alpha_other.flips[0] == Flip("t00", "yes", "no")
    assert not rows[1].change.excludes_zero
    assert rows[1].flips == []


def test_partial_changes_are_not_listed_as_flips(tmp_path: Path) -> None:
    ts = [task(i) for i in range(3)]
    cfg = config(("alpha",))
    write_snapshot(tmp_path / "a", ts, {("alpha", t.id): verdict("yes", t) for t in ts}, cfg=cfg)
    write_snapshot(
        tmp_path / "b", ts, {("alpha", t.id): verdict("partial", t) for t in ts}, cfg=cfg
    )

    rows = compare(tmp_path / "a", tmp_path / "b", ts, cfg)

    assert [(r.group, r.flips) for r in rows] == [("other", [])]


def test_render_marks_flagged_rows_and_explains_fresh(tmp_path: Path) -> None:
    ts = [task(i) for i in range(4)]
    cfg = config(("alpha",))
    write_snapshot(tmp_path / "a", ts, {("alpha", t.id): verdict("no", t) for t in ts}, cfg=cfg)
    write_snapshot(tmp_path / "b", ts, {("alpha", t.id): verdict("yes", t) for t in ts}, cfg=cfg)

    text = render("snap-a", "snap-b", compare(tmp_path / "a", tmp_path / "b", ts, cfg))

    assert text.startswith("# Drift: snap-a -> snap-b\n")
    assert "| alpha | other | 4 | +1.00 [+1.00, +1.00] | yes | t00 (no -> yes)," in text
    assert "drift there may be the world changing" in text


def test_render_with_no_flips_shows_a_dash(tmp_path: Path) -> None:
    ts = [task(i) for i in range(2)]
    cfg = config(("alpha",))
    same = {("alpha", t.id): verdict("yes", t) for t in ts}
    write_snapshot(tmp_path / "a", ts, same, cfg=cfg)
    write_snapshot(tmp_path / "b", ts, same, cfg=cfg)

    text = render("a", "b", compare(tmp_path / "a", tmp_path / "b", ts, cfg))

    assert "| alpha | other | 2 | +0.00 [+0.00, +0.00] | no | - |" in text
