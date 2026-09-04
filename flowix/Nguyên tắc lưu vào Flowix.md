---
key: pmwof1sx
---
# Nguyên tắc lưu vào Flowix

Tài liệu tham chiếu: cái gì vào Flowix, ghi thế nào, khi nào xóa. Dùng chung cho người + agent DSH + team.

## 1. Phân loại trước khi lưu — cái gì vào đâu

| Loại | Nơi lưu | Ví dụ |
|---|---|---|
| Trạng thái công việc, runbook, findings, quyết định trung gian, protocol | **Flowix** | task-state, checklist, "cách reset X" |
| Sự kiện đã đóng, lessons, memories dài hạn | **OpenViking** (plugin tự extract) | báo cáo team, lessons, entities |
| Code/YAML/dataset/chart — thứ phải chạy lại được | **Workspace files** | strategy yaml, backtest script |
| Roster/DAG/mailbox của team | **.agent-teams** (platform) | — |

Quy tắc gọn: **trạng thái vận hành → Flowix · sự kiện đã đóng → OpenViking · artifact chạy được → file.**

## 2. Ghi

1. **1 chủ đề = 1 note** — mỗi note một nhiệm vụ/kết luận; không gộp nhiều chuyện.
2. **Title theo chủ đề, không theo ngày** — để search ra đúng.
3. **Gắn tag mọi note** — project + loại (runbook/task/decision/finding).
4. Nội dung: **kết luận + căn cứ ngắn**, định dạng "đã làm gì → kết quả gì → tiếp theo gì".
5. Không lưu binary vào note; attachments chỉ khi thật cần.

## 3. Tra cứu

- **search trước, không duyệt thủ công** (`flowix search "keyword" --limit 20`).
- Index search có thể **lag với note tạo bằng CLI** — không thấy ngay thì chờ hoặc show theo id.
- Preview trong list/show có thể cũ hơn nội dung — muốn chắc đọc full note.

## 4. Cập nhật & xóa

- Thay đổi nhỏ → `edit` (luôn `--dry-run` trước); thay cả note → `write`.
- `delete` là **vĩnh viễn** — chỉ xóa khi note hết giá trị hoặc trùng lặp.
- Note trung gian đã đóng: nếu quan trọng dài hạn → để OpenViking giữ (plugin tự extract từ chat), rồi mới xóa bản Flowix tạm.
- Note tham chiếu (protocol/như note này) → giữ lâu, ít sửa.

## 5. Team

- Mọi member dùng chung `FLOWIX_HOME` workspace → **một nguồn sự thật**.
- Member ghi status/findings ngắn gọn để captain tổng hợp; không lưu contract (objective/acceptance) vào Flowix — platform quản.

## 6. Dọn dẹp định kỳ

- Phát hiện note trùng/rác thì xóa ngay — **store loãng làm search kém đi** (chất lượng retrieval giảm theo lượng rác).