# HƯỚNG DẪN SỬ DỤNG WP CLEAN REBUILD

Tài liệu này được viết theo kiểu **chạy lệnh từ trên xuống dưới**.

- Phần A: chỉ cần chạy đúng thứ tự.
- Nếu một lệnh lỗi: dừng tại lệnh đó, kéo xuống **Phần B — Xử lý lỗi** và tìm đúng tên command.
- Không cần đọc toàn bộ phần giải thích trước khi chạy.

> **Quan trọng:** lệnh `rebuild-config --execute` là mốc destructive thật. Từ lúc tool báo `Entering destructive boundary...`, dữ liệu cũ trong WordPress root bắt đầu bị xóa.

---

# PHẦN A — THỨ TỰ CÁC LỆNH CẦN CHẠY

Ví dụ đang dùng:

```text
Profile: .\sites\ftp.json
Backup:  .\backups\tinytensorvn.com
Report:  .\reports\tinytensorvn.com
```

Nếu domain khác thì thay `tinytensorvn.com` bằng domain tương ứng.

## 1. Cập nhật tool

```powershell
git pull
.\START.bat
.\wpclean.bat doctor
```

PASS khi thấy:

```text
CLI runtime looks usable.
```

---

## 2. Test FTP/FTPS

```powershell
.\wpclean.bat ftp-test-config .\sites\ftp.json
```

PASS khi thấy kết nối thành công và `WordPress root configured` đúng thư mục website.

---

## 3. Backup filesystem

```powershell
.\wpclean.bat backup-config .\sites\ftp.json
```

Kết quả hợp lệ có thể là một trong hai dạng:

```text
Filesystem backup verification passed.
```

hoặc:

```text
Filesystem backup verification passed with N explicit exclusion(s).
```

`PASS WITH EXCLUSIONS` vẫn được tiếp tục. File bị exclusion đã được ghi vào report và **không được restore**.

---

## 4. Backup database

```powershell
.\wpclean.bat db-backup-config .\sites\ftp.json
```

PASS khi có:

```text
Database backup completed
SHA-256: ...
```

Database gốc nằm tại:

```text
backups\tinytensorvn.com\database\original.sql
```

---

## 5. Xem trạng thái backup

```powershell
.\wpclean.bat backup-status .\backups\tinytensorvn.com
```

Mục tiêu là nhìn thấy đầy đủ các phần chính:

```text
database
uploads
themes
plugins
config
manifest
```

`mu-plugins`, `php.ini`, `.user.ini`, `robots.txt` có thể không tồn tại trên một số hosting.

---

## 6. Verify backup

```powershell
.\wpclean.bat verify-backup .\backups\tinytensorvn.com
```

PASS khi thấy:

```text
Backup verification passed.
```

hoặc:

```text
Backup verification passed with N explicit exclusion(s).
```

---

## 7. Scan backup

```powershell
.\wpclean.bat scan-backup .\backups\tinytensorvn.com
```

Command này scan database và uploads từ backup.

Nếu thấy malware/finding thì **không xóa file khỏi backup gốc**. Bước `prepare-clean-config` sẽ tạo restore set riêng.

---

## 8. Tạo clean staging

```powershell
.\wpclean.bat prepare-clean-config .\sites\ftp.json .\backups\tinytensorvn.com
```

PASS khi thấy:

```text
Clean restore staging completed and SHA-256 verification passed.
```

Sau bước này phải có:

```text
backups\tinytensorvn.com\clean\database\clean.sql
backups\tinytensorvn.com\clean\uploads\
backups\tinytensorvn.com\clean\manifest.json
```

`clean.sql` mới là database dùng để rebuild, không phải `original.sql`.

---

## 9. Preflight FAST

```powershell
.\wpclean.bat rebuild-preflight .\sites\ftp.json .\backups\tinytensorvn.com --fast
```

PASS khi thấy:

```text
PREFLIGHT PASS — destructive rebuild may be unlocked in the next stage.
```

Bước này chưa xóa gì trên hosting.

---

## 10. Dry-run rebuild

```powershell
.\wpclean.bat rebuild-config .\sites\ftp.json .\backups\tinytensorvn.com
```

PASS khi thấy:

```text
DRY ARM ONLY — nothing was changed remotely.
```

