import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BRIDGE_PATH = ROOT / "packages" / "python-bridge"
if str(PYTHON_BRIDGE_PATH) not in sys.path:
    sys.path.insert(0, str(PYTHON_BRIDGE_PATH))

from app import ftp_client as ftp_module
from app.ftp_client import StorageFTPClient


class _FakeSFTPConn:
    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.putfo_calls = 0

    def get_channel(self):
        class _Ch:
            def get_transport(self_inner):
                class _T:
                    is_active = True

                return _T()

        return _Ch()

    def putfo(self, _buf, _path):
        self.putfo_calls += 1
        if self.putfo_calls <= self.fail_times:
            import paramiko

            raise paramiko.SSHException("Server connection dropped: ")
        return None

    def close(self):
        pass


def test_upload_bytes_retries_on_ssh_exception(monkeypatch):
    client = StorageFTPClient(host="example.com", user="u", password="p", port=22, base_dir="/remote")
    fake = _FakeSFTPConn(fail_times=2)
    connect_calls = {"n": 0}

    def fake_ensure():
        connect_calls["n"] += 1
        return fake

    monkeypatch.setattr(client, "_ensure_connected", fake_ensure)
    monkeypatch.setattr(client, "_ensure_base_dir", lambda _conn: None)
    monkeypatch.setattr(client, "_ensure_remote_dir", lambda _conn, _path: None)
    monkeypatch.setattr(client, "_close_conn", lambda: None)

    assert client.upload_bytes(b"payload", "proj/index.html") is True
    assert fake.putfo_calls == 3
    assert connect_calls["n"] == 3


def test_upload_bytes_raises_after_max_ssh_retries(monkeypatch):
    client = StorageFTPClient(host="example.com", user="u", password="p", port=22, base_dir="/remote")
    fake = _FakeSFTPConn(fail_times=99)

    monkeypatch.setattr(client, "_ensure_connected", lambda: fake)
    monkeypatch.setattr(client, "_ensure_base_dir", lambda _conn: None)
    monkeypatch.setattr(client, "_ensure_remote_dir", lambda _conn, _path: None)
    monkeypatch.setattr(client, "_close_conn", lambda: None)

    import paramiko

    with pytest.raises(paramiko.SSHException, match="Server connection dropped"):
        client.upload_bytes(b"payload", "proj/index.html")

    assert fake.putfo_calls == ftp_module._UPLOAD_MAX_ATTEMPTS
