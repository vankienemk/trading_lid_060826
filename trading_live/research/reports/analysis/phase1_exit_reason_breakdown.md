# Phase 1: Exit Reason Breakdown

**Dataset**: frozen 974 events, 973 net-valid.

## Bucket 0-39 (n_net 782, total 783)

- Overall: PF 0.9914, avg -0.0045R, P(net>0) 0.4054

| exit_reason | n_net | share_total | avg_net_r | PF | contrib_to_exp |
|---|---|---|---|---|---|
| target | 148 | 0.189 | 2.0 | None | 0.3785 |
| stop | 381 | 0.4866 | -1.0 | 0.0 | -0.4872 |
| time | 253 | 0.3231 | 0.322 | 3.5891 | 0.1042 |
| ambiguous | 0 | — | — | — | — |

## Bucket 40-49 (n_net 188, total 188)

- Overall: PF 1.23, avg 0.0919R, P(net>0) 0.4894

| exit_reason | n_net | share_total | avg_net_r | PF | contrib_to_exp |
|---|---|---|---|---|---|
| target | 29 | 0.1543 | 2.0 | None | 0.3085 |
| stop | 66 | 0.3511 | -1.0 | 0.0 | -0.3511 |
| time | 93 | 0.4947 | 0.2719 | 3.7579 | 0.1345 |
| ambiguous | 0 | — | — | — | — |

## Bucket 50-59 (n_net 3, total 3)

- Overall: PF 0.8744, avg -0.0419R, P(net>0) 0.6667

| exit_reason | n_net | share_total | avg_net_r | PF | contrib_to_exp |
|---|---|---|---|---|---|
| target | 0 | — | — | — | — |
| stop | 1 | 0.3333 | -1.0 | 0.0 | -0.3333 |
| time | 2 | 0.6667 | 0.4372 | None | 0.2915 |
| ambiguous | 0 | — | — | — | — |

## Bucket 60+ (n_net 0, total 0)

No events.

## Key: What changes from 0-39 to 40-49?

The 40-49 bucket has positive expectancy despite lower win rate because the loss rate drops much more (48.7% -> 35.1%) than the win rate (18.9% -> 15.4%), and time-out contribution stays positive.

Check the `contribution_to_expectancy` column above. In 0-39 the stop contribution (-0.49R) nearly cancels the target contribution (+0.38R) while time adds only +0.10R. In 40-49, stop contribution shrinks to -0.35R while time adds +0.13R, yielding net positive.

This means: **the setup's edge in 40-49 is driven by avoiding bad stops, not by hitting better targets.** The same R:R (+2/-1) applies everywhere.

