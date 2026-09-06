# Lot Sizing Audit — 2026-09-06

> **KẾT LUẬN (1 dòng): KHÔNG tồn tại công thức tính lot theo risk % trong `trading_live/live/` — lot gửi lên MT5 luôn là **hằng số `0.01`** ở path tự động; các field `position_sizing` trong config (`risk_per_trade_pct`, `base_multiplier`, `max_position_size_lots`, `min_position_size_lots`) được khai báo nhưng **không được nối vào bất kỳ phép tính lot nào.** Đây là phát hiện (không phải "no bug") — risk theo % tài khoản KHÔNG được điều khiển ở tầng live.**

Phạm vi audit (chỉ đọc, không sửa): `trading_live/live/` — `engine/`, `state/`, `mcp/`, cùng config `configs/symbols/{XAUUSD,EURUSD}.yaml`. Toàn bộ kết luận dưới đây dẫn chứng bằng `path:line` cụ thể.

---

## Câu trả lời 6 câu hỏi (mục A)

### Q1. Công thức tính lot hiện tại là gì?

**Trả lời: Không có công thức tính lot theo risk. Lot = hằng số `0.01`.** Có 2 path gửi lệnh, cả hai đều không tính lot từ risk:

- **Path tự động (auto-execute):** `signal_polling_engine_v2._auto_send_order` (line 551) gọi `self._exec_layer.send_order(...)` (line 572–583) mà **không truyền `volume=`**:

  ```python
  # live/engine/signal_polling_engine_v2.py:572-583
  result = self._exec_layer.send_order(
      asset=cand.symbol,
      direction=cand.direction,
      entry_price=cand.entry_price,
      stop_loss=cand.stop_price,
      take_profit=cand.target_price,
      signal_id=signal_id,
      rule_score=cand.rule_score,
      model_probability=cand.model_prob,
      automation_level=level,
      kill_switch_blocking=False,
  )
  ```

  Vì `send_order` có tham số mặc định, volume rơi vào default:

  ```python
  # live/engine/execution_layer_v2.py:305-307
  def send_order(self, asset: str, direction: str,
                 entry_price: float, stop_loss: float,
                 take_profit: float, volume: float = 0.01, ...)
  ```

- **Path thủ công (GUI):** `gui_bridge.send_order(lot_size: float = 0.01)` (gui_bridge.py:358) — mặc định cũng 0.01, và gọi `self._exec_layer.place_order(volume=lot_size, ...)` (gui_bridge.py:369). **`ExecutionLayer` không có method `place_order`** (chỉ có `send_order`, execution_layer_v2.py:305) → lỗi `AttributeError` bị nuốt bởi `except Exception` (gui_bridge.py:383–388) → path thủ công **không bao giờ gửi được lệnh** (xem Q6).

- **Volume đi thẳng lên MT5, không qua bất kỳ phép tính nào:**
  ```python
  # live/engine/execution_layer_v2.py:346-354
  order_params = {
      "symbol": asset, "volume": volume,
      "order_type": order_type, "price": entry_price,
      "sl": stop_loss, "tp": take_profit,
      "comment": comment, "magic": MAGIC_NUMBER,
  }
  ...
  order_result = self._call_mcp("trade_send_market_order", order_params)
  ```

  → Tóm tắt: **`lot = 0.01` (hằng số).** Không dùng balance, không dùng risk%, không dùng stop-distance, không dùng point value/contract size.

### Q2. `stop_distance` dùng để tính lot lấy từ đâu — có bằng `|entry - stop|` của Signal Engine, hay bị tính lại ở Execution Layer?

**Trả lời:** `stop_distance` được tính duy nhất **một lần** trong Signal Engine và **không hề dùng để tính lot** (vì không có phép tính lot). Nó chỉ dùng để suy ra `target_price`:

```python
# live/engine/signal_engine_v2.py:432-437
if is_long:
    stop_price = extreme - buffer_atr * atr_sweep
    target_price = entry_price + reward_r * (entry_price - stop_price)
else:
    stop_price = extreme + buffer_atr * atr_sweep
    target_price = entry_price - reward_r * (stop_price - entry_price)
```

