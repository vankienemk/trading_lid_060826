# Đặc tả công việc — Kiểm tra Lot Sizing, Stop Buffer theo Regime, và Entry Timing

**Ngày:** 2026-09-05
**Phạm vi:** 3 việc độc lập, thứ tự ưu tiên: A (audit, rẻ) → B (A/B test, đã có bằng chứng mạnh) → C (exploratory, chưa vội)
**Nguyên tắc bắt buộc áp dụng cho cả 3 việc:** không dùng lại dữ liệu đã "cháy" (XAUUSD 2018→2022-06, EURUSD Full) để tinh chỉnh bất kỳ tham số nào; nếu cần OOS mới, dùng dữ liệu paper trading đang tích lũy hoặc giai đoạn thời gian chưa từng đụng tới.

---

## Việc A — Audit công thức tính Lot Size (kỹ thuật, làm trước tiên, không cần dữ liệu mới)

### Mục tiêu
Xác nhận công thức tính lot không có bug, không phải tìm "cải thiện" — đây là việc kiểm tra đúng/sai, không phải tối ưu.

### Checklist agent phải trả lời (kèm trích code cụ thể cho mỗi câu)

1. Công thức tính lot hiện tại là gì? Trích nguyên đoạn code.
2. `stop_distance` dùng để tính lot lấy từ đâu — có đúng bằng `|entry_price - stop_price|` đã tính trong Signal Engine, hay bị tính lại một lần nữa ở Execution Layer (nguy cơ lệch nếu 2 nơi tính khác công thức)?
3. Point value / contract size có được lấy đúng theo từng symbol không (gọi MCP `symbol_info` hay hard-code)? Nếu hard-code, liệt kê giá trị đang dùng cho XAUUSD và EURUSD.
4. `size_multiplier` (1.0 XAUUSD / 0.5 EURUSD) có được nhân vào **trước khi** tính lot hay **sau khi** tính lot? Nếu nhân sau khi tính lot theo risk%, kết quả tương đương giảm risk% thực tế — cần xác nhận đây có đúng ý đồ thiết kế không.
5. Lot cuối cùng có được làm tròn theo `volume_step`/`volume_min`/`volume_max` của broker (đọc từ MCP) không? Làm tròn lên hay xuống — làm tròn lên sẽ khiến risk thực tế luôn ≥ risk dự kiến.
6. Có giới hạn nào chặn lot tối đa tuyệt đối (ví dụ không bao giờ vượt X lot dù risk% tính ra lớn hơn) để tránh lỗi tính toán gây lệnh khổng lồ ngoài ý muốn không?

### Output — bắt buộc dùng đúng format sau

Tạo file `reports/audit/lot_sizing_audit_{YYYYMMDD}.md`, cấu trúc:

```markdown
# Lot Sizing Audit — {ngày}

## Câu trả lời 6 câu hỏi (mục A)
[trả lời từng câu, kèm trích code path:line]

## Bảng ví dụ tính tay (bắt buộc, không chỉ mô tả công thức)
| Symbol | Balance | Risk% | Entry | Stop | Stop distance | Lot tính ra (trước làm tròn) | Lot cuối (sau làm tròn) | Risk thực tế ($) | Risk thực tế (%) |
|---|---|---|---|---|---|---|---|---|---|
| XAUUSD | 10,000 | 1% | 2650.50 | 2648.20 | ... | ... | ... | ... | ... |
| EURUSD | 10,000 | 1% (×0.5 multiplier) | ... | ... | ... | ... | ... | ... | ... |

## Kết luận
- [ ] Không phát hiện bug
- [ ] Phát hiện bug — mô tả cụ thể, mức độ ảnh hưởng (risk thực tế lệch bao nhiêu % so với dự kiến)
```

Bảng ví dụ tính tay là phần quan trọng nhất — cho phép tôi và bạn tự kiểm tra lại bằng máy tính cầm tay trong 30 giây, không cần đọc code.

---

## Việc B — A/B Test: Stop Buffer theo Volatility Regime

