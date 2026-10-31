"""本文件验证供应商级频控、跨重启预算、缓存、限流熔断和无隐含重定向。"""

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from stocksieve.errors import DomainError
from stocksieve.sources import RateGate, SourceDeferred, SourceHTTP


def test_budget_survives_restart_and_crosses_shanghai_midnight(tmp_path):
    path = tmp_path / 'rate.sqlite'
    now = datetime(2026, 10, 30, 15, 59, 30, tzinfo=timezone.utc).timestamp()
    first = RateGate(path, daily_limit=2)
    first.reserve('sina', now)
    with pytest.raises(SourceDeferred):
        RateGate(path, daily_limit=2).reserve('sina', now + 1)
    first.reserve('sina', now + 10)
    with pytest.raises(SourceDeferred):
        RateGate(path, daily_limit=2).reserve('sina', now + 20)
    first.reserve('sina', now + 31)


def test_cache_and_vendor_aliases_share_real_request_budget(tmp_path):
    calls = []
    def transport(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={'value': 1})
    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        http = SourceHTTP(RateGate(tmp_path / 'rate.sqlite'), tmp_path / 'cache', client)
        assert await http.get_json('quotes', 'https://finance.sina.com.cn/data') == {'value': 1}
        assert await http.get_json('different_alias', 'https://finance.sina.com.cn/data') == {'value': 1}
        with pytest.raises(SourceDeferred):
            await http.get_json('other_purpose', 'https://quotes.sina.cn/another')
        await http.close()
    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize('status', [403, 418, 429])
def test_rate_limit_does_not_retry_and_honors_cooldown(tmp_path, status):
    calls = []
    def transport(request):
        calls.append(request.url)
        return httpx.Response(status, headers={'Retry-After': '172800'})
    async def run():
        http = SourceHTTP(RateGate(tmp_path / 'rate.sqlite'), tmp_path / 'cache', httpx.AsyncClient(transport=httpx.MockTransport(transport)))
        with pytest.raises(DomainError):
            await http.get_json('provider', 'https://example.test/data')
        with pytest.raises(SourceDeferred):
            await http.get_json('provider', 'https://example.test/data')
        await http.close()
    asyncio.run(run())
    assert len(calls) == 1


def test_html_and_redirects_are_not_treated_as_valid_data(tmp_path):
    async def run():
        for index, response in enumerate([httpx.Response(200, text='<html>captcha</html>'), httpx.Response(302, headers={'Location': 'https://other.test/'})]):
            calls = []
            def transport(request):
                calls.append(request.url)
                return response
            http = SourceHTTP(RateGate(tmp_path / f'rate{index}.sqlite'), tmp_path / f'cache{index}', httpx.AsyncClient(transport=httpx.MockTransport(transport), follow_redirects=True))
            with pytest.raises(DomainError):
                await http.get_json('provider', 'https://example.test/')
            assert len(calls) == 1
            await http.close()
    asyncio.run(run())


def test_public_document_cache_retains_original_fetch_time(tmp_path):
    calls = []
    def transport(request):
        calls.append(request.url)
        return httpx.Response(200, text='<html>公开资料</html>')
    async def run():
        http = SourceHTTP(RateGate(tmp_path / 'rate.sqlite'), tmp_path / 'cache', httpx.AsyncClient(transport=httpx.MockTransport(transport)))
        first = await http.document('provider', 'https://www.sse.com.cn/document')
        second = await http.document('different-purpose', 'https://www.sse.com.cn/document')
        assert first == second and len(calls) == 1
        await http.close()
    asyncio.run(run())
