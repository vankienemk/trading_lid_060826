# ĐẶC TẢ HỆ THỐNG PAPER TRADING — XAUUSD/EURUSD Liquidity Sweep V2

**Trạng thái:** Giai đoạn thống kê đã đóng băng. Tài liệu này là đặc tả đầy đủ để build hệ thống Giai đoạn 8 (Paper Trading), độc lập với lịch sử hội thoại trước đó.

**Đọc tài liệu này trước khi làm bất kỳ việc gì.** Nếu có xung đột giữa trí nhớ/giả định của bạn và tài liệu này, tài liệu này luôn thắng.

---

## 0. Bối cảnh đã hoàn tất (không cần làm lại)

Hệ thống đã trải qua đầy đủ pipeline nghiên cứu: xây baseline detector, sửa leakage (v1.1.0 → v1.2.0), phát hiện root cause (R:R +2/-1 thiếu robust, sweep cùng chiều trend chiếm 72% pool và âm), tái định nghĩa setup V2, test cross-asset (XAUUSD + EURUSD), pooled OOS meta-analysis với block bootstrap đã fence đúng, heterogeneity test, ACF check. Kết luận cuối: **V2 pass strict generalization test (CI lower bound = 1.092 > 1.0)**.

**Không quay lại bất kỳ bước nào ở trên.** Không tinh chỉnh lại tham số. Không dùng lại dữ liệu XAUUSD 2018→2022-06 hay EURUSD Full để tune bất cứ gì — hai tập này đã "cháy" (burned) vĩnh viễn cho mục đích validation.

---

## 1. Cấu hình V2 đã đóng băng (bất di bất dịch) — mở rộng multi-symbol

Hệ thống phải hỗ trợ nhiều symbol, nhưng **mỗi symbol có config riêng, đóng băng độc lập**. Không có một file config chung cho mọi symbol — vì mỗi symbol khi thêm mới đều phải trải qua kiểm định riêng (xem mục 1b), không được mặc định "chắc cũng ổn như XAUUSD/EURUSD".

Cấu trúc thư mục:

```
configs/symbols/
├── XAUUSD.yaml       # đã đóng băng, validated, evidence CI lower bound 1.092
├── EURUSD.yaml       # đã đóng băng, validated, evidence CI lower bound 1.092 (pooled)
├── _template.yaml    # template cho symbol mới, KHÔNG được tự chuyển sang "validated" khi chưa qua gate
```

Nội dung `XAUUSD.yaml` (ví dụ, giữ nguyên như trước):

```yaml
symbol: XAUUSD
status: validated              # validated | candidate | rejected
frozen_date: 2026-09-05
frozen_by: pooled_oos_meta_analysis_verdict

filters:
  sweep_direction: nguoc_trend
  max_penetration_atr: 0.20
  min_penetration_atr: 0.05
  min_wick_ratio: 0.35
  min_reclaim_atr: 0.00

confirmation:
  enabled: true
  max_wait_bars: 3
  min_body_ratio: 0.60
  min_range_atr: 0.80

entry:
  mode: next_open_after_confirmation

risk_reward:
  stop_buffer_atr: 0.10
  reward_r: 3.0

labeling:
  horizon_bars: 16
  same_bar_policy: ambiguous
  time_barrier_result: mark_to_market

execution:
  size_multiplier: 1.0
  breakeven_cost_backtest: 0.273R

model_artifacts:
  model_path: artifacts/models/XAUUSD/model.pkl
  calibrator_path: artifacts/models/XAUUSD/calibrator.pkl
  feature_schema: artifacts/feature_schemas/XAUUSD/features.json

validation_evidence:
  pooled_pf: 1.305
  pooled_ci95: [1.092, 1.561]
  method: "block bootstrap fenced by asset, block_size = floor(min(25, 2*sqrt(n)))"
```

