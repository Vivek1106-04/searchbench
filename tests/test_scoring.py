from pathlib import Path

import pytest

from searchbench.judge import snapshot_pairs
from searchbench.scoring import on_domain, score_pairs
from tests.snapshots import PRIMARY, task, verdict, write_snapshot


@pytest.mark.parametrize(
    ("url", "hit"),
    [
        ("https://gold.example/x", True),
        ("https://docs.gold.example/x", True),
        ("https://GOLD.example/x", True),
        ("https://notgold.example/x", False),
        ("https://gold.example.evil.com/x", False),
        ("not a url", False),
    ],
)
def test_on_domain_matches_host_and_subdomains(url: str, hit: bool) -> None:
    assert on_domain(url, ["gold.example"]) is hit


def test_scored_pairs_expose_score_rank_and_domain_hit(tmp_path: Path) -> None:
    tasks = [task(1), task(2), task(3), task(4)]
    write_snapshot(
        tmp_path,
        tasks,
        {
            ("alpha", "t01"): verdict("yes", tasks[0], index=2),
            ("alpha", "t02"): verdict("partial", tasks[1]),
            ("alpha", "t03"): verdict("no", tasks[2]),
            ("alpha", "t04"): verdict("yes", tasks[3], quote="made up"),
        },
    )
    pairs = snapshot_pairs(tasks, ["alpha"], tmp_path, 5, 2000)

    scored = score_pairs(pairs, tmp_path, PRIMARY, 1, 0.0)

    assert [s.score for s in scored] == [1.0, 0.5, 0.0, 0.0]
    assert [s.rank for s in scored] == [2, 1, None, None]
    assert [s.grounded.hallucinated for s in scored] == [False, False, False, True]
    assert all(s.domain_hit for s in scored)


def test_missing_judgment_points_to_bench_judge(tmp_path: Path) -> None:
    tasks = [task(1)]
    write_snapshot(tmp_path, tasks, {("alpha", "t01"): verdict("yes", tasks[0])})
    pairs = snapshot_pairs(tasks, ["alpha"], tmp_path, 5, 2000)

    with pytest.raises(ValueError, match="run `bench judge` first"):
        score_pairs(pairs, tmp_path, "never-ran", 1, 0.0)
