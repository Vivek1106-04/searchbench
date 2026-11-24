import json
import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path

from searchbench.judge import RUBRIC, Pair
from searchbench.models import HumanLabel, Support

LABELS_PER_CATEGORY = 8
ANSWERS: dict[str, Support] = {"y": "yes", "p": "partial", "n": "no"}
QUIT = "q"


def label_sample(
    pairs: Sequence[Pair], verdicts: Sequence[Support], per_category: int, seed: int
) -> list[Pair]:
    """A seeded sample with the same number of pairs from every category, in mixed order.

    Within a category, pairs are drawn in turn from each judge verdict, so rare verdicts
    are labelled too; with nearly all labels "yes", kappa cannot be estimated.
    """
    rng = random.Random(seed)
    groups: dict[str, dict[Support, list[Pair]]] = defaultdict(lambda: defaultdict(list))
    for pair, verdict in zip(pairs, verdicts, strict=True):
        groups[pair.task.category][verdict].append(pair)
    sample: list[Pair] = []
    for category in sorted(groups):
        pools = [rng.sample(pool, len(pool)) for _, pool in sorted(groups[category].items())]
        picked = 0
        while picked < per_category and any(pools):
            for pool in pools:
                if pool and picked < per_category:
                    sample.append(pool.pop())
                    picked += 1
    rng.shuffle(sample)
    return sample


def load_labels(path: Path) -> list[HumanLabel]:
    if not path.exists():
        return []
    return [HumanLabel.model_validate_json(line) for line in path.read_text().splitlines() if line]


def render(pair: Pair, number: int, total: int) -> str:
    """What the judge saw, without URLs or the vendor name."""
    task = pair.task
    lines = [
        f"--- pair {number} of {total} ---",
        f"Question: {task.query}",
        f"Known answer: {task.gold_answer}",
        f"Example evidence: {task.gold_evidence}",
        "",
        RUBRIC,
        "",
    ]
    for i, result in enumerate(pair.response.results, start=1):
        lines += [f"[{i}] {result.title}", f"    {result.content}", ""]
    if not pair.response.results:
        lines.append("(no results)")
    return "\n".join(lines)


def run_labeling(
    sample: Sequence[Pair],
    snapshot: str,
    path: Path,
    ask: Callable[[str], str],
    show: Callable[[str], None],
    round_number: int = 1,
) -> int:
    """Ask for a label on every unlabelled pair; returns how many were recorded."""
    done = {
        (label.task_id, label.vendor) for label in load_labels(path) if label.snapshot == snapshot
    }
    todo = [pair for pair in sample if (pair.task.id, pair.vendor) not in done]
    path.parent.mkdir(parents=True, exist_ok=True)
    for recorded, pair in enumerate(todo):
        show(render(pair, len(sample) - len(todo) + recorded + 1, len(sample)))
        answer = ask("Supported? [y]es / [p]artial / [n]o / [q]uit").strip().lower()
        while answer not in ANSWERS and answer != QUIT:
            answer = ask("Type y, p, n or q").strip().lower()
        if answer == QUIT:
            return recorded
        label = HumanLabel(
            snapshot=snapshot,
            task_id=pair.task.id,
            vendor=pair.vendor,
            supported=ANSWERS[answer],
            round=round_number,
        )
        with path.open("a", encoding="ascii") as f:
            f.write(json.dumps(label.model_dump(), ensure_ascii=True) + "\n")
    return len(todo)
