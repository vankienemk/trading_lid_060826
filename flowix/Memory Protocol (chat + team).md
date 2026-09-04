---
key: c70vt0c8
---
# Memory Protocol (chat + team)

Cách agent dùng bộ nhớ có hiệu quả — dùng chung cho người và team agent.

## Chat (khung hội thoại DSH)
1. Session start: đọc `<openviking-context>` được inject TRƯỚC — không search lại thứ đã có.
2. Retrieval ladder: `search(mode=context)` → `find` → `read` (mở URI) → `grep/glob` (số chính xác).
3. Ghi có chủ đích: chỉ durable facts / decisions / preferences. Rác làm loãng retrieval.
4. Cuối phiên: OpenViking tự extract memories — ít khi cần remember().
5. Flowix = notes nhanh / runbook / task-state: 1 chủ đề 1 note, gắn tag, dùng search.

## Team agent
- Teammate là agent mới, KHÔNG thấy context của captain → bộ nhớ của họ = prompt/contract + file workspace + store chung.
- Flowix (workspace, FLOWIX_HOME=$PWD/.flowix) = scratchpad chung: mọi member đọc/ghi không cần escalation.
- Contract bắt buộc: objective / acceptance / inScope / verify — đây là "bộ nhớ chính" của từng member.
- Captain: tổng hợp kết quả member → lưu dài hạn vào OpenViking (reports/lessons/entities).
- Báo cáo giữa các vòng: ngắn, có cấu trúc, hành động được.

## User giúp agent
- /compact khi agent báo context ~70% (đo bằng /tmp/ctx.py).
- Đặt title/tag rõ ràng; dùng forget() để xóa rác khi cần.
- Không giữ mọi thứ — tự động extract + Flowix đã lo phần lớn.