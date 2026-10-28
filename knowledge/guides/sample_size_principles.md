# Choosing the sample-size formula and its parameters

When to read: before compute_sample_size, every time.
How to use: decide the formula from the PRIMARY objective, then pick each parameter by the rules below; give each
parameter an evidence id (E#) or an explained assumption; read the tool's answer (feasibility) and revise if needed.

Formula from the primary objective
- Estimate one prevalence/proportion → single_proportion (precision d); one mean → single_mean.
- Compare two groups on a proportion → two_proportions; on a mean → two_means; before/after in the same people →
  paired_mean; association of two continuous variables → correlation; case-control → case_control_unmatched.
- Time-to-event or many groups: use the closest available formula for the primary comparison and say so.

Where the parameters come from (in this order)
1. A comparable population: same population, setting and outcome definition (screening population_similarity ≥ 2).
   Same cut-off as your outcome definition (deficiency < 20 ng/mL is not "suboptimal" < 30 ng/mL).
2. A pooled estimate (meta-analysis, large cohort) when single studies disagree.
3. A conservative assumption, explained (p = 0.5 for an unknown prevalence; the minimal clinically important
   difference for a trial).
Never take an effect from one small study whose effect is much larger than pooled estimates; never combine the overall
rates of two unrelated studies as if they were two groups; never borrow from a different population (emergency for
elective, neonatal for adult) without saying why it is still valid.

Corrections
- Attrition / non-response: people who may be lost (10–20%). Not for observation opportunities or records.
- Design effect (DEFF): cluster or multistage sampling, or repeated observations within people/wards (typical 1.5–2;
  or 1 + (m − 1) × ICC).
- Finite population when the whole population is small.

Feasibility and reporting
- Compare the final n with what the setting can recruit in the study period; if it is larger, change the design,
  setting, period or primary outcome, or justify.
- State the final n per group AND in total, and copy the worked calculation.
- Optionally give a sensitivity n for a more conservative effect.
