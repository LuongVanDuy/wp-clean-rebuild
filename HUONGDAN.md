# HƯỚNG DẪN SỬ DỤNG WP CLEAN REBUILD

Tài liệu này là runbook chạy `wp-clean-rebuild` từ đầu đến cuối trên Windows PowerShell.

Mục tiêu:

- biết chính xác đang ở bước nào;
- biết bước nào đã PASS;
- biết khi lỗi thì xử lý ở đâu;
- tránh chạy lại destructive command một cách mù quáng;
- giữ nguyên backup gốc để rollback/forensic.

> **Quan trọng:** từ Bước 1 đến Bước 9 chủ yếu là read-only hoặc xử lý local. Từ `rebuild-config --execute` ở Bước 10 trở đi là destructive thật trên hosting.

---

## 0. Ví dụ cấu trúc sử dụng

Ví dụ site:

```text
tinytensorvn.com
```

Profile:

```text
sites\ftp.json
```

Backup:

```text
backups\tinytensorvn.com
```

Report:

```text
reports\tinytensorvn.com
```

Chạy lệnh tại thư mục project:

```powershell
D:
cd D:\DuyAnhWeb\wp-clean-rebuild\wp-clean-rebuild
```

---

# 1. Cập nhật tool và kiểm tra môi trường

```powershell
git pull
.\START.bat
.\wpclean.bat doctor
```

Kết quả mong đợi:

```text
Python: 3.13.x
Platform: Windows-...
CLI runtime looks usable.
```

### Nếu lỗi `uv` hoặc Python

Chạy lại:

```powershell
.\START.bat
```

Nếu thấy:

```text
Failed to hardlink files; falling back to full copy
```

thì đây chỉ là warning của `uv` trên Windows, không phải lỗi chức năng.

### PASS khi

```text
CLI runtime looks usable.
```

---

# 2. Kiểm tra profile website

Ví dụ `sites\ftp.json`:

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
  "workers": 6,
  "blockMb": 1
}
```

Các field quan trọng:

```text
host
username
password
protocol
remotePath
siteUrl
```

Không commit credential thật lên GitHub.

---

# 3. Test FTP/FTPS

```powershell
.\wpclean.bat ftp-test-config .\sites\ftp.json
```

Kết quả mong đợi:

```text
FTP connection OK.
Host: tinytensorvn.com:21
Remote cwd: /
WordPress root configured: /domains/tinytensorvn.com/public_html
```

### Nếu lỗi login

Kiểm tra lại:

```text
host
username
password
port
```

### Nếu remote path sai

Sửa:

```json
"remotePath": "/domains/tinytensorvn.com/public_html"
```

### Nếu dùng FTP thường

Warning:

```text
credentials/data are not transport-encrypted
```

là cảnh báo bảo mật, không phải lỗi thực thi.

### PASS khi

Có `FTP connection OK` và `remotePath` đúng.

---

# 4. Backup filesystem

```powershell
.\wpclean.bat backup-config .\sites\ftp.json
```

Tool sẽ backup các nhóm chính:

```text
wp-content/uploads
wp-content/themes
wp-content/plugins
wp-content/mu-plugins
wp-config.php
.htaccess
.user.ini
php.ini
robots.txt
```

Output tốt:

```text
Files discovered: ...
Downloaded: ...
Manifest: backups\tinytensorvn.com\manifest.json
Filesystem backup completed and SHA-256 verification passed.
Database is not exported by FTP. Run db-backup-config for the database stage.
```

### Nếu `php.ini` không tồn tại

Ví dụ:

```text
Skipped .../php.ini: 550 ... No such file or directory
```

Có thể tiếp tục nếu hosting thực sự không có file này.

### Nếu backup bị ngắt giữa chừng

Chạy lại:

```powershell
.\wpclean.bat backup-config .\sites\ftp.json
```

Resume mặc định bật.

Không cần xóa backup cũ.

### PASS khi

Có:

```text
Filesystem backup completed and SHA-256 verification passed.
```

---

# 5. Backup database

```powershell
.\wpclean.bat db-backup-config .\sites\ftp.json
```

Database được lưu tại:

```text
backups\tinytensorvn.com\database\original.sql
```

Output mong đợi:

```text
Database backup completed: ...\database\original.sql
Size: ...
SHA-256: ...
Full backup manifest regenerated and verification passed
```

### Nếu PHP bridge lỗi

Kiểm tra:

```text
siteUrl
wp-config.php
DB_NAME
DB_USER
DB_PASSWORD
DB_HOST
```

Website cũng phải truy cập được PHP bridge qua HTTP/HTTPS.

Sau khi sửa, chạy lại:

```powershell
.\wpclean.bat db-backup-config .\sites\ftp.json
```

### PASS khi

Có đủ:

```text
database\original.sql
manifest.json
```

và manifest verification PASS.

---

# 6. Kiểm tra backup

## Xem các thành phần backup

```powershell
.\wpclean.bat backup-status .\backups\tinytensorvn.com
```

Tool kiểm tra:

```text
database
uploads
themes
plugins
mu-plugins
config
manifest
```

## Verify SHA-256

```powershell
.\wpclean.bat verify-backup .\backups\tinytensorvn.com
```

Output tốt:

```text
Backup verification passed.
```

### Nếu báo missing file

Ví dụ:

```text
missing: uploads/2026/08/file.zip
```

Nghĩa là backup local đã bị chỉnh sửa sau khi manifest được tạo.

**Không tạo manifest mới chỉ để bỏ qua lỗi này.**

Hãy phục hồi đúng file bị thiếu hoặc chạy lại backup stage phù hợp.

### Nguyên tắc

Không tự tay edit/xóa/move file trong:

```text
backups\<host>\
```

Backup gốc là snapshot rollback/forensic.

---

# 7. Scan malware

```powershell
.\wpclean.bat scan-backup .\backups\tinytensorvn.com
```

Command sẽ:

1. verify manifest;
2. scan `database/original.sql`;
3. scan `uploads/`.

Ví dụ finding:

```text
Risk 100/100 — CRITICAL
QUARANTINE / DROP FROM CLEAN RESTORE
```

### Nếu database báo

```text
No findings above the current threshold.
```

Hiểu là scanner hiện tại chưa thấy pattern vượt threshold, không phải chứng minh database sạch tuyệt đối.

### Nếu uploads có CRITICAL

Không xóa file khỏi backup gốc.

Bước `prepare-clean-config` sẽ tạo `clean/uploads` và loại file nguy hiểm khỏi restore set.

### PASS khi

Scan chạy xong và findings đã được review.

---

# 8. Tạo clean staging

```powershell
.\wpclean.bat prepare-clean-config .\sites\ftp.json .\backups\tinytensorvn.com
```

Sau khi chạy:

```text
backups\tinytensorvn.com\
├── uploads\
├── database\
│   └── original.sql
└── clean\
    ├── uploads\
    ├── database\
    │   └── clean.sql
    ├── clean-report.json
    └── manifest.json
