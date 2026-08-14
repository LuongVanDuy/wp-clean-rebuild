from __future__ import annotations

from pathlib import Path, PurePosixPath

from .ftp import FTPTransport, RemoteFile


_ORIGINAL_DOWNLOAD_ONE = FTPTransport._download_one
_PATCH_MARKER = "_wpclean_zero_byte_safe"


def _zero_byte_safe_download_one(
    self: FTPTransport,
    remote_file: RemoteFile,
    remote_root: str,
    local_root: Path,
    resume: bool,
    progress=None,
) -> tuple[str, int]:
    """Materialize zero-byte remote files instead of returning before a local file exists.

    The base FTP downloader uses the remote SIZE value to short-circuit completed
    transfers. For a 0-byte remote file, a missing local file also has an implicit
    offset of 0, so the old logic could report success without creating the file.
    Final verification would then fail when opening the nonexistent temp file.
    """
    if remote_file.size == 0:
        rel = PurePosixPath(remote_file.path).relative_to(PurePosixPath(remote_root))
        local_path = local_root.joinpath(*rel.parts)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        if resume and local_path.exists() and local_path.stat().st_size == 0:
            return "skipped", 0

        # Create the missing empty file, or truncate a stale non-empty local file
        # when the remote source is authoritatively reported as zero bytes.
        local_path.write_bytes(b"")
        return "downloaded", 0

    return _ORIGINAL_DOWNLOAD_ONE(
        self,
        remote_file,
        remote_root,
        local_root,
        resume,
        progress,
    )


def apply_zero_byte_download_fix() -> None:
    if getattr(FTPTransport._download_one, _PATCH_MARKER, False):
        return
    setattr(_zero_byte_safe_download_one, _PATCH_MARKER, True)
    FTPTransport._download_one = _zero_byte_safe_download_one  # type: ignore[method-assign]


__all__ = ["apply_zero_byte_download_fix"]
