# WP Clean Rebuild

Local Windows GUI and technical CLI for rebuilding a compromised WordPress installation from trusted sources while preserving and triaging the database and uploads.

## Current status

V0.2 implements the complete guarded recovery workflow:

- Offline SQL malware scanning with explainable risk scores.
- Offline uploads scanning, including PHP hidden behind media extensions.
- SHA-256 backup manifest generation and verification.
- High-throughput FTP/FTPS filesystem backup.
- Parallel transfer workers with persistent per-thread connections.
- Resume support via FTP REST.
- MLSD directory discovery with legacy fallback.
- Backup of uploads, themes, plugins, mu-plugins and forensic config files.
- Clean restore staging for the database and uploads.
- Guarded destructive rebuild with explicit domain confirmation.
- Safe DB-only resume after the destructive boundary.
- Trusted WordPress core and WordPress.org plugin reinstall.
- Flatsome/child-theme and MU-plugin safety gates.
- Local Vietnamese GUI with persistent project history and multi-project execution.
- Structured Vietnamese error codes, recovery guidance and live job health.

Destructive rebuild remains locked until backup, verification, clean staging and preflight requirements pass.

## Windows — easiest setup

You do **not** need to install Python manually.

1. Pull/download the latest repository.
2. Double-click `WP-Clean-Rebuild.exe` (or `GIAODIEN.bat`).
3. If `uv` is missing, the launcher shows the official download source and asks for confirmation.
4. Press `Y` to install `uv`.
5. Setup installs managed Python 3.13 and all project dependencies automatically.
6. The browser opens the local operator dashboard. Keep the launcher window open.

Technical CLI example:

```powershell
.\wpclean.bat doctor
.\wpclean.bat --help
```

The bootstrap downloads uv only from the official Astral installer at `https://astral.sh/uv/install.ps1`.

### Update an existing clone

```powershell
git pull
.\START.bat
```

## Manual development setup

For developers who already manage Python themselves:

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate

pip install -e .
pip install pytest
```

## Commands

With the Windows launcher:

```powershell
.\wpclean.bat doctor
.\wpclean.bat scan-sql database.sql
.\wpclean.bat scan-uploads .\wp-content\uploads
.\wpclean.bat manifest .\backup
.\wpclean.bat verify-backup .\backup
```

### Test FTP/FTPS

Password is prompted securely by default, or can be supplied through the environment variable `WPCLEAN_FTP_PASSWORD`.

```powershell
.\wpclean.bat ftp-test `
  --host ftp.example.com `
  --user account `
  --tls
```

### High-speed WordPress filesystem backup

```powershell
.\wpclean.bat backup-ftp `
  --host ftp.example.com `
  --user account `
  --remote-root /public_html `
  --out .\backups\example.com `
  --tls `
  --workers 6 `
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

## Rebuild workflow

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

## GUI status and diagnostics

The local dashboard shows stage progress, elapsed time, last engine signal, file counts, transfer speed and retry state. Failures use stable codes such as `FTP-AUTH-001` together with a Vietnamese explanation, recovery action and expandable technical details. Persistent history is stored under `reports/<host>/activity-log.jsonl` with configured secrets redacted.

## Safety model

Read-only and backup operations run first. Destructive rebuild refuses to continue unless backup verification, clean staging and preflight evidence pass. The operator must enter the exact project domain before crossing the destructive boundary.

This tool is intended only for WordPress installations you own or are authorized to administer.
