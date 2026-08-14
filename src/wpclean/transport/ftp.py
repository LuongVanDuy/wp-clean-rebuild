from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from ftplib import FTP, FTP_TLS, error_perm
from pathlib import Path, PurePosixPath
from typing import Callable
import os
import threading
import time


ProgressCallback = Callable[[dict], None]


@dataclass(slots=True)
class FTPConfig:
    host: str
    username: str
    password: str
    port: int = 21
    tls: bool = True
    passive: bool = True
    timeout: float = 30.0
    workers: int = 6
    block_size: int = 1024 * 1024


@dataclass(slots=True)
class RemoteFile:
    path: str
    size: int | None


@dataclass(slots=True)
class TransferStats:
    files_total: int
    files_downloaded: int
    files_skipped: int
    bytes_downloaded: int
    elapsed_seconds: float

    @property
    def bytes_per_second(self) -> float:
        if self.elapsed_seconds <= 0:
            return 0.0
        return self.bytes_downloaded / self.elapsed_seconds


class FTPTransport:
    """High-throughput FTP/FTPS downloader with bounded parallelism and resume support."""

    def __init__(self, config: FTPConfig):
        self.config = config
        self._local = threading.local()

    def _new_client(self) -> FTP:
        client: FTP
        if self.config.tls:
            tls = FTP_TLS(timeout=self.config.timeout)
            tls.connect(self.config.host, self.config.port)
            tls.login(self.config.username, self.config.password)
            tls.prot_p()
            client = tls
        else:
            ftp = FTP(timeout=self.config.timeout)
            ftp.connect(self.config.host, self.config.port)
            ftp.login(self.config.username, self.config.password)
            client = ftp
        client.set_pasv(self.config.passive)
        return client

    def _thread_client(self) -> FTP:
        client = getattr(self._local, "client", None)
        if client is None:
            client = self._new_client()
            self._local.client = client
        return client

    def test_connection(self) -> str:
        client = self._new_client()
        try:
            return client.pwd()
        finally:
            try:
                client.quit()
            except Exception:
                client.close()

    def directory_exists(self, remote_path: str) -> bool:
        """Verify one configured remote directory without recursively listing it."""
        client = self._new_client()
        current = None
        try:
            current = client.pwd()
            client.cwd(str(PurePosixPath(remote_path)))
            return True
        except error_perm:
            return False
        finally:
            if current is not None:
                try:
                    client.cwd(current)
                except Exception:
                    pass
            try:
                client.quit()
            except Exception:
                client.close()

    def _mlsd(self, client: FTP, remote_dir: str):
        try:
            yield from client.mlsd(remote_dir, facts=["type", "size"])
            return
        except (error_perm, AttributeError):
            pass

        current = client.pwd()
        try:
            client.cwd(remote_dir)
            for name in client.nlst():
                name = PurePosixPath(name).name
                if name in {".", ".."}:
                    continue
                item_path = str(PurePosixPath(remote_dir) / name)
                try:
                    client.cwd(item_path)
                    client.cwd(remote_dir)
                    yield name, {"type": "dir"}
                except error_perm:
                    size = None
                    try:
                        size = client.size(item_path)
                    except Exception:
                        pass
                    facts = {"type": "file"}
                    if size is not None:
                        facts["size"] = str(size)
                    yield name, facts
        finally:
            try:
                client.cwd(current)
            except Exception:
                pass

    def list_files_recursive(self, remote_root: str, progress: ProgressCallback | None = None) -> list[RemoteFile]:
        remote_root = str(PurePosixPath(remote_root))
        client = self._new_client()
        files: list[RemoteFile] = []
        stack = [remote_root]
        dirs_scanned = 0
        try:
            while stack:
                current = stack.pop()
                dirs_scanned += 1
                if progress:
                    progress({
                        "phase": "discover",
                        "remote_root": remote_root,
                        "current_dir": current,
                        "dirs_scanned": dirs_scanned,
                        "files_found": len(files),
                    })
                for name, facts in self._mlsd(client, current):
                    if name in {".", ".."}:
                        continue
                    path = str(PurePosixPath(current) / name)
                    kind = (facts.get("type") or "").lower()
                    if kind in {"dir", "cdir", "pdir"}:
                        if kind == "dir":
                            stack.append(path)
                        continue
                    if kind == "file" or not kind:
                        raw_size = facts.get("size")
                        size = int(raw_size) if raw_size and raw_size.isdigit() else None
                        files.append(RemoteFile(path=path, size=size))
            if progress:
                progress({
                    "phase": "discovered",
                    "remote_root": remote_root,
                    "dirs_scanned": dirs_scanned,
                    "files_found": len(files),
                    "bytes_total": sum(item.size or 0 for item in files),
                })
            return files
        finally:
            try:
                client.quit()
            except Exception:
                client.close()

    def _download_one(
        self,
        remote_file: RemoteFile,
        remote_root: str,
        local_root: Path,
        resume: bool,
    ) -> tuple[str, int]:
        client = self._thread_client()
        rel = PurePosixPath(remote_file.path).relative_to(PurePosixPath(remote_root))
        local_path = local_root.joinpath(*rel.parts)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        offset = local_path.stat().st_size if resume and local_path.exists() else 0
        if remote_file.size is not None and offset == remote_file.size:
            return "skipped", 0
        if remote_file.size is not None and offset > remote_file.size:
            local_path.unlink(missing_ok=True)
            offset = 0

        mode = "ab" if offset else "wb"
        start_size = offset
        try:
            with local_path.open(mode) as fh:
                client.retrbinary(
                    f"RETR {remote_file.path}",
                    fh.write,
                    blocksize=self.config.block_size,
                    rest=offset if offset else None,
                )
        except Exception:
            try:
                client.close()
            except Exception:
                pass
            self._local.client = None
            raise

        final_size = local_path.stat().st_size
        return "downloaded", max(0, final_size - start_size)

    def download_tree(
        self,
        remote_root: str,
        local_root: Path,
        resume: bool = True,
        progress: ProgressCallback | None = None,
    ) -> TransferStats:
        local_root.mkdir(parents=True, exist_ok=True)
        files = self.list_files_recursive(remote_root, progress=progress)
        started = time.monotonic()
        downloaded = 0
        skipped = 0
        bytes_downloaded = 0
        completed = 0
        total_bytes = sum(item.size or 0 for item in files)

        with ThreadPoolExecutor(max_workers=max(1, self.config.workers), thread_name_prefix="ftp") as pool:
            futures = {
                pool.submit(self._download_one, item, remote_root, local_root, resume): item
                for item in files
            }
            for future in as_completed(futures):
                item = futures[future]
                status, transferred = future.result()
                completed += 1
                bytes_downloaded += transferred
                if status == "skipped":
                    skipped += 1
                else:
                    downloaded += 1

                if progress:
                    elapsed = max(time.monotonic() - started, 0.001)
                    progress({
                        "phase": "transfer",
                        "remote_root": remote_root,
                        "current_file": item.path,
                        "files_total": len(files),
                        "files_completed": completed,
                        "files_downloaded": downloaded,
                        "files_skipped": skipped,
                        "bytes_downloaded": bytes_downloaded,
                        "bytes_total": total_bytes,
                        "elapsed_seconds": elapsed,
                        "bytes_per_second": bytes_downloaded / elapsed,
                    })

        elapsed = time.monotonic() - started
        if progress:
            progress({
                "phase": "complete",
                "remote_root": remote_root,
                "files_total": len(files),
                "files_downloaded": downloaded,
                "files_skipped": skipped,
                "bytes_downloaded": bytes_downloaded,
                "bytes_total": total_bytes,
                "elapsed_seconds": elapsed,
                "bytes_per_second": bytes_downloaded / elapsed if elapsed > 0 else 0.0,
            })

        return TransferStats(
            files_total=len(files),
            files_downloaded=downloaded,
            files_skipped=skipped,
            bytes_downloaded=bytes_downloaded,
            elapsed_seconds=elapsed,
        )

    def download_file(self, remote_path: str, local_path: Path, resume: bool = True) -> TransferStats:
        client = self._new_client()
        try:
            size = None
            try:
                size = client.size(remote_path)
            except Exception:
                pass
        finally:
            try:
                client.quit()
            except Exception:
                client.close()

        remote = RemoteFile(path=remote_path, size=size)
        remote_root = str(PurePosixPath(remote_path).parent)
        local_root = local_path.parent
        expected_name = PurePosixPath(remote_path).name
        if local_path.name != expected_name:
            temp = local_root / expected_name
            status, transferred = self._download_one(remote, remote_root, local_root, resume)
            if temp != local_path:
                os.replace(temp, local_path)
        else:
            status, transferred = self._download_one(remote, remote_root, local_root, resume)
        return TransferStats(
            files_total=1,
            files_downloaded=0 if status == "skipped" else 1,
            files_skipped=1 if status == "skipped" else 0,
            bytes_downloaded=transferred,
            elapsed_seconds=0.0,
        )
