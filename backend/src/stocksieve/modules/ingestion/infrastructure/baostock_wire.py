"""本文件在官方SDK协议边界执行缓存、实际发包记账、超时和大小限制；仅使用免费匿名通道。"""

import hashlib
import json
import re
import select
import socket
import threading
import time
import zlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from stocksieve.modules.ingestion.application.ports import SourceDeferred
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
from stocksieve.shared.errors import DomainError
from stocksieve.shared.time import SHANGHAI


class BaostockWire:
    """SDK使用进程级连接；每个worker仅创建一个实例，由串行同步队列使用。"""

    def __init__(self, policy: SourcePolicy, cache: Path) -> None:
        import baostock as sdk
        import baostock.common.contants as constants
        import baostock.common.context as context
        import baostock.util.socketutil as transport
        self.sdk, self.constants = sdk, constants
        context.user_id, context.apiKey = 'anonymous', '0'
        self.policy, self.cache = policy, cache
        cache.mkdir(parents=True, exist_ok=True)
        self.socket: socket.socket | None = None
        self.logged = False
        self.last_used = 0.0
        self.recovering_connection = False
        self.lock = threading.RLock()
        # SDK只负责构建和解析消息；连接和实际发送均由此处统一控制。
        def connect(client: Any, api_key: str = '0') -> None:
            if api_key != '0':
                raise DomainError('SOURCE_CONFIGURATION_INVALID', '当前仅允许免费匿名数据通道。', 503)
            context.default_socket = self
        transport.SocketUtil.connect = connect
        transport.send_msg = self.request

    def close(self) -> None:
        if self.socket:
            self.socket.close()
        self.socket, self.logged = None, False

    def connection_alive(self) -> bool:
        """被动检查服务器已关闭的空闲连接，不发送保活探测包或消耗额外请求。"""
        if self.socket is None:
            return False
        try:
            readable, _, _ = select.select([self.socket], [], [], 0)
            return not readable or bool(self.socket.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT))
        except BlockingIOError:
            return True
        except OSError:
            return False

    def lifetime(self, method: str, body: str) -> int:
        if method == 'query_stock_basic':
            return 86400
        if method in {'query_daily_history_k_AStock', 'query_daily_adjust_factor'}:
            try:
                day = date.fromisoformat(body.split('\1')[-2])
                return 86400 if (datetime.now(SHANGHAI).date() - day).days < 7 else 30 * 86400
            except ValueError:
                return 600
        return 86400

    def request(self, message: str) -> str:
        with self.lock:
            body = message[self.constants.MESSAGE_HEADER_LENGTH:]
            method = body.split('\1', 1)[0]
            dates = re.findall(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', body)
            stream_key = method + ':' + dates[-1] if method == 'query_daily_history_k_AStock' and dates else hashlib.sha256(message.encode()).hexdigest()
            path = self.cache / (hashlib.sha256(message.encode()).hexdigest() + '.json')
            cacheable = method not in {'login', 'logout'}
            if cacheable and path.exists():
                try:
                    saved = json.loads(path.read_text())
                    same_day = datetime.fromtimestamp(saved['at'], SHANGHAI).date() == datetime.now(SHANGHAI).date()
                    if 0 <= time.time() - saved['at'] < self.lifetime(method, body) and (method not in {'query_stock_basic', 'query_trade_dates'} or same_day):
                        return saved['reply']
                except (OSError, ValueError, KeyError):
                    pass
            if cacheable and (not self.logged or time.monotonic() - self.last_used > 30 or not self.connection_alive()):
                self.close()
                result = self.sdk.login()
                if result.error_code != '0':
                    raise DomainError('SOURCE_UNAVAILABLE', '免费数据通道登录未成功。', 503)
                self.logged = True
            # 登录、查询、隐含分页和退出均在发包前计入同一持久账本。
            self.policy.reserve('baostock')
            content = bytearray()
            try:
                if self.socket is None:
                    self.socket = socket.create_connection((self.constants.BAOSTOCK_SERVER_IP, self.constants.BAOSTOCK_SERVER_PORT), timeout=15)
                self.socket.sendall((message + '\n').encode())
                deadline = time.monotonic() + 120
                while not content.endswith(b'<![CDATA[]]>\n'):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('渠道响应超时')
                    self.socket.settimeout(min(30, remaining))
                    chunk = self.socket.recv(65536)
                    if not chunk:
                        raise ConnectionError('渠道关闭连接')
                    content.extend(chunk)
                    if len(content) > 8 * 1024 * 1024:
                        raise ValueError('渠道响应过大')
                header_size = self.constants.MESSAGE_HEADER_LENGTH
                header = bytes(content[:header_size]).decode()
                parts = header.split(self.constants.MESSAGE_SPLIT)
                if parts[1] in self.constants.COMPRESSED_MESSAGE_TYPE_TUPLE:
                    length = int(parts[2])
                    unpacker = zlib.decompressobj()
                    result_bytes = unpacker.decompress(content[header_size:header_size + length], 8 * 1024 * 1024 + 1)
                    if len(result_bytes) > 8 * 1024 * 1024 or not unpacker.eof:
                        raise ValueError('渠道解压内容过大或不完整')
                    reply = header + result_bytes.decode()
                else:
                    reply = bytes(content).decode()
                self.last_used = time.monotonic()
            except (OSError, ValueError, zlib.error) as error:
                diagnostic = {'at': datetime.now(timezone.utc).isoformat(), 'method': method, 'type': type(error).__name__,
                              'connectionRecoveryAlreadyTried': self.recovering_connection,
                              'receiveDeadlineSeconds': 120,
                              'requestedDates': re.findall(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', body), 'receivedBytes': len(content)}
                if content:
                    (self.cache / 'last-failure-body.bin').write_bytes(content)
                (self.cache / 'last-failure.json').write_text(json.dumps(diagnostic))
                self.close()
                valid_partial = False
                if cacheable and len(content) > 21:
                    try:
                        fields = content[:21].decode().split('\1')
                        decoder = zlib.decompressobj()
                        prefix = decoder.decompress(content[21:], 4096)
                        valid_partial = not decoder.eof and prefix.startswith(('0\1success\1' + method + '\1anonymous\1').encode())
                    except (ValueError, UnicodeDecodeError, zlib.error):
                        pass
                if isinstance(error, OSError) and valid_partial:
                    until = self.policy.retry_partial_stream('baostock', stream_key)
                    if until:
                        raise SourceDeferred(until, '成功数据流中断，按有限短退避恢复当前只读分块。') from None
                if isinstance(error, ConnectionError) and not self.recovering_connection:
                    # 普通TCP空闲失效允许一次有计次的重新登录；连续读取仍失败才进入网络冷却。
                    self.recovering_connection = True
                    raise SourceDeferred(datetime.now(timezone.utc) + timedelta(seconds=self.policy.interval), '旧连接失效，按访问间隔重新建立一次连接。') from None
                self.policy.block('baostock', 3600 if isinstance(error, OSError) else 86400)
                raise DomainError('SOURCE_TIMEOUT' if isinstance(error, OSError) else 'DATA_NOT_AVAILABLE', '数据渠道访问失败，已暂停后续请求。', 503) from error
            if cacheable:
                self.recovering_connection = False
                self.policy.complete_stream(stream_key)
            if cacheable and reply[header_size:].split('\1', 1)[0] == '0':
                temporary = path.with_suffix('.' + uuid4().hex + '.tmp')
                temporary.write_text(json.dumps({'at': time.time(), 'reply': reply}))
                temporary.replace(path)
            if method == 'login':
                self.logged = reply[header_size:].split('\1', 1)[0] == '0'
            elif method == 'logout':
                self.close()
            return reply

    @staticmethod
    def rows(result: Any) -> list[dict[str, str]]:
        """完整读取SDK分页；分页同样经过受控request，失败不能截断为成功。"""
        rows: list[dict[str, str]] = []
        while True:
            if result.error_code != '0':
                raise DomainError('DATA_NOT_AVAILABLE', '免费通道未返回所需数据：' + str(result.error_msg)[:150], 503)
            if any(len(row) != len(result.fields) for row in result.data):
                raise DomainError('DATA_INCOMPLETE', '数据字段数量不一致。', 503)
            rows.extend(dict(zip(result.fields, row)) for row in result.data)
            result.cur_row_num = len(result.data)
            if not result.next():
                if result.error_code != '0':
                    raise DomainError('DATA_INCOMPLETE', '渠道后续分页读取失败。', 503)
                break
        return rows