**`status` field là chốt an toàn quan trọng nhất trong toàn hệ thống**: Signal Engine **chỉ được phép chạy live/paper cho symbol có `status: validated`**. Symbol `status: candidate` chỉ chạy được ở chế độ backtest/nghiên cứu nội bộ, không bao giờ được gọi tới Execution Layer dù người dùng có bấm nút gì trên GUI.

### 1b. Quy trình thêm symbol mới — bắt buộc qua "Symbol Onboarding Wizard" trên GUI, không có đường tắt

Vì thêm symbol mới đồng nghĩa train lại, đây là điểm dễ bị phá vỡ kỷ luật nhất — một GUI dễ dùng có thể khiến người dùng (hoặc agent) muốn "thêm symbol, train, bật live" trong vài cú click. Hệ thống phải **chặn cứng** khả năng này bằng cách chia thành các bước tuần tự, mỗi bước phải qua mới mở khóa bước sau (nút bị disable/xám nếu chưa đủ điều kiện):

| Bước | Nội dung | Điều kiện để mở khóa bước kế tiếp |
|---|---|---|
| 1. Data audit | Nạp OHLCV symbol mới (từ MT5 export hoặc MCP history), validate OHLC, kiểm tra gap | Không lỗi validate, báo cáo data quality hiển thị trên GUI |
| 2. Build events | Chạy sweep detector + confirmation + dedup theo đúng logic causal đã dùng cho XAUUSD/EURUSD | Event table sinh ra không rỗng, không trùng ID |
| 3. Build dataset | Feature pipeline (32 feature registry) + labeling triple-barrier | Không lỗi no-lookahead test tự động chạy kèm |
| 4. Train + Time-based split | Train model theo đúng train/validation/test time-based split, KHÔNG random split | Model + calibrator được lưu vào `artifacts/models/{SYMBOL}/` |
| 5. Walk-forward OOS | Chạy walk-forward validation trên chính symbol này | Báo cáo PR-AUC/PF theo fold hiển thị đầy đủ trên GUI |
| 6. **Gate quyết định** | So khớp kết quả với tiêu chí generalization đã dùng cho XAUUSD/EURUSD: **CI 95% lower bound của PF phải > 1.0** trên tập test/OOS chưa từng đụng tới trong bước 4-5 | Chỉ khi đạt mới cho phép đổi `status: candidate` → `status: validated` |
| 7. Kích hoạt | Chỉ symbol `validated` mới xuất hiện trong danh sách symbol có thể bật Signal Engine trên GUI chính | — |

**Nếu gate ở bước 6 không đạt** (CI chứa 1, hoặc PF < 1): GUI phải hiển thị rõ ràng "KHÔNG ĐỦ BẰNG CHỨNG — status giữ `rejected`", kèm số liệu cụ thể, và **không có nút nào để ép chuyển sang validated bằng tay** — muốn override, người dùng phải tự sửa trực tiếp file YAML (hành động có chủ đích, không phải click nhầm trên GUI).

Toàn bộ 7 bước chạy trong một tab riêng ("Symbol Onboarding") tách biệt hoàn toàn khỏi tab điều khiển live trading, để không nhầm lẫn giữa "đang nghiên cứu" và "đang chạy tiền thật (dù là demo)".

---

## 2. Nguyên tắc an toàn bắt buộc — đọc kỹ trước khi code bất cứ gì

### 2.1. Chỉ dùng tài khoản DEMO

Trước khi khởi tạo bất kỳ kết nối MT5 nào, gọi tool đọc account info và **hiển thị rõ cho người dùng**: account number, account type, server, balance. Người dùng phải xác nhận bằng mắt đây là demo trước khi cho phép bước tiếp theo. Nếu account type không rõ ràng là demo, dừng lại.

### 2.2. Ba mức độ tự động — bắt đầu từ mức thấp nhất

