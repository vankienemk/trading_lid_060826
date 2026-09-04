# Flowix — Hướng dẫn sử dụng nhanh

> "Notes for you, Memory for your agents" — ghi chú Markdown theo notebook, dùng chung cho người và agent DSH.

## Trạng thái hiện tại (verified 2026-09-01)

| Thành phần | Vị trí |
|---|---|
| CLI | `~/.local/bin/flowix` (alias → `flowix-cli` 0.1.0) |
| Database (index) | `.flowix/index.db` — **trong workspace** |
| File note | `flowix/` (vd: `flowix/Xin chao Flowix.md`) — **trong workspace** |
| Notebook | `work` (path `/Users/a/Documents/Coding/test/flowix`, 1 note `r00qbpw1`) |
| Backup (bản cũ) | `~/.flowix-backup/` — giữ nguyên, chưa xóa `~/.flowix` |

**Mọi lệnh sau đây chạy trong workspace, không cần escalation vào `~/.flowix`.**

## 1. Chuẩn bị môi trường

```bash
# Trong DSH agent: mỗi lần chạy lệnh đều cần FLOWIX_HOME (bash mới = shell mới)
export FLOWIX_HOME="$PWD/.flowix"          # $PWD = workspace đang chạy
# Hoặc dùng đường dẫn tuyệt đối:
export FLOWIX_HOME="/Users/a/Documents/Coding/test/.flowix"
```

Lưu ý: biến đúng là `FLOWIX_HOME` (KHÔNG phải `FLOWIX_DATA`).

## 2. Đọc (không sửa gì)

```bash
flowix notebooks                              # danh sách notebook
flowix list                                   # note trong notebook hiện tại
flowix list work                              # note trong notebook "work"
flowix show r00qbpw1                          # đọc 1 note theo id
flowix tags                                   # danh sách tag
flowix search "liquidity" --limit 20          # tìm nội dung text
flowix search "TODO" -j | jq '.matches[].title'   # JSON → jq
```

## 3. Ghi

```bash
echo "nội dung note" | flowix create          # tạo note mới (đọc từ stdin)
flowix create --file note.md                  # tạo từ file Markdown
flowix write <id> --file note.md              # ghi đè TOÀN BỘ note
flowix edit <id> -o "cũ" -n "mới"             # sửa đúng 1 đoạn khớp chính xác
flowix edit <id> -o "cũ" -n "mới" --dry-run   # xem trước kết quả, không sửa
flowix delete <id>                            # XÓA VĨNH VIỄN — cẩn thận
```

## 4. Qua tool MCP trong DSH (`mcp__flowix__memo`)

Agent dùng tool với tham số `action`: `notebooks` · `list` · `tags` · `search` · `show` · `create` · `write` · `edit` · `delete` · `plugin` (list/describe/create).

⚠️ Tool MCP launcher (GUI) hiện dùng `~/.flowix` mặc định — muốn nó dùng bản workspace thì khởi động GUI với `FLOWIX_HOME` set sẵn.

## 5. Luật vàng

1. `delete` là vĩnh viễn — không dùng khi chưa chắc chắn.
2. `write` thay cả note; thay đổi nhỏ dùng `edit` (+ `--dry-run` trước).
3. Mỗi note một chủ đề, gắn tag rõ ràng — chất lượng tìm kiếm giảm khi store đầy rác.
4. Ưu tiên `search` thay vì duyệt thủ công.
5. `flowix mcp` chạy MCP server qua stdio (thứ mà plugin DSH dùng).

## 6. Cấu trúc dữ liệu

- `.flowix/index.db` (SQLite): bảng `notebooks` (lưu **path tuyệt đối**), `memos` (chỉ lưu `notebook_id` + `filename`), kèm tags/todos/revisions…
- File note nằm ở thư mục `notebooks.path`, tên file = `filename` (vd `Xin chao Flowix.md`).
- Muốn đổi nơi chứa note: copy thư mục + `UPDATE notebooks SET path='...' WHERE id='work';`