# Báo cáo chi tiết hệ thống — XAUUSD M15 Liquidity Sweep

**Phiên bản báo cáo:** 1.0 · **Ngày:** 2026-09-04 · **Baseline đóng băng:** v1.2.0 (honest metrics, post leak-repair)
**Nguồn:** `huong_dan.md` (spec 2,778 dòng), `reports/freeze_status_v1.2.0.md`, `reports/bias_audit.md`, các report tái sinh đã md5-freeze.

---

## 1. Tổng quan

Hệ thống phát hiện & chấm điểm **Liquidity Sweep** cho **XAUUSD khung M15** theo hướng dẫn `huong_dan.md`: quét các cú quét thanh khoản (sweep) tại các vùng thanh khoản (swing/equal/previous-day high-low/rolling), xác nhận bằng nến xác nhận (confirmation), gán nhãn kết quả bằng **triple-barrier 2R/h16**, xây **2 bộ chấm điểm** (rule-based 0–100 theo §19 + ML ranking theo §22), kiểm soát chặt **no look-ahead** và **đóng băng baseline tái lập được (md5)**.

**Chặng đã qua:** 2 team AgentTeams (team 1: 34 tasks — audit & xây dựng & phát hiện leakage 45→32; team 2: 3 tasks — verify fix + QA độc lập + re-freeze v1.2.0). Toàn bộ deliverable là file trong workspace `xauusd-liquidity-sweep/`.

---

## 2. Kiến trúc & Pipeline

```
data/raw/XAUUSD_M15_202206020915_202608272245.csv (MT5 export, 99,692 bars)
   │  xauusd-audit      (src/pipelines/build_events.py: data_audit_cli)
   ▼
data/processed/xauusd_m15.parquet            (99,692 bars, UTC, schema chuẩn)
   │  xauusd-events     (build_events_cli — sweep + confirmation + dedup)
   ▼
data/processed/events.parquet                (3,772 events, group_rule=first causal)
   │  xauusd-dataset    (src/pipelines/build_dataset.py — features + labels + costs + rule score)
   ▼
data/processed/labeled_events.parquet        (974 events confirmed × 94 cột)
artifacts/datasets/liquidity_sweep_events.parquet (bản copy md5-giống hệt — 1 prepare version)
   │  xauusd-train      (src/pipelines/train_model.py — walk-forward + calibrate + eval)
   ▼
artifacts/models/model.pkl + calibrator.pkl  (30 features, impute_medians 2 cột, seed 42)
reports/metrics/test_metrics.json            (test PR-AUC 0.2275 / ROC 0.6041)
reports/walk_forward_report.{json,md}        (5 folds, PR-AUC OOS mean 0.3552)
reports/top_prob_subset_analysis.{json,md}   (OOS PF dùng net_result_r thật)
```

**4 CLI entry points** (pyproject `[project.scripts]`): `xauusd-audit`, `xauusd-events`, `xauusd-dataset`, `xauusd-train`. Lệnh tái lập toàn bộ: `xauusd-audit && xauusd-events && xauusd-dataset && xauusd-train` (seed 42) — rc=0.

**Cấu trúc mã nguồn** (`src/`, 15 module nhóm):
| Nhóm | Module chính | Chức năng |
|---|---|---|
| data | `loader.py`, `validator.py`, `resampler.py` | Nạp MT5 CSV → parquet chuẩn; validate; resample |
| indicators | `atr.py`, `trend.py`, `volume.py` | ATR(14) rolling-mean, atr_percentile_causal, trend, volume z-score |
| liquidity | `rolling_levels.py`, `swing_levels.py`, `equal_levels.py`, `level_registry.py` | Vùng thanh khoản rolling(20), swing fractal (3/3/200), equal pools (tol 0.10ATR, min 2 touches, max 200), registry causal |
| events | `sweep_detector.py`, `confirmation.py`, `deduplication.py` | Phát hiện sweep, xác nhận (close_break/displacement/structure_break), cooldown dedup |
| features | `feature_pipeline.py`, `htf.py`, `sessions.py`, `registry.py` | 32 features causal, registry `artifacts/feature_schemas/features.json` |
| labeling | `triple_barrier.py`, `excursions.py`, `outcome_builder.py` | Label 2R/h16, MFE/MAE, multi-horizon outcome, costs |
| scoring | `rule_score.py` | Rule score 0–100, 6 thành phần, bucket report |
| modeling | `train.py`, `split.py`, `calibration.py` | Prepare (whitelist 30 numeric), split purge+embargo, walk-forward, calibrate, evaluate |
| pipelines | `build_events.py`, `build_dataset.py`, `train_model.py` | CLI orchestration |
| visualization | `event_chart.py`, `score_chart.py`, `equity_curve.py`, `review_round2.py` | Biểu đồ nến sự kiện, bucket, equity |