Giá trị `stop_distance` này (thực chất là `stop_price`) được truyền nguyên vẹn xuống Execution Layer dưới dạng `stop_loss=cand.stop_price` (signal_polling_engine_v2.py:576) rồi đưa vào MCP `"sl": stop_loss` (execution_layer_v2.py:349). **Không có nơi nào "tính lại" stop_distance lần hai.** Vì vậy rủi ro "2 nơi tính khác nhau" (mà spec nêu) là **không xảy ra** — nhưng lý do là vì không tồn tại công thức lot-from-stop-distance, chứ không phải vì code khớp hoàn hảo.

### Q3. Point value / contract size — lấy đúng theo symbol (gọi `symbol_info`) hay hard-code?

**Trả lời: Không đọc, cũng không hard-code — vì không cần đến.** `get_symbol_info` chỉ là pass-through MCP:

```python
# live/engine/execution_layer_v2.py:263-264
def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
    return self._call_mcp("get_symbol_info", {"symbol": symbol})
```

Không có dòng nào trong `live/` parse `contract_size`/`tick_value`/`point_value`/`volume_step`/`volume_min`/`volume_max`. Duy nhất các số theo symbol là trong YAML:

| field | XAUUSD | EURUSD |
|---|---|---|
| `broker.digits` | 5 | 5 |
| `broker.lot_size` | 0.01 | 0.01 |
| `position_sizing.risk_per_trade_pct` | 1.0 | 0.5 |
| `position_sizing.base_multiplier` | 1.0 | 0.5 |
| `position_sizing.max_position_size_lots` | 1.0 | 0.5 |
| `position_sizing.min_position_size_lots` | 0.01 | 0.01 |

(không có contract size/point value cho XAUUSD hay EURUSD → **không hard-code**.) Nguồn: `configs/symbols/XAUUSD.yaml` (dòng 25–53), `configs/symbols/EURUSD.yaml` (dòng 25–54).

### Q4. `size_multiplier` (1.0 XAUUSD / 0.5 EURUSD) nhân **trước** hay **sau** khi tính lot?

**Trả lời: Không nhân vào đâu cả.** `base_multiplier` được đọc từ YAML và lưu vào `SymbolConfig.position_size_multiplier`:

```python
# live/state/risk_guard_v2.py:112-115
base_mul = ps.get("base_multiplier", 1.0)
...
sc = SymbolConfig(name=name, status=status,
                  position_size_multiplier=base_mul, active=is_active)
```

Và `get_position_size(asset)` chỉ trả về chính giá trị này:

```python
# live/state/risk_guard_v2.py:166-170
def get_position_size(self, asset: str) -> float:
    sc = self._state.get_symbol_config(asset)
    if sc is not None:
        return sc.position_size_multiplier
    return self._symbol_configs.get(asset, {}).get("position_sizing", {}).get("base_multiplier", 1.0)
```

Giá trị này **không được đưa vào `send_order`** — nó chỉ dùng cho hiển thị registry/onboarding (gui_tab_onboarding.py, gui_bridge.py:575). Nghĩa là với path tự động, lot luôn 0.01 bất kể multiplier. Ngữ cảnh "nhân trước hay sau khi tính lot" của spec là **không áp dụng được** (không có phép tính lot). Lưu ý toán học: nếu có công thức, nhân risk-budget × multiplier rồi chia value-per-lot, hay chia trước rồi nhân sau, cho **cùng kết quả** (phép nhân giao hoán) — nên khác biệt chỉ đến từ bước làm tròn/cap, ở đây không tồn tại.

### Q5. Lot cuối có được làm tròn theo `volume_step`/`volume_min`/`volume_max` (từ MCP)? Làm tròn lên hay xuống?

**Trả lời: KHÔNG.** Client `live/` không đọc, không làm tròn, không chuẩn hóa volume. `"volume": volume` đi thẳng vào MCP (execution_layer_v2.py:347). `max_position_size_lots`/`min_position_size_lots` có trong YAML nhưng **không một file `.py` nào tham chiếu** (grep toàn kho chỉ trả về YAML). Do đó không có "làm tròn lên" hay "làm tròn xuống" ở phía client — việc chấp nhận/làm tròn do broker (MT5) quyết định theo `volume_step` phía server, client không can thiệp nên **không có đường dẫn "risk thực tế luôn ≥ risk dự kiến" do làm tròn lên**.

