from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import re
import secrets
import time
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .backup import verify_manifest
from .rebuild_execute import (
    _database_import_bridge,
    _delete_remote_file,
    _read_remote_import_checkpoint,
    _upload_file,
    _upload_text,
)
from .site_config import SiteConnectionProfile
from .sql_import_compat import prepared_sql_for_import
from .transport import FTPTransport


ProgressCallback = Callable[[dict], None]
TEMP_IMPORT_NAME = re.compile(r"^wpclean-import-[0-9a-f]{16}\.(?:php|dat|state(?:\.tmp)?)$")


@dataclass(slots=True)
class ResumeDatabaseResult:
    statements: int
    bridge_removed: bool
    data_removed: bool
    stale_files_removed: list[str]


def _bridge_error_detail(body: bytes, *, status: int | None = None) -> str:
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return f"HTTP {status}: empty response body" if status else "empty response body"

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        compact = " ".join(text.split())
        if len(compact) > 1200:
            compact = compact[:1200] + "..."
        prefix = f"HTTP {status}: " if status else ""
        return prefix + compact

    parts: list[str] = []
    message = payload.get("message")
    if message:
        parts.append(str(message))
    if payload.get("statement") is not None:
        parts.append(f"statement={payload.get('statement')}")
    if payload.get("offset") is not None:
        parts.append(f"offset={payload.get('offset')}")
    if payload.get("errno") is not None:
        parts.append(f"mysql_errno={payload.get('errno')}")
    if payload.get("error"):
        parts.append(f"mysql_error={payload.get('error')}")
    if payload.get("line") is not None:
        parts.append(f"php_line={payload.get('line')}")
    if not parts:
        parts.append(json.dumps(payload, ensure_ascii=False))
    prefix = f"HTTP {status}: " if status else ""
    return prefix + "; ".join(parts)


