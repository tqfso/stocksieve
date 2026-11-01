"""本文件用单次受控请求核验免费概念目录或成员，复用正式频控与缓存，不写业务表。"""

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from stocksieve.modules.ingestion.application.ports import SourceDeferred
from stocksieve.modules.ingestion.infrastructure.http import SourceHTTP
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
from stocksieve.shared.errors import DomainError
from stocksieve.shared.settings import Settings


def check_tdx(endpoint: str = 'sdk-default') -> None:
    """只连接SDK默认公开服务器，关闭心跳和自动重试；握手与查询逐包计次。"""
    from pytdx.hq import TdxHq_API
    settings = Settings.from_env()
    policy = SourcePolicy(settings.rate_dir, settings.request_interval, settings.source_daily_limit)
    api = TdxHq_API(heartbeat=False, auto_retry=False, raise_exception=True)

    class PacedSocket:
        def __init__(self, original: Any) -> None:
            self.original = original

        def __getattr__(self, name: str) -> Any:
            return getattr(self.original, name)

        def send(self, payload: bytes) -> int:
            while True:
                try:
                    policy.reserve('tdx')
                    break
                except SourceDeferred as waiting:
                    delay = waiting.until.timestamp() - time.time()
                    if delay > 30:
                        raise
                    time.sleep(max(.01, delay))
            return self.original.send(payload)

    try:
        policy.reserve('tdx')
        api.need_setup = False
        if endpoint == 'sdk-first':
            from pytdx.config.hosts import hq_hosts
            _, host, port = hq_hosts[0]
            api.connect(host, port, time_out=10)
        else:
            api.connect(time_out=10)
        print(json.dumps({'source': 'tdx', 'endpoint': endpoint, 'connected': True}), flush=True)
        api.client = PacedSocket(api.client)
        api.setup()
        result = api.get_block_info_meta('block_gn.dat')
        print(json.dumps({'source': 'tdx-sdk-default', 'file': 'block_gn.dat', 'size': result.get('size'),
                          'hash': result.get('hash_value', b'').decode(errors='replace')}, ensure_ascii=False))
    except SourceDeferred:
        raise
    except Exception as error:
        policy.block('tdx', 3600)
        raise DomainError('SOURCE_UNAVAILABLE', '通达信公开服务器连接或目录元资料核验失败，已暂停该供应商。', 503) from error
    finally:
        try:
            api.disconnect()
        except Exception:
            pass


