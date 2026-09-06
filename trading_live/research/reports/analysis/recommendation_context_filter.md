# Khuyến nghị dựa trên bằng chứng — Filter context / Stop buffer / Level_type

**Tài liệu tổng hợp t4 + t5** · Ngày: 2026-09-04 · Tác giả: Lead_Integrator (t7, team v2)
**Phạm vi:** doc khuyến nghị thuần — **KHÔNG sửa code, KHÔNG đụng baseline v1.2.0** (src/, artifacts/,
data/, configs/, freeze_status v1.2.0 giữ nguyên). Mọi số liệu lấy từ file đã QA xác nhận deterministic
(md5 ổn định) trong `reports/analysis/`:
`level_type_quality.{json,md}` (t4) · `trend_context_analysis.{json,md}` (t4) ·
`stop_buffer_regime.{json,md}` (t4) · `level_type_model_analysis.{json,md}` (t5).

**Dữ liệu nền:** 974 confirmed events frozen (973 net-valid), `net_result_r` **thật**; config chi phí hiện
tại = 0 → `net_result_r` ≈ gross (trước chi phí thực). Xác suất model = **OOS** (5-fold CV seed 42),
parity anchors PASS: test-split PR-AUC 0.2275 ≡ frozen `test_metrics.json`; top-prob PF
1.1653/1.3045/1.2784 ≡ frozen `top_prob_subset_analysis.json`; WF OOS PR-AUC mean 0.3552 ≡ frozen report.

> **Ngữ nghĩa `level_type` (quan trọng):** cả 974 confirmed event đều là sweep của rolling level
> (`level_id` rolling_high/low — baseline), nhưng cột `level_type` mang **structural registry level**
> (equal/swing/prev_day) mà feature pipeline gán cho từng event → **`rolling` có n=0 trong frozen pool**.
> So sánh rolling-vs-structural phải dùng review-round-2 pool (chart ground truth, `manual_review_v2.csv`).

---

## 0. Tóm tắt điều hành

| Giả thuyết (từ chart review) | Mức ủng hộ của bằng chứng | Khuyến nghị |
|---|---|---|
| (a) Chỉ giữ swing/equal/prev_day (bỏ rolling) cải thiện model | **KHÔNG được hỗ trợ** — rolling n=0 trong frozen pool; nhóm gộp == toàn pool | Giữ nguyên model pool. Không có gì để "bỏ" |
| (b) Sweep cùng chiều trend H1 (catch-the-knife) kém hơn | **Yếu–vừa** — đúng hướng (win 0.165 vs 0.225, p≈0.03 chưa hiệu chỉnh) nhưng P(net>0) không khác | Thử nghiệm (penalty trong rule-score), **chưa** hard-filter |
| (c) Stop buffer 0.10·ATR quá sát ở regime thấp | **Tương hợp mạnh trên phân tích loser** (stop share 88% vs 63%; p<1e-5) | Thử nghiệm A/B re-label toàn pool trước khi đổi config |

**Kết luận chính: không có can thiệp nào đủ bằng chứng để sửa baseline v1.2.0 ngay.** Cả 3 hướng đều là
ứng viên **thí nghiệm A/B out-of-sample**, theo quy trình §4; chỉ re-freeze sau khi thắng trên OOS.

---

## 1. Trả lời giả thuyết (a) — level_type: swing/equal/prev_day có tốt hơn rolling không?

### 1.1 Frozen pool (974) — theo structural level_type

| level_type | n | win_rate (hit 2R) [CI95] | P(net>0) [CI95] | avg_net_r | PF | PR-AUC OOS | ROC-AUC OOS |
|---|---|---|---|---|---|---|---|
| rolling | 0 | — | — | — | — | — | — |
| swing | 473 | 0.1818 [0.1497, 0.2191] | 0.4334 [0.3895, 0.4784] | +0.0382 | 1.0788 | 0.2510 | 0.6144 |
| equal | 487 | 0.1852 [0.1532, 0.2221] | 0.4177 [0.3747, 0.4620] | +0.0051 | 1.0100 | 0.2663 | 0.6155 |
| prev_day | 14 | 0.0714 [0.0127, 0.3147] | 0.2143 [0.0757, 0.4759] | −0.4975 | 0.2783 | 0.1000 | 0.3077 |
| **swing+equal+prev_day (= toàn pool)** | 974 | 0.1817 [0.1588, 0.2072] | 0.4224 [0.3917, 0.4537] | +0.0140 | 1.0279 | 0.2546 | 0.6164 |

- **PR-AUC phụ thuộc baseline** (positive_rate riêng: swing 0.182, equal 0.185, prev_day 0.071) → đọc
  ROC-AUC (0.614 vs 0.616: **không tách biệt**) khi so giữa nhóm.
