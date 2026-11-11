# Resurrection: ranking mechanism ({{ as_of }})

This document is rendered from `docs/RANKING_MECHANISM.template.md` for a given run. The template contains no numbers: every figure, formula
and hyperparameter below is a variable filled from the feature files, the model zoo configuration, the trial ledger and the stored
out-of-sample results. The rendered file is output and is not committed.

Holdout: {{ holdout }}.

## 1. Features and sign priors

Sign priors are the expected direction of the relationship with the forward return (plus one: higher is better; minus one: lower is
better; none: no prior). They are inputs to the sign-prior composites and to the sign-constrained ridge only; the other learners ignore them.

| feature | category | family | prior | set |
|---|---|---|---|---|
{% for x in features %}
| {{ x.name }} | {{ x.category }} | {{ x.family }} | {{ x.prior }} | {{ x.set }} |
{% endfor %}

## 2. Per-cross-section transform

Every transform statistic is fitted on one anchor's cross-section only.

- Winsorising: {{ formulas.winsor }}.
- Global transform: {{ formulas.rank_gauss }}.
- Industry transform: {{ formulas.industry }}.
- Missing values: {{ formulas.missing }}.
- Training target: {{ formulas.target }}.

One matrix feeds every model, so "same features" is literally true.

## 3. Model zoo and fixed hyperparameters

The hyperparameters are a-priori choices recorded in the trial registry; they are not tuned and they are not facts taken from the source work.
A change of any value is a new trial.

| learner | parameters, annual family | parameters, tactical family |
|---|---|---|
{% for z in zoo %}
| {{ z.learner }} | `{{ z.params_1y }}` | `{{ z.params_tactical }}` |
{% endfor %}

## 4. Rank, score and buckets

- Percentile: {{ formulas.percentile }}.
- Score: {{ formulas.score }}.
- Buckets: {{ formulas.decile }}.
- Table rows in order: {{ bucket_rows | join(", ") }}.

## 5. Calibration and conformal band

- Expected excess return per bucket: {{ formulas.alpha }}.
- Band: {{ formulas.conformal }}.
- Conviction: {{ formulas.conviction }}.
- The nested scheme uses only earlier counted anchors whose labels have ended and never a sealed anchor; the pooled scheme is descriptive.

## 6. Tables for the champion and the baseline, per horizon

{% for fam in families %}
### {{ loop.index }}. {{ fam.label }}: family `{{ fam.family }}`, trials in the family: {{ fam.n }}

Champion by the selection rule: `{{ fam.champion }}`. Baseline: `{{ fam.baseline }}`. Selection rule: {{ formulas.ninety_ten }}.

{% for name, tbl in fam.tables.items() %}
{{ tbl }}

{% endfor %}
{% endfor %}

## 7. Consensus

{{ formulas.votes }}. {{ formulas.rankavg }}. Consensus tables, the size-matched comparisons and their confidence intervals are in
`RESEARCH_OOS.md`, section three of each family.

## 8. Surprise outcomes

Outcomes are post-anchor by design (analyst surprise and fundamental surprise without analysts), isolated from the feature and model code,
and reported for the sets above in `RESEARCH_OOS.md`, section four of each family.

## 9. Data and bias block (mandatory)

Deflated Sharpe ratio definition used in the gates: {{ formulas.dsr }}.

{% for fam in families %}
### {{ loop.index }}. {{ fam.family }} (trials in the family: {{ fam.n }})

```
{{ fam.bias_block }}
```

{% endfor %}