Execution plan hiện tại phải có các ý chính:

```text
Verify backup + clean staging
Download fresh WordPress core
Wipe WordPress root, giữ .well-known
Upload fresh WordPress core
Upload fresh wp-config.php
Upload clean WordPress .htaccess
Restore clean/uploads
Import clean/database/clean.sql
Cleanup temporary import files
```

---

## 11. Rebuild thật — XÓA DATA CŨ VÀ CÀI WORDPRESS LẠI

### Khuyến nghị: không restore PHP cũ

```powershell
.\wpclean.bat rebuild-config .\sites\ftp.json .\backups\tinytensorvn.com --execute
```

Lệnh này sẽ:

```text
1. Verify backup lần cuối
2. Verify clean staging lần cuối
3. Tải WordPress mới từ wordpress.org
4. Validate/extract core local
5. Bắt đầu destructive boundary
6. Xóa nội dung WordPress root, giữ .well-known
7. Upload WordPress core mới
8. Tạo và upload wp-config.php mới + salts mới
9. Tạo và upload .htaccess WordPress sạch
10. Restore clean/uploads
11. Không restore plugin/theme cũ
12. Import clean/database/clean.sql
13. Xóa PHP import bridge + SQL data tạm
14. Ghi rebuild-execute.json
```

### Chỉ dùng khi bạn chủ động muốn restore plugin/theme cũ

```powershell
.\wpclean.bat rebuild-config .\sites\ftp.json .\backups\tinytensorvn.com --execute --restore-backup-code
```

> `--restore-backup-code` có thể đưa PHP bị compromise quay lại. Chỉ dùng khi bạn chấp nhận tự review plugin/theme sau rebuild.

PASS cuối cùng khi thấy:

```text
REBUILD COMPLETED
```

và các trạng thái chính:

```text
Fresh core uploaded: ...
Clean uploads restored: ...
Fresh wp-config.php uploaded: True
Clean .htaccess uploaded: True
Database imported: True
Temporary import cleanup: bridge_removed=True, data_removed=True
```

---

## 12. Kiểm tra website sau rebuild

Mở:

```text
https://<domain>/
https://<domain>/wp-admin/
```

Theo workflow hiện tại:

```text
Username: admin
Password: cùng password FTP
```

Kiểm tra nhanh:

```text
Trang chủ
Trang con / permalink
wp-admin
Media Library
Ảnh/uploads
PHP error log
Browser console
```

Nếu không restore plugin/theme cũ thì sau đó cài lại plugin/theme từ nguồn tin cậy.

---

# PHẦN B — XỬ LÝ LỖI THEO TỪNG COMMAND

Khi lỗi ở command nào thì tìm đúng heading command đó bên dưới.

---

## Lỗi: `doctor`

Command:

```powershell
.\wpclean.bat doctor
```

Nếu `uv`/Python chưa sẵn sàng:

```powershell
.\START.bat
.\wpclean.bat doctor
```

Warning kiểu:

```text
Failed to hardlink files; falling back to full copy
```

thường chỉ là warning của `uv` trên Windows.

---

## Lỗi: `ftp-test-config`

Command:

```powershell
.\wpclean.bat ftp-test-config .\sites\ftp.json
```

Kiểm tra trong profile:

```text
host
username
password
port
protocol
remotePath
```

Ví dụ:

```json
{
  "host": "tinytensorvn.com",
  "username": "FTP_USERNAME",
  "password": "FTP_PASSWORD",
  "protocol": "ftp",
  "port": 21,
  "remotePath": "/domains/tinytensorvn.com/public_html",
  "siteUrl": "https://tinytensorvn.com",
  "passive": true,
  "workers": 4,
  "blockMb": 1
}
```

Warning plain FTP không phải lỗi:

```text
credentials/data are not transport-encrypted
```

---

## Lỗi: `backup-config`

Command:

```powershell
.\wpclean.bat backup-config .\sites\ftp.json
```

### FTP bị reset / WinError 10054

Tool sẽ tự retry và reconnect.

Nếu một file riêng lẻ vẫn không đọc được sau retry, tool có thể trả:

```text
PASS WITH EXCLUSIONS
```

Ví dụ:

```text
EXCLUDED .../wp-content/themes/.../about.php
```

