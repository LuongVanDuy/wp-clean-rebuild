# HƯỚNG DẪN SỬ DỤNG WP CLEAN REBUILD

# PHẦN A — NHÂN SỰ CHỈ CẦN MỞ GIAO DIỆN

## BƯỚC 1 — Mở giao diện điều khiển

**Câu lệnh:**

```powershell
.\GIAODIEN.bat
```

`GIAODIEN.bat` sẽ tự kiểm tra `uv`, Python 3.13 và thư viện dự án. Nếu máy mới còn thiếu thành phần, hệ thống sẽ hỏi và hỗ trợ cài tự động.

Sau đó trình duyệt tự mở giao diện local tại địa chỉ dạng:

```text
http://127.0.0.1:8765/
```

GUI chỉ bind `127.0.0.1`, không mở dịch vụ cho máy khác trong mạng LAN.

Nếu GUI gặp sự cố, kỹ thuật vẫn có thể dùng workflow terminal dự phòng:

```powershell
.\BATDAU.bat
```

---

# PHẦN B — GIAO DIỆN SẼ TỰ LÀM GÌ

Nhân sự không cần nhớ command kỹ thuật. Trên dashboard chỉ cần:

```text
chọn dự án cũ hoặc Tạo dự án mới
→ Test FTP
→ bấm Tiếp tục
→ hệ thống tự chạy các bước an toàn liên tiếp
→ chỉ dừng khi cần người dùng xác nhận
```

Flow chính:

```text
Kiểm tra môi trường
→ chọn/tạo dự án
→ test FTP + đúng remotePath
→ backup file
→ backup database
→ verify SHA-256
→ scan database/uploads
→ tạo clean staging
→ preflight
→ yêu cầu nhập lại domain trước destructive rebuild
→ rebuild WordPress + database sạch
→ xử lý Flatsome/theme con
→ xử lý plugin WordPress.org/plugin private
→ quét/khôi phục MU-plugin sạch
→ kiểm tra nhanh frontend + wp-admin
→ nhân sự xác nhận hoàn tất
```

## Tạo dự án mới

Bấm **Tạo dự án** trên giao diện và nhập:

```text
Tên dự án
FTP host
tài khoản FTP
mật khẩu FTP
FTP / FTPS
port
remotePath
siteUrl
workers
blockMb
```

GUI tự sinh:

```text
sites\<ten-du-an>.json
```

`sites/*.json` đã nằm trong `.gitignore`, không được commit credential lên GitHub.

## Chọn dự án cũ

Dashboard tự đọc backup/report/state và hiển thị phần trăm tiến độ cùng bước tiếp theo.

## Đọc trạng thái và log trên GUI

Khi một bước đang chạy, dashboard hiển thị:

```text
thời gian đã chạy
→ tín hiệu cuối từ engine
→ số file hoàn tất / tổng số file
→ tốc độ truyền hiện tại
→ file hoặc đường dẫn đang xử lý
```

Các trạng thái có ý nghĩa khác nhau:

```text
Đang hoạt động      = engine vừa phát tín hiệu
Đang chờ phản hồi   = đang chờ hosting/network trả lời
Phản hồi chậm       = chưa có tín hiệu mới trong hơn một phút
Có dấu hiệu bị treo = chưa có tín hiệu mới trong hơn ba phút
```

Nếu có lỗi, GUI hiển thị mã ổn định, giải thích tiếng Việt và cách xử lý. Ví dụ:

```text
FTP-AUTH-001       = sai tài khoản hoặc mật khẩu FTP
FTP-TIMEOUT-001    = hosting không phản hồi
FTP-PERM-001       = hosting từ chối quyền file
BACKUP-INTEGRITY-001 = backup/manifest không còn nguyên vẹn
DB-IMPORT-001      = database chưa import hoàn tất
```

Nhấn **Chi tiết kỹ thuật** khi cần gửi lỗi cho kỹ thuật. Không chạy lại wipe bằng tay; chỉ dùng nút thử lại đúng bước hoặc DB-only resume do GUI cung cấp.

