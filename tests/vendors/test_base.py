from searchbench.vendors.base import normalize_content


def test_normalize_collapses_whitespace_and_strips() -> None:
    assert normalize_content("  a\n\n b\t c  ", 100) == "a b c"


def test_normalize_truncates_after_collapsing() -> None:
    assert normalize_content("a    b    c", 3) == "a b"


def test_normalize_empty() -> None:
    assert normalize_content(" \n ", 10) == ""
