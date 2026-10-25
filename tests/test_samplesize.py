"""Every reference value of knowledge/sample_size.md must be reproduced exactly."""

import pytest

from engine.samplesize import SampleSizeError, compute, prepare_params, worked_calculation


@pytest.mark.parametrize(
    ("formula", "params", "raw", "n1", "n2"),
    [
        ("single_proportion", {"p": 0.5, "d": 0.05, "alpha": 0.05}, 384.15, 385, None),
        ("single_mean", {"sigma": 10, "d": 2}, 96.04, 97, None),
        ("two_proportions", {"p1": 0.6, "p2": 0.4, "power": 0.8}, 96.92, 97, 97),
        ("two_means", {"sigma": 10, "delta": 5, "power": 0.8}, 62.79, 63, 63),
        ("paired_mean", {"sigma_d": 8, "delta": 4, "power": 0.8}, 31.40, 32, None),
        ("correlation", {"r": 0.3, "power": 0.8}, 84.93, 85, None),
        ("case_control_unmatched", {"p0": 0.3, "odds_ratio": 2, "power": 0.8}, 140.66, 141, 141),
    ],
)
def test_reference_values(formula, params, raw, n1, n2):
    result = compute(formula, params)
    assert round(result.raw_n1, 2) == pytest.approx(raw, abs=0.005)
    assert result.n1 == n1
    assert result.n2 == n2


def test_case_control_p1():
    result = compute("case_control_unmatched", {"p0": 0.3, "odds_ratio": 2})
    assert round(result.extra["p1"], 4) == 0.4615
    assert result.final_total == 282


def test_nonresponse_inflation_per_group():
    result = compute("two_proportions", {"p1": 0.6, "p2": 0.4}, rate=0.1)
    assert result.final_n1 == 108  # ceil(97 / 0.9) = ceil(107.8)
    assert result.final_total == 216
    assert compute("single_proportion", {"p": 0.5, "d": 0.05}, rate=10).final_n1 == 428  # 10 means 10%


def test_finite_population_correction():
    result = compute("single_proportion", {"p": 0.5, "d": 0.05, "N": 1000})
    assert result.n1 == 278  # 384.15 / (1 + 383.15/1000) = 277.7


def test_percentages_are_read_as_proportions():
    params, _ = prepare_params("two_proportions", {"p1": 60, "p2": 40})
    assert params["p1"] == 0.6 and params["p2"] == 0.4


def test_parameter_names_are_validated():
    params, notes = prepare_params("single_mean", {"sigma": 10, "d": 2, "banana": 3})
    assert "banana" not in params and any("banana" in n for n in notes)
    with pytest.raises(SampleSizeError, match="missing"):
        prepare_params("two_means", {"sigma": 10})
    with pytest.raises(SampleSizeError):
        compute("two_proportions", {"p1": 0.4, "p2": 0.4})


def test_worked_calculation_states_the_final_number():
    result = compute("single_proportion", {"p": 0.3, "d": 0.05}, rate=0.1)
    text_en = worked_calculation(result, {"p": "{P2}", "d": "convention"}, "en")
    assert "Final sample size: 359" in text_en and "{P2}" in text_en
    text_fa = worked_calculation(result, {"p": "{P2}"}, "fa")
    assert "حجم نمونه نهایی: 359" in text_fa
