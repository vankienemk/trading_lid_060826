# Stop-Buffer-by-Regime A/B test — frozen 974 (2026-09-06)

> **KẾT LUẬN: KHÔNG biến thể nào đạt tiêu chí thành công đã pre-register — giữ nguyên Control (0.10 / 0.10 / 0.10).** Cải thiện expectancy của Variant 2 chỉ **+0.0018R** (yêu cầu >0.05R); PF 95% CI của cả hai variant đều **chứa** control PF → không phân biệt được. Đây là **ước lượng TẠM (PROVISIONAL)** trên pool frozen 974 (XAUUSD 2022-06→2026-08), **không phải xác nhận cuối cùng** — dữ liệu paper-trading chưa đủ để xác nhận.

---

## Pre-registered criteria (chốt TRƯỚC khi chạy — không thay đổi sau khi thấy số)

| # | Tiêu chí |
|---|---|
| C1 | Expectancy net **OOS** cải thiện **> 0.05R** so với control |
| C2 | Wilson/bootstrap **95% CI của PF không chứa control PF** |
| C3 | **n ≥ 30** lệnh cho mỗi biến thể |

Biến thể cố định trước (không thêm/bớt sau khi thấy kết quả):

| Biến thể | stop_buffer_atr (low / normal / high) |
|---|---|
| Control (hiện tại) | 0.10 / 0.10 / 0.10 |
| Variant 1 | 0.15 / 0.10 / 0.08 |
| Variant 2 | 0.20 / 0.10 / 0.08 |

---

## Bảng kết quả chính (pooled, pre-cost, cost_r=0)

| Biến thể | n | PF | PF CI95 | Avg net R | Breakeven cost | Stop rate | Time rate | Target rate |
|---|---|---|---|---|---|---|---|---|
| Control | 973 | **1.0279** | [0.8923, 1.1831] | **+0.0140** | 0.0140 | 46.0% | 35.8% | 18.2% |
| Variant 1 | 973 | **1.0250** | [0.8906, 1.1791] | **+0.0126** | 0.0126 | 46.0% | 36.0% | 18.0% |
| Variant 2 | 973 | **1.0317** | [0.8954, 1.1863] | **+0.0158** | 0.0158 | 45.6% | 36.6% | 17.8% |

- **Anchor (control) trùng khớp frozen:** PF=1.0279, avg net=+0.0140R, P(net>0)=0.4224, n_net=973 — đúng bằng anchor trong `reports/analysis/cost_0_05r_analysis.md` (whole_pool).

### Đối chiếu tiêu chí

| Tiêu chí | Control | Variant 1 | Variant 2 | Kết quả |
|---|---|---|---|---|
| C1: Δ expectancy > 0.05R | — | -0.0014R | +0.0018R | ❌ FAIL (kém xa 0.05R) |
| C2: PF CI không chứa control PF (1.0279) | — | [0.8906, 1.1791] chứa 1.0279 | [0.8954, 1.1863] chứa 1.0279 | ❌ FAIL (cả hai) |
| C3: n ≥ 30/variant | 973 | 973 | 973 | ✅ PASS |

**Không biến thể nào đạt tiêu chí (0/2) → Giữ nguyên Control.**

---

## Biểu đồ

- `stop_buffer_pf_comparison.png` — bar chart PF của 3 biến thể, kèm CI95 (bootstrap percentile, B=3000, seed=42), đường kẻ ngang PF=1.0. Cả 3 cột ~1.03, CI của cả 3 đều **trùm qua** 1.0 → không biến thể nào vượt ngưỡng rõ ràng.
- `stop_buffer_mae_distribution.png` — histogram + boxplot phân phối MAE_R của các **lệnh thua**, Control vs Variant 2, đường tham chiếu MAE_R=1.0 (stop cũ).

---

## Hiệu ứng cơ chế trên lệnh thua (MAE_R)

| Nhóm | Control (0.10) | Variant 2 (low 0.20) |
|---|---|---|
| Số lệnh thua (tổng) | 562 | 559 |
| Số lệnh thua regime low | 230 | 226 |
| median MAE_R (tổng loser) | 1.627 | 1.613 |
| median MAE_R (regime low loser) | **1.874** | **1.788** |
| median MAE_R (regime high loser) | 1.289 | 1.279 |

- Phân phối MAE_R **regime low** của control tái tạo đúng phân tích cũ (median 1.874, mean 2.365, stop share 87.8%) → cơ sở so sánh nhất quán.
- Nới buffer (0.10→0.20 ở low) **giảm** MAE_R của loser regime low và giảm nhẹ số loser. Cơ chế đúng hướng.
- **Nhưng** khoản giảm rất nhỏ và **không chuyển thành cải thiện expectancy**: nới buffer đồng thời **tăng risk** (stop xa hơn → target 2R xa hơn, khó hit hơn) và chuyển một phần stop-out thành time-out thay vì thành target hit. Trade-off ròng ≈ 0, nằm trong nhiễu.

