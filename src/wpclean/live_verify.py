from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from tempfile import TemporaryDirectory
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

from .site_config import SiteConnectionProfile
from .transport import FTPTransport, RemoteFile


ProgressCallback = Callable[[dict], None]
CORE_CHECKSUM_API = "https://api.wordpress.org/core/checksums/1.0/"
SCAN_SUFFIXES = {
    ".php", ".phtml", ".phar", ".php3", ".php4", ".php5", ".php7", ".php8",
    ".js", ".htaccess", ".txt", ".json",
}
EXECUTABLE_SUFFIXES = {
    ".php", ".phtml", ".phar", ".php3", ".php4", ".php5", ".php7", ".php8",
    ".cgi", ".pl", ".py", ".sh", ".bash",
}
KNOWN_MALWARE_MARKERS = (
    b"vivid-toolkit-tap",
    b"plugin name: bold recorder bit",
    b"sc_th_begin",
    b"oi05awbus3",
)
TEMP_NAME_RE = re.compile(r"^wpclean-(?:db-|import-).+\.(?:php|dat|sql)$", re.I)
ROOT_ALLOWED_DYNAMIC = {
    "wp-config.php",
    ".htaccess",
    ".user.ini",
    "php.ini",
    "robots.txt",
}


@dataclass(slots=True)
class LiveVerifyIssue:
    category: str
    path: str
    detail: str
    severity: str = "BLOCK"


@dataclass(slots=True)
class HttpCheck:
    url: str
    ok: bool
    status: int | None = None
    final_url: str = ""
    detail: str = ""