- Vì nhóm gộp **là** toàn pool (n=974, identity assert trong script t5), mệnh đề "chỉ giữ
  swing/equal/prev_day cải thiện PR-AUC" **không có đối tượng để thực thi**: pooled PR-AUC 0.2546 ==
  whole-pool CV-OOS 0.2546. Chênh lệch 0.2275 (test-split 195 ev) vs 0.2546 (CV 974) vs 0.3552 (WF) là
  **khác biệt protocol**, không phải chất lượng model khi lọc level_type.
- prev_day (n=14 < 30): chất lượng thực hiện tệ nhất mọi chiều (PF 0.2783, P(net>0) 0.2143
  [0.0757, 0.4759], PR-AUC OOS 0.100 ≈ ngang mức random với positive rate tp 0.071, ROC 0.308 < 0.5)
  nhưng **n quá nhỏ, CI rộng — không kết luận**.

### 1.2 Review-round-2 pool (chart ground truth — nơi duy nhất có rolling)

| level_type | source pool | n | P(net>0) [CI95] | avg_net_r | PF |
|---|---|---|---|---|---|
| rolling | confirmed | 71 | 0.5070 [0.3934, 0.6199] | +0.3322 | 1.7881 |
| equal | level_sweep | 27 | 0.4074 [0.2451, 0.5927] | +0.1918 | 1.3237 |
| swing | level_sweep | 28 | 0.2500 [0.1268, 0.4336] | −0.3088 | 0.5883 |
| prev_day | level_sweep | 24 | 0.1667 [0.0668, 0.3585] | −0.5000 | 0.4000 |

→ Trên mẫu chart-truth, **rolling KHÔNG phải nhóm kém nhất** (thậm chí tốt nhất P(net>0) 0.507, PF
1.788). Mẫu stratified + n nhỏ → **chỉ mang tính gợi ý**, nhưng đủ để bác mặc định "rolling = pha loãng".

### 1.3 Kết luận (a)

**KHÔNG hỗ trợ.** Không có sự kiện level_type='rolling' nào trong frozen 974 để loại; lọc structural
không đổi được PR-AUC/PF; dữ liệu chart-truth còn nghiêng về phía rolling không tệ. **Không thay đổi
model pool.** prev_day đáng theo dõi nếu dữ liệu mở rộng (n>30) — chưa đủ n hôm nay.

---

## 2. Trả lời giả thuyết (b) — trend context "catch-the-knife"

`h1_trend` = dấu của slope EMA(50) H1 tại bar event (causal, **binary sign ±1 — không có cường độ**).

### 2.1 Nhóm gộp (định nghĩa đúng nghĩa "sweep cùng chiều trend H1 mạnh")

- **Nhóm "bắt dao" — sweep cùng chiều đà H1 (cung chieu):** SHORT khi H1 uptrend (up-thrust qua high
  trong sóng lên) **hoặc** LONG khi H1 downtrend (down-thrust qua low trong sóng xuống) — lệnh fade đà H1
  tại extreme. Chart case thua LVL-000561 (short @ prev_day_high trong rally mạnh), LVL-002241
  (long @ equal_low trong downtrend) thuộc nhóm này.
- **Nhóm ngược trend:** LONG khi H1 uptrend / SHORT khi H1 downtrend — đà sweep ngược trend, lệnh đi theo
  trend H1.

| group | n (net) | win_rate (2R) [CI95] | P(net>0) [CI95] | avg_net_r | PF |
|---|---|---|---|---|---|
| sweep **cung** chieu trend H1 (dao) | 698 | **0.1648** [0.1391, 0.1941] | 0.4212 [0.3837, 0.4567] | −0.0098 | **0.9804** |
| sweep **nguoc** trend | 275 | **0.2255** [0.1800, 0.2784] | 0.4255 [0.3684, 0.4845] | +0.0745 | **1.1488** |

Chi tiết 2×2 (direction × h1_trend) đồng thuận: LONG+H1-up win 0.2162 vs LONG+H1-down 0.1554;
SHORT+H1-down 0.2362 vs SHORT+H1-up 0.1737 → **giao dịch ngược đà H1 luôn có win thấp hơn**.

### 2.2 Định lượng mức ủng hộ

- Win-rate (hit 2R): knife 0.1648 vs counter 0.2255 — CI gần chạm (upper 0.1941 vs lower 0.1800);
  chi-square 2×2 p≈0.027 (Yates p≈0.034, **chưa hiệu chỉnh multiple-testing**).