async def check(kind: str, board: str | None, batch_size: int = 2) -> None:
    """目录和成员均只取第一页5项；验证样本不表示已完成全市场覆盖。"""
    settings = Settings.from_env()
    policy = SourcePolicy(settings.rate_dir, settings.request_interval, settings.source_daily_limit)
    client = SourceHTTP(policy, settings.state_dir / 'source-cache')
    async def response_status(response: Any) -> None:
        print(json.dumps({'status': response.status_code, 'path': response.request.url.path,
                          'location': response.headers.get('location'), 'retryAfter': response.headers.get('retry-after')}, ensure_ascii=False))
    client.client.event_hooks['response'].append(response_status)
    try:
        if kind == 'baidu':
            symbols = ['000001', '300750']
            if batch_size > 2:
                metadata = json.loads((settings.state_dir / 'source-cache/tencent-metadata.json').read_text())
                stored = metadata.get('values', metadata)
                universe = list(dict.fromkeys(['600000', '000001', '300750', '688981', '302132'] + [symbol[2:] for symbol in sorted(stored) if symbol.startswith(('sh', 'sz'))]))
                symbols = universe[:batch_size]
            value = await client.get_json('baidu', 'https://finance.pae.baidu.com/api/getrelatedblock',
                {'stock': json.dumps([{'code': symbol, 'market': 'ab', 'type': 'stock'} for symbol in symbols], separators=(',', ':')),
                 'finClientType': 'pc'}, cache_seconds=86400)
            result = value.get('Result') or {}
            print(json.dumps({'source': 'baidu', 'errorCode': value.get('ResultCode'), 'message': value.get('ResultMsg'),
                              'requested': len(symbols), 'returned': len(result), 'missing': sorted(set(symbols) - set(result)),
                              'conceptsKnown': sum(any(group['name'] == '概念' for group in groups) for groups in result.values()),
                              'sample': {code: [{'name': group['name'], 'count': len(group.get('list', []))} for group in groups] for code, groups in list(result.items())[:5]}}, ensure_ascii=False))
            return
        if kind == 'em-board-relations':
            value = await client.get_json('eastmoney', 'https://datacenter.eastmoney.com/securities/api/data/v1/get',
                {'reportName': 'RPT_F10_CORETHEME_BOARDTYPE', 'columns': 'ALL', 'pageSize': 5, 'pageNumber': 1,
                 'source': 'WEB', 'client': 'WEB'}, cache_seconds=86400)
            result = value.get('result') or {}
            print(json.dumps({'source': 'eastmoney', 'kind': kind, 'success': value.get('success'), 'code': value.get('code'),
                              'message': value.get('message'), 'count': result.get('count'), 'pages': result.get('pages'),
                              'sample': (result.get('data') or [])[:3]}, ensure_ascii=False))
            return
        if kind == 'ths-pagination':
            content, _ = await client.document('ths', 'https://s.thsi.cn/js/q/newq/common_v2.1.min.js')
            text = content.decode('utf-8', errors='replace')
            for term in ('baseUrl', 'requestQuery', 'ajax', 'mpager'):
                position = text.find(term)
                print(json.dumps({'term': term, 'context': text[max(0, position-180):position+500] if position >= 0 else ''}, ensure_ascii=False))
            return
        if kind in ('ths-page', 'ths-page2', 'ths-full-page2'):
            url = 'https://q.10jqka.com.cn/gn/detail/code/301558/' if kind == 'ths-page' else 'https://q.10jqka.com.cn/gn/detail/field/199112/order/desc/page/2/code/301558/' if kind == 'ths-full-page2' else 'https://q.10jqka.com.cn/gn/detail/field/199112/order/desc/page/2/ajax/1/code/301558'
            content, fetched = await client.document('ths', url)
            text = content.decode('gb18030', errors='replace')
            print(json.dumps({'source': 'ths', 'fetchedAt': fetched.isoformat(), 'bytes': len(content),
                              'title': re.findall(r'<title>(.*?)</title>', text, re.S)[:1],
                              'securityLinks': len(set(re.findall(r'stockpage\.10jqka\.com\.cn/(\d{6})', text))),
                              'conceptLinks': len(set(re.findall(r'/gn/detail/code/(\d+)', text))),
                              'pagination': re.findall(r'class="page_info">([^<]+)', text),
                              'sampleCodes': sorted(set(re.findall(r'stockpage\.10jqka\.com\.cn/(\d{6})', text)))[:3]}, ensure_ascii=False))
            return
        params: dict[str, Any] = {'pn': 1, 'pz': 5, 'po': 1, 'np': 1, 'fltt': 2, 'invt': 2,
                                  'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
                                  'fid': 'f12', 'fields': 'f12,f13,f14',
                                  'fs': f'b:{board} f:!50' if kind == 'members' else 'm:90 t:3 f:!50'}
        host = '29' if kind == 'members' else '79'
        value = await client.get_json('eastmoney', f'https://{host}.push2.eastmoney.com/api/qt/clist/get', params, cache_seconds=86400)
        data = value.get('data') if isinstance(value, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get('diff'), (dict, list)):
            raise DomainError('DATA_NOT_AVAILABLE', '概念渠道未返回可核验的目录或成员结构。', 503)
        items = data['diff']
        if isinstance(items, dict):
            items = list(items.values())
        print(json.dumps({'source': 'eastmoney', 'kind': kind, 'reportedTotal': data.get('total'), 'sample': items[:3]}, ensure_ascii=False))
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('list', 'members', 'em-board-relations', 'baidu', 'tdx-meta', 'ths-page', 'ths-page2', 'ths-full-page2', 'ths-pagination'))
    parser.add_argument('--wait-seconds', type=int, default=0)
    parser.add_argument('--batch-size', type=int, choices=(2, 20, 50), default=2)
    parser.add_argument('--tdx-endpoint', choices=('sdk-default', 'sdk-first'), default='sdk-default')
    parser.add_argument('--board')
    args = parser.parse_args()
    if args.kind == 'members' and not args.board:
        parser.error('成员核验需指定实际目录中的--board编码。')
    deadline = time.monotonic() + max(0, args.wait_seconds)
    last_wait = None
    while True:
        try:
            if args.kind == 'tdx-meta':
                check_tdx(args.tdx_endpoint)
            else:
                asyncio.run(check(args.kind, args.board, args.batch_size))
            break
        except SourceDeferred as waiting:
            delay = waiting.until.timestamp() - time.time()
            if last_wait != waiting.until:
                print('遵守频控，下次可执行：' + waiting.until.isoformat(), flush=True)
                last_wait = waiting.until
            if time.monotonic() + delay > deadline:
                return 3
            time.sleep(min(30, max(.01, delay)))
        except DomainError as error:
            print(error.code + ': ' + error.message)
            if error.__cause__:
                print('诊断：' + type(error.__cause__).__name__ + ' ' + str(error.__cause__)[:180])
            return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
