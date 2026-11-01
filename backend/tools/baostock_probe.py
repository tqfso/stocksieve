"""本文件通过官方SDK低频验证Baostock免费匿名通道，所有协议发包共用探测预算。"""

from __future__ import annotations

import fcntl
import json
import socket
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from probe_sources import Deferred, Guard, SHANGHAI


def run(target: date, directory: Path, *, execute: bool, offline: bool) -> int:
    """登录、查询、分页和退出都在实际socket发送位置计数，不绕过既有限频。"""
    if not execute:
        print(json.dumps({'mode': 'offline' if offline else 'preview', 'source': 'baostock',
                          'maximumRequests': 8, 'queries': ['calendar', 'basic', 'bse_history', 'bse_factor', 'main_history', 'main_factor']}, ensure_ascii=False))
        return 0
    import baostock as bs
    import baostock.common.context as context
    import baostock.common.contants as constants
    import baostock.util.socketutil as transport

    directory.mkdir(exist_ok=True)
    with (directory / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        guard = Guard(directory)
        report: dict[str, Any] = {'source': 'baostock', 'targetDate': str(target), 'coverageVerified': False,
                                  'startedAt': datetime.now(SHANGHAI).isoformat(), 'results': []}
        output = directory / ('baostock-' + datetime.now(SHANGHAI).strftime('%Y%m%dT%H%M%S%f') + '.json')
        channel: Any = None
        logged_in = False

        class BoundedSocket:
            """限制收包大小并检测断开连接，避免SDK在EOF后无限循环。"""

            def __init__(self, connection: socket.socket) -> None:
                self.connection = connection
                self.first = True
                self.received = 0

            def send(self, message: bytes) -> int:
                if not self.first:
                    guard.reserve('baostock')
                self.first = False
                self.received = 0
                self.connection.sendall(message)
                return len(message)

            def recv(self, size: int) -> bytes:
                data = self.connection.recv(size)
                self.received += len(data)
                if not data or self.received > 4 * 1024 * 1024:
                    raise ConnectionError('Baostock连接关闭或响应超过限制。')
                return data

            def close(self) -> None:
                self.connection.close()

        def connect(client: Any, api_key: str = '0') -> None:
            nonlocal channel
            if api_key != '0':
                raise ValueError('探测只使用免费匿名通道。')
            guard.reserve('baostock')
            channel = BoundedSocket(socket.create_connection((constants.BAOSTOCK_SERVER_IP, constants.BAOSTOCK_SERVER_PORT), timeout=15))
            context.default_socket = channel

        def unpack(data: bytes) -> bytes:
            result = zlib.decompressobj().decompress(data, 4 * 1024 * 1024 + 1)
            if len(result) > 4 * 1024 * 1024:
                raise ValueError('Baostock解压响应超过限制。')
            return result

        class Compression:
            decompress = staticmethod(unpack)

        def save(name: str, result: Any) -> None:
            rows = getattr(result, 'data', [])
            record = {'id': name, 'errorCode': result.error_code, 'message': result.error_msg,
                      'fields': result.fields, 'rows': rows, 'rowCount': len(rows),
                      'completeFirstPage': len(rows) < int(getattr(result, 'per_page_count', 10000))}
            report['results'].append(record)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            print(f"baostock_{name}: code={result.error_code}, rows={len(rows)}", flush=True)

        # 1. 仅替换当前进程的传输边界，沿用SDK构造和解释协议。
        original_connect, original_zlib = transport.SocketUtil.connect, transport.zlib
        transport.SocketUtil.connect, transport.zlib = connect, Compression
        try:
            result = bs.login()
            save('login', result)
            logged_in = result.error_code == '0'
            if not logged_in:
                guard.cooldown('baostock', 3600)
                return 1
            # 2. 每种能力只取一个响应，不调用隐含自动分页的get_data。
            fields = 'date,code,open,high,low,close,volume,amount,turn,tradestatus,isST'
            start = str(target - timedelta(days=400))
            queries = [
                ('calendar', lambda: bs.query_trade_dates(str(target - timedelta(days=730)), str(date(target.year, 12, 31)))),
                ('basic', lambda: bs.query_stock_basic()),
                ('bse_history', lambda: bs.query_history_k_data_plus('bj.920992', fields, start, str(target), adjustflag='3')),
                ('bse_factor', lambda: bs.query_adjust_factor('bj.920992', start, str(target))),
                ('main_history', lambda: bs.query_history_k_data_plus('sh.600000', fields, start, str(target), adjustflag='3')),
                ('main_factor', lambda: bs.query_adjust_factor('sh.600000', start, str(target))),
            ]
            for name, query in queries:
                save(name, query())
        except (OSError, ValueError, Deferred) as error:
            if not isinstance(error, Deferred):
                guard.cooldown('baostock', 3600)
            report['error'] = str(error)
        finally:
            try:
                if logged_in:
                    save('logout', bs.logout())
            except (OSError, ValueError, Deferred):
                pass
            if channel:
                channel.close()
            transport.SocketUtil.connect, transport.zlib = original_connect, original_zlib
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            print(f'报告：{output}', flush=True)
        return 0 if not report.get('error') and all(row['errorCode'] == '0' and row['rowCount'] for row in report['results'] if row['id'] not in {'login', 'logout'}) else 1
