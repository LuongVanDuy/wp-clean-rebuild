from __future__ import annotations

from ftplib import error_perm

import pytest

from wpclean.permission_recovery import wipe_remote_root_with_permission_recovery


class FakeClient:
    def __init__(self, *, succeed_after: int | None, chmod_supported: bool = True):
        self.succeed_after = succeed_after
        self.chmod_supported = chmod_supported
        self.delete_calls = 0
        self.commands: list[str] = []
        self.deleted = False

    def delete(self, path: str):
        self.delete_calls += 1
        if self.succeed_after is not None and self.delete_calls >= self.succeed_after:
            self.deleted = True
            return "250 deleted"
        raise error_perm("550 Permission denied")

    def sendcmd(self, command: str):
        self.commands.append(command)
        if not self.chmod_supported:
            raise error_perm("500 SITE CHMOD not understood")
        return "200 SITE CHMOD command successful"

    def rmd(self, path: str):
        return "250 removed"

    def quit(self):
        return "221 bye"

    def close(self):
        pass


class FakeTransport:
    def __init__(self, client: FakeClient):
        self.client = client

    def _new_client(self):
        return self.client

    def _mlsd(self, client, path: str):
        if path == "/public_html" and not client.deleted:
            return iter([("locked.php", {"type": "file"})])
        return iter([])


def test_wipe_recovers_permission_with_file_and_parent_chmod():
    client = FakeClient(succeed_after=2)
    transport = FakeTransport(client)

    files, dirs, preserved = wipe_remote_root_with_permission_recovery(
        transport,  # type: ignore[arg-type]
        "/public_html",
    )

    assert files == 1
    assert dirs == 0
    assert preserved == []
    assert client.delete_calls == 2
    assert "SITE CHMOD 666 /public_html/locked.php" in client.commands
    assert "SITE CHMOD 755 /public_html" in client.commands
    assert not any("CHMOD 777" in command for command in client.commands)


def test_wipe_uses_777_only_as_last_resort():
    client = FakeClient(succeed_after=3)
    transport = FakeTransport(client)

    files, _dirs, _preserved = wipe_remote_root_with_permission_recovery(
        transport,  # type: ignore[arg-type]
        "/public_html",
    )

    assert files == 1
    assert client.delete_calls == 3
    assert "SITE CHMOD 777 /public_html/locked.php" in client.commands
    assert "SITE CHMOD 777 /public_html" in client.commands


def test_wipe_reports_owner_acl_problem_when_ftp_cannot_recover():
    client = FakeClient(succeed_after=None, chmod_supported=False)
    transport = FakeTransport(client)

    with pytest.raises(RuntimeError) as caught:
        wipe_remote_root_with_permission_recovery(
            transport,  # type: ignore[arg-type]
            "/public_html",
        )

    message = str(caught.value)
    assert "FTP không thể xóa file" in message
    assert "owner" in message
    assert "ACL" in message
    assert "File Manager" in message
    assert "SITE CHMOD 777 /public_html/locked.php" in client.commands


class DisappearingClient(FakeClient):
    def __init__(self):
        super().__init__(succeed_after=None)
        self.deleted = False

    def delete(self, path: str):
        self.delete_calls += 1
        self.deleted = True
        raise error_perm("550 The system cannot find the path specified")


def test_wipe_treats_file_that_disappears_after_listing_as_already_deleted():
    client = DisappearingClient()
    transport = FakeTransport(client)

    files, dirs, preserved = wipe_remote_root_with_permission_recovery(
        transport,  # type: ignore[arg-type]
        "/public_html",
    )

    assert files == 1
    assert dirs == 0
    assert preserved == []
    assert client.delete_calls == 1
    assert client.commands == [], "missing paths must not trigger chmod recovery"


class VanishingDirectoryTransport(FakeTransport):
    def _mlsd(self, client, path: str):
        if path == "/public_html":
            if getattr(client, "dir_removed", False):
                return iter([])
            return iter([("cache", {"type": "dir"})])
        if path == "/public_html/cache":
            client.dir_removed = True
            raise error_perm("550 No such file or directory")
        return iter([])


class VanishingDirectoryClient(FakeClient):
    def __init__(self):
        super().__init__(succeed_after=1)
        self.dir_removed = False

    def rmd(self, path: str):
        self.dir_removed = True
        raise FileNotFoundError(3, "The system cannot find the path specified", path)


def test_wipe_treats_directory_that_disappears_during_recursion_as_already_deleted():
    client = VanishingDirectoryClient()
    transport = VanishingDirectoryTransport(client)

    files, dirs, preserved = wipe_remote_root_with_permission_recovery(
        transport,  # type: ignore[arg-type]
        "/public_html",
    )

    assert files == 0
    assert dirs == 1
    assert preserved == []
