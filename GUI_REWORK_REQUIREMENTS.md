# Paper Trading V2 GUI — Rework Requirements

**Ngày:** 2026-09-05  
**Mục tiêu:** Đơn giản hóa GUI, tăng khả năng quan sát hệ thống, loại bỏ wizard 7 bước không cần thiết.

Tài liệu này tổng hợp toàn bộ yêu cầu để agent team thực hiện lại phần GUI và tích hợp backend liên quan.

---

## 1. Tổng quan thay đổi

### 1.1. Thay đổi lớn

| Hạng mục | Trước đây | Yêu cầu mới |
|----------|-----------|-------------|
| Symbol Onboarding | 7-step wizard (Data Audit → Train → Gate → Activate) | Chỉ **chọn / load model** đã có sẵn rồi Activate |
| Kiểm soát hệ thống | Khó biết hệ thống có đang chạy thật không | **Màn hình System Log** chi tiết, real-time, có filter theo level |
| Trạng thái symbol | Nhiều status trung gian (auditing, training…) | Chỉ giữ `validated` / `candidate` / `rejected` |
| Model | Hard-code hoặc train mới mỗi lần | **Model Registry** — quản lý model đã train sẵn |

### 1.2. Nguyên tắc giữ nguyên

- Chỉ symbol có `status = "validated"` mới được Signal Engine chạy.
- Emergency Stop (nút đỏ cố định) vẫn hoạt động toàn cục.
- Mọi hành động quan trọng phải có confirmation dialog.
- Mọi thay đổi phải ghi log (user action + system log).
- Dark theme hiện tại được giữ.

---

## 2. Model Registry (mới)

### 2.1. Cấu trúc

Mỗi model được mô tả bằng metadata:

```yaml
model_id: "xauusd_v2_h16_20260905"
symbol_origin: "XAUUSD"
model_path: "artifacts/models/XAUUSD/model.pkl"
calibrator_path: "artifacts/models/XAUUSD/calibrator.pkl"
feature_schema: "artifacts/feature_schemas/XAUUSD/features.json"
horizon: 16
target: "outcome_2r_h16"
metrics:
  pf_oos: 1.305
  ci_lower: 1.092
  ci_upper: 1.561
  n_trades: 312
created_at: "2026-09-05"
notes: "Pooled OOS meta-analysis V2"
```

### 2.2. Quản lý

- Lưu tại `configs/models/` hoặc `artifacts/models/index.yaml`.
- Hỗ trợ nhiều version cho cùng một symbol origin.
- Cho phép trỏ tới model local path (browse folder chứa `.pkl` + calibrator + features.json).

### 2.3. API cần có trong SystemBridge

```python
get_available_models() → List[ModelInfo]
get_model(model_id: str) → ModelInfo | None
assign_model_to_symbol(symbol: str, model_id: str) → bool
add_symbol_with_model(symbol: str, model_id: str, activate: bool = True) → bool
```

---

## 3. SymbolConfig cập nhật

```python
@dataclass
class SymbolConfig:
    name: str
    status: str                     # "validated" | "candidate" | "rejected"
    active: bool = False
    model_id: Optional[str] = None  # tham chiếu Model Registry
    position_size_multiplier: float = 1.0
    automation_level: int = 1       # 1 = Manual, 2 = Semi-auto
    # các field khác giữ nguyên nếu đang dùng
```

**Quy tắc:**
- Chỉ khi `status == "validated"` **và** `model_id` hợp lệ thì Signal Engine mới được phép chạy cho symbol đó.
- `active` chỉ là cờ bật/tắt runtime (không ảnh hưởng status).

---

## 4. Tab Symbol Onboarding (viết lại hoàn toàn)

### 4.1. Layout

```
┌─────────────────────────────────────────────────────────────┐
│ 📋 Symbol Onboarding                                        │
│ Thêm symbol mới bằng cách gán model đã train sẵn.           │
│ Chỉ symbol status=validated mới chạy live signal engine.    │
│                                                             │
│ [ + Add New Symbol ]                                        │
│                                                             │
│ ┌──────────┬───────────┬────────────────────────┬────────┬─────────────────────┐
│ │ Symbol   │ Status    │ Model đang dùng        │ Active │ Actions             │
│ ├──────────┼───────────┼────────────────────────┼────────┼─────────────────────┤
│ │ EURUSD   │ validated │ xauusd_v2_h16_20260905 │ ✅ Yes │ [Change Model] [Deactivate] │
│ │ XAUUSD   │ validated │ xauusd_v2_h16_20260905 │ ✅ Yes │ [Change Model] [Deactivate] │
│ └──────────┴───────────┴────────────────────────┴────────┴─────────────────────┘
└─────────────────────────────────────────────────────────────┘
```

### 4.2. Luồng “Add New Symbol”

