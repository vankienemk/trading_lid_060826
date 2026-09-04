# Báo Cáo Tổng Hợp Phase 1 — Tái Định Nghĩa Setup XAUUSD M15 Liquidity Sweep

**Ngày:** 2026-09-04  
**Nguồn dữ liệu:** Frozen dataset v1.2.0 — 974 events, 973 net-valid (1 ambiguous)  
**Pipeline:** Gate t8 đã verify — 13/13 frozen md5 intact, parity pass, 3 scripts rc=0  

---

## 1. Tại Sao Edge Biến Mất — Phân Tích Gốc Rễ

### 1.1 Exit Reason Breakdown — Cấu Trúc Kỳ Vọng

Toàn bộ pool (n=973) ở cost=0R:

| Exit Reason | n | Share | Avg Return | PF | Contribution |
|---|---|---|---|---|---|
| Target (win) | 177 | 18.2% | +2.0R | ∞ | +0.3638R |
| Stop (loss) | 448 | 46.0% | -1.0R | 0.0 | **-0.4604R** |
| Time (MTM) | 348 | 35.8% | +0.3092R | 3.65 | +0.1106R |

**Tổng expectancy:** +0.014R  

**Thông điệp chính:** Margin edge toàn bộ pool chỉ **+0.014R** — gần bằng không. Stop contribution (-0.46R) gần như triệt tiêu target (+0.36R). Chỉ nhờ time-out mark-to-market (+0.11R) mà expectancy còn dương.

### 1.2 So Sánh Score Bucket 0-39 vs 40-49

| Metric | Bucket 0-39 (n=782) | Bucket 40-49 (n=188) | Thay đổi |
|---|---|---|---|
| PF | 0.9914 | **1.23** | +0.24 |
| Avg return | -0.0045R | **+0.0919R** | +0.096R |
| P(net>0) | 0.405 | 0.489 | +8.4% |
| Stop rate | 48.7% | **35.1%** | -13.6% |
| Target rate | 18.9% | 15.4% | -3.5% |
| Time rate | 32.3% | **49.5%** | +17.2% |

**Kết luận quan trọng:** Edge của Rule 40-49 **không đến từ hit target tốt hơn** (R:R giống nhau +2/-1 ở mọi bucket). Edge đến từ **stop rate thấp hơn 13.6%** — tức setup tránh được stop nhiều hơn, cho time-out nhiều hơn cơ hội ghi nhận close dương.

### 1.3 Trend Context — cung_chiều vs ngược

| Group | n | PF (cost=0) | Avg R | Breakeven |
|---|---|---|---|---|
| whole_pool | 973 | 1.0279 | +0.014R | 0.014R |
| sweep_cung_chieu (fade trend) | 698 | **0.9804** | **-0.0098R** | 0.0R (chết trước cost) |
| sweep_nguoc (follow trend) | 275 | **1.1488** | **+0.0745R** | 0.0745R |
| cung_rule_40_49 | 133 | 1.1543 | +0.0636R | 0.0636R |
| nguoc_rule_40_49 | 55 | 1.4341 | +0.1605R | 0.1605R |

**Phát hiện cốt lõi:**
- **71.8% pool là sweep_cung_chieu** — và nhóm này đã âm ngay cả ở cost=0R (PF 0.98). Đây là nguyên nhân chính kéo toàn bộ expectancy xuống.
- **sweep_nguoc (28.2% pool) giữ PF >1** ở cost 0 đến cost 0.07R. Đây là subset khả quan nhất.
- Time-exit rate khác biệt cung (37.5%) vs ngược (31.3%) không significant (z=1.84, p=0.0665).

### 1.4 Gốc Rễ Tổng Quan

| Root Cause | Impact |
|---|---|
| **Cấu trúc R:R +2/-1 thiếu robust** | Stop loss (-1R) quá nặng so với target (+2R). Với win rate ~18%, mỗi stop phá hủy ~5 target |
| **sweep_cung_chieu chiếm 72% pool** | Nhóm này PF <1 ở cost=0 — kéo sập toàn bộ expectancy |
| **Cost kill threshold quá thấp** | Breakeven toàn pool chỉ 0.014R — bất kỳ cost thực tế nào (0.03–0.05R) cũng giết edge |
| **Time-out là lifeline, không phải edge** | 35.8% events thoát nhờ time — nếu time barrier bị đóng (zero), PF rơi từ 1.03 xuống 0.79 |

---

## 2. Breakeven Map — Subset Nào Còn Edge Ở Mức Cost Nào

### 2.1 Breakeven Table