def cleanup_stale_import_files(transport: FTPTransport, remote_root: str) -> list[str]:
    """Delete only temp import artifacts created by this tool in the WordPress root."""
    client = transport._new_client()
    removed: list[str] = []
    try:
        for name, facts in list(transport._mlsd(client, remote_root)):
            if not TEMP_IMPORT_NAME.fullmatch(name):
                continue
            kind = (facts.get("type") or "").lower()
            if kind in {"dir", "cdir", "pdir"}:
                continue
            remote_path = str(PurePosixPath(remote_root) / name)
            try:
                client.delete(remote_path)
                removed.append(remote_path)
            except Exception:
                # A failed cleanup is surfaced by the following import if the
                # hosting environment still blocks these temporary files.
                pass
        return removed
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def import_database_with_diagnostics(
    profile: SiteConnectionProfile,
    transport: FTPTransport,
    sql_path: Path,
    *,
    progress: ProgressCallback | None = None,
) -> tuple[int, bool, bool]:
    """Import clean SQL and preserve the bridge's error body on HTTP failures."""
    token = secrets.token_hex(32)
    nonce = secrets.token_hex(8)
    data_name = f"wpclean-import-{nonce}.dat"
    bridge_name = f"wpclean-import-{nonce}.php"
    state_name = f"wpclean-import-{nonce}.state"
    remote_data = str(PurePosixPath(profile.remote_path) / data_name)
    remote_bridge = str(PurePosixPath(profile.remote_path) / bridge_name)
    remote_state = str(PurePosixPath(profile.remote_path) / state_name)
    bridge_url = f"{profile.web_base_url}/{bridge_name}"
    bridge_removed = False
    data_removed = False
    state_removed = False
    statements = 0
    execution_error: Exception | None = None

    with prepared_sql_for_import(sql_path) as (import_sql, bit_rewrites):
        if progress:
            progress(
                {
                    "phase": "db_import_prepare",
                    "bit_values_normalized": bit_rewrites,
                    "current": sql_path.name,
                }
            )
            progress({"phase": "db_import_upload", "current": data_name})
        _upload_file(transport, remote_data, import_sql)
    _upload_text(
        transport,
        remote_state,
        json.dumps({"offset": 0, "statements": 0, "done": False}),
    )
    _upload_text(
        transport,
        remote_bridge,
        _database_import_bridge(token, data_name, state_name),
    )

    try:
        if progress:
            progress({"phase": "db_import_execute", "url": bridge_url})
        batch = 0
        checkpoint_wait_retries = 0
        max_checkpoint_wait_retries = 10
        last_checkpoint = (0, 0)
        while True:
            request = Request(
                f"{bridge_url}?batch={batch}&retry={checkpoint_wait_retries}",
                headers={
                    "User-Agent": "WP-Clean-Rebuild/0.6",
                    "X-WPClean-Token": token,
                    "Accept": "application/json",
                    "Cache-Control": "no-cache",
                },
                method="GET",
            )
            try:
                with urlopen(request, timeout=180) as response:
                    raw = response.read()
            except HTTPError as exc:
                body = exc.read()
                if 500 <= exc.code < 600 and not body.strip():
                    time.sleep(0.75)
                    state = _read_remote_import_checkpoint(transport, remote_state)
                    if state is not None:
                        checkpoint = (
                            int(state.get("offset", 0)),
                            int(state.get("statements", 0)),
                        )
                        if checkpoint > last_checkpoint or bool(state.get("done")):
                            statements = checkpoint[1]
                            total_bytes = int(state.get("total_bytes", 0))
                            if progress:
                                progress(
                                    {
                                        "phase": "db_import_execute",
                                        "items_completed": statements,
                                        "bytes_completed": checkpoint[0],
                                        "bytes_total": total_bytes,
                                        "current": f"Đã import {statements} câu SQL",
                                    }
                                )
                            last_checkpoint = checkpoint
                            checkpoint_wait_retries = 0
                            if state.get("done"):
                                break
                            batch += 1
                            continue

                    checkpoint_wait_retries += 1
                    if checkpoint_wait_retries <= max_checkpoint_wait_retries:
                        if progress:
                            progress(
                                {
                                    "phase": "db_import_retry",
                                    "attempt": checkpoint_wait_retries,
                                    "max_attempts": max_checkpoint_wait_retries,
                                    "current": "Hosting ngắt request; đang kiểm tra checkpoint FTP",
                                }
                            )
                        time.sleep(float(min(checkpoint_wait_retries, 3)))
                        continue
                raise RuntimeError(
                    "Database import bridge failed: "
                    + _bridge_error_detail(body, status=exc.code)
                ) from exc

            try:
                payload = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "Database import bridge returned invalid JSON: "
                    + _bridge_error_detail(raw)
                ) from exc
            if not payload.get("ok"):
                raise RuntimeError(
                    "Database import bridge failed: "
                    + _bridge_error_detail(raw)
                )

            statements = int(payload.get("statements", 0))
            offset = int(payload.get("offset", 0))
            total_bytes = int(payload.get("total_bytes", 0))
            checkpoint = (offset, statements)
            if progress:
                progress(
                    {
                        "phase": "db_import_execute",
                        "items_completed": statements,
                        "bytes_completed": offset,
                        "bytes_total": total_bytes,
                        "current": f"Đã import {statements} câu SQL",
                    }
                )
            if payload.get("done"):
                break
            if checkpoint <= last_checkpoint:
                raise RuntimeError("Database import bridge did not advance its checkpoint.")
            last_checkpoint = checkpoint
            checkpoint_wait_retries = 0
            batch += 1
            if batch > 10000:
                raise RuntimeError("Database import exceeded the safe batch request limit.")
    except Exception as exc:
        execution_error = exc
    finally:
        bridge_removed = _delete_remote_file(transport, remote_bridge)
        data_removed = _delete_remote_file(transport, remote_data)
        state_removed = _delete_remote_file(transport, remote_state)
        if progress:
            progress(
                {
                    "phase": "db_import_cleanup",
                    "bridge_removed": bridge_removed,
                    "data_removed": data_removed,
                    "state_removed": state_removed,
                }
            )

    cleanup_problem = not bridge_removed or not data_removed or not state_removed
    if execution_error is not None:
        if cleanup_problem:
            raise RuntimeError(
                f"Database import failed ({execution_error}) and temporary import cleanup was incomplete: "
                f"bridge_removed={bridge_removed}, data_removed={data_removed}, state_removed={state_removed}"
            ) from execution_error
        raise execution_error
    if cleanup_problem:
        raise RuntimeError(
            "Database import completed but temporary import cleanup was incomplete: "
            f"bridge_removed={bridge_removed}, data_removed={data_removed}, state_removed={state_removed}"
        )
    return statements, bridge_removed, data_removed


