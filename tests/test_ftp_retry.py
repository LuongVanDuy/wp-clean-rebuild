from pathlib import Path

from wpclean.transport.ftp import FTPConfig, FTPTransport, RemoteFile


class ResetOnceClient:
    def __init__(self):
        self.closed = False

    def retrbinary(self, command, callback, blocksize=8192, rest=None):
        assert command.endswith("/file.bin")
        assert rest is None
        callback(b"abc")
        raise ConnectionResetError(10054, "remote reset")

    def close(self):
        self.closed = True


class ResumeClient:
    def __init__(self):
        self.closed = False

    def retrbinary(self, command, callback, blocksize=8192, rest=None):
        assert command.endswith("/file.bin")
        assert rest == 3
        callback(b"def")

    def close(self):
        self.closed = True


def test_download_reconnects_and_resumes_after_connection_reset(tmp_path: Path):
    transport = FTPTransport(
        FTPConfig(
            host="example.test",
            username="user",
            password="pass",
            tls=False,
            retries=2,
        )
    )
    clients = [ResetOnceClient(), ResumeClient()]
    transport._new_client = lambda: clients.pop(0)  # type: ignore[method-assign]

    events = []
    status, transferred = transport._download_one(
        RemoteFile("/root/file.bin", 6),
        "/root",
        tmp_path,
        True,
        events.append,
    )

    assert status == "downloaded"
    assert transferred == 6
    assert (tmp_path / "file.bin").read_bytes() == b"abcdef"
    assert len(events) == 1
    assert events[0]["phase"] == "retry"
    assert events[0]["current_file"] == "/root/file.bin"
    assert events[0]["attempt"] == 2
    assert events[0]["resume_offset"] == 3


def test_download_failure_names_file_after_retry_budget(tmp_path: Path):
    class AlwaysResetClient:
        def retrbinary(self, command, callback, blocksize=8192, rest=None):
            raise ConnectionResetError(10054, "remote reset")

        def close(self):
            pass

    transport = FTPTransport(
        FTPConfig(
            host="example.test",
            username="user",
            password="pass",
            tls=False,
            retries=1,
        )
    )
    transport._new_client = lambda: AlwaysResetClient()  # type: ignore[method-assign]

    try:
        transport._download_one(
            RemoteFile("/root/problem.bin", 10),
            "/root",
            tmp_path,
            True,
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected RuntimeError")

    assert "after 2 attempts" in message
    assert "/root/problem.bin" in message
    assert "ConnectionResetError" in message
