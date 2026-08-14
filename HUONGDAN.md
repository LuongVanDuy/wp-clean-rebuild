# HƯỚNG DẪN SỬ DỤNG WP CLEAN REBUILD

# PHẦN A — NHÂN SỰ CHỈ CẦN CHẠY 1 FILE

## BƯỚC 1 — Mở trình xử lý tự động

**Câu lệnh:**

```powershell
.\BATDAU.bat
```

---

# PHẦN B — BATDAU SẼ TỰ LÀM GÌ

`BATDAU.bat` là workflow chính dành cho nhân sự vận hành.

Trình hướng dẫn sẽ tự chạy theo thứ tự:

```text
Kiểm tra môi trường
→ nếu thiếu uv/Python/thư viện thì hỏi có cài tự động hay không
→ chọn dự án cũ hoặc tạo dự án mới
→ test FTP + đúng remotePath
→ backup file
→ backup database
→ verify SHA-256
→ scan database/uploads
→ tạo clean staging
→ preflight
→ hỏi xác nhận trước bước phá hủy
→ rebuild WordPress + database sạch
→ xử lý Flatsome/theme con
→ xử lý plugin WordPress.org/plugin private
→ final verify
→ PASS / PASS WITH WARNINGS / BLOCKED
```

## Tạo dự án mới

Wizard sẽ hỏi và tự sinh file trong:

```text
sites\<ten-du-an>.json
```

Các thông tin cần nhập:

```text
FTP host
tài khoản FTP
mật khẩu FTP
ftp / ftps
port
remotePath
siteUrl
passive mode
workers
blockMb
```

`sites/*.json` đã nằm trong `.gitignore`, không được commit credential lên GitHub.

## Chọn dự án cũ

Wizard tự đọc các report hiện có và đề xuất bước cần chạy tiếp.

Ví dụ:

```text
đã backup nhưng chưa clean
→ tiếp tục từ clean

đã rebuild core/database nhưng theme con đang sửa
→ tiếp tục từ theme

đã xong theme/plugin nhưng final verify lỗi
→ chạy lại final verify
```

Sau khi đã có bằng chứng `rebuild_ready`, wizard không được quay ngược lại backup/rebuild chỉ vì thiếu metadata cũ.

## Theme con bị nghi mã độc

Wizard dừng và chỉ rõ:

```text
repairs\<domain>\themes\<child-theme>\working-copy
```

Kỹ thuật chỉ sửa `working-copy`.

Không sửa:

```text
backups\<domain>\themes\<child-theme>
```

Sau khi sửa xong, chạy lại:

```powershell
.\BATDAU.bat
```

Wizard tự quay lại bước theme và scan lại working-copy.

## Plugin

Plugin có trên WordPress.org:

```text
tải package sạch mới nhất
→ giải nén local
→ validate
→ upload
```

Plugin không có trên WordPress.org:

```text
không restore code backup
→ báo nhân sự cài bản sạch thủ công từ vendor
```

## Final verify

Wizard kiểm tra:

```text
WordPress core checksum
PHP/executable trong uploads
known malware markers
file bridge/temp còn sót
file thực thi lạ ở root
frontend HTTP
/wp-admin HTTP
```

Kết quả cuối:

```text
✅ PASS
⚠ PASS WITH WARNINGS
❌ BLOCKED
```

---

# PHẦN C — COMMAND KỸ THUẬT / RECOVERY

Các command dưới đây dành cho kỹ thuật, không phải workflow thông thường của nhân sự.

## Kiểm tra môi trường

```powershell
.\wpclean.bat doctor
```

## Test FTP

```powershell
.\wpclean.bat ftp-test-config .\sites\ftp.json
```

## Backup file

```powershell
.\wpclean.bat backup-config .\sites\ftp.json
```

## Backup database

```powershell
.\wpclean.bat db-backup-config .\sites\ftp.json
```

## Verify backup

```powershell
.\wpclean.bat verify-backup .\backups\<domain>
```

## Scan backup

```powershell
.\wpclean.bat scan-backup .\backups\<domain>
```

## Tạo clean staging

```powershell
.\wpclean.bat prepare-clean-config .\sites\ftp.json .\backups\<domain>
```

## Preflight

```powershell
.\wpclean.bat rebuild-preflight .\sites\ftp.json .\backups\<domain> --fast
```

## Rebuild destructive

```powershell
.\wpclean.bat rebuild-config .\sites\ftp.json .\backups\<domain> --execute
```

Không chạy lại `--execute` chỉ để sửa lỗi theme/plugin/database sau khi destructive boundary đã qua.

## Resume database

```powershell
.\wpclean.bat rebuild-resume-db-config .\sites\ftp.json .\backups\<domain>
```

## Theme-only

```powershell
.\wpclean.bat rebuild-theme-config .\sites\ftp.json .\backups\<domain>
```

## Plugin-only

```powershell
.\wpclean.bat rebuild-plugin-config .\sites\ftp.json .\backups\<domain>
```

## Final verify-only

```powershell
.\wpclean.bat verify-live-config .\sites\ftp.json .\backups\<domain>
```

---

# PHẦN D — NGUYÊN TẮC AN TOÀN

1. Backup gốc là immutable evidence, không sửa trực tiếp.
2. Malware chỉ bị loại khỏi clean/repair/restore path.
3. Không restore mù plugin/theme PHP từ site đã nhiễm.
4. Destructive rebuild chỉ chạy sau backup + clean + preflight.
5. Nếu theme con bị block, sửa `repairs/.../working-copy` rồi chạy `BATDAU.bat` lại.
6. Nếu plugin private/premium cần dùng, upload bản sạch từ vendor.
7. Chỉ bàn giao khi final verify đạt `PASS` hoặc đã hiểu rõ mọi warning trong `PASS WITH WARNINGS`.