| Mức | Mô tả | Điều kiện chuyển lên mức tiếp theo |
|---|---|---|
| **Mức 1 — Manual confirm** | Agent tính signal, hiển thị lên UI, chờ người dùng bấm nút "Gửi lệnh" mới gọi MCP đặt lệnh | Mặc định khi mới bắt đầu |
| **Mức 2 — Semi-auto** | Agent tự gửi lệnh khi thỏa điều kiện, nhưng dừng ngay nếu kill-switch kích hoạt | Chỉ chuyển sau khi có ≥30 lệnh chạy ổn định ở Mức 1 và người dùng xác nhận bằng lời |
| **Mức 3 — Full-auto** | Không khuyến nghị trong phạm vi paper trading | Không tự chuyển, cần quyết định rõ ràng từ người dùng |

Hệ thống UI phải có **công tắc hiển thị rõ đang ở mức nào**, không được ngầm định.

### 2.3. Magic number / comment để tách lệnh hệ thống khỏi lệnh tay

Mọi lệnh gửi qua hệ thống này phải gắn `magic_number` cố định (ví dụ `20791`) và `comment` chứa mã định danh (ví dụ `LSW-V2-{event_id}`) để phân biệt với lệnh người dùng tự đặt tay trên cùng tài khoản MT5.

### 2.4. Không sửa tham số khi đang chạy

Nếu cần thay đổi bất kỳ giá trị nào trong `v2_frozen.yaml` khi hệ thống đang chạy, phải dừng hệ thống, ghi log lý do thay đổi, tạo phiên bản config mới (`v2_frozen_v2.yaml`), không ghi đè file cũ.

---

## 3. Kiến trúc hệ thống

```
┌──────────────────────┐
│   MT5 (demo)          │◄──── MCP tools (get_price, get_ohlc, place_order, get_positions, close_position)
└──────────┬───────────┘
           │
┌──────────▼───────────┐        ┌────────────────────────┐
│  Symbol Registry       │◄──────►│ Symbol Onboarding Wizard│  — 7 bước, gate cứng (mục 1b)
│  (configs/symbols/*.yaml,      │  chỉ chạy trên dữ liệu    │
│   status: validated/          │  lịch sử, KHÔNG bao giờ    │
│   candidate/rejected)         │  gọi Execution Layer       │
└──────────┬───────────┘        └────────────────────────┘
           │ chỉ symbol validated
┌──────────▼───────────┐
│  Signal Engine         │  — 1 instance riêng cho mỗi symbol validated, chạy pipeline
│  (per-symbol, đọc      │     riêng của symbol đó: level → sweep → confirmation →
│   config + model       │     feature (32 cột registry) → rule_score/model_prob
│   artifacts riêng)     │
└──────────┬───────────┘
           │ candidate signal
┌──────────▼───────────┐
│  Risk Guard            │  — kill-switch riêng theo từng symbol, position sizing theo
│  (per-symbol)          │     size_multiplier trong config symbol đó
└──────────┬───────────┘
           │ nếu pass
┌──────────▼───────────┐       ┌──────────────────┐
│  Execution Layer       │──────►│  Logger (local DB) │  — ghi TRƯỚC khi gửi lệnh
│  (gọi MCP place_order, │       │  SQLite, có cột    │
│   có xác nhận nếu       │       │  symbol để phân    │
│   automation Mức 1)    │       │  biệt              │
└──────────┬───────────┘       └──────────────────┘
           │
┌──────────▼───────────┐
│  Python GUI (mục 7)    │  — điều khiển, giám sát, onboarding, KHÔNG code trực tiếp qua đây
└───────────────────────┘
```

---

## 4. Signal Engine — yêu cầu chi tiết

