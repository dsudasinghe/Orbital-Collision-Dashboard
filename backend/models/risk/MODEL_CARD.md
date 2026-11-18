# Model card: collision-risk forecaster

**Task.** For a close-approach event, predict the collision risk (log10 Pc) in its final conjunction data message (CDM, released within 1 day of closest approach) from the CDMs released at least 2 days before. Operators decide on avoidance manoeuvres about 2 days out, so this is the forecast that matters. Same task and scoring as the ESA Kelvins Collision Avoidance Challenge (Uriot et al. 2020, arXiv:2008.03069).

**Model.** A learned three-parameter rule on the latest known risk `r` (`orbital/ml/rule.py`, parameters in `rule.json`):

| Latest risk | Prediction |
| --- | --- |
| `r >= -6` | `max(r - 0.1, -5.999)` (high risk) |
| `-7.3 <= r < -6` | `-5.6` (promoted to high risk) |
| `r < -7.3` | low risk (`-6.001`) |

**Data.** ESA Kelvins dataset (CC-BY-4.0, doi:10.5281/zenodo.4463683): CDMs ESA received 2015-2019, anonymised. Training: 8,293 events shaped like the test set (66 end high-risk). Test: the challenge's official 2,167 events (150 high-risk), with the labels released after the competition.

**Metric.** The challenge's `L = MSE_HR / F2` (lower is better): squared error on events whose final risk is high (≥ 1e-6), divided by an F2 score that counts a missed high-risk event four times as heavily as a false alarm.

## Results

| Model | CV loss (train, prior-weighted) | Test loss | Test MSE_HR | Test F2 |
| --- | --- | --- | --- | --- |
| Constant −5 | — | 2.504 | 0.679 | 0.271 |
| Latest known risk (naive baseline) | 0.515 ± 0.326 | 0.694 | 0.513 | 0.739 |
| Baseline + LightGBM classifier promotions | 0.497 ± 0.322 | 0.692 | 0.513 | 0.742 |
| **Learned rule (deployed)** | **0.394 ± 0.165** | **0.634** | **0.468** | **0.738** |

**8.7% lower loss than the naive baseline on the official test set.** For reference, on the challenge's final leaderboard the baseline scored 0.694, 12 of 97 teams beat it, the winner scored 0.556 and 10th place 0.649. On test the rule catches 135 of 150 high-risk events (baseline 115) at the cost of 180 false alarms (baseline 63).

## How it was chosen

- **No test-set tuning.** All choices were made by 5-fold × 6-repeat stratified cross-validation on training events; the official test set was scored once, after selection. `orbital ml-risk` reproduces every number here in about 20 seconds.
- **Label-shift correction.** The organisers put most high-risk events in the test set: 6.9% of test events end high-risk against 0.8% of eligible training events. Unweighted, every model learns that "risk usually falls away" and misses real high-risk events in test. Training high-risk events are therefore weighted ×9.27 (the odds ratio between the two published shares) in both training and CV scoring. This uses one published statistic (150 high-risk test events), not test labels. With it, CV predicted the test F2 almost exactly (0.745 vs 0.738).
- **Grid.** Promotion threshold, promoted value and flagged shift over 40 combinations; the top six all scored 0.394-0.399 in CV, so the choice is not a lucky outlier.

## What did not work

- **LightGBM** on 119 features (the latest CDM's ESA fields plus risk and miss-distance history), as a regressor (raw, clipped and residual targets), as a classifier, and as a classifier limited to borderline events. Without the prior correction, every variant scored far worse than the baseline; with it, the best variant matched the baseline on test (0.692 vs 0.694). With 66 high-risk training events the trees found no signal beyond the latest risk. This matches the challenge: automated pipelines scored 0.83-1.0 on test.
- The winning team's rules were tuned against the public leaderboard (their training-set loss got worse, 0.804 → 1.077, while test improved). The rule here is the same idea, fitted on training data alone.

## Limitations

- **Small positive class.** 66 training high-risk events; CV loss varies ±0.17 across folds.
- **Behaviour, not physics.** The rule encodes how ESA's CDM risk evolves (it tends to fall towards TCA; borderline events sometimes rise). It knows nothing about the specific objects.
- **Prior dependence.** The promotion threshold depends on how common high-risk events are where it is applied. The 6.9% test share is not the share among Space-Track public CDMs.
- **Use in this app.** Space-Track's public CDMs carry only Pc, miss distance and TCA, not ESA's covariance and orbit-determination fields, which the rule doesn't use anyway. Public CDMs are emergency-reportable events, often released less than 2 days before TCA, outside the training condition. The dashboard shows the forecast as an experimental high/low flag, never as an operational risk value.

**Not for operational decisions.** Operators rely on official conjunction messages and their own assessment.
