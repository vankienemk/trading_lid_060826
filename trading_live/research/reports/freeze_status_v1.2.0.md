# Baseline Freeze Status — v1.2.0 (honest metrics, post leak-repair)

**Baseline version:** `v1.2.0` (re-freeze) · Date: 2026-09-04 · Owner: Lead_Integrator (t3, team v2)
**Supersedes:** `v1.1.0-freeze` (T16, 2026-09-02) — that model was trained on **45 features incl. 19
future-information columns** (label leakage); all of its frozen metrics (test ROC-AUC 0.981 /
PR-AUC 0.876; walk-forward OOS PR-AUC mean 0.968) are **invalid** and are replaced here.
Leak history & root cause: `reports/integration/T21_leakage_audit.md`, `reports/bias_audit.md` §8.

## 1. Repair chain executed before this freeze (team v2, t1 ML_Engineer + t2 QA_Auditor)

| Fix | Nội dung | Bằng chứng |
|---|---|---|
| F3-WF | `walk_forward_validation` (src/modeling/train.py L655-668) truyền `impute_medians` **train-only** vào `calibrate_model`/`evaluate_on_test` — hết imputation leak test-fold | 5 folds sạch, không NaN/không crash; WF OOS PR-AUC mean 0.3552 (t1, QA tái lập 0.3537 — sai khác nhiễu RF) |
| F3-CLI | `train_model.py` (L105-110) wire `impute_medians` vào calibrate/eval | `xauusd-train` rc=0, không NaN crash (t1 + QA tự chạy lại rc=0) |
| F1-PAYOFF | top-prob generator dùng **`net_result_r` thật** (real labeled trade result column); loại 1 ambiguous → n=973 net-valid | JSON có `net_result_source="net_result_r (real labeled trade result column)"` + `baseline_net_positive_rate=0.4224`; hết synthetic tp/sl/time=0 |
| F1-RACE | race generator guard `--force` | chạy không `--force` → `[skip]`, md5 không đổi |
| Regression suite | pytest | **376 passed** rc=0 (t1 + QA chạy lại độc lập) |

QA_Auditor (t2) verdict: **PASS** — tự tái lập CLI/WF/top-prob, strict-check WF không null/NaN,
md5 snapshot đối chiếu 13/13 và diff rỗng sau re-run (deterministic).

## 2. Final honest metrics (frozen — from regenerated reports, train-only imputation)

### 2.1 Test set (195 events, single final split) — `reports/metrics/test_metrics.json`

| Metric | Giá trị |
|---|---|
| PR-AUC | **0.2275** |
| ROC-AUC | **0.6041** |
| Brier | 0.1561 |
| Accuracy | 0.7949 |
| Precision / Recall / F1 | 0.0 (không có lệnh positive nào được dự đoán ở ngưỡng mặc định) |
| positive_rate | 0.1795 (baseline ~0.18) |

### 2.2 Walk-forward OOS (5 folds × 97 events, purge+embargo) — `reports/walk_forward_report.json`

| Metric | Mean | Std | Min–Max |
|---|---|---|---|
| PR-AUC | **0.3552** | 0.0291 | 0.3108–0.3943 |
| ROC-AUC | 0.7219 | 0.0143 | 0.7029–0.7433 |
| Brier | 0.1276 | 0.0107 | 0.1084–0.1410 |
| Accuracy | 0.8289 | 0.0212 | 0.8041–0.8660 |
| positive_rate | 0.1814 | 0.0168 | 0.1546–0.2062 |

### 2.3 Top-probability subsets, OOS, real `net_result_r` — `reports/top_prob_subset_analysis.json`

| Threshold | n (có net) | P(net>0) | Avg net (R) | Profit Factor |
|---|---|---|---|---|
| >= 0.20 | 360 | 0.4222 | 0.0897 | **1.1653** |
| >= 0.25 | 223 | 0.4395 | 0.1619 | **1.3045** |
| >= 0.40 | 32 | 0.4375 | 0.1487 | **1.2784** |

Baseline (toàn bộ 973 net-valid): P(net_result_r > 0) = **0.4224**.

## 3. Frozen artifacts + md5 (nguồn: `reports/integration/t1_rerun_md5_snapshot.json`)