1. Click **+ Add New Symbol**.
2. Dialog hiện ra với:
   - Dropdown / searchable list các symbol từ MCP (hoặc cho phép nhập tay).
   - Dropdown **chọn Model** từ Model Registry (hiển thị `model_id` + metrics ngắn: PF, CI lower, origin).
   - (Tùy chọn) Nút **Browse local model…** để chọn thư mục chứa model.
3. Click **Add & Activate**:
   - Tạo `SymbolConfig(status="validated", active=True, model_id=...)`.
   - Ghi log user action.
   - Refresh table + toast/thông báo thành công.
4. Confirmation dialog nhẹ (không cần gõ text).

### 4.3. Actions trên từng dòng

| Nút | Hành động |
|-----|-----------|
| **Change Model** | Mở dialog chọn model khác → cập nhật `model_id` |
| **Deactivate / Activate** | Chỉ đổi flag `active` (giữ status) |
| **Remove** (tùy chọn) | Xóa khỏi registry (cần confirmation mạnh) |

### 4.4. Những gì cần xóa

- Toàn bộ code 7-step wizard (`_start_wizard`, `_on_wizard_next`, step handlers, progress gauge…).
- Các status trung gian: `auditing`, `building_events`, `building_dataset`, `training`, `walk_forward`, `gating`.
- Logic hard-gate CI / train / walk-forward trong GUI.
- Bất kỳ lời gọi pipeline train/dataset/events từ GUI.

---

## 5. Tab / Panel System Log (mới — ưu tiên cao)

### 5.1. Mục tiêu

Người dùng phải **nhìn thấy rõ hệ thống đang hoạt động** sau khi Activate symbol:
- Khi nến M15 mới đóng → hệ thống làm gì.
- Đánh giá sweep, tính score, quyết định signal.
- Mọi thông tin liên quan đến đặt lệnh, kết nối MCP/MT5, risk guard, lỗi.

### 5.2. Vị trí

- Tab mới tên **“System Log”** (khuyến nghị), hoặc panel dưới cùng dạng collapsible luôn hiện.
- Auto-scroll xuống dòng mới nhất (có nút **Pause auto-scroll**).

### 5.3. Định dạng mỗi dòng log

```
[TIMESTAMP] [LEVEL] [SOURCE] [SYMBOL] Message
```

Ví dụ thực tế:

```
2026-09-05 21:45:00.123 [INFO ] [SignalEngine] [XAUUSD] New M15 candle closed @ 2650.45 → running detection
2026-09-05 21:45:00.187 [INFO ] [SignalEngine] [XAUUSD] Sweep detected (bullish, penetration=0.12 ATR) → score=72.4, prob=0.68
2026-09-05 21:45:00.201 [INFO ] [RiskGuard]   [XAUUSD] Signal passed risk checks (size=0.05 lot)
2026-09-05 21:45:00.345 [INFO ] [Execution]   [XAUUSD] Order sent → order_id=123456, entry=2650.50, SL=2648.20, TP=2657.10
2026-09-05 21:45:01.012 [INFO ] [Execution]   [XAUUSD] Order filled @ 2650.52 (slippage +0.02)
2026-09-05 21:46:00.000 [DEBUG] [SignalEngine] [EURUSD] New M15 candle → no sweep found
2026-09-05 21:47:15.500 [WARNING] [MCP]        []       Reconnecting to MCP (attempt 2/5)
```

### 5.4. Cấp độ log (Level)

| Level     | Màu hiển thị     | Ví dụ nội dung |
|-----------|------------------|----------------|
| DEBUG     | Xám              | Bắt đầu poll, đọc OHLC, feature raw |
| INFO      | Trắng / xanh nhạt| Nến mới, kết quả detection, signal, order sent/filled |
| WARNING   | Vàng             | Reconnect, data delay, score thấp, size bị giảm |
| ERROR     | Đỏ               | Lỗi kết nối, model load fail, exception |
| CRITICAL  | Đỏ đậm + bold    | Emergency Stop, kill-switch, mất kết nối kéo dài |

### 5.5. Bộ lọc (bắt buộc)

- **Level**: multi-select checkbox (mặc định ẩn DEBUG).
- **Symbol**: dropdown multi-select hoặc “All”.
- **Source**: SignalEngine / RiskGuard / Execution / MCP / System / UserAction…
- **Time range**: Last 5 min / 15 min / 1 hour / Today / Custom.
- **Search text**: full-text search trên message.
- Nút **Clear filters** và **Clear log** (có confirmation).

### 5.6. Tính năng bổ sung

- Click vào 1 dòng → xem chi tiết mở rộng (JSON `extra` nếu có).
- Nút **Export log** (CSV hoặc TXT).
- Giới hạn bộ nhớ: giữ tối đa ~10.000 dòng gần nhất hoặc 24 giờ.
- Thread-safe.

