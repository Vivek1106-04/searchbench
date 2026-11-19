from pathlib import Path

import pytest

from searchbench.cache import JsonlCache, cache_key
from searchbench.models import Judgment


def test_cache_key_is_stable_and_ignores_dict_order() -> None:
    a = cache_key("tavily", "q", {"k": 5, "chars": 2000})
    b = cache_key("tavily", "q", {"chars": 2000, "k": 5})

    assert a == b
    assert len(a) == 64
    assert cache_key("exa", "q", {"k": 5, "chars": 2000}) != a


def test_missing_file_is_empty_cache(tmp_path: Path) -> None:
    cache = JsonlCache(tmp_path / "none.jsonl", Judgment)

    assert len(cache) == 0
    assert cache.get("k") is None


def test_entries_survive_reopen(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "judge.jsonl"
    value = Judgment(supported="yes", supporting_result_index=2, quote="5432")
    JsonlCache(path, Judgment).put("k1", value)

    assert JsonlCache(path, Judgment).get("k1") == value


def test_later_entry_for_same_key_wins(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    cache = JsonlCache(path, Judgment)
    cache.put("k", Judgment(supported="no"))
    cache.put("k", Judgment(supported="partial"))

    reopened = JsonlCache(path, Judgment)
    assert len(reopened) == 1
    assert reopened.get("k") == Judgment(supported="partial")


def test_partial_last_line_from_crash_is_dropped(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    JsonlCache(path, Judgment).put("done", Judgment(supported="no"))
    with path.open("a") as f:
        f.write('{"key": "half", "val')

    cache = JsonlCache(path, Judgment)
    assert cache.get("half") is None
    cache.put("next", Judgment(supported="yes"))

    reopened = JsonlCache(path, Judgment)
    assert reopened.get("done") == Judgment(supported="no")
    assert reopened.get("next") == Judgment(supported="yes")


@pytest.mark.parametrize(
    "line",
    [
        "not json",
        '{"value": {"supported": "no"}}',
        '{"key": "k", "value": {"supported": "maybe"}}',
        "[1, 2]",
    ],
)
def test_corrupt_complete_line_raises_with_location(tmp_path: Path, line: str) -> None:
    path = tmp_path / "c.jsonl"
    path.write_text(line + "\n")

    with pytest.raises(ValueError, match=r"c\.jsonl:1: corrupt cache line"):
        JsonlCache(path, Judgment)
