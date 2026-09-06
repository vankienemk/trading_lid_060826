# V2 Design Analysis — Filter Intersection + R:R Optimization

**Dataset:** frozen 974 events, 973 net-valid.
**Cost base:** 0.05R / trade.
**Probability source:** 5-fold CV LogisticRegression OOS pred prob.

## 1. Filter Stack Comparison

| Stack | n | PF (pre-cost) | PF @ 0.05R | Breakeven cost |
|---|---|---|---|---|
| nguoc_trend only | 275 | 1.1488 | 1.0463 | 0.0745 |
| nguoc_trend + prob>=0.25 | 69 | 1.6781 | 1.5473 | 0.3312 |
| triple (nguoc+prob>=.25+pen<=.20) | 38 | 3.3027 | 3.0406 | 0.7697 |

## 2. Triple Intersection Detail

**Mask:** nguoc_trend & prob>=0.25 & penetration_atr<=0.20
**n:** 38 events (3.9054% of whole)
**Pre-cost:** PF=3.3027, avg=0.7697R
**Post-cost 0.05R:** PF=3.0406, avg=0.7197R
**Breakeven:** 0.7697

## 3. R:R Sweep on Triple Intersection

**Subset composition:** tp=18, sl=12, time=8 (n=38)

| R:R | PF @ 0.05R | avg R @ 0.05R | Breakeven |
|---|---|---|---|
| 1.0:1 | 1.6976 | 0.2461 | 0.2961 |
| 1.5:1 | 2.3691 | 0.4829 | 0.5329 |
| 2.0:1 | 3.0406 | 0.7197 | 0.7697 |
| 2.5:1 | 3.7121 | 0.9566 | >1.0R |
| 3.0:1 | 4.3836 | 1.1934 | >1.0R |

**Optimal R:R:** 3.0:1 (PF=4.3836 @ 0.05R cost)

## 4. Recommendation

### Optimal V2 Pipeline

- **Filter stack:** nguoc_trend & model_prob>=0.25 & penetration_atr<=0.20
- **Sample size:** 38 trades / 974 (3.91% of events)
- **Optimal R:R:** 3.0:1 (PF=4.3836 @ 0.05R cost)
- **Breakeven cost:** 0.7697 (triple intersection pre-R:R-adjustment)

### Filter Stack Progression

1. **nguoc_trend only** (n=275): PF=1.149 pre-cost → PF=1.0463 @ 0.05R
2. **+ prob>=0.25** (n=69): PF=1.6781 pre-cost → PF=1.5473 @ 0.05R
3. **+ pen<=0.20** (n=38): PF=3.3027 pre-cost → PF=3.0406 @ 0.05R

Each filter addition reduces n but aims to improve PF. The triple stack at 38/974 (3.9%) provides the strongest signal but limited sample.
For v2 pipeline, recommend continuing with the triple stack and configurable R:R (2.0:1 baseline, sweep 1.0-3.0).

## Parity

- [PASS] whole_pf_1.0279: PF=1.027866
- [PASS] whole_avg_0.014R: avg=0.013994
- [PASS] whole_Pnet>0_0.4224: P=0.422405
- [PASS] whole_n_973: n=973
- [PASS] nguoc_trend_n_275: n=275
- [PASS] nguoc_trend_PF_1.1488: PF=1.148824
- [PASS] top_prob_0.25_PF_1.3045_n_223: PF=1.304532822925939 n=223
- md5 guard: 13/13 files match

