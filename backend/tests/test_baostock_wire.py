"""本文件用受控字节流验证完整分块缓存与半包有界重试，不连接真实厂商。"""

import random
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from stocksieve.baostock_wire import BaostockWire
from stocksieve.sources import SourceDeferred


class Socket:
    def __init__(self, data: bytes, timeout: bool = False) -> None:
        self.data, self.timeout, self.sent = data, timeout, 0
    def sendall(self, data: bytes) -> None:
        self.sent += 1
    def settimeout(self, seconds: float) -> None:
        pass
    def recv(self, size: int) -> bytes:
        if self.data:
            result, self.data = self.data, b''
            return result
        if self.timeout:
            raise TimeoutError('test stream stalled')
        return b''
    def close(self) -> None:
        pass


@pytest.fixture
def transport(tmp_path: Path, request: Any) -> Any:
    import baostock.util.socketutil as sdk_transport
    original_connect, original_send = sdk_transport.SocketUtil.connect, sdk_transport.send_msg
    policy = SimpleNamespace(reserve=lambda *args: None, block=lambda *args: pytest.fail('成功流不应进入长暂停'),
        retry_partial_stream=lambda *args: datetime.now(timezone.utc) + timedelta(seconds=30), complete_stream=lambda *args: None, interval=10)
    wire = BaostockWire(policy, tmp_path)
    wire.logged = True
    wire.last_used = time.monotonic()
    wire.connection_alive = lambda: True
    def restore() -> None:
        wire.close()
        sdk_transport.SocketUtil.connect, sdk_transport.send_msg = original_connect, original_send
    request.addfinalizer(restore)
    return wire


def test_completed_compressed_reply_is_cached_without_a_second_request(transport: Any) -> None:
    body = b'0\1success\1query_daily_history_k_AStock\1anonymous\1{"record":[]}\1date,code\n'
    packed = zlib.compress(body)
    header = ('00.9.10\1' + '99\1' + f'{len(packed):010d}').encode()
    socket = Socket(header + packed + b'<![CDATA[]]>\n')
    transport.socket = socket
    message = 'x' * 21 + 'query_daily_history_k_AStock\1anonymous\1' + '2026-10-30\1checksum'
    reply = transport.request(message)
    assert reply == header.decode() + body.decode()
    assert transport.request(message) == reply and socket.sent == 1


def test_partial_success_reply_is_never_cached_as_complete(transport: Any, tmp_path: Path) -> None:
    body = b'0\1success\1query_daily_history_k_AStock\1anonymous\1' + random.Random(42).randbytes(20000)
    packed = zlib.compress(body)
    header = ('00.9.10\1' + '99\1' + f'{len(packed):010d}').encode()
    transport.socket = Socket(header + packed[:int(len(packed) * .8)], timeout=True)
    message = 'x' * 21 + 'query_daily_history_k_AStock\1anonymous\1' + '2026-10-30\1checksum'
    with pytest.raises(SourceDeferred):
        transport.request(message)
    assert (tmp_path / 'last-failure-body.bin').exists()
    assert all('"reply"' not in file.read_text() for file in tmp_path.glob('*.json'))
