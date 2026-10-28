"""Sample-size formulas, computed by code (knowledge/sample_size.md).

The model never does arithmetic: it picks a formula and fills in the parameters, each with a source. This module
checks the parameter names, fills conventional α and power when missing, computes n, rounds up, inflates for
non-response or drop-out and writes the worked calculation the section writer must copy.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from statistics import NormalDist

CONVENTIONS = {"alpha": 0.05, "power": 0.80}
PROPORTION_PARAMS = {"p", "p0", "p1", "p2", "alpha", "power", "sensitivity", "specificity", "prevalence"}


class SampleSizeError(ValueError):
    """The parameters cannot produce a sample size (missing, out of range, or identical groups)."""


@dataclass(frozen=True)
class Formula:
    """One formula: its parameters and how many groups the result describes."""

    id: str
    label: dict[str, str]
    required: tuple[str, ...]
    optional: dict[str, float | None]
    groups: int
    expression: str


FORMULAS: dict[str, Formula] = {
    "single_proportion": Formula(
        "single_proportion",
        {"en": "estimating a single proportion", "fa": "برآورد یک نسبت"},
        ("p", "d"),
        {"alpha": 0.05, "N": None},
        1,
        "n = Z(1−α/2)² × p × (1 − p) / d²",
    ),
    "single_mean": Formula(
        "single_mean",
        {"en": "estimating a single mean", "fa": "برآورد یک میانگین"},
        ("sigma", "d"),
        {"alpha": 0.05},
        1,
        "n = Z(1−α/2)² × σ² / d²",
    ),
    "two_proportions": Formula(
        "two_proportions",
        {"en": "comparing two proportions", "fa": "مقایسه دو نسبت"},
        ("p1", "p2"),
        {"alpha": 0.05, "power": 0.80, "r": 1.0},
        2,
        "n₁ = [Z(1−α/2) × √((1 + 1/r) × p̄(1 − p̄)) + Z(1−β) × √(p₁(1 − p₁) + p₂(1 − p₂)/r)]² / (p₁ − p₂)², "
        "p̄ = (p₁ + r·p₂)/(1 + r), n₂ = r × n₁",
    ),
    "two_means": Formula(
        "two_means",
        {"en": "comparing two means", "fa": "مقایسه دو میانگین"},
        ("sigma", "delta"),
        {"alpha": 0.05, "power": 0.80, "r": 1.0},
        2,
        "n₁ = (1 + 1/r) × σ² × (Z(1−α/2) + Z(1−β))² / Δ², n₂ = r × n₁",
    ),
    "paired_mean": Formula(
        "paired_mean",
        {"en": "a before–after (paired) comparison of means", "fa": "مقایسه قبل و بعد (زوجی) میانگین‌ها"},
        ("sigma_d", "delta"),
        {"alpha": 0.05, "power": 0.80},
        1,
        "n = ((Z(1−α/2) + Z(1−β)) × σd / Δ)²",
    ),
    "correlation": Formula(
        "correlation",
        {"en": "detecting a correlation", "fa": "بررسی همبستگی"},
        ("r",),
        {"alpha": 0.05, "power": 0.80},
        1,
        "C = ½ × ln((1 + r)/(1 − r)), n = ((Z(1−α/2) + Z(1−β)) / C)² + 3",
    ),
    "case_control_unmatched": Formula(
        "case_control_unmatched",
        {"en": "an unmatched case-control study", "fa": "مطالعه مورد-شاهدی همسان‌نشده"},
        ("p0", "odds_ratio"),
        {"alpha": 0.05, "power": 0.80, "r": 1.0},
        2,
        "p₁ = OR × p₀ / (1 + p₀ × (OR − 1)), then the two-proportions formula with p₁ (cases) and p₀ (controls)",
    ),
    # Buderer 1996: precision of sensitivity is set by the diseased participants, of specificity by the others
    "diagnostic_accuracy": Formula(
        "diagnostic_accuracy",
        {"en": "diagnostic accuracy (Buderer)", "fa": "دقت تشخیصی (روش Buderer)"},
        ("sensitivity", "prevalence", "d"),
        {"specificity": None, "alpha": 0.05},
        1,
        "n(Se) = Z(1−α/2)² × Se × (1 − Se) / (d² × Prev); n(Sp) = Z(1−α/2)² × Sp × (1 − Sp) / (d² × (1 − Prev)); "
        "n = max(n(Se), n(Sp))",
    ),
}


@dataclass
class SampleSizeResult:
    """The computed sample size and everything needed to explain it."""

    formula: str
    params: dict[str, float]
    raw_n1: float  # raw n (per group for two-group formulas, total otherwise), before rounding
    n1: int  # rounded up
    n2: int | None  # second group (two-group formulas only)
    rate: float | None  # non-response / drop-out rate, 0–1
    final_n1: int
    final_n2: int | None
    extra: dict[str, float] = field(default_factory=dict)  # e.g. p1 of a case-control study, n before N correction

    @property
    def groups(self) -> int:
        return 2 if self.n2 is not None else 1

    @property
    def final_total(self) -> int:
        """The final sample size the proposal must report."""
        return self.final_n1 + (self.final_n2 or 0)


def z_two_sided(alpha: float) -> float:
    """Z(1−α/2), exact normal quantile."""
    return NormalDist().inv_cdf(1 - alpha / 2)


def z_power(power: float) -> float:
    """Z(1−β) for the given power, exact normal quantile."""
    return NormalDist().inv_cdf(power)


def ceil_n(value: float) -> int:
    """Round up, ignoring floating-point dust such as 97.00000000001."""
    return math.ceil(round(value, 6))


def normalise_value(name: str, value: float) -> float:
    """Accept 60 for 60% where a proportion is expected (models often write percentages)."""
    if name in PROPORTION_PARAMS and 1 < value <= 100:
        return value / 100
    return value


def prepare_params(formula_id: str, given: dict[str, float]) -> tuple[dict[str, float], list[str]]:
    """Validate parameter names for the formula and fill conventions. Returns (params, notes about changes)."""
    if formula_id not in FORMULAS:
        raise SampleSizeError(f"unknown formula {formula_id!r}")
    formula = FORMULAS[formula_id]
    allowed = set(formula.required) | set(formula.optional)
    notes: list[str] = []
    params: dict[str, float] = {}
    for name, value in given.items():
        if name not in allowed:
            notes.append(f"ignored unknown parameter {name!r} for {formula_id}")
            continue
        if value is None:
            continue
        params[name] = normalise_value(name, float(value))
    for name, default in formula.optional.items():
        if name not in params and default is not None:
            params[name] = default
            if name in CONVENTIONS:
                notes.append(f"{name} = {default} (convention)")
    missing = [name for name in formula.required if name not in params]
    if missing:
        raise SampleSizeError(f"{formula_id}: missing parameter(s) {missing}")
    check_ranges(formula_id, params)
    return params, notes


def check_ranges(formula_id: str, params: dict[str, float]) -> None:
    """Reject values that make the formula meaningless."""

    def between(name: str, low: float, high: float) -> None:
        if name in params and not (low < params[name] < high):
            raise SampleSizeError(f"{formula_id}: {name} = {params[name]} must be between {low} and {high}")

    for name in ("p", "p0", "p1", "p2", "alpha", "power", "sensitivity", "specificity", "prevalence"):
        between(name, 0, 1)
    if formula_id == "correlation":
        between("r", -1, 1)
        if params["r"] == 0:
            raise SampleSizeError("correlation: r must not be 0")
    elif "r" in params and params["r"] <= 0:
        raise SampleSizeError(f"{formula_id}: r (allocation ratio) must be positive")
    for name in ("d", "sigma", "sigma_d"):
        if name in params and params[name] <= 0:
            raise SampleSizeError(f"{formula_id}: {name} must be positive")
    if "delta" in params and params["delta"] == 0:
        raise SampleSizeError(f"{formula_id}: delta must not be 0")
    if formula_id == "two_proportions" and params["p1"] == params["p2"]:
        raise SampleSizeError("two_proportions: p1 and p2 must differ")
    if formula_id == "case_control_unmatched" and (params["odds_ratio"] <= 0 or params["odds_ratio"] == 1):
        raise SampleSizeError("case_control_unmatched: odds_ratio must be positive and not 1")
    if "N" in params and params["N"] < 1:
        raise SampleSizeError("single_proportion: N must be at least 1")


def raw_single_proportion(p: dict[str, float], extra: dict[str, float]) -> float:
    n = z_two_sided(p["alpha"]) ** 2 * p["p"] * (1 - p["p"]) / p["d"] ** 2
    if p.get("N"):
        extra["n_infinite"] = n
        n = n / (1 + (n - 1) / p["N"])
    return n


def raw_single_mean(p: dict[str, float], extra: dict[str, float]) -> float:
    return z_two_sided(p["alpha"]) ** 2 * p["sigma"] ** 2 / p["d"] ** 2


def two_proportions_n1(p1: float, p2: float, alpha: float, power: float, r: float) -> float:
    p_bar = (p1 + r * p2) / (1 + r)
    term_alpha = z_two_sided(alpha) * math.sqrt((1 + 1 / r) * p_bar * (1 - p_bar))
    term_power = z_power(power) * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2) / r)
    return (term_alpha + term_power) ** 2 / (p1 - p2) ** 2


def raw_two_proportions(p: dict[str, float], extra: dict[str, float]) -> float:
    return two_proportions_n1(p["p1"], p["p2"], p["alpha"], p["power"], p["r"])


def raw_two_means(p: dict[str, float], extra: dict[str, float]) -> float:
    z_sum = z_two_sided(p["alpha"]) + z_power(p["power"])
    return (1 + 1 / p["r"]) * p["sigma"] ** 2 * z_sum**2 / p["delta"] ** 2


def raw_paired_mean(p: dict[str, float], extra: dict[str, float]) -> float:
    z_sum = z_two_sided(p["alpha"]) + z_power(p["power"])
    return (z_sum * p["sigma_d"] / p["delta"]) ** 2


def raw_correlation(p: dict[str, float], extra: dict[str, float]) -> float:
    r = abs(p["r"])
    c = 0.5 * math.log((1 + r) / (1 - r))
    extra["C"] = c
    z_sum = z_two_sided(p["alpha"]) + z_power(p["power"])
    return (z_sum / c) ** 2 + 3


def raw_case_control(p: dict[str, float], extra: dict[str, float]) -> float:
    p0, odds = p["p0"], p["odds_ratio"]
    p1 = odds * p0 / (1 + p0 * (odds - 1))
    extra["p1"] = p1
    return two_proportions_n1(p1, p0, p["alpha"], p["power"], p["r"])


def raw_diagnostic_accuracy(p: dict[str, float], extra: dict[str, float]) -> float:
    z2 = z_two_sided(p["alpha"]) ** 2
    se = z2 * p["sensitivity"] * (1 - p["sensitivity"]) / p["d"] ** 2
    extra["n_diseased"] = se
    extra["n_sensitivity"] = se / p["prevalence"]
    if p.get("specificity"):
        sp = z2 * p["specificity"] * (1 - p["specificity"]) / p["d"] ** 2
        extra["n_non_diseased"] = sp
        extra["n_specificity"] = sp / (1 - p["prevalence"])
    return max(extra["n_sensitivity"], extra.get("n_specificity", 0.0))


RAW: dict[str, Callable[[dict[str, float], dict[str, float]], float]] = {
    "single_proportion": raw_single_proportion,
    "single_mean": raw_single_mean,
    "two_proportions": raw_two_proportions,
    "two_means": raw_two_means,
    "paired_mean": raw_paired_mean,
    "correlation": raw_correlation,
    "case_control_unmatched": raw_case_control,
    "diagnostic_accuracy": raw_diagnostic_accuracy,
}


def compute(
    formula_id: str, given: dict[str, float], rate: float | None = None, design_effect: float | None = None
) -> SampleSizeResult:
    """Compute a sample size. `rate` is the expected non-response or drop-out (0–1, or a percentage).
    `design_effect` (DEFF > 1, cluster or multistage sampling) multiplies the raw n before rounding and attrition."""
    params, _ = prepare_params(formula_id, given)
    extra: dict[str, float] = {}
    raw = RAW[formula_id](params, extra)
    deff = float(design_effect) if design_effect else 1.0
    if not 1.0 <= deff <= 10.0:
        raise SampleSizeError(f"design effect must be between 1 and 10, got {design_effect}")
    if deff > 1.0:
        extra["design_effect"] = deff
        extra["raw_before_deff"] = raw
    n1 = ceil_n(raw * deff)
    n2 = ceil_n(params["r"] * raw * deff) if FORMULAS[formula_id].groups == 2 else None
    rate = normalise_rate(rate)
    final_n1 = inflate(n1, rate)
    final_n2 = inflate(n2, rate) if n2 is not None else None
    return SampleSizeResult(formula_id, params, raw, n1, n2, rate, final_n1, final_n2, extra)


def normalise_rate(rate: float | None) -> float | None:
    """10 → 0.10; values outside 0–0.9 are ignored (a rate of 95% would make no sense)."""
    if rate is None:
        return None
    rate = float(rate)
    if 1 <= rate < 100:
        rate = rate / 100
    if not (0 < rate < 0.9):
        return None
    return rate


def inflate(n: int, rate: float | None) -> int:
    """n_final = ⌈ n / (1 − rate) ⌉."""
    if not rate:
        return n
    return ceil_n(n / (1 - rate))


# --- the worked calculation ---------------------------------------------------------------------------------------

WORDS = {
    "en": {
        "formula": "Formula ({label})",
        "values": "Values",
        "substituted": "Calculation",
        "rounded": "Rounded up",
        "per_group": "per group",
        "group1_cc": "cases",
        "group2_cc": "controls",
        "group1": "group 1",
        "group2": "group 2",
        "rate": "Allowing for an expected non-response or drop-out rate of {pct}%",
        "deff": "Design effect (cluster sampling)",
        "final": "Final sample size",
        "total": "in total",
        "participants": "participants",
        "convention": "convention",
        "assumption": "assumption to be confirmed by the researcher",
        "paper": "from",
    },
    "fa": {
        "formula": "فرمول ({label})",
        "values": "مقادیر",
        "substituted": "محاسبه",
        "rounded": "گرد شده به بالا",
        "per_group": "در هر گروه",
        "group1_cc": "مورد",
        "group2_cc": "شاهد",
        "group1": "گروه ۱",
        "group2": "گروه ۲",
        "rate": "با در نظر گرفتن ریزش یا عدم پاسخ‌دهی مورد انتظار {pct} درصد",
        "deff": "اثر طرح (نمونه‌گیری خوشه‌ای)",
        "final": "حجم نمونه نهایی",
        "total": "در مجموع",
        "participants": "نفر",
        "convention": "قرارداد رایج",
        "assumption": "فرض؛ باید توسط پژوهشگر تأیید شود",
        "paper": "برگرفته از",
    },
}

SYMBOLS = {
    "p": "p",
    "d": "d",
    "alpha": "α",
    "power": "power (1 − β)",
    "N": "N",
    "sigma": "σ",
    "delta": "Δ",
    "sigma_d": "σd",
    "r": "r",
    "p0": "p₀",
    "p1": "p₁",
    "p2": "p₂",
    "odds_ratio": "OR",
    "sensitivity": "Se",
    "specificity": "Sp",
    "prevalence": "Prev",
}


def fmt(value: float, digits: int = 4) -> str:
    """Short number: 0.5, 1.96, 384.15 (never scientific notation)."""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def substitution(result: SampleSizeResult) -> str:
    """The formula with the numbers put in, so a reader can redo the arithmetic."""
    p = result.params
    za = fmt(z_two_sided(p["alpha"]), 2)
    zb = fmt(z_power(p["power"]), 4) if "power" in p else ""
    raw = fmt(result.raw_n1, 2)
    f = result.formula
    if f == "single_proportion":
        text = f"n = {za}² × {fmt(p['p'])} × (1 − {fmt(p['p'])}) / {fmt(p['d'])}²"
        if p.get("N"):
            text += f" = {fmt(result.extra['n_infinite'], 2)}; n′ = n / (1 + (n − 1)/{fmt(p['N'])})"
        return f"{text} = {raw}"
    if f == "single_mean":
        return f"n = {za}² × {fmt(p['sigma'])}² / {fmt(p['d'])}² = {raw}"
    if f == "two_means":
        return f"n₁ = (1 + 1/{fmt(p['r'])}) × {fmt(p['sigma'])}² × ({za} + {zb})² / {fmt(p['delta'])}² = {raw}"
    if f == "paired_mean":
        return f"n = (({za} + {zb}) × {fmt(p['sigma_d'])} / {fmt(p['delta'])})² = {raw}"
    if f == "correlation":
        c = fmt(result.extra["C"], 4)
        return f"C = ½ × ln((1 + {fmt(abs(p['r']))})/(1 − {fmt(abs(p['r']))})) = {c}; n = (({za} + {zb}) / {c})² + 3 = {raw}"
    if f == "case_control_unmatched":
        p1 = result.extra["p1"]
        return (
            f"p₁ = {fmt(p['odds_ratio'])} × {fmt(p['p0'])} / (1 + {fmt(p['p0'])} × ({fmt(p['odds_ratio'])} − 1)) = "
            f"{fmt(p1)}; n₁ (two proportions, p₁ = {fmt(p1)}, p₂ = {fmt(p['p0'])}, Z(1−α/2) = {za}, "
            f"Z(1−β) = {zb}, r = {fmt(p['r'])}) = {raw}"
        )
    if f == "diagnostic_accuracy":
        text = (f"n(Se) = {za}² × {fmt(p['sensitivity'])} × (1 − {fmt(p['sensitivity'])}) / ({fmt(p['d'])}² × "
                f"{fmt(p['prevalence'])}) = {fmt(result.extra['n_diseased'], 2)} / {fmt(p['prevalence'])} = "
                f"{fmt(result.extra['n_sensitivity'], 2)}")
        if "n_specificity" in result.extra:
            text += (f"; n(Sp) = {za}² × {fmt(p['specificity'])} × (1 − {fmt(p['specificity'])}) / ({fmt(p['d'])}² × "
                     f"(1 − {fmt(p['prevalence'])})) = {fmt(result.extra['n_specificity'], 2)}; n = max = {raw}")
        return text
    # two_proportions
    return f"n₁ (p₁ = {fmt(p['p1'])}, p₂ = {fmt(p['p2'])}, Z(1−α/2) = {za}, Z(1−β) = {zb}, r = {fmt(p['r'])}) = {raw}"


def worked_calculation(result: SampleSizeResult, sources: dict[str, str], lang: str) -> str:
    """The calculation in the output language: formula, values with their sources, arithmetic, final n.

    `sources` maps a parameter name to a short source text such as "convention", "assumption" or "{P2}".
    """
    w = WORDS[lang]
    formula = FORMULAS[result.formula]
    lines = [f"{w['formula'].format(label=formula.label[lang])}: {formula.expression}"]
    values = []
    for name, value in result.params.items():
        source = sources.get(name, "convention" if name in CONVENTIONS or name == "r" else "assumption")
        source_text = w["paper"] + " " + source if source.startswith("{") else w.get(source, source)
        values.append(f"{SYMBOLS.get(name, name)} = {fmt(value)} ({source_text})")
    lines.append(f"{w['values']}: " + "; ".join(values))
    lines.append(f"{w['substituted']}: {substitution(result)}")
    if result.extra.get("design_effect"):
        deff = result.extra["design_effect"]
        lines.append(f"{w['deff']}: {fmt(result.raw_n1, 2)} × {fmt(deff, 2)} = {fmt(result.raw_n1 * deff, 2)}")
    lines.append(rounded_line(result, w))
    if result.rate:
        lines.append(rate_line(result, w))
    lines.append(final_line(result, w))
    return "\n".join(lines)


def group_names(result: SampleSizeResult, w: dict[str, str]) -> tuple[str, str]:
    if result.formula == "case_control_unmatched":
        return w["group1_cc"], w["group2_cc"]
    return w["group1"], w["group2"]


def rounded_line(result: SampleSizeResult, w: dict[str, str]) -> str:
    if result.n2 is None:
        return f"{w['rounded']}: n = {result.n1}"
    g1, g2 = group_names(result, w)
    if result.n1 == result.n2:
        return f"{w['rounded']}: {result.n1} {w['per_group']}"
    return f"{w['rounded']}: {result.n1} ({g1}), {result.n2} ({g2})"


def rate_line(result: SampleSizeResult, w: dict[str, str]) -> str:
    pct = fmt(result.rate * 100, 1)
    text = w["rate"].format(pct=pct)
    parts = [f"{result.n1} / (1 − {fmt(result.rate, 3)}) = {fmt(result.n1 / (1 - result.rate), 2)} → {result.final_n1}"]
    if result.n2 is not None and result.n2 != result.n1:
        parts.append(
            f"{result.n2} / (1 − {fmt(result.rate, 3)}) = {fmt(result.n2 / (1 - result.rate), 2)} → {result.final_n2}"
        )
    return f"{text}: " + "; ".join(parts)


def final_line(result: SampleSizeResult, w: dict[str, str]) -> str:
    if result.final_n2 is None:
        return f"{w['final']}: {result.final_total} {w['participants']}"
    g1, g2 = group_names(result, w)
    if result.final_n1 == result.final_n2:
        detail = f"{result.final_n1} {w['per_group']}"
    else:
        detail = f"{result.final_n1} {g1}, {result.final_n2} {g2}"
    return f"{w['final']}: {result.final_total} {w['participants']} ({detail}, {w['total']} {result.final_total})"