```

Tool hiện tại:

- không sửa original backup;
- loại ZIP khỏi clean uploads;
- loại executable PHP trong uploads;
- loại malware finding nguy hiểm;
- xóa user WordPress cũ khỏi clean SQL;
- tạo 1 admin mới;
- username mặc định: `admin`;
- password admin: cùng password FTP theo workflow hiện tại;
- giữ đúng table prefix WordPress.

Output tốt:

```text
Clean restore staging completed and SHA-256 verification passed.
```

### Nếu original backup verify fail

Quay lại Bước 6.

### Nếu không detect được table prefix

Kiểm tra `database/original.sql` có dump đầy đủ các bảng WordPress hay không.

### PASS khi

Có:

```text
clean\database\clean.sql
clean\uploads\
clean\manifest.json
```

và SHA-256 PASS.

---

# 9. Fast preflight

```powershell
.\wpclean.bat rebuild-preflight .\sites\ftp.json .\backups\tinytensorvn.com --fast
```

FAST mode sẽ:

```text
verify original backup
verify clean staging
verify configured remote WordPress root
skip recursive FTP scan
```

Output mong đợi:

```text
✓ Original backup verified
✓ Clean staging verified
✓ Configured remote WordPress root verified
Preflight mode: FAST
Remote recursive inventory: SKIPPED
PREFLIGHT PASS
```

Report:

```text
reports\tinytensorvn.com\rebuild-preflight.json
```

### Nếu original backup fail

Quay lại Bước 6.

### Nếu clean staging fail

Chạy lại Bước 8.

### Nếu remote root fail

Dừng ngay và kiểm tra:

```json
"remotePath": "/domains/tinytensorvn.com/public_html"
```

### PASS khi

Có:

```text
PREFLIGHT PASS
```

---

# 10. Xem rebuild plan — chưa xóa

```powershell
.\wpclean.bat rebuild-config .\sites\ftp.json .\backups\tinytensorvn.com
```

Không có `--execute` thì tool chỉ hiện plan.

Output:

```text
DRY ARM ONLY — nothing was changed remotely.
```

Kiểm tra kỹ:

```text
Site
Remote WordPress root
Original backup
Clean staging
Preflight report
```

### PASS khi

Có `DRY ARM ONLY` và mọi path đều chính xác.

---

# 11. Rebuild thật — destructive

## Không restore plugin/theme cũ

```powershell
.\wpclean.bat rebuild-config .\sites\ftp.json .\backups\tinytensorvn.com --execute
```

## Restore cả plugin/theme/mu-plugin từ backup cũ

```powershell
.\wpclean.bat rebuild-config .\sites\ftp.json .\backups\tinytensorvn.com --execute --restore-backup-code
```

> `--restore-backup-code` có thể đưa PHP bị compromise quay lại. Đây là override dành cho developer tự review code sau rebuild.

Execution flow:

```text
1. Verify original backup
2. Verify clean staging
3. Download WordPress core sạch
4. Validate/extract core local
5. Tạo wp-config.php mới + salts mới
6. Enter destructive boundary
7. Wipe public_html, giữ .well-known
8. Upload WordPress core sạch
9. Upload wp-config.php mới
10. Restore clean/uploads
11. Restore plugins/themes nếu có --restore-backup-code
12. Upload clean.sql tạm thời
13. Tạo authenticated PHP import bridge
14. Import database
15. Xóa bridge/data tạm
16. Ghi execution report
```

### Dòng quan trọng

```text
Recovery artifacts ready. Entering destructive boundary...
```

Từ dòng này trở đi remote site có thể đã bắt đầu bị xóa.

---

# 12. Nếu rebuild lỗi giữa chừng

Nếu thấy:

```text
REBUILD STOPPED: ...
```

**Không chạy lại ngay lập tức.**

Mở:

```text
reports\tinytensorvn.com\rebuild-execute.json
```

## Lỗi trước destructive boundary

Nếu lỗi ở các stage:

```text
verify_original
verify_clean
download_core
extract_core
```

thì remote site chưa bị wipe.

Sửa lỗi rồi có thể chạy lại.

## Lỗi tại `wipe`

Remote đã bắt đầu bị xóa.

Không backup lại site hiện tại để ghi đè snapshot gốc.

Giữ nguyên:

```text
backups\tinytensorvn.com
```

và kiểm tra execution report.

## Lỗi tại upload core/uploads/plugins/themes

Remote có thể đang ở trạng thái rebuild dở dang.

Không chạy lại mù quáng.

Xem stage cuối trong report trước.

## Lỗi database import

Kiểm tra các stage:

```text
db_import_upload
db_import_execute
db_import_cleanup
```

Kiểm tra remote root xem còn file tạm dạng:

```text
wpclean-import-xxxxxxxx.php
wpclean-import-xxxxxxxx.dat
```

Nếu còn, cần cleanup trước khi tiếp tục.

---

# 13. Khi rebuild thành công

Output cuối phải gần như:

```text
REBUILD COMPLETED
WordPress version: ...
WordPress package SHA-256: ...
Remote wiped: ...
Fresh core uploaded: ...
Clean uploads restored: ...
Fresh wp-config.php uploaded: True
Database imported: True
Temporary import cleanup: bridge_removed=True, data_removed=True
```

Report:

```text
reports\tinytensorvn.com\rebuild-execute.json
```

Nếu dùng `--restore-backup-code`, output sẽ có thêm số lượng plugin/theme/mu-plugin đã restore.

---

# 14. Kiểm tra website sau rebuild

Kiểm tra:

```text
https://<domain>/
https://<domain>/wp-admin/
```

Theo workflow hiện tại:

```text
Username: admin
Password: cùng FTP password
```

Checklist:

```text
Trang chủ
Trang con
wp-admin
Media Library
Ảnh/uploads
Theme
Plugin
Permalink
Form/AJAX
Cron
Browser console
PHP error log
```

Nếu đã restore plugin/theme cũ, review PHP ngay sau rebuild.

---

# 15. Cách biết tiến trình hiện tại đã đến đâu

| Stage | Dấu hiệu | Ý nghĩa |
|---|---|---|
| Runtime | `doctor` PASS | CLI chạy được |
| FTP | `ftp-test-config` PASS | Kết nối hosting được |
| Files backup | `backup-report.json` + `manifest.json` | Filesystem đã backup |
| DB backup | `database/original.sql` | Database đã backup |
| Backup verified | `verify-backup` PASS | Snapshot integrity OK |
| Scan | `scan-backup` xong | Malware triage đã chạy |
| Clean staging | `clean/manifest.json` | Restore set sạch đã tạo |
| Clean DB | `clean/database/clean.sql` | User reset + DB staging xong |
| Preflight | `rebuild-preflight.json` | Đủ điều kiện rebuild |
| Dry-run | `DRY ARM ONLY` | Execute command load đúng |
| Execution started | `rebuild-execute.json` | Rebuild đã bắt đầu |
| Destructive started | `Entering destructive boundary` | Remote có thể đã bị wipe |
| Complete | `REBUILD COMPLETED` | Engine chạy hết |

---

# 16. Chuỗi command chạy lại từ đầu

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
```

Dừng ở đây để review lần cuối.

Khi chắc chắn muốn chạy destructive:

```powershell
.\wpclean.bat rebuild-config .\sites\ftp.json .\backups\tinytensorvn.com --execute --restore-backup-code
```

Hoặc nếu không muốn restore executable code cũ:

```powershell
.\wpclean.bat rebuild-config .\sites\ftp.json .\backups\tinytensorvn.com --execute
```

---

# 17. Nguyên tắc xử lý lỗi

1. **Không sửa trực tiếp backup gốc.**
2. **Không regenerate manifest chỉ để bỏ qua missing/hash mismatch.**
3. **Trước destructive boundary:** sửa lỗi rồi có thể chạy lại.
4. **Sau destructive boundary:** đọc `rebuild-execute.json` trước khi retry.
5. **Không overwrite backup gốc bằng trạng thái site đang rebuild dở.**
6. **PHP bridge/database temp file phải được cleanup.**
7. **Nếu restore plugin/theme cũ, coi đó là code chưa trusted cho đến khi review.**
8. **Nếu chưa chắc site đang ở stage nào, kiểm tra report trước khi chạy bất kỳ command destructive nào.**
