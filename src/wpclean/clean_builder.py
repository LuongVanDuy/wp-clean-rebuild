from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import bcrypt

from .backup import verify_manifest, write_manifest
from .scanners.uploads import EXECUTABLE_SUFFIXES, scan_uploads


CREATE_TABLE_RE = re.compile(
    r"CREATE TABLE(?: IF NOT EXISTS)?\s+`?([A-Za-z0-9_]+)`?\s*\(", re.I
)
INSERT_START_RE = re.compile(
    r"^(?:INSERT|REPLACE)\s+INTO\s+`?([A-Za-z0-9_]+)`?\s+VALUES\b", re.I
)


@dataclass(slots=True)
class CleanBuildReport:
    backup_root: str
    clean_root: str
    uploads_source: str
    uploads_clean: str
    uploads_copied: int = 0
    uploads_dropped: int = 0
    dropped_files: list[dict[str, str]] = field(default_factory=list)
    database_source: str = ""
    database_clean: str = ""
    table_prefix: str = ""
    admin_username: str = "admin"
    admin_email: str = ""
    password_source: str = "ftp-profile"
    warnings: list[str] = field(default_factory=list)
    clean_manifest: str = ""
    clean_verified: bool = False


def _mysql_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\x00", "\\0")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\x1a", "\\Z")
        .replace("'", "\\'")
    )


def wordpress_password_hash(password: str, *, rounds: int = 12) -> str:
    """Create a WordPress 6.8+ compatible bcrypt password hash.

    WordPress pre-hashes the trimmed password with HMAC-SHA384 using the
    domain-separation key ``wp-sha384``, base64-encodes it, bcrypts that value,
    then prefixes the bcrypt hash with ``$wp``.
    """
    trimmed = password.strip().encode("utf-8")
    digest = hmac.new(b"wp-sha384", trimmed, hashlib.sha384).digest()
    prehash = base64.b64encode(digest)
    encoded = bcrypt.hashpw(prehash, bcrypt.gensalt(rounds=rounds)).decode("ascii")
    return "$wp" + encoded


def _tables_in_dump(sql_path: Path) -> set[str]:
    tables: set[str] = set()
    with sql_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            match = CREATE_TABLE_RE.search(line)
            if match:
                tables.add(match.group(1))
    return tables


def detect_wordpress_prefix(sql_path: Path) -> tuple[str, set[str]]:
    tables = _tables_in_dump(sql_path)
    candidates: list[tuple[int, str]] = []
    for table in tables:
        if not table.endswith("users"):
            continue
        prefix = table[: -len("users")]
        score = 0
        for required in ("users", "usermeta", "options", "posts"):
            if f"{prefix}{required}" in tables:
                score += 1
        if score >= 3:
            candidates.append((score, prefix))

    if not candidates:
        raise ValueError("Could not detect the WordPress table prefix from the SQL dump.")

    candidates.sort(reverse=True)
    best_score = candidates[0][0]
    best = sorted(prefix for score, prefix in candidates if score == best_score)
    if len(best) != 1:
        raise ValueError(f"Ambiguous WordPress table prefix candidates: {', '.join(best)}")

    prefix = best[0]
    if f"{prefix}blogs" in tables or f"{prefix}sitemeta" in tables:
        raise ValueError(
            "WordPress multisite detected. Automatic single-admin reset is intentionally blocked "
            "until multisite super-admin semantics are implemented."
        )
    return prefix, tables


def _verify_standard_user_schema(sql_path: Path, prefix: str) -> None:
    text = sql_path.read_text(encoding="utf-8", errors="replace")
    users_marker = re.search(
        rf"CREATE TABLE(?: IF NOT EXISTS)?\s+`?{re.escape(prefix)}users`?\s*\((.*?)\)\s*ENGINE=",
        text,
        re.I | re.S,
    )
    meta_marker = re.search(
        rf"CREATE TABLE(?: IF NOT EXISTS)?\s+`?{re.escape(prefix)}usermeta`?\s*\((.*?)\)\s*ENGINE=",
        text,
        re.I | re.S,
    )
    if not users_marker or not meta_marker:
        raise ValueError("Could not verify wp_users/wp_usermeta table schemas.")

    users_sql = users_marker.group(1).lower()
    required_users = (
        "`id`",
        "`user_login`",
        "`user_pass`",
        "`user_nicename`",
        "`user_email`",
        "`user_url`",
        "`user_registered`",
        "`user_activation_key`",
        "`user_status`",
        "`display_name`",
    )
    if any(column not in users_sql for column in required_users):
        raise ValueError("wp_users schema is non-standard; refusing to inject an assumed row layout.")

    meta_sql = meta_marker.group(1).lower()
    required_meta = ("`umeta_id`", "`user_id`", "`meta_key`", "`meta_value`")
    if any(column not in meta_sql for column in required_meta):
        raise ValueError("wp_usermeta schema is non-standard; refusing to inject an assumed row layout.")