Ví dụ:

```text
đã backup nhưng chưa clean
→ Tiếp tục từ clean

đã rebuild core/database nhưng theme con đang sửa
→ Tiếp tục từ theme

đã xong theme/plugin/MU-plugin
→ Kiểm tra nhanh website
```

Sau khi đã có bằng chứng `rebuild_ready`, GUI không quay ngược lại backup/rebuild chỉ vì thiếu metadata cũ.

Nếu rebuild đã đi qua destructive boundary nhưng database chưa import xong, GUI ưu tiên:

```text
DB-only resume
```

không wipe website lần nữa.

## Rebuild phá hủy

GUI không tự chạy destructive rebuild ngay.

Trước khi rebuild, người vận hành phải nhập lại đúng domain dự án. Ví dụ:

```text
noithatdaiduong.com
```

sau đó mới bấm xác nhận.

## Theme con bị nghi mã độc

GUI dừng và hiển thị working-copy:

```text
repairs\<domain>\themes\<child-theme>\working-copy
```

Bấm **Mở thư mục sửa** để mở Windows Explorer.

Kỹ thuật chỉ sửa `working-copy`.

Không sửa:

```text
backups\<domain>\themes\<child-theme>
```

Sau khi sửa xong, bấm **Quét lại & tiếp tục**. Chỉ theme con PASS mới được upload.

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
→ GUI báo nhân sự cài bản sạch thủ công từ vendor
```

## MU-plugin

Sau plugin thường, GUI chạy MU-plugin stage:

```text
scan theo component
→ component sạch mới upload
→ component HIGH/CRITICAL hoặc unreadable bị block toàn bộ
```

## Kiểm tra cuối

Mặc định GUI dùng chế độ nhanh:

```text
HTTP frontend
HTTP /wp-admin
→ nhân sự mở website kiểm tra warning PHP / giao diện / chức năng chính
→ xác nhận hoàn tất
```

Deep live filesystem/checksum scan không chạy mặc định để tiết kiệm thời gian.

Kỹ thuật vẫn có thể chọn **Quét sâu** trên GUI hoặc chạy command kỹ thuật bên dưới khi cần.

## Xóa dự án hoàn tất

Sau khi project hoàn tất, GUI có nút **Xóa dự án local**.

Nút này chỉ xóa dữ liệu local của tool:

```text
sites\<project>.json
backups\...
reports\...
repairs\...
```

Website trên hosting không bị xóa hoặc sửa.

---

# PHẦN C — COMMAND KỸ THUẬT / RECOVERY

Các command dưới đây dành cho kỹ thuật, không phải workflow thông thường của nhân sự.

## Workflow terminal dự phòng

```powershell
.\BATDAU.bat
```

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

## MU-plugin-only

```powershell
.\wpclean.bat rebuild-mu-plugins-config .\sites\ftp.json .\backups\<domain>
```

## Deep final verify-only

```powershell
.\wpclean.bat verify-live-config .\sites\ftp.json .\backups\<domain>
```

## Xóa dự án bằng terminal

```powershell
.\XOADUAN.bat
```

---

# PHẦN D — NGUYÊN TẮC AN TOÀN

1. Backup gốc là immutable evidence, không sửa trực tiếp.
2. Malware chỉ bị loại khỏi clean/repair/restore path.
3. Không restore mù plugin/theme PHP từ site đã nhiễm.
4. Destructive rebuild chỉ chạy sau backup + clean + preflight và xác nhận rõ ràng.
5. Nếu theme con bị block, chỉ sửa `repairs/.../working-copy` rồi quét lại.
6. Nếu plugin private/premium cần dùng, upload bản sạch từ vendor.
7. MU-plugin bị HIGH/CRITICAL hoặc unreadable không được upload.
8. GUI mặc định dùng final quick check; deep scan dành cho kỹ thuật khi cần.
9. Không whitelist toàn bộ project trong antivirus vì `backups/` có thể chứa malware thật.
