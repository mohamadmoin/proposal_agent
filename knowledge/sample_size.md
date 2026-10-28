# Sample size: formulas computed by code

The model never does arithmetic. It **chooses** one formula from the design's list (`designs.yaml › sample_size`)
and **fills in the parameters**, and every parameter names its source:

- `paper`: the value appears in the abstract of a retrieved paper. Code checks that the number is really there and
  cites the paper;
- `convention`: a standard choice (α = 0.05, power = 0.80, d = 0.05 for a prevalence of 0.5);
- `assumption`: no source. The proposal shows it as an assumption the researcher must confirm.

Code then computes n, rounds **up**, inflates for expected non-response or drop-out, and gives the writer a worked
calculation to copy (formula, substituted values, result). The writer must report the final number exactly.

Notation: Z₁₋α/₂ = 1.96 for α = 0.05 (two-sided); Z₁₋β = 0.8416 for power 0.80. Use exact normal quantiles in code
(`statistics.NormalDist().inv_cdf`), not rounded constants.

| id | When | Parameters | Formula (n per group unless stated) |
|---|---|---|---|
| `single_proportion` | estimate a prevalence/proportion | p (expected proportion), d (absolute precision), α; optional N (population size) | n = Z₁₋α/₂² · p(1−p) / d². With N: n′ = n / (1 + (n−1)/N). Total n. |
| `single_mean` | estimate a mean | σ (SD), d (precision, same unit), α | n = Z₁₋α/₂² · σ² / d². Total n. |
| `two_proportions` | compare two proportions (cohort, RCT, comparative cross-sectional) | p₁, p₂, α, power, r = n₂/n₁ (default 1) | p̄ = (p₁ + r·p₂)/(1 + r); n₁ = [Z₁₋α/₂·√((1 + 1/r)·p̄(1−p̄)) + Z₁₋β·√(p₁(1−p₁) + p₂(1−p₂)/r)]² / (p₁ − p₂)²; n₂ = r·n₁ |
| `two_means` | compare two means | σ (common SD), Δ (difference to detect), α, power, r (default 1) | n₁ = (1 + 1/r)·σ²·(Z₁₋α/₂ + Z₁₋β)² / Δ²; n₂ = r·n₁ |
| `paired_mean` | before–after in the same people | σ_d (SD of the differences), Δ, α, power | n = ((Z₁₋α/₂ + Z₁₋β)·σ_d / Δ)². Total n. |
| `correlation` | detect a correlation r | r, α, power | C = ½·ln((1 + r)/(1 − r)); n = ((Z₁₋α/₂ + Z₁₋β)/C)² + 3. Total n. |
| `case_control_unmatched` | unmatched case-control | p₀ (exposure among controls), OR, α, power, r = controls per case (default 1) | p₁ = OR·p₀ / (1 + p₀·(OR − 1)); then `two_proportions` with p₁ (cases), p₂ = p₀ (controls) |
| `diagnostic_accuracy` | estimate sensitivity (and specificity) of a test with precision d (Buderer) | Se, Prev (expected prevalence of the target condition in the intended-use population), d, α; optional Sp | n(Se) = Z₁₋α/₂²·Se(1−Se) / (d²·Prev); n(Sp) = Z₁₋α/₂²·Sp(1−Sp) / (d²·(1−Prev)); n = max. Total n. Never use `single_proportion` on the total sample for sensitivity: its precision depends on the diseased participants only. |

Non-response / drop-out: n_final = ⌈ n / (1 − rate) ⌉, applied per group, when a rate is given.

A very large result (more than 2,000 in total) usually means the difference or precision to detect is small, e.g.
two observed proportions only 2 percentage points apart. The calculation stays as computed, and the researcher is
asked to check whether the smallest clinically important difference (or an acceptable precision) is larger.

## Reference values (used by the tests)

| Formula | Inputs | Raw n | Reported |
|---|---|---|---|
| single_proportion | p = 0.5, d = 0.05, α = 0.05 | 384.15 | 385 |
| single_mean | σ = 10, d = 2 | 96.04 | 97 |
| two_proportions | p₁ = 0.6, p₂ = 0.4, power 0.8 | 96.92 | 97 per group |
| two_means | σ = 10, Δ = 5, power 0.8 | 62.79 | 63 per group |
| paired_mean | σ_d = 8, Δ = 4, power 0.8 | 31.40 | 32 |
| correlation | r = 0.3, power 0.8 | 84.93 | 85 |
| case_control_unmatched | p₀ = 0.3, OR = 2, power 0.8 | p₁ = 0.4615; 140.66 | 141 cases + 141 controls |
| diagnostic_accuracy | Se = 0.85, Sp = 0.9, Prev = 0.45, d = 0.06 | n(Se) = 302.34, n(Sp) = 174.61 | 303 |

Learn more: Kirkwood & Sterne, *Essential Medical Statistics*, ch. 35; OpenEpi sample-size calculators
(https://www.openepi.com/).