| Group | n | Breakeven Cost | Expectancy@0.05R | PF@0.05R |
|---|---|---|---|---|
| whole_pool | 973 | **0.014R** | **-0.036R** **↓1** | 0.9322 |
| rule_40_49 | 188 | **0.0919R** | **+0.0419R** ✅ | 1.0986 |
| top_prob≥0.25 | 223 | **0.1619R** | **+0.1119R** ✅✅ | 1.1999 |
| top_prob≥0.20 | 360 | 0.0897R | +0.0397R ✅ | 1.0694 |
| sweep_nguoc | 275 | **0.0745R** | **+0.0245R** ✅ | 1.0463 |
| level_type_swing | 473 | 0.0382R | -0.0118R | 0.9771 |
| cung_rule_40_49 | 133 | 0.0636R | +0.0136R | 1.0152 |
| nguoc_rule_40_49 | 55 | 0.1605R | +0.1105R ✅✅ | 1.2894 |

### 2.2 Chiến Lược Cost

**Với cost 0.05R (broker thực tế thấp):**
- Còn edge: **rule_40_49 (+0.042R), top_prob≥0.25 (+0.112R), sweep_nguoc (+0.025R)**
- Mất edge: whole_pool (mất -0.036R), level_type_swing, level_type_equal

**Với cost 0.01R–0.02R (siêu thấp):**
- whole_pool hòa (PF 1.01–0.99), expectancy ~0
- level_type_swing còn nhẹ (avg +0.028R → +0.018R)
- rule_40_49 giữ tốt (PF 1.20 → 1.18)

### 2.3 Dịch Chuyển PF Theo Cost

- **whole_pool:** PF 1.03 @0.00 → **0.93 @0.05** (xuống 9.3%)
- **rule_40_49:** PF 1.23 @0.00 → **1.10 @0.05** (xuống 10.8%, nhưng vẫn >1)
- **sweep_nguoc:** PF 1.15 @0.00 → **1.05 @0.05** (xuống 9.0%, vẫn >1)
- **top_prob≥0.25:** PF 1.30 @0.00 → **1.20 @0.05** (xuống 8.0%, mạnh nhất)

**Chi tiết:** rule_40_49 giữ PF>1 cho đến cost **0.09R**, sweep_nguoc cho đến **0.07R**, top_prob≥0.25 cho đến trên **0.10R**.

---

## 3. Parameter Sensitivity — Tham Số Nào Nhạy Nhất

### 3.1 Ranking Parameters by PF Impact

| Parameter | Current Value | Best Setting | PF Impact | N Impact |
|---|---|---|---|---|
| **1. max_penetration_atr** | **0.50** | **≤0.20** | **1.03 → 1.29 (+25%)** | 973→396 (-59%) |
| 2. time_barrier policy | mark_to_market | time_as_0.5R | 1.03 → **1.18 (+15%)** | Không đổi |
| 3. min_penetration_atr | 0.05 | **0.05 (giữ nguyên)** | Giảm nếu raise | 973→853 (-12%@0.10) |
| 4. min_reclaim_atr | 0.00 | 0.02 (nhẹ) | Gần như phẳng (~1.03) | 973→962 (-1%) |
| 5. min_wick_ratio | 0.35 | **0.35 (giữ nguyên)** | Giảm nếu raise | 973→700 (-28%@0.45) |

### 3.2 Phân Tích Chi Tiết

**max_penetration_atr (độ xuyên thủng tối đa): NHẠY NHẤT**
- Giảm từ 0.50 → 0.20: PF tăng 1.03→1.29, avg return +0.131R (CI95 [0.011, 0.243] — significant!)
- Chi phí: mất 59% events (chỉ giữ 396). Đây là trade-off coverage vs quality.
- 95% CI at 0.20: [1.03, 1.60] — PF ≠ 1 ở mức 95% tin cậy.

**min_penetration_atr (độ xuyên thủng tối thiểu): ỔN ĐỊNH**
- Raise từ 0.05→0.10: PF giảm nhẹ (1.03→0.99), mất 12% events. **Không khuyến nghị raise.**

**min_wick_ratio (tỷ lệ bấc): KHÔNG NHẠY Ở BIÊN HIỆN TẠI**
- Raise từ 0.35→0.45: PF giảm (1.03→0.99). Về mặt lý thuyết wick lớn → setup chất lượng hơn, nhưng thực tế không.
- Raise mạnh 0.70: PF 1.08 (n=161, 16.5% kept) — quá ít để tin cậy.

**min_reclaim_atr (mức reclaim): KHÔNG NHẠY — NON-FACTOR**
- PF dao động 1.02–1.04 cho mọi threshold. Có thể giữ 0.00 hoặc raise đến 0.15 mà không ảnh hưởng đáng kể.