@dataclass(slots=True)
class LiveVerifyReport:
    host: str
    remote_root: str
    site_url: str
    wordpress_version: str = ""
    remote_files: int = 0
    core_expected: int = 0
    core_verified: int = 0
    core_missing: int = 0
    core_mismatched: int = 0
    core_unreadable: int = 0
    scanned_code_files: int = 0
    suspicious_markers: int = 0
    uploads_executables: int = 0
    temp_artifacts: int = 0
    unexpected_root_php: int = 0
    http_home: HttpCheck | None = None
    http_admin: HttpCheck | None = None
    issues: list[LiveVerifyIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = "BLOCKED"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _read_execution_report(report_path: Path) -> dict:
    if not report_path.is_file():
        return {}
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _fetch_core_checksums(version: str, *, timeout: int = 30) -> dict[str, str]:
    query = urlencode({"version": version, "locale": "en_US"})
    request = Request(
        f"{CORE_CHECKSUM_API}?{query}",
        headers={"User-Agent": "WP-Clean-Rebuild/0.5", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"WordPress checksum API unavailable: {type(exc).__name__}: {exc}") from exc

    checksums = payload.get("checksums") if isinstance(payload, dict) else None
    if not isinstance(checksums, dict) or not checksums:
        raise RuntimeError(f"No official WordPress checksums returned for version {version}.")
    return {str(path): str(digest).lower() for path, digest in checksums.items()}


def _is_core_path(rel: str) -> bool:
    normalized = rel.replace("\\", "/").lstrip("/")
    if normalized.startswith("wp-content/"):
        return False
    if normalized in ROOT_ALLOWED_DYNAMIC:
        return False
    if normalized.startswith("wp-admin/") or normalized.startswith("wp-includes/"):
        return True
    return "/" not in normalized and normalized.endswith(".php")


def _remote_map(files: list[RemoteFile], remote_root: str) -> dict[str, RemoteFile]:
    root = PurePosixPath(remote_root)
    result: dict[str, RemoteFile] = {}
    for item in files:
        try:
            rel = str(PurePosixPath(item.path).relative_to(root)).replace("\\", "/")
        except ValueError:
            continue
        result[rel] = item
    return result


def _hash_remote_files(
    transport: FTPTransport,
    remote_root: str,
    targets: list[RemoteFile],
    *,
    algorithm: str,
    progress: ProgressCallback | None = None,
) -> tuple[dict[str, str], list[str]]:
    root = PurePosixPath(remote_root)
    hashes: dict[str, str] = {}
    unreadable: list[str] = []
    completed = 0

    with TemporaryDirectory(prefix="wpclean-live-core-") as temp_dir:
        local_root = Path(temp_dir)

        def worker(item: RemoteFile) -> tuple[str, str]:
            transport._download_one(item, remote_root, local_root, False)
            rel = str(PurePosixPath(item.path).relative_to(root)).replace("\\", "/")
            local = local_root.joinpath(*PurePosixPath(rel).parts)
            digest = hashlib.new(algorithm)
            with local.open("rb") as fh:
                while chunk := fh.read(1024 * 1024):
                    digest.update(chunk)
            local.unlink(missing_ok=True)
            return rel, digest.hexdigest().lower()

        with ThreadPoolExecutor(max_workers=max(1, transport.config.workers), thread_name_prefix="verify-core") as pool:
            futures = {pool.submit(worker, item): item for item in targets}
            for future in as_completed(futures):
                item = futures[future]
                completed += 1
                try:
                    rel, digest = future.result()
                    hashes[rel] = digest
                except Exception as exc:
                    try:
                        rel = str(PurePosixPath(item.path).relative_to(root)).replace("\\", "/")
                    except ValueError:
                        rel = item.path
                    unreadable.append(f"{rel}: {type(exc).__name__}: {exc}")
                if progress:
                    progress({"phase": "core_hash", "completed": completed, "total": len(targets)})
    return hashes, unreadable


def _download_scan_file(transport: FTPTransport, remote_root: str, item: RemoteFile, temp_root: Path) -> tuple[str, bytes]:
    transport._download_one(item, remote_root, temp_root, False)
    rel = str(PurePosixPath(item.path).relative_to(PurePosixPath(remote_root))).replace("\\", "/")
    local = temp_root.joinpath(*PurePosixPath(rel).parts)
    data = local.read_bytes()
    local.unlink(missing_ok=True)
    return rel, data


def _http_check(url: str, *, admin: bool = False, timeout: int = 25) -> HttpCheck:
    request = Request(
        url,
        headers={"User-Agent": "WP-Clean-Rebuild/0.5", "Accept": "text/html,*/*;q=0.8"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            final_url = response.geturl()
            body = response.read(512 * 1024).decode("utf-8", errors="replace").lower()
    except HTTPError as exc:
        return HttpCheck(url=url, ok=False, status=exc.code, final_url=exc.geturl(), detail=f"HTTP {exc.code}")
    except (URLError, TimeoutError) as exc:
        return HttpCheck(url=url, ok=False, detail=f"{type(exc).__name__}: {exc}")

    ok = 200 <= status < 400
    detail = ""
    if admin:
        admin_signal = (
            "wp-login.php" in final_url.lower()
            or "/wp-admin" in final_url.lower()
            or "user_login" in body
            or "loginform" in body
            or "dashboard" in body
        )
        if not admin_signal:
            ok = False
            detail = "Response did not look like WordPress admin/login."
    return HttpCheck(url=url, ok=ok, status=status, final_url=final_url, detail=detail)


def verify_live_site(
    *,
    profile: SiteConnectionProfile,
    transport: FTPTransport,
    report_path: Path,
    progress: ProgressCallback | None = None,
) -> LiveVerifyReport:
    report = LiveVerifyReport(
        host=profile.host,
        remote_root=profile.remote_path,
        site_url=profile.web_base_url,
    )
    execution = _read_execution_report(report_path)
    report.wordpress_version = str(execution.get("wordpress_version") or "").strip()
    if not report.wordpress_version:
        report.warnings.append("Execution report does not contain wordpress_version; official core checksum verification was skipped.")

    if progress:
        progress({"phase": "inventory"})
    files = transport.list_files_recursive(profile.remote_path)
    report.remote_files = len(files)
    remote = _remote_map(files, profile.remote_path)

    # Block obvious temporary bridge/data leftovers and executable payloads in uploads.
    for rel, item in remote.items():
        path = PurePosixPath(rel)
        lower = rel.lower()
        name = path.name
        suffix = path.suffix.lower()

        if TEMP_NAME_RE.fullmatch(name):
            report.temp_artifacts += 1
            report.issues.append(LiveVerifyIssue("temporary-artifact", rel, "WP Clean temporary bridge/data file is still present."))

        if lower.startswith("wp-content/uploads/") and suffix in EXECUTABLE_SUFFIXES:
            report.uploads_executables += 1
            report.issues.append(LiveVerifyIssue("uploads-executable", rel, "Executable file exists under wp-content/uploads."))

        if "/" not in rel and suffix in EXECUTABLE_SUFFIXES:
            allowed_root_php = _is_core_path(rel) or rel in {"wp-config.php"}
            if not allowed_root_php:
                report.unexpected_root_php += 1
                report.issues.append(LiveVerifyIssue("unexpected-root-code", rel, "Unexpected executable file exists in WordPress root."))

    # Official core checksum verification outside wp-content.
    if report.wordpress_version:
        try:
            if progress:
                progress({"phase": "core_checksums", "version": report.wordpress_version})
            checksums = _fetch_core_checksums(report.wordpress_version)
            expected = {path: digest for path, digest in checksums.items() if _is_core_path(path)}
            report.core_expected = len(expected)
            missing = sorted(path for path in expected if path not in remote)
            report.core_missing = len(missing)
            for rel in missing:
                report.issues.append(LiveVerifyIssue("core-missing", rel, "Official WordPress core file is missing."))

            targets = [remote[path] for path in expected if path in remote]
            hashes, unreadable = _hash_remote_files(
                transport,
                profile.remote_path,
                targets,
                algorithm="md5",
                progress=progress,
            )
            report.core_unreadable = len(unreadable)
            for detail in unreadable:
                rel = detail.split(":", 1)[0]
                report.issues.append(LiveVerifyIssue("core-unreadable", rel, detail))

            for rel, actual in hashes.items():
                expected_digest = expected.get(rel)
                if expected_digest and actual != expected_digest:
                    report.core_mismatched += 1
                    report.issues.append(
                        LiveVerifyIssue(
                            "core-checksum",
                            rel,
                            f"Official checksum mismatch: expected {expected_digest}, got {actual}.",
                        )
                    )
                else:
                    report.core_verified += 1
        except Exception as exc:
            report.warnings.append(f"Official WordPress core checksum verification unavailable: {type(exc).__name__}: {exc}")

    # Scan runtime code for known malware markers. This intentionally avoids a broad
    # heuristic score here to reduce false positives from legitimate plugin code.
    scan_targets = [
        item
        for rel, item in remote.items()
        if (
            rel.lower().startswith("wp-content/plugins/")
            or rel.lower().startswith("wp-content/themes/")
            or rel.lower().startswith("wp-content/mu-plugins/")
            or rel.lower().startswith("wp-content/uploads/")
        )
        and PurePosixPath(rel).suffix.lower() in SCAN_SUFFIXES
        and (item.size is None or item.size <= 12 * 1024 * 1024)
    ]

    if progress:
        progress({"phase": "malware_scan_start", "total": len(scan_targets)})
    scanned = 0
    with TemporaryDirectory(prefix="wpclean-live-scan-") as temp_dir:
        temp_root = Path(temp_dir)
        with ThreadPoolExecutor(max_workers=max(1, transport.config.workers), thread_name_prefix="verify-scan") as pool:
            futures = {
                pool.submit(_download_scan_file, transport, profile.remote_path, item, temp_root): item
                for item in scan_targets
            }
            for future in as_completed(futures):
                scanned += 1
                item = futures[future]
                try:
                    rel, data = future.result()
                except Exception as exc:
                    rel = item.path
                    report.issues.append(
                        LiveVerifyIssue(
                            "scan-unreadable",
                            rel,
                            f"Could not read runtime code during final verification: {type(exc).__name__}: {exc}",
                        )
                    )
                    continue
                lowered = data.lower()
                matched = [marker.decode("ascii", errors="replace") for marker in KNOWN_MALWARE_MARKERS if marker in lowered]
                if matched:
                    report.suspicious_markers += 1
                    report.issues.append(
                        LiveVerifyIssue(
                            "known-malware-marker",
                            rel,
                            "Known malware marker(s) found: " + ", ".join(matched),
                        )
                    )
                if progress:
                    progress({"phase": "malware_scan", "completed": scanned, "total": len(scan_targets), "current": rel})
    report.scanned_code_files = scanned

    # HTTP health checks are read-only and follow normal redirects.
    if progress:
        progress({"phase": "http"})
    base = profile.web_base_url.rstrip("/") + "/"
    report.http_home = _http_check(base)
    report.http_admin = _http_check(urljoin(base, "wp-admin/"), admin=True)
    if not report.http_home.ok:
        report.issues.append(
            LiveVerifyIssue("http-home", report.http_home.url, report.http_home.detail or f"HTTP status {report.http_home.status}")
        )
    if not report.http_admin.ok:
        report.issues.append(
            LiveVerifyIssue("http-admin", report.http_admin.url, report.http_admin.detail or f"HTTP status {report.http_admin.status}")
        )

    if report.issues:
        report.status = "BLOCKED"
    elif report.warnings:
        report.status = "PASS WITH WARNINGS"
    else:
        report.status = "PASS"
    return report


def save_live_verify_report(report: LiveVerifyReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


__all__ = ["LiveVerifyReport", "verify_live_site", "save_live_verify_report"]