File này:

- được ghi path + lỗi vào `backup-report.json`;
- không có partial file trong backup;
- không được restore;
- không làm fail toàn backup nếu chỉ là file riêng lẻ.

### Nếu cả stage lỗi

Ví dụ toàn bộ uploads/plugins/themes không backup được thì vẫn là `BLOCKED`.

Không được bỏ qua lỗi cả stage.

### Nếu `php.ini` không tồn tại

Ví dụ:

```text
Skipped .../php.ini: 550 No such file or directory
```

Đây là optional trên nhiều hosting và không block workflow.

### Nếu backup bị dừng giữa chừng

Không xóa backup local. Chạy lại cùng command:

```powershell
.\wpclean.bat backup-config .\sites\ftp.json
```

Resume mặc định bật.

---

## Lỗi: `db-backup-config`

Command:

```powershell
.\wpclean.bat db-backup-config .\sites\ftp.json
```

Nếu đã thấy:

```text
Database backup completed
Size: ...
SHA-256: ...
```

thì DB dump đã hoàn tất.

Tool có thể đồng thời report trạng thái full recovery set, nhưng cần phân biệt lỗi database với warning/exclusion filesystem.

Nếu PHP bridge thực sự lỗi, kiểm tra:

```text
siteUrl
wp-config.php
DB_NAME
DB_USER
DB_PASSWORD
DB_HOST
```

Bridge tạm phải truy cập được qua HTTP/HTTPS.

---

## Lỗi: `backup-status`

Command:

```powershell
.\wpclean.bat backup-status .\backups\tinytensorvn.com
```

Dùng command này để biết stage nào đang thiếu.

Không sửa/xóa file trực tiếp trong backup chỉ để làm status đẹp hơn.

---

## Lỗi: `verify-backup`

Command:

```powershell
.\wpclean.bat verify-backup .\backups\tinytensorvn.com
```

### `PASS WITH EXCLUSIONS`

Được tiếp tục nếu exclusion là file riêng lẻ đã được tool ghi rõ và loại khỏi restore.

### `missing`, `size mismatch`, `hash mismatch`

Đây là lỗi integrity thật.

Không chạy `manifest` để tạo manifest mới chỉ nhằm che lỗi.

Backup gốc phải được giữ làm snapshot rollback/forensic.

---

## Lỗi: `scan-backup`

Command:

```powershell
.\wpclean.bat scan-backup .\backups\tinytensorvn.com
```

Nếu thấy malware:

```text
CRITICAL
QUARANTINE / DROP FROM CLEAN RESTORE
```

Không xóa file khỏi backup gốc.

Để `prepare-clean-config` loại nó khỏi clean restore set.

`No findings above the current threshold` không có nghĩa là đã chứng minh DB sạch tuyệt đối.

---

## Lỗi: `prepare-clean-config`

Command:

```powershell
.\wpclean.bat prepare-clean-config .\sites\ftp.json .\backups\tinytensorvn.com
```

### Original backup verify fail

Quay lại:

```powershell
.\wpclean.bat verify-backup .\backups\tinytensorvn.com
```

### Không detect được table prefix

Kiểm tra `database\original.sql` có dump đầy đủ bảng WordPress hay không.

### Clean staging PASS

Phải thấy:

```text
Clean restore staging completed and SHA-256 verification passed.
```

Không sửa tay `clean.sql`/`clean/uploads` sau khi manifest đã được tạo nếu không có lý do rõ ràng.

---

## Lỗi: `rebuild-preflight --fast`

Command:

```powershell
.\wpclean.bat rebuild-preflight .\sites\ftp.json .\backups\tinytensorvn.com --fast
```

### Original backup fail

Quay lại `verify-backup`.

### Clean staging fail

Chạy lại `prepare-clean-config` sau khi xác định nguyên nhân.

### Remote root fail

Dừng ngay và kiểm tra `remotePath`.

Không chạy `--execute` nếu remote root chưa chắc chắn.

---

## Lỗi: dry-run `rebuild-config`

Command:

```powershell
.\wpclean.bat rebuild-config .\sites\ftp.json .\backups\tinytensorvn.com
```

Dry-run phải có:

```text
DRY ARM ONLY — nothing was changed remotely.
```