- **P(net>0): 0.4212 vs 0.4255 — gần như bằng nhau (p≈0.90).** Khác biệt nằm ở *đuôi thắng 2R*, không
  phải xác suất có lời. Nhóm dao vẫn ~42% lệnh dương net — phần lớn là các lệnh time-out/thắng nhỏ.
- Trọng lượng: nhóm dao chiếm **698/973 ≈ 72%** pool → hard-filter sẽ xoá 2/3 tín hiệu, rất nặng.

### 2.3 Kết luận (b)

**Đúng hướng nhưng mức ủng hộ yếu–vừa, chưa đủ để hard-filter.** Khác biệt win-rate có ý nghĩa thống kê
biên (p≈0.03), P(net>0) không khác; phân tích là mô tả in-sample trên chính pool định nghĩa nhóm; và
**"H1 mạnh" chưa được đo** (chỉ có dấu ±, frozen data không có cột cường độ trend). Khuyến nghị: thí
nghiệm **giảm điểm rule-score cho nhóm dao** (không chặn trade, không đổi detector), hoặc bổ sung feature
cường độ trend ngoài baseline để phân biệt "mạnh" thật sự (§3, §4).

---

## 3. Trả lời giả thuyết (c) — stop buffer 0.10·ATR có quá sát ở regime thấp?

Phạm vi: 562 lệnh thua (`net_result_r<0`: 448 stop + 114 time). MAE_R = `mae_r_h16` frozen. Stop =
swept extreme ± 0.10·ATR; risk = |entry − stop|; target ±2R. (MAE_R − 1R ≈ độ sâu wick vượt stop trong
bar exit; không gồm bar sau exit.)

| regime | n loser | stop/time | MAE_R median | p75 | p90 | P(MAE>1R) [CI95] | P(MAE>1.5R) [CI95] | buffer price | buffer % risk |
|---|---|---|---|---|---|---|---|---|---|
| **low** | 230 | 202/28 (87.8%) | 1.874 | 2.823 | 4.320 | 0.878 [0.830, 0.914] | 0.687 [0.624, 0.743] | 0.196 | 5.0% |
| normal | 180 | 150/30 (83.3%) | 1.613 | 2.511 | 3.912 | 0.833 [0.772, 0.881] | 0.539 [0.466, 0.610] | 0.267 | 5.5% |
| **high** | 151 | 95/56 (62.9%) | 1.289 | 1.992 | 2.787 | 0.629 [0.550, 0.702] | 0.397 [0.323, 0.477] | 0.401 | 5.7% |

- **Mann-Whitney low vs high (MAE_R loser):** U=23504, **p=5.3e-9**; P(MAE>1R) low vs high: chi-square
  p<1e-5; stop share low 0.878 [0.830, 0.914] vs high 0.629 [0.550, 0.702] — tách biệt rõ.
- Diễn giải: loser regime thấp bị **stop nhiều hơn hẳn** (87.8% vs 62.9%) và **pierce sâu hơn** (median
  MAE_R 1.87R ⇒ ~0.87R vượt stop vs high ~0.29R) — tương hợp case chart LVL-003144 (swing_low: retest
  wick ở extreme trong ATR nhỏ chạm stop trước khi kịp target 2R).
- Sắc thái quan trọng: **buffer % risk ~5% ở mọi regime** (5.0/5.5/5.7%) — buffer *tương đối theo ATR*
  gần như hằng số; cái khác là quy mô giá tuyệt đối (0.196 vs 0.401) và độ sâu pierce.

### 3.1 Kết luận (c)

**Tương hợp mạnh nhất trong 3 giả thuyết — nhưng chỉ trên phân tích loser.** Chưa định lượng được tác
động ròng lên expectancy toàn pool: buffer rộng hơn làm risk/trade lớn hơn (R scale), đổi win rate, đổi
kích thước lệnh, có thể chuyển một phần stop-loser thành winner 2R **hoặc** chỉ làm thua nhiều hơn.
→ Khuyến nghị: thí nghiệm A/B re-label toàn pool với stop buffer theo regime (§4) trước khi đổi config;
đây là thí nghiệm rẻ nhất (chỉ đổi tham số labeling, không đụng model/detector).

---

## 4. Khuyến nghị hành động cụ thể (theo mức ủng hộ — chỉ khuyến nghị, KHÔNG sửa code)

