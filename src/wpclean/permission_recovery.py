from __future__ import annotations

from ftplib import error_perm
from pathlib import PurePosixPath
from typing import Callable

from . import rebuild_execute as rebuild_engine
from .transport import FTPTransport


ProgressCallback = Callable[[dict], None]


def _chmod(client, path: str, mode: str) -> tuple[bool, str]:
    try:
        response = client.sendcmd(f"SITE CHMOD {mode} {path}")
        return True, str(response)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _missing_error(exc: Exception) -> bool:
    """Return True only for errors that clearly mean the FTP path is already gone.

    DELETE/RMD/MLSD are inherently racy: a plugin, host-side cleanup, malware,
    antivirus, or another server process may remove an entry after MLSD listed
    it but before we delete it. In that case the desired wipe state has already
    been reached and deletion is safely idempotent.
    """
    text = str(exc).lower()
    markers = (
        "no such file",
        "no such directory",
        "file not found",
        "path not found",
        "does not exist",
        "cannot find the file",
        "cannot find the path",
        "system cannot find the file",
        "system cannot find the path",
        "winerror 2",
        "winerror 3",
    )
    return isinstance(exc, FileNotFoundError) or any(marker in text for marker in markers)


def _permission_error(exc: Exception) -> bool:
    if _missing_error(exc):
        return False
    text = str(exc).lower()
    return isinstance(exc, error_perm) or "permission denied" in text or text.startswith("550")


def _delete_file(client, path: str, *, progress: ProgressCallback | None = None) -> None:
    first_error: Exception | None = None
    try:
        client.delete(path)
        return
    except Exception as exc:
        if _missing_error(exc):
            return
        if not _permission_error(exc):
            raise
        first_error = exc

    parent = str(PurePosixPath(path).parent)
    attempts: list[str] = []
    last_exc: Exception = first_error or RuntimeError("unknown FTP delete error")
    for file_mode, parent_mode in (("666", "755"), ("777", "777")):
        file_ok, file_detail = _chmod(client, path, file_mode)
        parent_ok, parent_detail = _chmod(client, parent, parent_mode)
        attempts.append(
            f"file CHMOD {file_mode}={file_ok} ({file_detail}); "
            f"parent CHMOD {parent_mode}={parent_ok} ({parent_detail})"
        )
        if progress:
            progress(
                {
                    "phase": "permission_recovery",
                    "kind": "file",
                    "path": path,
                    "file_mode": file_mode,
                    "parent_mode": parent_mode,
                }
            )
        try:
            client.delete(path)
            return
        except Exception as retry_exc:
            if _missing_error(retry_exc):
                return
            if not _permission_error(retry_exc):
                raise
            last_exc = retry_exc

    raise RuntimeError(
        "FTP không thể xóa file dù đã thử đổi quyền tạm thời đến 777: "
        f"{path}. Có thể file/thư mục thuộc owner khác, bị ACL/immutable hoặc hosting chặn SITE CHMOD. "
        "Cần xử lý ownership/permission bằng File Manager, SSH hoặc hỗ trợ hosting. "
        f"Chi tiết: {' | '.join(attempts)} | lỗi cuối: {last_exc}"
    )


def _remove_dir(client, path: str, *, progress: ProgressCallback | None = None) -> None:
    first_error: Exception | None = None
    try:
        client.rmd(path)
        return
    except Exception as exc:
        if _missing_error(exc):
            return
        if not _permission_error(exc):
            raise
        first_error = exc

    parent = str(PurePosixPath(path).parent)
    attempts: list[str] = []
    last_exc: Exception = first_error or RuntimeError("unknown FTP rmd error")
    for dir_mode, parent_mode in (("755", "755"), ("777", "777")):
        dir_ok, dir_detail = _chmod(client, path, dir_mode)
        parent_ok, parent_detail = _chmod(client, parent, parent_mode)
        attempts.append(
            f"dir CHMOD {dir_mode}={dir_ok} ({dir_detail}); "
            f"parent CHMOD {parent_mode}={parent_ok} ({parent_detail})"
        )
        if progress:
            progress(
                {
                    "phase": "permission_recovery",
                    "kind": "directory",
                    "path": path,
                    "dir_mode": dir_mode,
                    "parent_mode": parent_mode,
                }
            )
        try:
            client.rmd(path)
            return
        except Exception as retry_exc:
            if _missing_error(retry_exc):
                return
            if not _permission_error(retry_exc):
                raise
            last_exc = retry_exc

    raise RuntimeError(
        "FTP không thể xóa thư mục dù đã thử đổi quyền tạm thời đến 777: "
        f"{path}. Có thể thư mục thuộc owner khác, bị ACL/immutable hoặc hosting chặn SITE CHMOD. "
        "Cần xử lý ownership/permission bằng File Manager, SSH hoặc hỗ trợ hosting. "
        f"Chi tiết: {' | '.join(attempts)} | lỗi cuối: {last_exc}"
    )


