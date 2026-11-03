# Type-confidence calibration, 2026-11-03

`dev/eval/calibrate.py --n 57 --seed 1`, claude-sonnet-4-6, on the saved entries of the
172-entry baseline (`last-run-2026-11-03-base172`). One call per source: the model sees the
source and the baseline entry and gives plausible types with probabilities. Cost $1.66
(plus a $0.46 two-source smoke test).

Draw: 57 sources, 18 with a wrong type (32%; the baseline rate is 22%, so this draw is
error-heavy by chance).

| Stated confidence in the produced type | n | Correct |
|---|---|---|
| < 0.5 | 2 | 0 |
| 0.5–0.7 | 1 | 0 |
| 0.7–0.9 | 17 | 10 (59%) |
| ≥ 0.9 | 37 | 29 (78%) |

- Brier 0.202; AUC 0.79: confidence ranks errors below correct entries, but is overconfident.
- Stated values cluster at 0.90–0.97, so only a high threshold separates them.

| Search when confidence below | Searched | Wrong types caught | Missed |
|---|---|---|---|
| 0.80 | 8 | 8/18 | 10 |
| 0.90 | 20 | 10/18 | 8 |
| 0.93 | 40 | 17/18 | 1 |
| 0.97 | 47 | 18/18 | 0 |

- The expected type was among the listed alternatives for 11 of the 18 errors; for 7 the
  model gave it 0. A search loop must be able to reach types the model did not list.
- One draw, 18 errors: indicative only.