### Bối cảnh (đã có từ phân tích trước, không làm lại)
Buffer cố định `0.10 × ATR` khiến 87.8% lệnh thua ở regime biến động thấp là do bị stop (so với 62.9% ở regime cao), pierce sâu hơn (median MAE_R 1.87R). Effect size mạnh (p=5.3e-9) nhưng chỉ đo trên nhóm thua — chưa biết tác động ròng lên expectancy toàn pool.

### Thiết kế thí nghiệm — pre-register TRƯỚC khi chạy

**Biến thể cần test** (cố định trước, không thêm/bớt sau khi thấy kết quả):

| Biến thể | stop_buffer_atr theo regime (low / normal / high) |
|---|---|
| Control (hiện tại) | 0.10 / 0.10 / 0.10 |
| Variant 1 | 0.15 / 0.10 / 0.08 |
| Variant 2 | 0.20 / 0.10 / 0.08 |

**Tiêu chí thành công** (viết ra trước, không đổi sau khi nhìn số):
- Expectancy net OOS cải thiện > 0.05R so với control **và**
- Wilson/bootstrap 95% CI của PF không chứa control PF **và**
- n ≥ 30 lệnh cho mỗi biến thể

**Dữ liệu dùng để test:** paper trading data tích lũy được (không dùng lại XAUUSD-OOS/EURUSD-Full). Nếu paper trading chưa đủ n, dùng phương án tạm: relabel lại đúng 974-event pool đã freeze với 3 biến thể trên, đánh giá bằng walk-forward (không phải để chọn tham số mới, chỉ để có ước lượng sơ bộ trong lúc chờ đủ dữ liệu live) — **ghi rõ trong report đây là ước lượng tạm, không phải xác nhận cuối cùng**.

### Output — bắt buộc dùng đúng format sau

File `reports/experiments/stop_buffer_regime_ab_{YYYYMMDD}.md` + file ảnh đi kèm cùng thư mục.

**1. Bảng kết quả chính** (giống format các báo cáo trước, để dễ so sánh):

| Biến thể | n | PF | PF CI95 | Avg net R | Breakeven cost | Stop rate | Time rate | Target rate |
|---|---|---|---|---|---|---|---|---|
| Control | | | | | | | | |
| Variant 1 | | | | | | | | |
| Variant 2 | | | | | | | | |

**2. Biểu đồ bắt buộc — 2 chart, mỗi chart 1 file .png riêng:**

- `stop_buffer_pf_comparison.png`: bar chart PF của 3 biến thể, kèm error bar (CI 95%), có đường kẻ ngang tại PF=1.0 để nhìn ngay biến thể nào vượt qua ngưỡng.
- `stop_buffer_mae_distribution.png`: histogram/boxplot phân phối MAE_R của các lệnh thua, so sánh control vs variant tốt nhất, side-by-side — để nhìn trực quan việc "nới buffer có giảm số lệnh bị pierce sát ngưỡng cũ không".

**3. Kết luận bằng 1 dòng rõ ràng ở đầu file** (trước cả bảng số liệu), ví dụ:
> **KẾT LUẬN: Variant 1 đạt tiêu chí thành công đã pre-register / KHÔNG biến thể nào đạt tiêu chí — giữ nguyên control.**

Không diễn giải dài dòng trước khi nói kết luận — để tôi đọc dòng đầu là biết ngay có cần đọc tiếp không.

---

## Việc C — Khảo sát mô tả Entry Timing (KHÔNG train model, chỉ thống kê mô tả)

### Mục tiêu
Trả lời câu hỏi "entry hiện tại (open nến kế tiếp sau confirmation) có phải điểm vào tệ không" bằng dữ liệu, trước khi cân nhắc bất kỳ mô hình nào.

### Cách làm — chỉ dùng dữ liệu đã có (974-event pool), KHÔNG cần dữ liệu mới cho bước khảo sát này vì đây là mô tả cấu trúc, không phải kiểm định tham số mới để trade

Với mỗi event đã có, tính thêm (không đổi entry thật, chỉ mô phỏng song song để so sánh):