def _copy_upload_file_resilient(path: Path, target: Path, relative: Path) -> None:
    """Copy one clean upload, tolerating a transient Windows path race once.

    Antivirus software can inspect/quarantine files in the backup directory while
    clean staging is being built. If the source still exists, recreate the target
    parent and retry once. If the source itself disappeared, stop with an operator-
    friendly integrity error instead of silently producing an incomplete restore.
    """
    for attempt in range(2):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            return
        except FileNotFoundError as exc:
            if not path.is_file():
                rel = relative.as_posix()
                raise RuntimeError(
                    f"File backup đã biến mất khi tạo dữ liệu sạch: uploads/{rel}. "
                    "Có thể antivirus đã cách ly/xóa file trong thư mục backups. "
                    "Backup gốc không còn nguyên vẹn; hãy khôi phục file từ quarantine "
                    "hoặc chạy backup lại rồi bấm Thử lại."
                ) from exc
            if attempt == 0:
                continue
            rel = relative.as_posix()
            raise RuntimeError(
                f"Windows không tạo được đường dẫn clean staging cho uploads/{rel}. "
                "File nguồn vẫn tồn tại; hãy kiểm tra quyền ghi local, antivirus hoặc giới hạn đường dẫn rồi thử lại."
            ) from exc


def _copy_clean_uploads(source: Path, destination: Path) -> tuple[int, list[dict[str, str]]]:
    findings = scan_uploads(source)
    drop_reasons: dict[Path, str] = {}
    for finding in findings:
        finding_path = Path(finding.location).resolve()
        if finding.score >= 60 or "DROP FROM CLEAN RESTORE" in finding.recommended_action:
            reasons = "; ".join(signal.name for signal in finding.signals) or "scanner policy"
            drop_reasons[finding_path] = reasons

    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    copied = 0
    dropped: list[dict[str, str]] = []
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        reason: str | None = None
        suffix = path.suffix.lower()

        if suffix == ".zip":
            reason = "archive policy: ZIP is not restored"
        elif suffix in EXECUTABLE_SUFFIXES:
            reason = "executable file under uploads"
        elif path.resolve() in drop_reasons:
            reason = drop_reasons[path.resolve()]

        if reason:
            dropped.append({"path": relative.as_posix(), "reason": reason})
            continue

        target = destination / relative
        _copy_upload_file_resilient(path, target, relative)
        copied += 1

    return copied, dropped