---

## 3. Dữ liệu

**Nguồn:** MT5 export `XAUUSD_M15_202206020915_202608272245.csv`, 2022-06-02 → 2026-08-27.

| Kiểm tra | Kết quả |
|---|---|
| Row count | 99,692 (M15) |
| Duplicate / missing / invalid OHLC | 0 / 0 / 0 |
| Suspected gap | 1,112 |
| Volume | tick volume (cột VOL thật = 0 toàn bộ) |
| Timezone | File ở **broker server time** → chuẩn hóa UTC `auto`: ngày mở 01:00 → UTC+3, mở 00:00 → UTC+2; 753/754 maintenance break rơi đúng 20:45 UTC (khớp DST) |

**Tuyến sự kiện:** 99,692 bars → **3,772 sweep events** → **974 confirmed events** (primary definition: entry = next open sau confirmation, nhóm `group_rule=first` causal; 99 = 67 tail + 32 embargo — giải thích 974 vs 875). Khoảng thời gian labeled: 2022-06-05 → 2026-08-27.

**Dataset labeled:** 974 events × **94 cột** = event meta + 32 features registry + entry/stop/target + 20 cột excursion (mfe/mae/close_return max/min × h4/h8/h16/h32) + 12 outcome (3 reward × 4 horizon) + exit + cost (gross_result_r, cost_r, net_result_r) + 6 score components + rule_score.

> ⚠️ Các cột excursion/outcome **chỉ để phân tích/label**, không bao giờ vào feature (whitelist 32 chặn cứng — xem §5).

---

## 4. Phát hiện sự kiện (detection)

**Sweep detector** (`src/events/sweep_detector.py`): giá phá thủng vùng thanh khoản rồi đóng nến trở lại (reclaim).
- Sweep long: `low < liq_low` (strict) và `close > liq_low`; sweep short đối xứng.
- Bộ lọc: penetration 0.05–0.50 ATR, wick ratio ≥ 0.35, min reclaim 0.
- Cooldown 4 bars, **`group_rule: first`** — bar đại diện là bar ĐẦU của run (causal, không nhìn tương lai). Các biến thể `deepest_penetration`/`strongest_reclaim` chỉ dùng trong A/B, **không bao giờ** trong labeling/feature/scoring.

**Vùng thanh khoản** (tất cả causal, `known_at` đúng thời điểm):
- Rolling: lookback 20 (t3).
- Swing: fractal 3 trái/3 phải, max_age 200 bars, biết tại `t + right_bars` (t8).
- Equal pools: tolerance 0.10 ATR, min 2 touches, max_age 200, single-pass clustering (t8).
- Previous-day high/low + H1 swing flags.

**Confirmation** (`src/events/confirmation.py`): nến xác nhận trong ≤ 3 bars, phá vỡ extreme sweep, body ≥ 0.60, range ≥ 0.80 ATR; 3 loại: close_break / displacement / structure_break.

---

## 5. Feature registry — 32 features causal

Registry đóng băng: `artifacts/feature_schemas/features.json` — **32 features**, toàn bộ causal (không nhìn tương lai). Phân nhóm:

| Nhóm | Features |
|---|---|
| candle (5) | range_atr, body_ratio, lower_wick_ratio, upper_wick_ratio, close_location |
| sweep (4) | penetration_atr, reclaim_atr, wick_ratio, sweep_volume_zscore |
| liquidity (7) | level_type†, level_age_bars, level_touch_count, bars_since_last_touch, equal_level_dispersion_atr, level_is_previous_day_high_low, level_is_h1_swing, level_already_partially_swept |
| volatility (2) | atr_percentile, volatility_regime† |
| volume (1) | volume_zscore |
| time (5) | hour_utc, day_of_week, session_asia/london/new_york |
| htf (3) | h1_trend, distance_to_previous_day_high_atr, distance_to_previous_day_low_atr |
| confirmation (4) | confirmation_delay_bars, confirmation_range_atr, confirmation_body_ratio, confirmation_volume_zscore |