### 5.7. Backend Logger

Mở rộng `logger_v2.py` (hoặc tạo centralized logger):

```python
def log(level: str, source: str, symbol: str | None, message: str, extra: dict | None = None):
    # 1. Ghi SQLite (bảng system_logs)
    # 2. Đẩy vào in-memory ring buffer (cho GUI)
    # 3. (Tùy chọn) ghi file .log xoay vòng
```

**Bắt buộc** các module sau phải gọi logger ở mọi điểm quan trọng:
- `signal_engine_v2.py` (đặc biệt sau mỗi nến M15 mới)
- `risk_guard_v2.py`
- `execution_layer_v2.py`
- MCP / connection logic trong `gui_bridge.py`

GUI dùng `QTimer` (300–500 ms) hoặc Qt signal để cập nhật real-time.

---

## 6. Các thay đổi Backend liên quan

### 6.1. SystemBridge

- Thêm Model Registry methods (mục 2.3).
- Đảm bảo `refresh_state_from_mcp()` và polling vẫn hoạt động.
- Log mọi user action quan trọng.

### 6.2. Signal Engine

- Khi khởi tạo / chạy cho 1 symbol → đọc `model_id` từ `SymbolConfig` và load đúng:
  - `model.pkl`
  - `calibrator.pkl`
  - `feature_schema`
- Nếu `model_id` thiếu hoặc file không tồn tại → log ERROR và bỏ qua symbol đó.

### 6.3. Shared State

- Cập nhật `SymbolConfig` (thêm `model_id`).
- Loại bỏ logic liên quan đến các status trung gian của wizard cũ.

---

## 7. File / Module cần chỉnh sửa

| File / Module | Hành động |
|---------------|-----------|
| `gui_tab_onboarding.py` | **Viết lại hoàn toàn** (table + dialog đơn giản) |
| `gui_main.py` | Thêm tab System Log, giữ Emergency Stop |
| `gui_bridge.py` / `SystemBridge` | Thêm Model Registry + method gán model |
| `shared_app_state_v2.py` | Cập nhật `SymbolConfig` (thêm `model_id`) |
| `logger_v2.py` | Mở rộng thành centralized system logger |
| `signal_engine_v2.py` | Load model theo `model_id` |
| `configs/models/` hoặc `artifacts/models/index.yaml` | Tạo mới để quản lý model |
| Xóa / comment | Toàn bộ wizard 7 bước + status trung gian |

---

## 8. Acceptance Criteria

### 8.1. Symbol Onboarding

1. Có thể thêm symbol mới chỉ bằng chọn symbol + chọn model → status = `validated`, active = true.
2. Table hiển thị rõ model đang được gán.
3. Có thể đổi model của symbol đã có mà không cần xóa/re-add.
4. Không còn bất kỳ nút / flow train model nào trong GUI.
5. Layout không bị cắt chữ, dialog sạch sẽ.

### 8.2. System Log

1. Sau khi Activate symbol, khi nến M15 mới đóng → xuất hiện ít nhất 1 dòng INFO mô tả hành động của Signal Engine.
2. Có thể lọc chỉ xem ERROR + CRITICAL của 1 symbol cụ thể.
3. Log không bị mất khi chuyển tab.
4. Auto-scroll hoạt động, có thể tạm dừng.
5. Mọi hành động đặt lệnh / lỗi kết nối đều hiện rõ với đủ thông tin (order_id, price, reason…).
6. Performance ổn định (không lag GUI).

### 8.3. An toàn

- Chỉ symbol `validated` + có `model_id` hợp lệ mới chạy Signal Engine.
- Emergency Stop vẫn hoạt động.
- Mọi thay đổi quan trọng đều có confirmation + được ghi log.

---

## 9. Thứ tự ưu tiên triển khai đề xuất

1. **Model Registry + cập nhật SymbolConfig** (nền tảng).
2. **Viết lại Tab Symbol Onboarding** (bỏ wizard).
3. **Centralized Logger + Tab System Log**.
4. Cập nhật Signal Engine để load model theo `model_id`.
5. Dọn dẹp code cũ + cập nhật tài liệu (`KNOWLEDGE_BASE.md`).

---

## 10. Ghi chú cho Agent Team

- Ưu tiên trải nghiệm đơn giản, nhanh, dễ quan sát.
- Giữ nguyên dark theme và style hiện tại.
- Không phá vỡ các cơ chế an toàn đã có (Emergency Stop, confirmation, status gate).
- Sau khi hoàn thành, cập nhật `KNOWLEDGE_BASE.md` và loại bỏ mô tả 7-step wizard trong spec nếu còn.

---

**Kết thúc tài liệu yêu cầu.**