def resume_database_import(
    *,
    profile: SiteConnectionProfile,
    transport: FTPTransport,
    backup_root: Path,
    execution_report_path: Path,
    progress: ProgressCallback | None = None,
) -> ResumeDatabaseResult:
    """Resume only the failed clean database import after a destructive rebuild."""
    clean_root = backup_root / "clean"
    clean_manifest = clean_root / "manifest.json"
    clean_sql = clean_root / "database" / "clean.sql"

    clean_ok, clean_problems = verify_manifest(clean_root, clean_manifest)
    if not clean_ok:
        raise ValueError("Clean staging verification failed: " + "; ".join(clean_problems))
    if not clean_sql.is_file() or clean_sql.stat().st_size == 0:
        raise ValueError(f"Clean SQL is missing or empty: {clean_sql}")
    if not execution_report_path.is_file():
        raise ValueError(f"Execution report is missing: {execution_report_path}")

    report = json.loads(execution_report_path.read_text(encoding="utf-8"))
    if str(report.get("host")) != profile.host:
        raise ValueError("Execution report host does not match the active site profile.")
    if str(report.get("remote_root")) != profile.remote_path:
        raise ValueError("Execution report remote root does not match the active site profile.")
    if report.get("database_imported"):
        raise ValueError("Execution report already marks the database import as completed.")

    required_remote_state = {
        "wiped_files": int(report.get("wiped_files") or 0) > 0,
        "core_uploaded": int(report.get("core_uploaded") or 0) > 0,
        "wp_config_uploaded": bool(report.get("wp_config_uploaded")),
        "htaccess_uploaded": bool(report.get("htaccess_uploaded")),
    }
    missing = [name for name, ok in required_remote_state.items() if not ok]
    if missing:
        raise ValueError(
            "Execution report does not prove the rebuild reached database-import stage; missing state: "
            + ", ".join(missing)
        )

    stale_removed = cleanup_stale_import_files(transport, profile.remote_path)
    if progress:
        progress({"phase": "db_resume_ready", "stale_removed": len(stale_removed)})

    try:
        statements, bridge_removed, data_removed = import_database_with_diagnostics(
            profile,
            transport,
            clean_sql,
            progress=progress,
        )
    except Exception as exc:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        warnings = list(report.get("warnings") or [])
        warnings.append(f"Database resume stopped: {exc}")
        report["warnings"] = warnings
        report["resume_database_import"] = {
            "attempted_at": report["finished_at"],
            "stale_files_removed": stale_removed,
            "completed": False,
        }
        execution_report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        raise

    report["database_imported"] = True
    report["database_statements"] = statements
    report["temp_bridge_removed"] = bridge_removed
    report["temp_sql_removed"] = data_removed
    report["completed"] = True
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    warnings = [
        str(item)
        for item in (report.get("warnings") or [])
        if not str(item).startswith("Execution stopped:")
        and not str(item).startswith("Database resume stopped:")
    ]
    warnings.append("Database import resumed successfully without repeating the remote wipe.")
    report["warnings"] = warnings
    report["resume_database_import"] = {
        "attempted_at": report["finished_at"],
        "stale_files_removed": stale_removed,
        "completed": True,
        "statements": statements,
    }
    execution_report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return ResumeDatabaseResult(
        statements=statements,
        bridge_removed=bridge_removed,
        data_removed=data_removed,
        stale_files_removed=stale_removed,
    )