† `level_type`, `volatility_regime` là categorical → bị loại bởi `select_dtypes(include=[np.number])` trong modeling. **Model dùng 30 numeric** (Option B, t26) — không dùng LabelEncoder (tránh persist/leak preprocessing), inference nhận row thường có string.

**Chống leak:** `prepare_features_and_labels` dùng **whitelist** đúng 32 tên từ registry (không blacklist); 3 test chống hồi quy: `test_feature_count_is_32_from_registry`, `test_prefix_banned_columns_dropped` + suite truncation-invariance.

---

## 6. Labeling & Trade simulation

- **Entry:** `next_open_after_confirmation` (mode B). Stop = sweep extreme ± buffer 0.10 ATR. Target 1R/1.5R/2R.
- **Triple-barrier 2R/h16** (primary): barrier trên +2.0R (tp), dưới −1.0R (sl), thời gian 16 bars với kết quả mark-to-market; `same_bar_policy: ambiguous`.
- Outcome tokens: `{tp, sl, time, ambiguous}`. Phân bố 974: **sl 448 · time 348 · tp 177 · ambiguous 1** → win (hit 2R) = **18.17%**.
- **No look-ahead bắt buộc:** rolling `shift(1)`, swing `known_at = t+right_bars`, level state tại bar hiện tại, HTF chỉ dùng nến đã đóng; purge + embargo 32 bars giữa train/val/test.
- **Cost:** config hiện tại **cost = 0** (half_spread/slippage/commission 0 — placeholder, quyết định #4 T16) → `net_result_r` hiện ≈ gross. Cột `net_result_r` là kết quả thật (973 net-valid; 1 ambiguous NaN).

---

## 7. Chấm điểm

**A. Rule-based score 0–100** (`src/scoring/rule_score.py`, §19): 6 thành phần theo trọng số từ config (không hard-code):
level 20 · sweep 25 · reclaim 15 · confirmation 20 · context 10 · volume 10.

Kết quả bucket (cost-free, `reports/score_bucket_report.json`):
| Bucket | n | Win rate | Avg net (R) | PF |
|---|---|---|---|---|
| 0–39 | 783 | 18.9% | −0.0045 | 0.9914 |
| 40–49 | 188 | 15.4% | +0.0919 | **1.23** |
| 50–59 | 3 | 0% | −0.0419 | 0.8744 |

→ Bucket 40–49 có tín hiệu dương nhẹ (+0.092R, PF 1.23) nhưng **CI bao gồm 0** (n nhỏ, phân tích `reports/score_bucket_exit_reason.md`) — chỉ gợi ý, chưa đủ bằng chứng.

**B. ML model (ranking, §22):** LR → RF → [CatBoost/LightGBM] chọn best theo val PR-AUC; calibration isotonic trên validation; đánh giá 1 lần trên test (rule 30.5/30.6). Config final chỉ dùng **logistic_regression**. Kết quả trung thực — xem §8.

---

## 8. Metrics trung thực cuối (baseline v1.2.0, md5-frozen)

> Lịch sử: baseline v1.1.0 (T16, 2026-09-02) dính **leakage 45 features** (19 cột tương lai: mfe/mae/close_return/max/min h4/h8/h32 + bars_held + atr/risk_price) → metrics ảo (test ROC 0.981 / PR 0.876 / WF 0.968). Đã sửa whitelist (t22/t24), Option B (t26), impute train-only (t29), wire impute_medians CLI/WF (t32/team v2 t1), generator net_result_r thật (t32/team v2 t1) → **v1.2.0** (t3). Chi tiết: `reports/integration/T21_leakage_audit.md`, `reports/bias_audit.md` §8.8.

### 8.1 Test set (195 events, split cuối duy nhất)
| Metric | Giá trị |
|---|---|
| PR-AUC | **0.2275** |
| ROC-AUC | **0.6041** |
| Brier | 0.1561 |
| Accuracy | 0.7949 |
| Precision/Recall/F1 | 0.0 (không có lệnh positive ở ngưỡng mặc định) |
| positive_rate | 0.1795 (baseline ~0.18) |

### 8.2 Walk-forward OOS (5 folds × 97 events, purge+embargo)
| Metric | Mean | Std | Min–Max |
|---|---|---|---|
| PR-AUC | **0.3552** | 0.0291 | 0.3108–0.3943 |
| ROC-AUC | 0.7219 | 0.0143 | 0.7029–0.7433 |
| Brier | 0.1276 | 0.0107 | 0.1084–0.1410 |
| positive_rate | 0.1814 | 0.0168 | 0.1546–0.2062 |

Per-fold model: fold 0–2 LR, fold 3–4 RF (Δ thắng ~0.0001 = nhiễu).

### 8.3 Top-probability subset OOS — PF dùng `net_result_r` THẬT
| Ngưỡng | n (có net) | P(net>0) | Avg net (R) | PF |
|---|---|---|---|---|
| ≥ 0.20 | 360 | 0.4222 | +0.0897 | **1.1653** |
| ≥ 0.25 | 223 | 0.4395 | +0.1619 | **1.3045** |
| ≥ 0.40 | 32 | 0.4375 | +0.1487 | **1.2784** |

Baseline toàn bộ 973 net-valid: P(net>0) = **0.4224**. Wilson 95% CI của mọi subset đều chứa baseline (th0.25 [37.6–50.5%]; th0.4 [28.2–60.7%]).

### 8.4 Rule-score bucket 40–49
+0.092R, PF ~1.23, CI bao gồm 0 — **gợi ý, chưa đủ bằng chứng thống kê**.

---

## 9. Kết luận trung thực (từ freeze_status_v1.2.0.md)

1. **Model sạch phân biệt YẾU**: test PR-AUC 0.2275 ≈ baseline positive_rate 0.1795, ROC 0.6041 (hơi trên 0.5); WF OOS PR-AUC 0.3552. → **Chỉ dùng để ranking, không làm bộ quyết định trade.**
2. **Subset top-prob OOS PF > 1** (1.1653/1.3045/1.2784) nhưng n nhỏ (360/223/32) và CI chứa baseline → **chưa có bằng chứng thống kê tách khỏi baseline**; chỉ mang tính gợi ý.
3. **Chi phí:** cost hiện = 0 → net_result_r ≈ gross; áp cost thực ~0.05R/trade làm expectancy baseline (+0.014R) thành âm, biên subset chỉ marginal → **KHÔNG trade ML nguyên trạng**.
4. **Phân biệt khái niệm:** **'win'** = hit 2R tp = 18.17% (177/974) ≠ **P(net_result_r>0)** = 42.24% (gồm tp + một phần time-out net dương). Hai con số khác định nghĩa — không so trực tiếp.

---

## 10. Chất lượng & tái lập

- **pytest:** 376 passed rc=0 (20 test files, suite mở rộng; QA chạy độc lập). Suite no-lookahead truncation-invariance luôn xanh.
- **Md5 freeze:** `reports/integration/t1_rerun_md5_snapshot.json` — **13 artifacts** (model.pkl, calibrator.pkl, test_metrics, walk_forward_report json/md + figure, top_prob json/md, score_bucket json/md + figure, 2 dataset copies). QA chụp S0=S1=S2 → **diff rỗng** = deterministic, không ghi đè sau freeze.
- **model.pkl keys:** `{model, scaler, feature_names(30), impute_medians(2)}` — không encoders; imputation fit train-only theo từng split.
- **QA độc lập:** team v2 t2 verdict **PASS 8/8** (tự chạy lại CLI/WF/top-prob/inference smoke, md5 đối chiếu 13/13).
- **Bias audit:** `reports/bias_audit.md` — W4 (critical, leakage) → **RESOLVED**, §8.8; N1/N2 accepted; `reports/verification_checklist.json` đã đổi số thật + SUPERSEDED cũ.

**Môi trường:** Python 3.9.6 · sklearn 1.6.1 · pandas 2.3.3 · numpy 2.0.2 · interpreter `.venv/bin/python`.

---

## 11. Công việc mở (deferred / đề xuất)

| Hạng mục | Trạng thái | Ghi chú |
|---|---|---|
| Cost run thật (spread points→USD: half_spread = raw_spread×0.01/2, floor cho 5,215 rows spread=0) | Deferred | Để có PF/expectancy sau chi phí thật; dự kiến làm edge subset biến mất |
| Paper trading rule-score strategy B | Chưa làm | Chiến lược quyết định hiện tại (score 40–49 gợi ý) |
| Tuning thêm (features, thresholds, model selection) | Chưa làm | Nằm ngoài baseline đã đóng băng |
| Xóa team AgentTeams v2 | Hỏi user | 3 member idle; toàn bộ kết quả là file trong workspace |

---

*— Hết báo cáo hệ thống v1.0 —*