| # | Khuyến nghị | Mức ủng hộ | Hành động đề xuất | Tác động kỳ vọng | Rủi ro / kiểm soát |
|---|---|---|---|---|---|
| R1 | **Stop buffer theo volatility regime** | Trung bình–mạnh (chỉ trên loser) | Thí nghiệm A/B: config thay `stop_buffer_atr` theo regime — vd low 0.15–0.20, normal 0.10, high 0.08; re-run dataset/label + đánh giá OOS | Giảm stop-out sớm regime thấp; có thể tăng tp 2R | Risk/trade đổi; phải đo expectancy toàn pool + CI, không chỉ loser |
| R2 | **Penalty trend-context trong rule-score** (giảm điểm nhóm dao) | Yếu–vừa | A/B score variant: catch-the-knife −5/−10 điểm vào `rule_score` (thí nghiệm riêng); detector giữ nguyên | Giảm volume nhóm PF 0.98; cải thiện expectancy bucket cao | Mất 72% volume nếu thành hard-filter (không làm); cần OOS period mới; hiệu chỉnh multiple-testing |
| R3 | **Giữ nguyên model pool theo level_type** | Không hỗ trợ đổi | Không đổi; theo dõi prev_day khi data mở rộng (n>30) | — | prev_day n=14 không kết luận; cấm suy diễn từ CI rộng |
| R4 | **(Tùy chọn, ngoài baseline)** Feature cường độ trend | Thiếu dữ liệu | Thêm cột magnitude (|EMA-slope|, ADX-like, distance-to-EMA) → phân biệt "H1 mạnh" thật | Test lại (b) với ngưỡng mạnh thực | Feature change = ngoài baseline; phải re-audit causality + re-freeze riêng |

### 4.1 Cách test đề xuất (bắt buộc trước khi đổi baseline v1.2.0)

1. **Không dùng cùng 974 để vừa khám phá vừa xác nhận.** Khám phá (t4/t5) đã xong. Xác nhận phải là
   out-of-sample: (i) period dữ liệu mới ngoài 99,692 bars, hoặc (ii) walk-forward mở rộng (giữ 20%
   cuối theo thời gian làm test, đánh giá **một lần**), hoặc (iii) nếu bắt buộc dùng 974 → pre-register
   ngưỡng trước, chạy CV lặp và báo cáo cả distribution, không chọn ngưỡng sau khi nhìn kết quả.
2. **Pre-register tiêu chí thành công** (ví dụ): expectancy net OOS cải thiện > 0.05R **và** Wilson 95%
   CI không chứa baseline; PF > 1.15; subset n ≥ 30; đo bằng `net_result_r` **với cost thực ~0.05R**
   (config hiện cost=0 → mọi số gross; quyết định phải dùng số sau chi phí).
3. **A/B stop buffer (R1):** giữ nguyên feature/model; chỉ thay `stop_buffer_atr` theo regime trong
   config thí nghiệm; re-run labeling/dataset; so sánh expectancy/win/PF/drawdown trên test OOS vs
   artifact v1.2.0 (giữ nguyên bản frozen làm control).
4. **A/B trend penalty (R2):** thêm variant score (không sửa detector); so sánh phân phối score-bucket +
   expectancy OOS giữa score gốc và score −penalty.
5. **Chỉ re-freeze khi thắng OOS** theo quy trình chuẩn: mở repair task → QA độc lập → md5 freeze
   (bản v1.2.0 giữ nguyên cho tới khi bản mới được đóng băng).

---

## 5. Giới hạn (bắt buộc ghi nhận)

- **n nhỏ:** prev_day n=14; subset top-prob th0.4 chỉ 13–32/group; review pool 24–71 → Wilson CI rộng,
  mọi kết luận ở các nhóm này chỉ là gợi ý.
- **Multiple-testing:** t4/t5 chạy nhiều lát cắt (4 cell × level_type × 7 threshold × 3 regime × …);
  p-value chưa hiệu chỉnh (p≈0.03 ở (b); p<1e-5 ở (c)) chỉ là tham khảo, nguy cơ false positive thực.
- **In-sample description:** mọi khác biệt trong doc này được đo trên chính pool đã dùng để định nghĩa
  nhóm — cần OOS xác nhận trước khi hành động.
- **"H1 mạnh" chưa được đo:** frozen data chỉ có dấu binary của EMA(50) slope; không có cột cường độ →
  giả thuyết (b) mới chỉ test được nửa đầu (cùng/ngược chiều), chưa test được ngưỡng "mạnh".
- **Chi phí = 0 placeholder:** `net_result_r` ≈ gross; PF/expectancy trong doc là trước chi phí thực
  (~0.05R sẽ dịch toàn bộ xuống âm/marginal — xem freeze v1.2.0).
- **`rolling` n=0 trong frozen pool:** so sánh rolling-vs-structural chỉ hợp lệ trên review pool
  (stratified, indicative).
- Doc này không thay đổi baseline v1.2.0; các file phân tích nguồn được QA xác nhận deterministic
  (md5 ổn định) — xem từng file `.md/.json` trong `reports/analysis/` để truy vết số.

— Hết tài liệu khuyến nghị (t7) —