| # | Path | MD5 | Size (B) |
|---|---|---|---|
| 1 | `artifacts/models/model.pkl` (30 registry features, impute_medians 2, seed 42) | `69d65883bef616ebe23993478629fe45` | 4104 |
| 2 | `artifacts/models/calibrator.pkl` | `ca1d7e61b4a47f376e4c0d0a6a136615` | 2428 |
| 3 | `reports/metrics/test_metrics.json` | `3087b9e3639df95f2b5eb4d033175c4a` | 249 |
| 4 | `reports/walk_forward_report.json` | `59e67682b1e626d5f8f6ecf6b3810e94` | 3857 |
| 5 | `reports/walk_forward_report.md` | `2a1e726fb314bc8176c9ffae3478371a` | 2351 |
| 6 | `reports/figures/walk_forward_folds.png` | `cc82fd73a0f14161b262286d9e00d2af` | 146447 |
| 7 | `reports/top_prob_subset_analysis.json` | `d360daa483c8fd10caaffbae49730fac` | 4498 |
| 8 | `reports/top_prob_subset_analysis.md` | `0ef590b8204425422b2255bb03623b9b` | 2340 |
| 9 | `reports/score_bucket_report.json` | `a1b92f87d6bb12300b907721a44b7893` | 697 |
| 10 | `reports/score_bucket_report.md` | `6d0ce7062c38d1c07be70a667ed1137a` | 1182 |
| 11 | `reports/figures/score_bucket_performance.png` | `94beafe7766a7c6c2038f415954cb54d` | 121575 |
| 12 | `data/processed/labeled_events.parquet` | `fac22cea82ef7d0df646a9106239a70c` | 508831 |
| 13 | `artifacts/datasets/liquidity_sweep_events.parquet` | `fac22cea82ef7d0df646a9106239a70c` | 508831 |

- Dataset: **1 prepare version** (2 bản copy md5 giống nhau, n=974 events).
- Determinism: t1 re-capture diff rỗng; QA (t2) chụp lần 2 (S1/S2) diff rỗng → re-run không đổi,
  không ghi đè sau freeze.
- `model.pkl` feature_names = **30**, toàn bộ thuộc registry 32 (`level_type`/`volatility_regime`
  categorical bị loại bởi `select_dtypes(include=[np.number])` — feature loss theo thiết kế, không leak).
- Lệnh tái lập: `xauusd-audit && xauusd-events && xauusd-dataset && xauusd-train` (seed 42) — rc=0.

## 4. Kết luận trung thực (tiếng Việt)

1. **Model sạch (30 features, không look-ahead) phân biệt YẾU**: test PR-AUC 0.2275 ~ gần baseline
   positive_rate 0.1795, ROC-AUC 0.6041 (hơi trên 0.5); WF OOS PR-AUC mean 0.3552 (baseline OOS
   positive_rate 0.181). Model **chỉ dùng để ranking**, không dùng làm bộ quyết định trade.
2. **Subset top-prob OOS có PF > 1** (th 0.2/0.25/0.4: PF 1.1653/1.3045/1.2784) nhưng **n nhỏ**
   (360/223/32) và **P(net>0) của mọi subset đều nằm trong Wilson 95% CI chứa baseline 42.24%**
   (vd th0.25: CI [37.6–50.5%]; th0.4: CI [28.2–60.7%]) → **chưa có bằng chứng thống kê tách khỏi
   baseline**, kết quả chỉ mang tính **gợi ý**.
3. **Chi phí**: config hiện tại dùng cost = 0 (half_spread/slippage/commission 0 — placeholder,
   quyết định #4 của T16) nên `net_result_r` hiện ≈ gross; khi áp chi phí thực **~0.05R/trade**,
   expectancy toàn baseline (+0.014R) thành âm và biên của subset nhỏ chỉ còn marginal →
   **KHÔNG trade ML nguyên trạng**; tiếp tục dùng chiến lược B (rule-score) đã freeze làm bộ
   quyết định, ML chỉ để xếp hạng tham khảo.
4. **Phân biệt rõ hai khái niệm** (tránh nhầm lẫn đã từng xảy ra):
   - **'win'** = hit 2R target (tp) = 177/974 = **18.17%** (~18.2%, `baseline_win_rate` 0.1817);
   - **P(net_result_r > 0)** = **0.4224** (42.2% số lệnh có net_result_r dương sau cost model —
     gồm tp + một phần time-out với net dương).
   Hai con số khác nhau về định nghĩa, không thể so sánh trực tiếp.

## 5. Freeze gates (đã đóng)

- CLI `xauusd-train` rc=0, không NaN crash (t1 + QA tái lập).
- pytest **376 passed** rc=0 (QA chạy độc lập).
- WF report tái sinh từ train-only imputation (mtime 2026-09-04 16:04), strict-check không null/NaN.
- top-prob dùng `net_result_r` thật (`net_result_source`), không còn synthetic value.
- md5 ổn định qua re-run (snapshot S0 khớp t1 13/13; S1/S2 diff rỗng).
- QA_Auditor (t2) verdict: **PASS** (độc lập, không dựa báo cáo t1).

— End of freeze status v1.2.0 —