---

## Phân tách theo regime (context)

| Regime | n | Control PF | Control avg R | V1 PF | V1 avg R | V2 PF | V2 avg R |
|---|---|---|---|---|---|---|---|
| low | 391 | 1.0758 | +0.0411 | 1.0668 | +0.0360 | 1.0832 | +0.0441 |
| normal | 310 | 1.0275 | +0.0144 | 1.0275 | +0.0144 | 1.0275 | +0.0144 |
| high | 272 | 0.9396 | -0.0254 | 0.9456 | -0.0231 | 0.9456 | -0.0231 |

---

## Walk-forward (5 folds theo thời gian) — kiểm tra tính ổn định

| Fold | Khoảng thời gian | n | Control PF | V1 PF | V2 PF | Control avg | V1 avg | V2 avg |
|---|---|---|---|---|---|---|---|---|
| 1 | 2022-06-05 → 2023-03-16 | 195 | 0.8170 | 0.8033 | 0.8014 | -0.1011 | -0.1097 | -0.1107 |
| 2 | 2023-03-16 → 2024-02-12 | 195 | 1.5520 | 1.5392 | 1.5808 | +0.2279 | +0.2226 | +0.2367 |
| 3 | 2024-02-13 → 2024-12-05 | 194 | 0.9702 | 0.9917 | 0.9918 | -0.0152 | -0.0042 | -0.0041 |
| 4 | 2024-12-06 → 2025-09-22 | 195 | 0.9137 | 0.8872 | 0.9110 | -0.0466 | -0.0614 | -0.0479 |
| 5 | 2025-09-23 → 2026-08-27 | 195 | 1.0098 | 1.0316 | 1.0104 | +0.0048 | +0.0155 | +0.0051 |

→ Các biến thể **không** thắng control nhất quán qua các fold: lúc kém hơn (fold 1, 4), lúc nhỉnh hơn rất nhẹ (fold 3, 5), fold 2 gần như ngang. **Không có cải thiện ổn định.**

---

## Cảnh báo — đây là ƯỚC LƯỢNG TẠM, không phải xác nhận cuối cùng

- **Dữ liệu paper trading chưa đủ n** → đây là phương án tạm: **relabel pool frozen 974** với 3 biến thể, đánh giá bằng **walk-forward**. Không dùng để chọn tham số mới — chỉ ước lượng sơ bộ trong lúc chờ đủ dữ liệu live.
- **Pool frozen 974 = XAUUSD 2022-06 → 2026-08** (`data/processed/labeled_events.parquet`) — **không** đụng tới vùng dữ liệu đã 'cháy' (XAUUSD 2018→2022-06, EURUSD Full) → tuân thủ nguyên tắc *no reusing burned data to tune params*.
- Cả 3 biến thể được pre-register **trước** khi chạy; tiêu chí ghi trước như trên. Kết luận 'giữ nguyên control' là kết quả trung thực của pre-registration, không phải 'điều chỉnh tiêu chí sau khi nhìn số'.
- Để xác nhận cuối cùng cần **paper-trading OOS thật** đủ n trước khi đổi bất kỳ config nào trong Signal Engine.

---

## Phương pháp (tóm tắt)

- **Stop rule** (`triple_barrier.compute_trade_levels`): stop = sweep extreme ± `buffer·ATR`; risk = |entry − stop|; target = ±2R. Nới/nới-bớt buffer chỉ thay đổi stop/risk/target/outcome; **entry, sweep_extreme, ATR giữ nguyên**.
- **Re-simulation**: `label_event_arrays` (horizon=16, reward_r=2, ambiguous, mark_to_market) với buffer theo `volatility_regime` của từng event. Buffer `None/NaN` regime (1 event đầu chuỗi, SWP-000006) gán = normal (0.10) cho mọi biến thể → trung tính.
- **CI**: percentile bootstrap 95% (B=3000, seed=42). **PF** = tổng lãi / tổng lỗ (pre-cost, cost_r=0). **Breakeven cost** = mức cost c tại đó PF=1 ⟺ c = avg net R (khớp `cost_0_05r_analysis.md`).
- **n** = trades net-valid, loại 1 ambiguous (control 973; trùng anchor frozen). cost_r=0 nên net == gross.
- Sinh ra từ: `pipeline_v2/scripts/analysis/stop_buffer_regime_ab.py` → `stop_buffer_regime_ab_20260906.json` + `..._events.parquet`; chart từ cùng script.