def wipe_remote_root_with_permission_recovery(
    transport: FTPTransport,
    remote_root: str,
    *,
    progress: ProgressCallback | None = None,
) -> tuple[int, int, list[str]]:
    """Wipe WordPress root and recover ordinary Unix FTP permissions when possible.

    SITE CHMOD never elevates the FTP account beyond hosting ownership/ACL rules.
    777 is used only as a last-resort temporary deletion aid; the target is then
    immediately deleted instead of leaving permissive files on the rebuilt site.
    Paths that disappear between MLSD and DELETE/RMD are treated as successful
    deletion because the requested final state has already been reached.
    """
    client = transport._new_client()
    deleted_files = 0
    deleted_dirs = 0
    preserved: list[str] = []

    def emit(current: str) -> None:
        if progress:
            progress(
                {
                    "phase": "wipe",
                    "deleted_files": deleted_files,
                    "deleted_dirs": deleted_dirs,
                    "current": current,
                }
            )

    def ensure_listable(path: str) -> list[tuple[str, dict]]:
        first_error: Exception | None = None
        try:
            return list(transport._mlsd(client, path))
        except Exception as exc:
            if _missing_error(exc):
                return []
            if not _permission_error(exc):
                raise
            first_error = exc

        # A locked directory may also deny MLSD. Try owner-safe access first,
        # then the explicit last-resort 777 requested for remediation.
        last_exc: Exception = first_error or RuntimeError("unknown FTP list error")
        for mode in ("755", "777"):
            _chmod(client, path, mode)
            try:
                return list(transport._mlsd(client, path))
            except Exception as retry_exc:
                if _missing_error(retry_exc):
                    return []
                if not _permission_error(retry_exc):
                    raise
                last_exc = retry_exc
        raise RuntimeError(
            f"FTP không thể đọc thư mục để xóa: {path}. "
            "Đã thử CHMOD 755/777 nhưng tài khoản FTP vẫn không đủ quyền; "
            f"cần File Manager/SSH/hosting xử lý ownership hoặc ACL. Lỗi cuối: {last_exc}"
        )

    def remove_tree(path: str, *, root: bool = False) -> None:
        nonlocal deleted_files, deleted_dirs
        entries = ensure_listable(path)
        for name, facts in entries:
            if name in {".", ".."}:
                continue
            if root and name == ".well-known":
                preserved.append(str(PurePosixPath(path) / name))
                continue
            child = str(PurePosixPath(path) / name)
            kind = (facts.get("type") or "").lower()
            if kind in {"dir", "cdir", "pdir"}:
                if kind == "dir":
                    remove_tree(child)
                    _remove_dir(client, child, progress=progress)
                    deleted_dirs += 1
                    emit(child)
                continue
            _delete_file(client, child, progress=progress)
            deleted_files += 1
            emit(child)

    try:
        remove_tree(remote_root, root=True)
        remaining = []
        for name, _facts in ensure_listable(remote_root):
            if name in {".", "..", ".well-known"}:
                continue
            remaining.append(name)
        if remaining:
            raise RuntimeError(
                "Sau khi wipe vẫn còn file/thư mục trong WordPress root: "
                + ", ".join(remaining[:20])
            )
        return deleted_files, deleted_dirs, preserved
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


# execute_rebuild resolves this global from rebuild_execute at runtime. Patch the
# shared engine once when the package is imported so BATDAU and rebuild-config
# both get the hardened deletion behavior.
rebuild_engine._wipe_remote_root = wipe_remote_root_with_permission_recovery


__all__ = [
    "wipe_remote_root_with_permission_recovery",
]
