from collections import Counter
from datetime import date
from pathlib import Path

from searchbench.judge import Pair
from searchbench.label import label_sample, load_labels, render, run_labeling
from searchbench.models import Category, HumanLabel, Result, Task, VendorResponse

CATEGORIES: list[Category] = ["factual", "technical_docs", "fresh", "multi_hop", "long_tail"]


def pair(i: int, category: Category, vendor: str = "tavily", n_results: int = 2) -> Pair:
    task = Task(
        id=f"t{i}",
        category=category,
        query=f"question {i}",
        gold_answer="5432",
        gold_evidence="5432 by default",
        gold_domains=["postgresql.org"],
        as_of=date(2026, 11, 19),
        time_sensitive=False,
    )
    results = [
        Result(url=f"https://secret-{j}.example", title=f"Title {j}", content=f"Text {j}")
        for j in range(n_results)
    ]
    response = VendorResponse(results=results, latency_ms=1.0, cost_usd=0.0, raw={})
    return Pair(task, vendor, response)


def all_pairs() -> list[Pair]:
    return [
        pair(i * 10 + j, category, vendor)
        for i, category in enumerate(CATEGORIES)
        for j in range(12)
        for vendor in ("tavily", "exa", "firecrawl")
    ]


class Script:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.shown: list[str] = []

    def ask(self, message: str) -> str:
        return self.answers.pop(0)

    def show(self, text: str) -> None:
        self.shown.append(text)


def test_sample_is_stratified_seeded_and_mixed() -> None:
    sample = label_sample(all_pairs(), 8, seed=1)

    assert len(sample) == 40
    assert Counter(p.task.category for p in sample) == dict.fromkeys(CATEGORIES, 8)
    assert sample == label_sample(all_pairs(), 8, seed=1)
    assert len({p.task.category for p in sample[:8]}) > 1


def test_small_category_contributes_what_it_has() -> None:
    pairs = [pair(1, "fresh"), pair(2, "fresh"), *[pair(10 + i, "factual") for i in range(9)]]

    sample = label_sample(pairs, 8, seed=1)

    assert Counter(p.task.category for p in sample) == {"fresh": 2, "factual": 8}


def test_render_is_blind_to_urls_and_vendor() -> None:
    text = render(pair(1, "factual", vendor="firecrawl"), 3, 40)

    assert "pair 3 of 40" in text
    assert "Known answer: 5432" in text
    assert "[2] Title 1" in text
    assert "secret" not in text
    assert "firecrawl" not in text


def test_render_marks_empty_results() -> None:
    assert "(no results)" in render(pair(1, "factual", n_results=0), 1, 1)


def test_labels_are_recorded_and_bad_input_is_asked_again(tmp_path: Path) -> None:
    path = tmp_path / "labels" / "human_labels.jsonl"
    sample = [pair(1, "factual"), pair(2, "fresh")]
    script = Script("y", "maybe", "P")

    recorded = run_labeling(sample, "snap", path, script.ask, script.show)

    assert recorded == 2
    assert load_labels(path) == [
        HumanLabel(snapshot="snap", task_id="t1", vendor="tavily", supported="yes"),
        HumanLabel(snapshot="snap", task_id="t2", vendor="tavily", supported="partial"),
    ]


def test_quit_keeps_progress_and_resume_skips_labelled_pairs(tmp_path: Path) -> None:
    path = tmp_path / "human_labels.jsonl"
    sample = [pair(1, "factual"), pair(2, "fresh"), pair(3, "long_tail")]

    first = Script("n", "q")
    assert run_labeling(sample, "snap", path, first.ask, first.show) == 1
    second = Script("y", "y")
    assert run_labeling(sample, "snap", path, second.ask, second.show) == 2

    assert [label.task_id for label in load_labels(path)] == ["t1", "t2", "t3"]
    assert "pair 2 of 3" in second.shown[0]


def test_labels_from_another_snapshot_do_not_count_as_done(tmp_path: Path) -> None:
    path = tmp_path / "human_labels.jsonl"
    old = Script("y")
    run_labeling([pair(1, "factual")], "old", path, old.ask, old.show)

    new = Script("n")
    assert run_labeling([pair(1, "factual")], "new", path, new.ask, new.show) == 1


def test_missing_labels_file_is_empty(tmp_path: Path) -> None:
    assert load_labels(tmp_path / "none.jsonl") == []
