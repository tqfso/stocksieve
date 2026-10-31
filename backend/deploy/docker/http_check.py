"""本文件通过真实 HTTP 请求验收运行中的后端，明确区分健康、登录和数据流水线状态。"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


class CheckFailed(Exception):
    """检查失败只显示路径与状态，不回显口令、令牌或完整响应。"""


class NoRedirect(HTTPRedirectHandler):
    """不跟随跳转，避免把测试请求与认证信息发往其他地址。"""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class Client:
    """通过真实网络请求校验状态与追踪信息，不打印响应中的认证数据。"""

    def __init__(self, base: str) -> None:
        parsed = urlsplit(base)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise CheckFailed('后端地址需为不含凭据的 HTTP(S) 地址。')
        self.base = base.rstrip('/')
        self.opener = build_opener(NoRedirect())
        self.token: str | None = None
        self.checks: list[dict[str, Any]] = []

    def request(self, method: str, path: str, payload: Any = None, expected: int = 200) -> Any:
        request_id = 'http_' + uuid4().hex
        headers = {'X-Request-ID': request_id}
        data = None
        if payload is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(payload, ensure_ascii=False).encode()
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        request = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=10)
        except HTTPError as error:
            response = error
        except (URLError, TimeoutError, OSError) as error:
            raise CheckFailed(f'{method} {path} 未能连接后端。') from error
        with response:
            status = response.status
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise CheckFailed(f'{method} {path} 响应超过检查上限。')
            if status != expected:
                raise CheckFailed(f'{method} {path} 返回 {status}，期望 {expected}。')
            if response.headers.get('X-Request-ID') != request_id:
                raise CheckFailed(f'{method} {path} 追踪响应头不一致。')
        if status == 204:
            if raw:
                raise CheckFailed('204响应不应包含正文。')
            result = None
        else:
            try:
                result = json.loads(raw)
                if result['requestId'] != request_id:
                    raise CheckFailed(f'{method} {path} 正文追踪标识不一致。')
            except (ValueError, KeyError, TypeError) as error:
                raise CheckFailed(f'{method} {path} 未返回有效的统一JSON响应。') from error
        self.checks.append({'method': method, 'path': path, 'status': status})
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--username')
    parser.add_argument('--password-env', default='STOCKSIEVE_HTTP_PASSWORD')
    parser.add_argument('--require-data', action='store_true')
    args = parser.parse_args()
    client: Client | None = None
    data_present: bool | None = None
    authenticated = False
    try:
        if args.require_data and not args.username:
            raise CheckFailed('数据验收需要提供登录账号和口令环境变量。')
        client = Client(args.base_url)
        # 1. 无凭据也能检查进程、真实数据库连接和匿名权限。
        client.request('GET', '/health/live')
        client.request('GET', '/health/ready')
        client.request('GET', '/api/v1/auth/me', expected=401)
        client.request('POST', '/api/v1/auth/login', {'username': 'http_probe', 'password': ''}, expected=400)
        if args.username:
            # 2. 有凭据时检查登录及只读业务，空库与已发布数据分开判断。
            password = os.environ.get(args.password_env)
            if not password:
                raise CheckFailed('未找到指定口令环境变量，不尝试猜测口令。')
            login = client.request('POST', '/api/v1/auth/login', {'username': args.username, 'password': password})['data']
            client.token = login['accessToken']
            authenticated = True
            user = client.request('GET', '/api/v1/auth/me')['data']
            strategies = client.request('GET', '/api/v1/strategies')['data']['items']
            if {item['id'] for item in strategies} != {'short', 'swing', 'long', 'trend_wait', 'base_wait'}:
                raise CheckFailed('策略目录未完整返回。')
            market = client.request('GET', '/api/v1/market/status')['data']
            data_present = market['publishedSnapshot'] is not None
            if data_present:
                snapshot_id = market['publishedSnapshot']['id']
                client.request('GET', '/api/v1/catalog/filters?' + urlencode({'snapshotId': snapshot_id}))
                for strategy in strategies:
                    page = client.request('GET', '/api/v1/opportunities?' + urlencode({'strategy': strategy['id'], 'snapshotId': snapshot_id, 'pageSize': 1}))['data']
                    if page['items']:
                        stock_id = page['items'][0]['stock']['stockId']
                        client.request('GET', f'/api/v1/stocks/{stock_id}?' + urlencode({'snapshotId': snapshot_id}))
                        client.request('GET', f'/api/v1/stocks/{stock_id}/candles?' + urlencode({'snapshotId': snapshot_id, 'limit': 5}))
                        break
            else:
                client.request('GET', '/api/v1/opportunities?strategy=short', expected=409)
                if args.require_data:
                    raise CheckFailed('尚无已发布快照，不能宣称真实数据流水线通过。')
            client.request('GET', '/api/v1/admin/users', expected=200 if user['role'] == 'admin' else 403)
            client.request('GET', '/api/v1/analyses')
        if client.token:
            # 3. 校验退出撤销后再输出本次实际完成的检查范围。
            client.request('POST', '/api/v1/auth/logout', expected=204)
            client.request('GET', '/api/v1/auth/me', expected=401)
            client.token = None
        print(json.dumps({'httpChecksPassed': len(client.checks), 'authenticatedChecks': authenticated,
                          'publishedDataPresent': data_present, 'checks': client.checks}, ensure_ascii=False, indent=2))
        if not authenticated:
            print('仅完成基础HTTP检查；登录、任务受理和真实数据流水线尚未验收。')
        elif data_present is False:
            print('已验证空库状态的HTTP行为；真实数据同步尚未验收。')
        print('本脚本不执行行情同步或模型调用，不能代替任务受理与真实数据流水线验收。')
        return 0
    except CheckFailed as error:
        print(f'HTTP验收未通过：{error}')
        return 1
    except (KeyError, TypeError, ValueError):
        print('HTTP验收未通过：后端响应结构不符合接口约定。')
        return 1
    finally:
        if client and client.token:
            try:
                client.request('POST', '/api/v1/auth/logout', expected=204)
            except CheckFailed:
                print('测试会话退出未确认，请留意会话有效期。')


if __name__ == '__main__':
    raise SystemExit(main())