**time_barrier exit policy: QUAN TRỌNG**
- Mark-to-market hiện tại là đúng: zero_time_exits làm PF rơi từ 1.03 xuống 0.79.
- **time_as_0.5R (giả time-out thành +0.5R): PF 1.18, avg +0.082R, P(net>0) 53.96%** — đây là chính sách tối ưu nhất về PF.
- Lưu ý: time_as_0.5R là scenario giả định (không phải chính sách có thể trade thực tế), nhưng cho thấy time-out mark-to-market là lifeline quan trọng.

### 3.3 Combo Analysis

| Combo | n | PF | Avg R | Fraction |
|---|---|---|---|---|
| rule_40_49 & max_pen≤0.20 | **77** | **1.50** | **+0.171R** | 7.9% |
| + min_wick≥0.35 | 77 | 1.50 | +0.171R | 7.9% |

Combo rule_40_49 + max_pen≤0.20 cho PF 1.50, avg +0.171R. Nhưng chỉ 77 events (7.9%) — sample quá nhỏ, CI95 rộng [0.87, 2.55].

---

## 4. Concrete Recommendations

### 4.1 Dừng/Lưu Trữ Pipeline Hiện Tại

**Khuyến nghị: DỪNG trade live setup Liquidity Sweep hiện tại.**
- Baseline expectancy -0.036R ở cost 0.05R, PF 0.93 — không thể trade.
- 71.8% pool (sweep_cung_chieu) âm ngay cả trước cost.
- R1/R2 từ team v2 đã outdated — cần định nghĩa lại hoàn toàn.

### 4.2 Tái Định Nghĩa Setup — Event Definition v2

**Đề xuất định nghĩa setup mới dựa trên Phase 1 findings:**

1. **Giới hạn trend context:**
   - Chỉ trade sweep_nguoc (follow H1 trend) — subset 275 events, PF 1.05 @0.05R
   - Loại bỏ sweep_cung_chieu (fade trend) — 699 events âm

2. **Score threshold:**
   - Khuyến nghị filter score ≥40 (rule 40-49 hoặc tương đương)
   - Breakeven 0.092R, PF 1.10 @0.05R
   - Hoặc top_prob≥0.25 (n=223, PF 1.20 @0.05R)

3. **Tighten max_penetration:**
   - Giảm từ 0.50 xuống 0.20 hoặc 0.30
   - Tại 0.20: PF 1.29 (CI95 1.03–1.60), avg +0.131R — **significant**
   - Tại 0.30: PF 1.09, giữ 65% events

4. **Giữ nguyên các tham số khác:**
   - min_penetration_atr = 0.05 (optimal)
   - min_wick_ratio = 0.35 (không lợi ích khi raise)
   - min_reclaim_atr = 0.00 (non-factor)
   - time_barrier = mark_to_market (tối ưu cho live trading)

5. **Cân nhắc R:R adjustment:**
   - R:R +2/-1 là thiếu robust cho setup hiện tại
   - Khuyến nghị nghiên cứu R:R +1.5/-1 hoặc target sớm hơn (partial take-profit) để cải thiện win rate

### 4.3 Điều Chỉnh Cost Model

| Mức độ | Hành động | Chi tiết |
|---|---|---|
| **Bắt buộc** | Sử dụng cost 0.05R trong mọi backtest forward | Mô phỏng broker thực tế. Baseline 0.05R (spread 2pip + commission 3pip trên XAUUSD). |
| **Khuyến nghị** | Test cost 0.03R (tối ưu ưu đãi) | Nếu broker cho cost thấp hơn, rule_40_49 expectancy tăng lên +0.062R. |
| **Cảnh báo** | Không trade ở cost <0.01R assumption | Breakeven toàn pool 0.014R — bất kỳ backtest bỏ qua cost đều sai lệch. |

### 4.4 V2 Setup — Definitive Filter Stack

```
Setup v2 (proposed):
  1. sweep_direction = nguoc_trend (follow H1)
  2. model_score ≥ 0.25 (top_prob)  **HOẶC**  rule_calibrated ≥ 40
  3. penetration_atr ≤ 0.20  (hoặc 0.30 nếu muốn coverage hơn)
  4. wick_ratio ≥ 0.35  (giữ nguyên)
  5. reclaim_atr ≥ 0.00  (giữ nguyên)
  6. Exit: mark_to_market time barrier, R:R = +2/-1
```

**Expected metrics (ước lượng từ combo data):**
- n ≈ 50–80 events (trên 974 gốc)
- PF ~1.35–1.50
- Avg return ~+0.15–0.17R
- Win rate ~45–50%
- 95% CI vẫn rộng do sample nhỏ — cần x