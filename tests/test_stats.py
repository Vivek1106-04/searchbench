import math

import pytest

from searchbench.stats import (
    Interval,
    bootstrap_ci,
    cohen_kappa,
    paired_bootstrap_ci,
    percentile,
)


def test_constant_values_have_a_zero_width_interval() -> None:
    assert bootstrap_ci([0.5] * 60) == Interval(0.5, 0.5, 0.5)


def test_bootstrap_matches_the_normal_approximation_for_a_proportion() -> None:
    values = [1.0] * 30 + [0.0] * 30
    standard_error = math.sqrt(0.5 * 0.5 / 60)

    ci = bootstrap_ci(values)

    assert ci.estimate == 0.5
    assert ci.low == pytest.approx(0.5 - 1.96 * standard_error, abs=0.02)
    assert ci.high == pytest.approx(0.5 + 1.96 * standard_error, abs=0.02)


def test_bootstrap_is_reproducible_with_a_fixed_seed() -> None:
    values = [0.13, 0.91, 0.42, 0.77, 0.05, 0.66, 0.38, 0.29, 0.84, 0.51]

    assert bootstrap_ci(values) == bootstrap_ci(values)
    assert bootstrap_ci(values, seed=1) != bootstrap_ci(values, seed=2)


def test_bootstrap_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="at least one value"):
        bootstrap_ci([])


def test_paired_difference_of_identical_vendors_is_not_significant() -> None:
    a = [1.0, 0.0, 0.5, 1.0]

    ci = paired_bootstrap_ci(a, a)

    assert ci == Interval(0.0, 0.0, 0.0)
    assert not ci.excludes_zero


def test_paired_difference_that_always_favours_a_is_significant() -> None:
    ci = paired_bootstrap_ci([1.0] * 10, [0.0] * 10)

    assert ci == Interval(1.0, 1.0, 1.0)
    assert ci.excludes_zero


def test_paired_bootstrap_resamples_tasks_not_vendors() -> None:
    a = [1.0, 1.0, 0.0, 0.0]
    b = [0.0, 0.0, 1.0, 1.0]

    ci = paired_bootstrap_ci(a, b)

    assert ci.estimate == 0.0
    assert ci.low < 0 < ci.high


@pytest.mark.parametrize(
    ("interval", "excludes"),
    [
        (Interval(0.1, 0.01, 0.2), True),
        (Interval(-0.1, -0.2, -0.01), True),
        (Interval(0.0, -0.1, 0.1), False),
    ],
)
def test_excludes_zero(interval: Interval, excludes: bool) -> None:
    assert interval.excludes_zero is excludes


def test_paired_bootstrap_rejects_unequal_lengths() -> None:
    with pytest.raises(ValueError, match="differ in length"):
        paired_bootstrap_ci([1.0], [1.0, 0.0])


def test_kappa_two_categories_hand_computed() -> None:
    # 50 items: both yes 20, a yes / b no 5, a no / b yes 10, both no 15.
    # p_o = 35/50 = 0.7; p_e = 0.5*0.6 + 0.5*0.4 = 0.5; kappa = 0.2/0.5 = 0.4
    a = ["yes"] * 25 + ["no"] * 25
    b = ["yes"] * 20 + ["no"] * 5 + ["yes"] * 10 + ["no"] * 15

    assert cohen_kappa(a, b) == pytest.approx(0.4)


def test_kappa_three_categories_hand_computed() -> None:
    # 10 items, agree on 7. a: yes 4, partial 3, no 3; b: yes 5, partial 2, no 3.
    # p_o = 0.7; p_e = 0.4*0.5 + 0.3*0.2 + 0.3*0.3 = 0.35; kappa = 0.35/0.65
    a = ["yes", "yes", "yes", "yes", "partial", "partial", "partial", "no", "no", "no"]
    b = ["yes", "yes", "yes", "partial", "partial", "yes", "yes", "no", "no", "no"]

    assert cohen_kappa(a, b) == pytest.approx(0.35 / 0.65)


def test_kappa_perfect_and_chance_agreement() -> None:
    assert cohen_kappa(["yes", "no", "partial"], ["yes", "no", "partial"]) == 1.0
    assert cohen_kappa(["yes", "no"], ["no", "yes"]) == pytest.approx(-1.0)


def test_kappa_when_both_raters_use_one_label() -> None:
    assert cohen_kappa(["no"] * 5, ["no"] * 5) == 1.0


@pytest.mark.parametrize(("a", "b"), [([], []), (["yes"], ["yes", "no"])])
def test_kappa_rejects_bad_input(a: list[str], b: list[str]) -> None:
    with pytest.raises(ValueError):
        cohen_kappa(a, b)


def test_percentile_interpolates_linearly() -> None:
    values = [100.0, 200.0, 300.0, 400.0, 500.0]

    assert percentile(values, 50) == 300.0
    assert percentile(values, 95) == pytest.approx(480.0)


def test_percentile_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        percentile([], 50)
