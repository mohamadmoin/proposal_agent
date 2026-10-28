# Diagnostic-accuracy study (STARD)

When to read: after choosing a diagnostic-accuracy design.
How to use: decide the index test, the reference standard and the target condition first (record_decision).

Essentials of the protocol
- Target condition, index test (with its positivity threshold, pre-specified) and reference standard (the best
  available, applied to all or with a stated verification strategy).
- Participants: consecutive or random sample of the intended-use population (not cases and healthy controls only),
  setting, eligibility.
- Blinding of index-test readers to the reference result and vice versa; time interval between tests.
- Sample size: formula `diagnostic_accuracy` (Buderer). The precision of sensitivity depends on the diseased
  participants only, so n(diseased) is divided by the expected prevalence in the intended-use population. Never apply
  a single-proportion formula with p = sensitivity to the total sample. Prevalence needs a source (or a stated
  assumption), like sensitivity. Add specificity when it is a primary aim.
- Indeterminate or non-diagnostic results (e.g. a non-visualised appendix): record them as a category, say how they
  enter the 2×2 table, and run a worst- and best-case sensitivity analysis.
- Verification: apply the reference standard to everyone, or follow up those not verified (e.g. 14-day follow-up).
  Avoid differential verification (surgery only for test-positives) and incorporation bias (the index test as part
  of the reference standard); when unavoidable, name it as a limitation.
- Analysis: sensitivity, specificity, PPV, NPV, likelihood ratios with 95% CI; ROC/AUC for continuous tests (with a
  matching aim for any cut-off you plan to derive); subgroup comparisons only with a stated aim.