1. **Entry giả định A — pullback**: nếu trong vòng 1-2 nến sau entry thật, giá có pullback về gần hơn phía level bị sweep (ví dụ 30-50% khoảng entry→stop), giả sử vào tại đó thay vì tại open — tính lại risk/reward theo entry mới.
2. **Entry giả định B — chờ xác nhận thêm 1 nến**: dịch entry sang open của nến sau nữa (k+2 thay vì k+1), giữ nguyên stop/target logic.

So sánh 3 cột entry (thật / giả định A / giả định B) trên cùng bộ event:
- % event mà giả định A/B **có thể khớp lệnh được** (giá có thực sự pullback đủ, hay chạy thẳng bỏ lỡ hoàn toàn).
- Trong số khớp được, so PF/avg_net_r giữa 3 cách.

### Output — bắt buộc dùng đúng format sau

File `reports/exploratory/entry_timing_descriptive_{YYYYMMDD}.md`.

**Bảng bắt buộc:**

| Entry mode | % event khớp lệnh được | n (trong số khớp) | PF | Avg net R | Avg risk (R gốc so với entry thật) |
|---|---|---|---|---|---|
| Entry thật (baseline) | 100% | 974 | | | 1.0x (chuẩn) |
| Giả định A (pullback) | | | | | |
| Giả định B (chờ thêm 1 nến) | | | | | |

**Biểu đồ bắt buộc:** `entry_timing_comparison.png` — 1 chart dạng grouped bar, trục X là 3 entry mode, 2 trục Y phụ (PF và % khớp lệnh) để thấy ngay trade-off giữa "vào đẹp hơn" và "bỏ lỡ nhiều hơn".

**Dòng kết luận đầu file, một trong 3 dạng:**
> - **Không có dấu hiệu entry giả định nào tốt hơn đáng kể → không cần nghiên cứu thêm, giữ nguyên entry hiện tại.**
> - **Có dấu hiệu đáng chú ý ở [A/B] → đề xuất bước tiếp theo: pre-register và A/B test thật trên paper trading, KHÔNG train model ngay.**
> - **Dữ liệu không đủ để kết luận (n khớp lệnh quá nhỏ) → cần thêm dữ liệu trước khi bàn tiếp.**

**Quan trọng:** đây là bước mô tả (descriptive), không phải bước quyết định. Dù kết quả ra sao, **không tự động đổi entry logic trong Signal Engine đang chạy live** dựa trên kết quả Việc C — chỉ dùng để quyết định có đáng đầu tư thêm effort nghiên cứu hướng này hay không.

---

## Chuẩn chung cho mọi report/chart (áp dụng cả 3 việc trên và các việc sau này)

Để tôi và bạn luôn xem theo cùng một cách, quen mắt qua từng lần:

1. **Tên file có ngày** (`_{YYYYMMDD}`) — không ghi đè report cũ, để so sánh lịch sử.
2. **Dòng kết luận luôn nằm ở đầu file**, in đậm, 1-3 dòng, trả lời thẳng câu hỏi ban đầu — không bắt người đọc lướt hết file mới biết kết quả.
3. **Bảng số liệu luôn có cột CI 95%** khi có thể tính được — không báo con số đơn lẻ (PF=1.3) mà không kèm khoảng tin cậy.
4. **Biểu đồ luôn là file `.png` riêng**, đặt cùng thư mục với report `.md`, được nhắc tên trực tiếp trong report (không nhúng base64, không để agent mô tả bằng lời thay cho hình).
5. **Biểu đồ luôn có đường/vùng tham chiếu** khi so sánh với ngưỡng quan trọng (PF=1.0, CI chứa/không chứa 1) — vẽ rõ đường kẻ ngang tại ngưỡng đó, không bắt người xem tự nhẩm.
6. **Mọi báo cáo thí nghiệm (không phải audit thuần kỹ thuật) đều phải có mục "Pre-registered criteria"** ngay sau dòng kết luận — liệt kê lại tiêu chí đã chốt trước khi chạy, để dễ đối chiếu xem kết luận có đúng theo tiêu chí đã hứa hay bị "co giãn" sau khi thấy số.
7. Khi báo cáo cho tôi trong chat, agent chỉ cần dán **dòng kết luận + bảng chính**, không dán toàn văn report — file đầy đủ để tôi mở khi cần đào sâu.