- Mỗi symbol `validated` có **một instance Signal Engine riêng**, chạy độc lập, đọc đúng config và model artifacts của symbol đó (`configs/symbols/{SYMBOL}.yaml`, `artifacts/models/{SYMBOL}/`). Không dùng chung một bộ model cho nhiều symbol.
- Chạy lại đúng logic đã đóng băng cho các module: liquidity level (rolling/swing/equal/prev-day, tất cả causal với `known_at`), sweep detector, confirmation, feature pipeline (32 feature registry, không thêm/bớt) — logic pipeline giống nhau giữa các symbol, chỉ khác tham số và model đã train riêng.
- Áp filter V2: `nguoc_trend` (so hướng sweep với dấu H1 EMA(50) slope) và `max_penetration_atr` theo giá trị đã đóng băng cho symbol đó (mỗi symbol có thể có giá trị riêng nếu quy trình onboarding cho ra tham số khác XAUUSD/EURUSD — không mặc định copy nguyên 0.20 cho mọi symbol mới).
- Khi có candidate event, tính `rule_score` và `model_probability` dùng đúng `model.pkl` + `calibrator.pkl` của symbol đó, **KHÔNG train lại trên dữ liệu live/paper trading**.
- Entry = open của nến kế tiếp sau confirmation. Stop/target tính theo tham số đã đóng băng riêng của symbol.
- **Không được nhìn dữ liệu tương lai** — giữ nguyên toàn bộ nguyên tắc no-lookahead.

---

## 5. Logging — schema bắt buộc

Ghi vào SQLite hoặc CSV **local trên máy**, độc lập với MT5 (không phụ thuộc MT5 còn sống để giữ lịch sử).

| Cột | Mô tả |
|---|---|
| event_id | ID duy nhất |
| asset | XAUUSD / EURUSD |
| signal_time | thời điểm phát hiện sweep |
| confirmation_time | thời điểm confirmation |
| features_json | toàn bộ 32 feature causal tại thời điểm entry (để so khớp ngược với pipeline offline) |
| rule_score, model_probability | |
| entry_planned, stop_planned, target_planned | tính từ pipeline, TRƯỚC khi gửi lệnh |
| order_sent_time | thời điểm gọi MCP place_order |
| entry_actual, slippage_actual | giá khớp thực tế từ MT5 trừ giá dự kiến |
| exit_time, exit_price, exit_reason | tp/sl/time |
| mfe_r, mae_r, net_result_r | tính sau khi đóng lệnh |
| automation_level | 1 (manual) / 2 (semi-auto) tại thời điểm lệnh này được gửi |
| kill_switch_status_at_signal | active/inactive tại thời điểm phát tín hiệu |

**Ghi trước khi gửi lệnh** (entry_planned, stop_planned, target_planned) để nếu MT5/MCP crash giữa chừng, vẫn còn dấu vết signal đã được sinh ra.

---

## 6. Kill-switch — logic bắt buộc

- Sau mỗi lệnh đóng, tính rolling PF và CI 95% (dùng **block bootstrap**, block_size = `floor(min(25, 2*sqrt(n)))`) trên tối đa 60 lệnh gần nhất **của từng symbol riêng biệt** — không gộp giữa các symbol khi tính kill-switch, dù có bao nhiêu symbol đang chạy song song.
- Nếu CI lower bound < 1.0 cho một symbol → dừng gửi lệnh mới cho **riêng symbol đó** (không ảnh hưởng các symbol khác), hiển thị cảnh báo rõ ràng trên GUI, yêu cầu xác nhận thủ công của người dùng mới được mở lại.
- Kill-switch chỉ áp dụng sau khi đã có ≥20 lệnh cho symbol đó — trước ngưỡng này, chỉ hiển thị số liệu, không tự động dừng.
- Khi thêm symbol mới qua Onboarding Wizard (mục 1b), kill-switch threshold mặc định giống các symbol đã validated, trừ khi bước 6 (gate) cho ra bằng chứng yếu hơn (CI lower bound gần 1.0) — trường hợp đó, GUI nên gợi ý giảm ngưỡng số lệnh tối thiểu xuống thấp hơn (ví dụ 15 thay vì 20) để phát hiện vấn đề sớm hơn, và bắt buộc size_multiplier khởi điểm thấp (≤0.5) cho symbol mới bất kể gate pass hay không — "mới pass gate lần đầu" luôn rủi ro hơn "đã pass nhiều vòng kiểm định" như XAUUSD/EURUSD.