Kiểm tra đúng:

```text
Site
Remote WordPress root
Original backup
Clean staging
Preflight report
```

Plan phải có `fresh WordPress core`, `fresh wp-config.php`, `clean WordPress .htaccess`, `clean/uploads`, và `clean.sql`.

Nếu sai path thì dừng, không chạy `--execute`.

---

# PHẦN C — XỬ LÝ LỖI KHI ĐÃ CHẠY `--execute`

Command destructive:

```powershell
.\wpclean.bat rebuild-config .\sites\ftp.json .\backups\tinytensorvn.com --execute
```

## 1. Lỗi trước destructive boundary

Nếu lỗi ở:

```text
verify_original
verify_clean
download_core
extract_core
```

thì remote site chưa bị wipe.

Sửa lỗi rồi có thể chạy lại.

---

## 2. Đã thấy `Entering destructive boundary...`

Từ đây site có thể đã bị xóa một phần hoặc toàn bộ.

Nếu command lỗi:

```text
REBUILD STOPPED: ...
```

**Không chạy lại ngay.**

Đọc report:

```text
reports\tinytensorvn.com\rebuild-execute.json
```

Xác định stage cuối trước khi quyết định bước tiếp theo.

---

## 3. Lỗi tại wipe

Remote có thể đang bị xóa dở.

Không backup lại trạng thái hiện tại để ghi đè snapshot gốc.

Giữ nguyên:

```text
backups\tinytensorvn.com\
```

---

## 4. Lỗi upload core / wp-config / .htaccess / uploads

Remote đang ở trạng thái rebuild dở dang.

Xem `rebuild-execute.json` để biết:

```text
wiped_files
wiped_dirs
core_uploaded
wp_config_uploaded
htaccess_uploaded
uploads_uploaded
```

Không rerun mù quáng.

---

## 5. Lỗi database import

Kiểm tra report:

```text
database_imported
database_statements
temp_bridge_removed
temp_sql_removed
```

Kiểm tra remote root có còn file tạm:

```text
wpclean-import-xxxxxxxx.php
wpclean-import-xxxxxxxx.dat
```

Nếu còn thì phải cleanup trước khi tiếp tục.

---

# PHẦN D — Ý NGHĨA 3 TRẠNG THÁI

## `PASS`

Bước hoàn tất bình thường.

## `PASS WITH EXCLUSIONS`

Bước hoàn tất nhưng có một số file riêng lẻ không thể đọc sau retry.

Tool đã:

```text
ghi rõ file
loại khỏi verification bắt buộc
loại khỏi restore
không giữ partial local
```

Có thể tiếp tục workflow.

## `BLOCKED` / `FAILED`

Lỗi ảnh hưởng integrity hoặc recovery artifact bắt buộc.

Ví dụ:

```text
DB thiếu/hỏng
wp-config.php thiếu
uploads stage thất bại hoàn toàn
manifest hash mismatch
clean staging verify fail
remote root sai
```

Không chạy destructive rebuild cho đến khi xử lý xong.

---

# CHECKLIST SIÊU NGẮN

Nếu mọi thứ bình thường, đây là toàn bộ chuỗi lệnh cần chạy:

```powershell
git pull
.\START.bat
.\wpclean.bat doctor

.\wpclean.bat ftp-test-config .\sites\ftp.json

.\wpclean.bat backup-config .\sites\ftp.json
.\wpclean.bat db-backup-config .\sites\ftp.json
.\wpclean.bat backup-status .\backups\tinytensorvn.com
.\wpclean.bat verify-backup .\backups\tinytensorvn.com

.\wpclean.bat scan-backup .\backups\tinytensorvn.com
.\wpclean.bat prepare-clean-config .\sites\ftp.json .\backups\tinytensorvn.com

.\wpclean.bat rebuild-preflight .\sites\ftp.json .\backups\tinytensorvn.com --fast
.\wpclean.bat rebuild-config .\sites\ftp.json .\backups\tinytensorvn.com

.\wpclean.bat rebuild-config .\sites\ftp.json .\backups\tinytensorvn.com --execute
```

Nếu một dòng lỗi: **dừng tại dòng đó → tìm command tương ứng ở Phần B hoặc Phần C → xử lý xong mới đi tiếp.**