### Q6. Có giới hạn nào chặn lot tối đa tuyệt đối không?

**Trả lời: KHÔNG có cap ở client.** `max_position_size_lots` (XAUUSD:1.0 / EURUSD:0.5) là **config chết** — không nơi nào đọc. Không có clamp nào trong chuỗi gửi lệnh. Thực tế hiện tại vẫn an toàn vì:
- Path tự động luôn gửi `0.01` (dưới mọi cap), nên không ảnh hưởng.
- Path thủ công (nếu chạy được) nhận lot do người dùng nhập, **không qua** cap — nhưng path này đang **hỏng** vì gọi `_exec_layer.place_order(...)` không tồn tại (gui_bridge.py:369 → `AttributeError` → trả `{"ok": False, "error": ...}`). Vì vậy hôm nay không thể gửi `volume` lớn qua GUI.

> Nếu sau này thêm công thức lot theo risk, sẽ **thiếu hẳn khóa "absolute max lot"** — một lệnh do lỗi tính toán vẫn có thể tạo `volume` khổng lồ.

---

## Bảng ví dụ tính tay (bắt buộc)

> **Giả định contract chuẩn (MT5 mặc định, KHÔNG lấy từ code) — bắt buộc để tính tay được:** XAUUSD 1.0 lot = 100 oz → `$1` giá = `$100`/lot; EURUSD 1.0 lot = 100,000 đơn vị → 1 pip (0.0001) = `$10`/lot. Balance = **$10,000** cho cả 2 symbol. Entry/stop lấy theo spec; `stop distance` = `|entry − stop|`. Công thức lý tưởng: `lot = (balance × risk% × base_multiplier) / (stop_distance_value × value_per_lot)`; làm tròn xuống bước 0.01 (step/min = 0.01).

### Bảng 1 — BẢNG TÍNH TAY THEO ĐÚNG 10 CỘT CỦA SPEC (công thức risk-based GIẢ ĐỊNH — KHÔNG có trong code; chỉ để kiểm chứng toán bằng máy tính cầm tay)

| Symbol | Balance | Risk% | Entry | Stop | Stop distance | Lot tính ra (trước làm tròn) | Lot cuối (sau làm tròn) | Risk thực tế ($) | Risk thực tế (%) |
|---|---|---|---|---|---|---|---|---|---|
| XAUUSD | 10,000 | 1% | 2650.50 | 2648.20 | 2.30 | 100/230 = **0.43478** | **0.43** | 0.43×230 = **98.90** | **0.989%** |
| EURUSD | 10,000 | 1% (×0.5 multiplier) | 1.08500 | 1.08300 | 0.00200 (20 pips) | 50/200 = **0.25** | **0.25** | 0.25×200 = **50.00** | **0.500%** |

Kiểm chứng 30s:
- **XAUUSD:** risk budget = 10,000×1% = **$100**. value per lot (stop 2.30) = 2.30×100 = **$230**. lot = 100/230 = 0.43478 → làm tròn xuống 0.01 → **0.43**. Risk thực tế = 0.43×230 = **$98.90** = **0.989%** (≈ ngân sách 1%).
- **EURUSD:** risk budget = 10,000×1%×0.5 = **$50**. value per lot (stop 20 pips) = 20×10 = **$200**. lot = 50/200 = **0.25**. Risk thực tế = 0.25×200 = **$50** = **0.500%** (≈ ngân sách 0.5% sau multiplier).

> ⚠️ Bảng 1 minh họa **công thức lý tưởng**; trong code **KHÔNG tồn tại** phép tính này. Code đặt lệnh bằng lot cố định `0.01` → xem Bảng 2 để thấy độ lệch.

### Bảng 2 — HÀNH VI THỰC TẾ CỦA CODE (gửi `volume=0.01` cố định; risk thực tế phụ thuộc hoàn toàn vào stop distance từng lệnh)