---

## 7. Python GUI — đặc tả cụ thể

### 7.1. Lựa chọn framework — agent phải xác nhận trước khi code

Yêu cầu chạy được trên desktop (laptop người dùng), cập nhật real-time (giá, vị thế, PF rolling), và có control panel (nút bấm, dropdown chọn symbol, bảng dữ liệu). Đề xuất 2 lựa chọn, agent chọn 1 và giải thích lý do trước khi bắt đầu code:

| Framework | Ưu điểm | Nhược điểm |
|---|---|---|
| **PySide6 (Qt for Python)** | Mature, threading tốt (QThread) để chạy Signal Engine nền song song UI, nhiều widget có sẵn (bảng, tab, dialog xác nhận), `pyqtgraph` cho biểu đồ real-time mượt | Cồng kềnh hơn, thời gian setup ban đầu lâu hơn |
| **Dear PyGui** | Hiệu năng cao, thiết kế sẵn cho dashboard dữ liệu real-time/tài chính, code gọn hơn | Hệ sinh thái widget hạn chế hơn Qt, ít tài liệu tiếng Việt/cộng đồng nhỏ hơn |

Không dùng Streamlit/web-based cho phần điều khiển chính — vì cần thao tác tức thời (bấm nút "Gửi lệnh", emergency stop) với độ trễ thấp và trạng thái cục bộ ổn định, không phù hợp mô hình reload trang của Streamlit.

### 7.2. Cấu trúc tab bắt buộc

**Tab 1 — Symbol Onboarding** (mục 1b)
- Danh sách symbol hiện có kèm `status` (validated/candidate/rejected) hiển thị bằng màu (xanh/vàng/đỏ).
- Nút "Thêm symbol mới" → mở wizard 7 bước, mỗi bước là một sub-tab hoặc step tuần tự, nút "Tiếp theo" bị disable tới khi bước hiện tại hoàn thành và hiển thị kết quả (báo cáo data quality, event count, walk-forward metrics...).
- Ở bước gate cuối, hiển thị rõ ràng bảng so sánh CI 95% với ngưỡng 1.0, và kết luận validated/rejected bằng chữ to, không mập mờ.

**Tab 2 — Live Control** (chỉ hiện symbol `validated`)
- Dropdown chọn symbol đang muốn xem/điều khiển (nếu chạy nhiều symbol song song, có thể hiện dạng nhiều panel con thay vì dropdown).
- Công tắc automation level (1/2/3) **riêng cho từng symbol** — hiển thị to, rõ, kèm màu cảnh báo nếu đang ở mức 2 trở lên.
- Bảng signal đang chờ duyệt (Mức 1): entry/stop/target/rule_score/model_probability, nút "Gửi lệnh" / "Bỏ qua" riêng từng dòng.
- Bảng vị thế đang mở theo symbol: entry, giá hiện tại, P/L tạm tính theo R.
- Nút **Emergency Stop** to, màu đỏ, luôn hiển thị cố định (không nằm trong tab con) — dừng toàn bộ Signal Engine mọi symbol ngay lập tức.

**Tab 3 — Performance Monitor**
- Bảng hiệu năng theo từng symbol: số lệnh, win rate, PF, CI 95% rolling, so sánh với breakeven cost đã tính từ backtest của symbol đó.
- Biểu đồ equity curve theo R, cập nhật real-time mỗi khi có lệnh đóng.
- Trạng thái kill-switch theo từng symbol: active/inactive, lý do nếu active, nút "Xác nhận mở lại" (yêu cầu gõ xác nhận, không chỉ 1 click).

**Tab 4 — Account & Connection**
- Thông tin account MT5 (number, type, server, balance) — hiển thị to nếu phát hiện account không phải demo (nền đỏ toàn màn hình, chặn thao tác khác cho tới khi xác nhận).
- Trạng thái kết nối MCP, nút reconnect thủ công.

