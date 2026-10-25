# Study-design questionnaire

Before naming a study design, the model answers these twelve questions about the user's study. This is a structured
chain of thought written by a clinician-researcher. The design router (`engine/methods.py`) sends the whole file. The
answers are stored and shown to the user, and the questions the model could not answer are listed so the user can
add details.

1. **What is the primary objective of my research?**
   - Am I looking to determine the *cause* of a disease or condition?
   - Am I evaluating the *effectiveness* of a treatment or intervention?
   - Am I interested in exploring patient *experiences* or perceptions?
   - Am I summarising existing research to reach a *comprehensive conclusion*?

   *Purpose:* clarifies the overall goal, which decides whether an observational, interventional or qualitative
   approach is suitable.

2. **What is the exposure or intervention I am interested in?**
   - Am I studying a *risk factor* (e.g. smoking, genetics)?
   - Am I evaluating the *effect of a treatment* (e.g. drug, surgery)?
   - Am I looking at a *behaviour or experience* (e.g. lifestyle, stigma)?

   *Purpose:* decides between an observational design (cohort, case-control) and an experimental one (RCT).

3. **What are the outcomes I want to measure?**
   - Are the outcomes *clinical* (e.g. disease occurrence, mortality, recovery)?
   - Are they *subjective* (e.g. quality of life, patient satisfaction)?
   - Are they *long-term effects*, or am I focused on *immediate outcomes*?

   *Purpose:* shapes the design, the time frame (cross-sectional vs longitudinal) and the data to collect.

4. **Do I have existing data, or will I need to collect new data?**
   - Is the data *available retrospectively* (e.g. medical records, past surveys)?
   - Will I need to collect *new prospective data*?
   - Am I performing a *systematic review* of published studies?

   *Purpose:* separates retrospective from prospective designs, and identifies systematic reviews and meta-analyses.

5. **How are participants being selected?**
   - Am I working with a *random sample* of the population, or selecting on specific criteria (e.g. patients with a
     condition)?
   - Am I looking at a *specific population* (e.g. pregnant women, elderly patients)?

   *Purpose:* guides the choice between case-control, cohort and clinical-trial designs.

6. **How will I ensure comparability between groups (if applicable)?**
   - Will participants be *randomly assigned* to treatment or control groups?
   - Am I comparing *pre-existing groups* (e.g. smokers vs non-smokers)?
   - Am I making a *within-subject* comparison (e.g. before and after treatment)?

   *Purpose:* decides whether an RCT, an observational cohort or a case-control design is needed.

7. **What resources and time frame do I have?**
   - Do I have access to a *large dataset*, or will I need to recruit patients?
   - What is the *duration* of the study (short-term vs long-term)?
   - Do I have enough *funding* for a clinical trial, or is an observational study more feasible?

   *Purpose:* practical limits (time, funding, data) decide whether an RCT, an observational study or a simpler design
   is feasible.

8. **Am I investigating an association or a causal relationship?**
   - Am I aiming to show a *correlation* between two factors (e.g. periodontal disease and pregnancy outcomes)?
   - Do I want to establish a *causal link* (e.g. whether a treatment directly leads to recovery)?

   *Purpose:* shows whether a robust experimental design is necessary or an observational one will do.

9. **Are there ethical considerations or risks involved?**
   - Are there *ethical limits* on running an experiment (e.g. withholding treatment)?
   - Am I working with *vulnerable populations* (e.g. children, pregnant women, people living with HIV)?

   *Purpose:* keeps the design within ethical standards; may point to observational or qualitative designs when an
   intervention is not acceptable.

10. **What level of evidence do I need?**
    - Do I need *high-level evidence* for clinical recommendations?
    - Am I exploring a *novel idea* or giving a *preliminary report* on a condition?
    - Am I synthesising evidence from existing literature?

    *Purpose:* chooses between a high-evidence design (RCT), an exploratory one (case series) or a synthesis
    (systematic review / meta-analysis).

11. **How will I analyse my data?**
    - Do I intend to use *descriptive statistics* (e.g. prevalence, incidence)?
    - Will I use *inferential statistics* (e.g. regression, survival analysis)?
    - Am I combining data from several studies in a *meta-analysis*?

    *Purpose:* shows whether the study needs numerical outcomes (quantitative) or themes and meanings (qualitative).

12. **What is the scope of my research?**
    - Am I interested in *individual patient outcomes* (case reports)?
    - Am I studying outcomes at the *population level* (epidemiological research)?
    - Am I trying to *summarise and generalise* findings across studies (systematic review / meta-analysis)?

    *Purpose:* defines the scale of the research, which narrows the choice between case reports, cohort studies,
    clinical trials and reviews.

## Conclusion

Some typical conclusions from the answers:

- The goal is the cause-effect relationship of an intervention on an outcome → a **randomised controlled trial**.
- The goal is the association between an exposure and an outcome over time, without manipulating variables → a
  **cohort study**.
- Data are examined retrospectively to identify risk factors → a **case-control study**.
- A status (prevalence, a level, an association) is measured at one point in time → a **cross-sectional study**.
- An intervention is evaluated without random assignment → a **quasi-experimental study**.
- Existing research is synthesised → a **systematic review or meta-analysis**.
- Patient experiences are explored → a **qualitative study** (not one of the supported templates; the general template is used).