def _write_clean_database(
    source: Path,
    destination: Path,
    *,
    prefix: str,
    tables: set[str],
    admin_username: str,
    admin_email: str,
    admin_password: str,
) -> None:
    _verify_standard_user_schema(source, prefix)
    destination.parent.mkdir(parents=True, exist_ok=True)

    users_table = f"{prefix}users"
    usermeta_table = f"{prefix}usermeta"
    skip_statement = False

    with source.open("r", encoding="utf-8", errors="replace") as src, destination.open(
        "w", encoding="utf-8", newline="\n"
    ) as out:
        out.write("-- WP Clean Rebuild sanitized database\n")
        out.write("-- Original users/usermeta data removed; one administrator injected.\n")

        for line in src:
            if not skip_statement:
                match = INSERT_START_RE.match(line)
                if match and match.group(1) in {users_table, usermeta_table}:
                    skip_statement = not line.rstrip().endswith(";")
                    continue
                out.write(line)
            else:
                if line.rstrip().endswith(";"):
                    skip_statement = False
                continue

        password_hash = wordpress_password_hash(admin_password)
        registered = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        username = _mysql_escape(admin_username)
        email = _mysql_escape(admin_email)
        nicename = _mysql_escape(re.sub(r"[^a-z0-9_-]+", "-", admin_username.lower()).strip("-") or "admin")
        display_name = _mysql_escape("Administrator")
        password_sql = _mysql_escape(password_hash)
        capabilities = _mysql_escape('a:1:{s:13:"administrator";b:1;}')

        out.write("\n-- WP Clean Rebuild: reset all WordPress users.\n")
        out.write(f"DELETE FROM `{usermeta_table}`;\n")
        out.write(f"DELETE FROM `{users_table}`;\n")
        out.write(
            f"INSERT INTO `{users_table}` VALUES "
            f"('1','{username}','{password_sql}','{nicename}','{email}','','{registered}','','0','{display_name}');\n"
        )
        out.write(
            f"INSERT INTO `{usermeta_table}` VALUES "
            f"(NULL,'1','{_mysql_escape(prefix + 'capabilities')}','{capabilities}'),"
            f"(NULL,'1','{_mysql_escape(prefix + 'user_level')}','10');\n"
        )

        if f"{prefix}posts" in tables:
            out.write(f"UPDATE `{prefix}posts` SET `post_author`=1 WHERE `post_author`<>0;\n")
        if f"{prefix}comments" in tables:
            out.write(f"UPDATE `{prefix}comments` SET `user_id`=0 WHERE `user_id`<>0;\n")
        if f"{prefix}links" in tables:
            out.write(f"UPDATE `{prefix}links` SET `link_owner`=1 WHERE `link_owner`<>0;\n")


def build_clean_restore(
    backup_root: Path,
    *,
    ftp_password: str,
    host: str,
    admin_username: str = "admin",
    admin_email: str | None = None,
) -> CleanBuildReport:
    manifest = backup_root / "manifest.json"
    if not manifest.is_file():
        raise ValueError("Original backup manifest.json is missing; clean staging is blocked.")
    ok, problems = verify_manifest(backup_root, manifest)
    if not ok:
        raise ValueError("Original backup verification failed: " + "; ".join(problems))

    if not ftp_password:
        raise ValueError("FTP password is empty; cannot create the requested administrator password.")

    uploads_source = backup_root / "uploads"
    db_source = backup_root / "database" / "original.sql"
    if not uploads_source.is_dir():
        raise ValueError("Original uploads backup is missing.")
    if not db_source.is_file():
        raise ValueError("Original database/original.sql is missing.")

    clean_root = backup_root / "clean"
    uploads_clean = clean_root / "uploads"
    database_clean = clean_root / "database" / "clean.sql"
    report_path = clean_root / "clean-report.json"

    prefix, tables = detect_wordpress_prefix(db_source)
    resolved_email = admin_email or f"admin@{host}"

    copied, dropped = _copy_clean_uploads(uploads_source, uploads_clean)
    _write_clean_database(
        db_source,
        database_clean,
        prefix=prefix,
        tables=tables,
        admin_username=admin_username,
        admin_email=resolved_email,
        admin_password=ftp_password,
    )

    report = CleanBuildReport(
        backup_root=str(backup_root),
        clean_root=str(clean_root),
        uploads_source=str(uploads_source),
        uploads_clean=str(uploads_clean),
        uploads_copied=copied,
        uploads_dropped=len(dropped),
        dropped_files=dropped,
        database_source=str(db_source),
        database_clean=str(database_clean),
        table_prefix=prefix,
        admin_username=admin_username,
        admin_email=resolved_email,
        warnings=[
            "Administrator password intentionally reuses the FTP credential as requested; rotate both after recovery.",
            "The original backup is immutable evidence and was not modified by this clean staging operation.",
        ],
    )

    clean_root.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")
    clean_manifest = write_manifest(clean_root)
    clean_ok, clean_problems = verify_manifest(clean_root, clean_manifest)
    if not clean_ok:
        raise RuntimeError("Clean staging manifest verification failed: " + "; ".join(clean_problems))

    report.clean_manifest = str(clean_manifest)
    report.clean_verified = True
    report_path.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")
    # Refresh the clean manifest because clean-report.json changed after verification metadata was added.
    clean_manifest = write_manifest(clean_root)
    clean_ok, clean_problems = verify_manifest(clean_root, clean_manifest)
    if not clean_ok:
        raise RuntimeError("Clean staging final verification failed: " + "; ".join(clean_problems))
    report.clean_manifest = str(clean_manifest)
    report.clean_verified = True
    return report