### 7.3. Ràng buộc thiết kế GUI quan trọng

- Mọi thao tác có khả năng gây hậu quả (gửi lệnh, đổi automation level, mở lại sau kill-switch, chuyển symbol sang validated bằng tay) đều phải có **dialog xác nhận thứ hai**, không thực thi ngay sau 1 click.
- GUI khởi động luôn ở trạng thái mặc định an toàn nhất (automation level = 1, không tự động mở symbol nào ở mức cao hơn từ phiên trước) — không lưu/khôi phục automation level giữa các lần khởi động lại ứng dụng.
- Log mọi thao tác người dùng trên GUI (đổi mức tự động, override kill-switch, bấm nút gửi lệnh) vào cùng hệ thống Logger ở mục 5, để có thể truy vết ai/khi nào đã thay đổi gì.

---

## 8. Quy trình khởi động (agent thực hiện theo đúng thứ tự)

1. Xác nhận framework GUI (mục 7.1) trước khi code bất cứ gì.
2. Đọc toàn bộ `configs/symbols/*.yaml` hiện có, hiển thị danh sách symbol và status lên GUI (Tab 1).
3. Kết nối MT5 qua MCP, gọi tool đọc account info, hiển thị và **chờ người dùng xác nhận đây là demo** trước khi cho phép bất kỳ thao tác nào khác trên GUI (Tab 4 phải là tab đầu tiên hoạt động được).
4. Khởi tạo logging DB (schema có cột `symbol`, tạo file mới nếu chưa có, không ghi đè nếu đã có dữ liệu cũ).
5. Với mỗi symbol có `status: validated`, khởi tạo Signal Engine riêng, mặc định **automation level = 1 (manual confirm)** — bất kể phiên trước đã ở mức nào.
6. Symbol `status: candidate/rejected` không được khởi tạo Signal Engine live — chỉ hiển thị trong Tab 1 (Onboarding) để tiếp tục nghiên cứu nếu muốn.
7. Khởi động GUI đầy đủ 4 tab (mục 7.2).
8. Báo cáo lại cho người dùng: những symbol nào đang chạy, ở mức tự động nào, kill-switch threshold từng symbol, chờ lệnh tiếp theo.

**Agent không tự ý bỏ qua bước 3 hoặc tự nâng automation level hoặc tự chuyển symbol sang validated** dù trong bất kỳ hoàn cảnh nào — kể cả khi người dùng có vẻ đã quen thuộc từ trước, vì đây chính là tài liệu để khôi phục ngữ cảnh đã mất.

---

## 9. Việc KHÔNG được làm

- Không train lại model trên dữ liệu paper trading để "cải thiện" — dữ liệu live chỉ dùng để đánh giá, không dùng để tune trong giai đoạn này.
- Không tự động nâng automation level.
- Không sửa `v2_frozen.yaml` khi đang chạy.
- Không tắt kill-switch để "chạy thử cho nhanh".
- Không gộp rolling CI của 2 tài sản khi tính kill-switch.
- Không dùng lại XAUUSD 2018→2022-06 hoặc EURUSD Full cho bất kỳ mục đích tinh chỉnh nào.

---

## 10. Câu hỏi agent cần trả lời trước khi bắt đầu code

1. MCP MT5 hiện có hỗ trợ `place_order` với `magic_number` và `comment` tùy chỉnh không?
2. Nếu mất kết nối MCP giữa lúc có lệnh đang mở, có cơ chế polling định kỳ để phát hiện và cảnh báo không?
3. Framework nào sẽ dùng cho UI — cần xác nhận trước khi code để tránh phải làm lại.

Trả lời 3 câu hỏi này trước, sau đó mới bắt đầu implement theo thứ tự: Logger → Signal Engine → Risk Guard/Kill-switch → Execution Layer (Mức 1 trước) → UI.
