# WP Clean Rebuild

Interactive CLI for rebuilding a compromised WordPress installation from trusted sources while preserving and triaging the database and uploads.

## V1 goals

- Inventory a WordPress site.
- Back up database, uploads, themes, plugins, mu-plugins and config files.
- Scan SQL dumps offline using explainable risk rules.
- Scan uploads offline for executable PHP, double extensions and extension/content mismatches.
- Require explicit confirmation before destructive actions.
- Keep an audit trail and recovery evidence.
- Prefer SFTP; keep transport adapters replaceable.
- Reinstall WordPress core and third-party extensions from trusted sources instead of restoring executable code from the compromised site.

## Safety model

V1 defaults to **audit-only**. Destructive repair/rebuild operations stay disabled until backup verification and rollback primitives are implemented.

## Planned workflow

```text
connect
  -> inventory
  -> immutable backup
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

This tool is intended only for WordPress installations you own or are authorized to administer.
