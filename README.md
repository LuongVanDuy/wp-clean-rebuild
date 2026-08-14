# WP Clean Rebuild

Interactive CLI for rebuilding a compromised WordPress installation from trusted sources while preserving and triaging the database and uploads.

## Current status

V0.2 implements the safe backup/audit foundation:

- Offline SQL malware scanning with explainable risk scores.
- Offline uploads scanning, including PHP hidden behind media extensions.
- SHA-256 backup manifest generation and verification.
- High-throughput FTP/FTPS filesystem backup.
- Parallel transfer workers with persistent per-thread connections.
- Resume support via FTP REST.
- MLSD directory discovery with legacy fallback.
- Backup of uploads, themes, plugins, mu-plugins and forensic config files.
- Destructive rebuild remains locked until verified backup/rollback requirements are complete.

## Install

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate

pip install -e .
```

## Commands

```bash
wpclean doctor
wpclean scan-sql database.sql
wpclean scan-uploads ./wp-content/uploads
wpclean manifest ./backup
wpclean verify-backup ./backup
```

### Test FTP/FTPS

Password is prompted securely by default, or can be supplied through the environment variable `WPCLEAN_FTP_PASSWORD`.

```bash
wpclean ftp-test \
  --host ftp.example.com \
  --user account \
  --tls
```

### High-speed WordPress filesystem backup

```bash
wpclean backup-ftp \
  --host ftp.example.com \
  --user account \
  --remote-root /public_html \
  --out ./backups/example.com \
  --tls \
  --workers 6 \
  --block-mb 1
```

For shared hosting, start with 4-6 workers. Increase toward 8 only when the FTP server allows enough simultaneous sessions and latency is the bottleneck. The CLI caps the worker count at 16 to avoid accidental connection storms.

`--resume` is enabled by default. Existing complete files are skipped; partial files continue from their current local byte offset when the server supports FTP REST.

Use `--plain-ftp` only when FTPS is unavailable. Plain FTP transmits credentials without transport encryption.

## What the FTP backup captures

```text
backup/
├── uploads/
├── themes/
├── plugins/
├── mu-plugins/
├── config/
│   ├── wp-config.php
│   ├── .htaccess
│   ├── .user.ini
│   ├── php.ini
│   └── robots.txt
├── backup-report.json
└── manifest.json
```

Theme/plugin backups are evidence/reference copies. They are not intended to be restored blindly after compromise.

## Database architecture

FTP is only a filesystem protocol, so database export is intentionally separated from FTP. The database layer will support independent strategies such as:

1. SSH/WP-CLI or `mysqldump` when SSH is available.
2. Direct MySQL connection when the hosting provider permits remote database access.
3. Hosting-control-panel/API adapters where available.

The cleaned database will be imported only after offline triage.

## Planned rebuild workflow

```text
connect
  -> inventory
  -> verified filesystem + database backup
  -> offline database scan
  -> offline uploads scan
  -> interactive triage
  -> wipe remote executable code
  -> deploy trusted WordPress core
  -> generate clean wp-config.php
  -> import cleaned database
  -> restore cleaned uploads
  -> reinstall trusted plugins/themes
  -> rotate salts
  -> final verification
  -> credential rotation checklist
```

## Safety model

This project currently defaults to non-destructive operations. A future rebuild command must refuse to continue unless backup verification and rollback evidence pass first.

This tool is intended only for WordPress installations you own or are authorized to administer.
