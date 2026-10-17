# Milestone estimator replay

The October 17, 2026 estimator correction replaces perpetual exponential
decline/growth with recent weekly levels and damped trends. This directory holds
private replay outputs only. No posts, history repairs or uploads are performed.

Run `python dev/audit_milestone_estimator.py --baseline PATH_TO_OLD_MODULE` from
the repository root to compare against a saved local copy of the pre-change
`forecast_milestones.py`. Without `--baseline`, only the current estimator is
evaluated. Model implementation: `milestone_estimator.py` in the streams scripts.

The replay uses the active album-track scope on March 31, June 30 and August 31,
2026. Each prediction uses only data available at that origin. Future observations
are used solely to score predictions; a held-out gap, estimated/merged row or
inconsistent daily ends the evaluation window. Missing/inconsistent training
data remains blocked and is recorded in `replay.json`.

## Recorded validation

943 eligible track/origin cases; 23 blocked cases. There were 116 verified next
100M milestone crossings; both estimators produced dates for the same 114,
with two beyond their five-year horizon. Median absolute error was 2 days for
both. Mean absolute error changed from 6.18 to 5.81 days.

Mean symmetric percentage error for held-out cumulative streams:

| Horizon | Cases | Previous | Replacement |
| --- | ---: | ---: | ---: |
| 7 days | 942 | 5.40% | 5.10% |
| 28 days | 939 | 5.75% | 5.77% |
| 90 days | 604 | 10.66% | 9.01% |
| 180 days | 284 | 9.40% | 10.57% |

The replacement fixes perpetual compounding and improves this sample's crossing
error and 90-day cumulative error. It does **not** outperform at every horizon:
180-day cumulative error is worse, and 28-day error is essentially unchanged.
These historical checks cannot validate exact dates years into the future or
anticipate new releases, promotion, viral changes, or future seasonal events.
Scenario dates and diagnostic bands are not calibrated prediction intervals.

## Ophelia regression, October 15 snapshot

The same verified total/daily observations feed both estimators. The replacement
selects its observed 28-day level from its past cumulative-stream backtests.
These are estimated dates, not corrected historical counts:

| Target | Previous Watch | Replacement |
| --- | --- | --- |
| 2B | May 13, 2027 | February 4, 2027 |
| 2.1B | January 18, 2028 | March 18, 2027 |

The projected additional 100M takes 42 days rather than 250. No Ophelia-specific
date, pace floor or special-case override is used.

All 335 active album tracks passed exact recent-data validation for the local
October 15 forecast refresh. New releases without enough held-out observations
use an explicitly identified observed seven-day pace, without an invented
decline or validation score. Missing data never uses that model as a fallback.

Tests: `python -m unittest dev.test_milestone_estimator dev.test_forecast_milestones dev.test_milestone_schedule`.
