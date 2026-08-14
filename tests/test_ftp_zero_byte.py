from pathlib import Path

from wpclean.transport.ftp import FTPConfig, FTPTransport, RemoteFile


def _transport() -> FTPTransport:
    return FTPTransport(
        FTPConfig(
            host="example.test",
            username="user",
            password="pass",
            tls=False,
        )
    )


def test_zero_byte_remote_file_is_created_without_network(tmp_path: Path):
    transport = _transport()

    status, transferred = transport._download_one(
        RemoteFile("/root/empty.txt", 0),
        "/root",
        tmp_path,
        False,
    )

    assert status == "downloaded"
    assert transferred == 0
    assert (tmp_path / "empty.txt").is_file()
    assert (tmp_path / "empty.txt").stat().st_size == 0


def test_zero_byte_resume_skips_existing_empty_file(tmp_path: Path):
    target = tmp_path / "empty.txt"
    target.write_bytes(b"")
    transport = _transport()

    status, transferred = transport._download_one(
        RemoteFile("/root/empty.txt", 0),
        "/root",
        tmp_path,
        True,
    )

    assert status == "skipped"
    assert transferred == 0
    assert target.stat().st_size == 0


def test_zero_byte_download_truncates_stale_local_file(tmp_path: Path):
    target = tmp_path / "empty.txt"
    target.write_bytes(b"stale")
    transport = _transport()

    status, transferred = transport._download_one(
        RemoteFile("/root/empty.txt", 0),
        "/root",
        tmp_path,
        True,
    )

    assert status == "downloaded"
    assert transferred == 0
    assert target.read_bytes() == b""