| Symbol | Balance | Risk% (config, UNUSED) | Entry | Stop | Stop distance | Lot (thực gửi) | Risk thực tế ($) | Risk thực tế (%) |
|---|---|---|---|---|---|---|---|---|
| XAUUSD | 10,000 | 1.0% | 2650.50 | 2648.20 | 2.30 | **0.01** | 2.30×100×0.01 = **2.30** | **0.023%** |
| EURUSD | 10,000 | 0.5% | 1.08500 | 1.08300 | 0.00200 (20 pips) | **0.01** | 20×10×0.01 = **2.00** | **0.020%** |

→ **Kết luận từ 2 bảng:** code gửi `0.01` lot cho cả hai → mỗi lệnh chỉ rủi ro ~$2–2.30 (**~0.02%**), so với ngân sách 1% (XAU) / 0.5% (EUR) mà config khai báo → **nhỏ hơn ~43× (XAU) / ~25× (EUR)**. Đây là hệ quả trực tiếp: **công thức lot theo risk% chưa được nối vào chuỗi đặt lệnh** (lot gần 0 so với ngân sách).

*Đối chiếu Q4: EURUSD áp `base_multiplier=0.5` **trước** khi chia (risk budget $50). Nếu áp **sau** khi tính lot: 100/200 = 0.5 → ×0.5 = 0.25 → **giống hệt**. Minh chứng nhân trước/sau cho cùng kết quả vì phép nhân giao hoán — ở đây không có bước làm tròn/cap xen giữa để tạo khác biệt.*

---

## Kết luận

- [x] **Phát hiện bug / thiếu triển khai** — mô tả cụ thể:

  1. **Không tồn tại công thức lot theo risk%.** Path tự động luôn gửi `volume=0.01` (default, execution_layer_v2.py:307; không truyền `volume=` ở signal_polling_engine_v2.py:572-583). **Ảnh hưởng:** risk % tài khoản con số thiết kế (1% XAU / 0.5% EUR trong config) **không bao giờ được thực thi** — risk thực tế bị phụ thuộc hoàn toàn vào stop distance từng lệnh và là ~0.02% trong ví dụ, tức **gần bằng 0** so với ngân sách 1%/0.5%. Hệ quả: về mặt risk, hệ thống đang "over-snug" (rủi ro quá nhỏ) chứ không phải "under-snug".
  2. **Config `position_sizing` là config chết trong tầng live:** `risk_per_trade_pct`, `base_multiplier`, `max_position_size_lots`, `min_position_size_lots` (XAUUSD.yaml:49-53 / EURUSD.yaml:49-54) chỉ được đọc một phần (risk_guard_v2.py:112,117) để đặt `breakeven_cost` và `position_size_multiplier`, nhưng **không nơi nào nhân/pipeline vào lot khi đặt lệnh**. `get_position_size()` (risk_guard_v2.py:166-170) trả về multiplier nhưng **không ai dùng nó cho `send_order`**.
  3. **Không có cap lot tối đa tuyệt đối** (không đọc `max_position_size_lots`) — an toàn tình cờ vì lot cố định 0.01.
  4. **Path gửi lệnh thủ công qua GUI bị hỏng:** `gui_bridge.send_order` gọi `_exec_layer.place_order(...)` (gui_bridge.py:369) mà `ExecutionLayer` **không có** method này → `AttributeError` bị nuốt (gui_bridge.py:383-388) → luôn trả `{"ok": False, ...}`. (Không thuộc trọng tâm lot formula nhưng cần biết.)

- [ ] Không phát hiện bug.

**Cấp độ ảnh hưởng tổng hợp:** Trọng tâm spec hỏi "công thức lot có bug không" — thực tế là **công thức chưa được viết**. Điều này **chặn** việc chạy đúng risk budget (mọi lệnh auto đều rủi ro ~0.02% thay vì ngân sách 1% XAU / 0.5% EUR). Mức độ lệch so với ngân sách khai báo: **risk thực tế chỉ bằng ~2.3% (XAU) và ~4.0% (EUR) của ngân sách thiết kế** (0.023% so 1% → nhỏ ~43×; 0.020% so 0.5% → nhỏ ~25×).

---
*Ghi chú: Audit này chỉ đọc code, không sửa bất kỳ file live nào. Mọi giả định contract size là chuẩn MT5 mặc định, dùng để minh họa phép tính tay; code không đọc contract size nào cả.*
