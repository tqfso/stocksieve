"""本文件用隔离浏览器上下文验证真实前端和HTTP接口，不输出口令、令牌或登录响应。"""

from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, urlencode, urlsplit

from playwright.sync_api import expect, sync_playwright


@contextmanager
def browser_page() -> Iterator[Any]:
    """优先启动本机浏览器；已有调试浏览器只新建隔离上下文，不读取或关闭其他页面。"""
    with sync_playwright() as playwright:
        owned = True
        try:
            browser = playwright.chromium.launch(executable_path='/snap/bin/chromium', headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage'], timeout=15000)
        except Exception:
            owned = False
            browser = playwright.chromium.connect_over_cdp('http://127.0.0.1:9222', timeout=5000)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='zh-CN')
        try:
            page = context.new_page()
            page.set_default_timeout(15000)
            yield page
        finally:
            context.close()
            if owned:
                browser.close()


def run_check(base: str, username: str, password: str, *, require_data: bool = False) -> dict[str, Any]:
    """验证账号、行情和页面；有数据时额外生成明确标记的后端模拟分析。"""
    __tracebackhide__ = True
    stage = '启动浏览器'
    checked: list[str] = []
    paths: set[str] = set()
    failures: list[str] = []
    try:
        with browser_page() as page:
            page.on('pageerror', lambda error: failures.append(type(error).__name__))
            page.on('request', lambda request: paths.add(request.url.split('/api/v1', 1)[-1].split('?', 1)[0]) if '/api/v1/' in request.url else None)
            stage = '登录'
            page.goto(base.rstrip('/') + '/#/login', wait_until='networkidle')
            page.get_by_placeholder('请输入账号', exact=True).fill(username)
            page.get_by_placeholder('请输入密码', exact=True).fill(password)
            page.get_by_role('button', name='登录工作台', exact=True).click()
            page.wait_for_url('**/#/opportunities*')
            expect(page.get_by_role('heading', name='发现值得研究的机会')).to_be_visible()
            checked.append('服务器登录')
            if require_data:
                stage = '机会列表与固定快照'
                expect(page.locator('.strategy-tabs b').first).not_to_have_text('—')
                tabs = page.get_by_role('tab')
                chosen = False
                for index in range(tabs.count()):
                    if int(tabs.nth(index).locator('b').inner_text()) > 0:
                        tabs.nth(index).click()
                        expect(page.locator('.stock-name').first).to_be_visible()
                        page.locator('.stock-name').first.click()
                        chosen = True
                        break
                if not chosen:
                    page.goto(base.rstrip('/') + '/#/stocks/SSE.600000', wait_until='networkidle')
                expect(page.locator('.candlestick-chart')).to_be_visible()
                if chosen:
                    expect(page.locator('.signal-reason')).to_be_visible()
                checked.append('机会列表、详情、日K线和规则信号')
                stage = '周期K线'
                page.get_by_role('button', name='周 K', exact=True).click()
                expect(page.locator('.ohlc-strip')).to_be_visible()
                page.get_by_role('button', name='年 K', exact=True).click()
                expect(page.locator('.ohlc-strip')).to_be_visible()
                checked.append('周K线和年K线')
                stage = '后端模拟分析'
                page.get_by_role('button', name='AI 分析报告').click()
                page.get_by_role('button', name='筹码', exact=False).click()
                page.get_by_role('button', name=re.compile(r'^(生成分析|重新生成)$')).click()
                expect(page.locator('.report-heading h2')).to_contain_text('模拟', timeout=30000)
                expect(page.locator('.chip-distribution')).to_be_visible()
                page.reload(wait_until='networkidle')
                page.get_by_role('button', name='AI 分析报告').click()
                page.get_by_role('button', name='筹码', exact=False).click()
                expect(page.locator('.report-heading h2')).to_contain_text('模拟')
                checked.append('后端模拟筹码报告及刷新恢复')
            else:
                stage = '空库状态'
                expect(page.get_by_text('收盘数据尚未准备好', exact=True)).to_be_visible()
                checked.append('真实空库准备状态')
            stage = '用户管理'
            page.get_by_role('link', name='用户管理', exact=True).click()
            expect(page.get_by_role('heading', name='为每位研究者开放工作台')).to_be_visible()
            expect(page.locator('.data-table tbody')).to_contain_text(username)
            page.reload(wait_until='networkidle')
            expect(page.locator('.data-table tbody')).to_contain_text(username)
            checked.append('用户管理及会话刷新恢复')
            stage = '数据管理'
            page.get_by_role('link', name='数据管理', exact=True).click()
            expect(page.get_by_role('heading', name='让研究从完整数据开始')).to_be_visible()
            checked.append('服务器任务管理')
            stage = '退出'
            page.locator('.sidebar-user').click()
            page.wait_for_url('**/#/login*')
            assert page.evaluate("sessionStorage.getItem('stocksieve.auth.v1')") is None
            checked.append('服务器退出与本地会话清理')
            if failures:
                raise RuntimeError('页面发生脚本错误。')
        return {'browserChecks': checked, 'apiPaths': sorted(paths), 'pageErrors': len(failures)}
    except Exception:
        raise RuntimeError(f'浏览器验收未通过，阶段：{stage}。') from None


def run_concept_check(base: str, username: str, password: str, *, screenshot: Path | None = None) -> dict[str, Any]:
    """实际选择一个已取得的题材，校验请求、成员归属和刷新恢复；不生成分析或改写行情。"""
    __tracebackhide__ = True
    stage = '登录与题材选项'
    try:
        with browser_page() as page:
            page.goto(base.rstrip('/') + '/#/login', wait_until='networkidle')
            page.get_by_placeholder('请输入账号', exact=True).fill(username)
            page.get_by_placeholder('请输入密码', exact=True).fill(password)
            page.get_by_role('button', name='登录工作台', exact=True).click()
            page.wait_for_url('**/#/opportunities*')
            choice = page.evaluate("""async () => {
                const token = JSON.parse(sessionStorage.getItem('stocksieve.auth.v1')).accessToken;
                const get = async path => {
                    const response = await fetch('/api/v1'+path, {headers:{Authorization:'Bearer '+token}});
                    if (!response.ok) throw new Error('接口未成功');
                    return (await response.json()).data;
                };
                const status = await get('/market/status');
                const id = status.publishedSnapshot.id;
                const filters = await get('/catalog/filters?snapshotId='+id);
                if (!filters.concepts.length || filters.coverage.concepts === 'unavailable') throw new Error('题材尚不可用');
                for (const strategy of ['short','swing','long','trend_wait','base_wait']) {
                    const result = await get('/opportunities?strategy='+strategy+'&pageSize=100&snapshotId='+id);
                    const stock = result.items.find(row => row.stock.concepts.length && row.stock.industry);
                    if (stock) return {snapshotId:id, strategy, concept:stock.stock.concepts[0], industry:stock.stock.industry};
                }
                return {snapshotId:id, strategy:'short', concept:filters.concepts[0], industry:filters.industries[0] ?? null};
            }""")
            stage = '选择题材与真实筛选请求'
            page.goto(base.rstrip('/') + '/#/opportunities?' + urlencode({'strategy': choice['strategy'], 'snapshotId': choice['snapshotId']}), wait_until='networkidle')
            if choice['industry']:
                stage = '输入定位行业'
                industry = page.locator('label').filter(has_text=re.compile(r'^\s*行业')).locator('.el-select')
                industry.click()
                industry.locator('.el-select__input').fill(choice['industry']['name'])
                expect(page.locator('[role="option"]:visible')).to_have_count(1)
                with page.expect_response(lambda response: '/api/v1/opportunities' in response.url and parse_qs(urlsplit(response.url).query).get('industryId') == [choice['industry']['id']]):
                    page.get_by_role('option', name=choice['industry']['name'], exact=True).click()
            stage = '输入定位题材'
            control = page.locator('label').filter(has_text='题材 / 概念').locator('.el-select')
            expect(control).not_to_have_class(re.compile(r'is-disabled'))
            control.click()
            control.locator('.el-select__input').fill(choice['concept']['name'])
            expect(page.locator('[role="option"]:visible')).to_have_count(1)
            def matching(response: Any) -> bool:
                return '/api/v1/opportunities' in response.url and parse_qs(urlsplit(response.url).query).get('conceptId') == [choice['concept']['id']]
            with page.expect_response(matching) as received:
                page.get_by_role('option', name=choice['concept']['name'], exact=True).click()
            response = received.value
            assert response.status == 200
            result = response.json()['data']
            assert result['snapshot']['id'] == choice['snapshotId']
            assert all(any(concept['id'] == choice['concept']['id'] for concept in item['stock']['concepts']) for item in result['items'])
            if choice['industry']:
                assert all((item['stock']['industry'] or {}).get('id') == choice['industry']['id'] for item in result['items'])
            if result['total']:
                expect(page.locator('.stock-name').first).to_be_visible()
            stage = '刷新恢复题材筛选'
            page.reload(wait_until='networkidle')
            expect(control).to_contain_text(choice['concept']['name'])
            if choice['industry']:
                expect(industry).to_contain_text(choice['industry']['name'])
            if screenshot:
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshot), full_page=True)
            page.locator('.sidebar-user').click()
            page.wait_for_url('**/#/login*')
            return {'industry': (choice['industry'] or {}).get('name'), 'concept': choice['concept']['name'], 'matchedStocks': result['total'], 'snapshotId': choice['snapshotId'], 'typedSearchChecked': True}
    except Exception:
        raise RuntimeError('题材浏览器验收未通过，阶段：' + stage + '。') from None


def run_strategy_check(base: str, username: str, password: str) -> dict[str, Any]:
    """只读核对五种策略的接口与页面计数，并检查空结果、切换和重置筛选。"""
    __tracebackhide__ = True
    stage = '登录与读取五种策略'
    try:
        with browser_page() as page:
            page.goto(base.rstrip('/') + '/#/login', wait_until='networkidle')
            page.get_by_placeholder('请输入账号', exact=True).fill(username)
            page.get_by_placeholder('请输入密码', exact=True).fill(password)
            page.get_by_role('button', name='登录工作台', exact=True).click()
            page.wait_for_url('**/#/opportunities*')
            results = page.evaluate("""async () => {
                const token = JSON.parse(sessionStorage.getItem('stocksieve.auth.v1')).accessToken;
                const get = async path => {
                    const response = await fetch('/api/v1'+path, {headers:{Authorization:'Bearer '+token}});
                    if (!response.ok) throw new Error('接口未成功');
                    return (await response.json()).data;
                };
                const status = await get('/market/status');
                const id = status.publishedSnapshot.id;
                const results = {};
                for (const strategy of ['short','swing','long','trend_wait','base_wait']) {
                    const result = await get('/opportunities?strategy='+strategy+'&pageSize=6&snapshotId='+id);
                    results[strategy] = {total:result.total, counts:result.summary.strategyCounts};
                }
                return {snapshotId:id, strategies:results};
            }""")
            counts = {key: result['total'] for key, result in results['strategies'].items()}
            assert all(result['counts'] == counts for result in results['strategies'].values())
            page.goto(base.rstrip('/') + '/#/opportunities?' + urlencode({'strategy': 'short', 'snapshotId': results['snapshotId']}), wait_until='networkidle')
            tabs = page.get_by_role('tab')
            for index, (key, count) in enumerate(counts.items()):
                stage = '切换策略：' + key
                tabs.nth(index).click()
                expect(tabs.nth(index)).to_have_attribute('aria-selected', 'true')
                expect(page.locator('.strategy-tabs b')).to_have_text([str(value) for value in counts.values()])
                expect(page.locator('.results-caption')).to_contain_text(f'{count} 只符合当前条件')
                expect(page.locator('.stock-name')).to_have_count(min(6, count))
                if count == 0:
                    expect(page.locator('.results-panel .state-panel')).to_be_visible()
                    expect(page.locator('.results-panel .state-panel h3')).to_contain_text('当日暂无')
                    expect(page.locator('.results-panel .state-panel')).to_contain_text('当日没有股票同时满足这类策略的全部条件')
                    expect(page.locator('.results-panel .state-panel button')).to_have_count(0)
            stage = '搜索无结果与重置筛选'
            page.get_by_placeholder('股票名称或代码', exact=True).fill('__stocksieve_no_match__')
            expect(page.locator('.strategy-tabs b')).to_have_text(['0'] * 5)
            expect(page.locator('.stock-name')).to_have_count(0)
            expect(page.locator('.results-panel .state-panel h3')).to_have_text('暂时没有符合条件的股票')
            page.locator('.results-panel').get_by_role('button', name='重置筛选', exact=True).click()
            expect(page.locator('.strategy-tabs b')).to_have_text([str(value) for value in counts.values()])
            expect(page.get_by_placeholder('股票名称或代码', exact=True)).to_have_value('')
            page.reload(wait_until='networkidle')
            expect(page.locator('.strategy-tabs b')).to_have_text([str(value) for value in counts.values()])
            expect(page.get_by_placeholder('股票名称或代码', exact=True)).to_have_value('')
            assert 'search' not in parse_qs(urlsplit(page.url.split('#', 1)[-1]).query)
            page.locator('.sidebar-user').click()
            page.wait_for_url('**/#/login*')
            return {'snapshotId': results['snapshotId'], 'strategyCounts': counts, 'filterResetChecked': True}
    except Exception:
        raise RuntimeError('策略浏览器验收未通过，阶段：' + stage + '。') from None


def run_access_check(base: str, username: str, password: str) -> None:
    """只供隔离测试：验证新增普通用户、服务端权限和改密撤销；不用于正式账号。"""
    __tracebackhide__ = True
    stage = '隔离账号登录'
    try:
        with browser_page() as page:
            def sign_in(account: str, secret: str) -> None:
                page.goto(base.rstrip('/') + '/#/login', wait_until='networkidle')
                page.get_by_placeholder('请输入账号', exact=True).fill(account)
                page.get_by_placeholder('请输入密码', exact=True).fill(secret)
                page.get_by_role('button', name='登录工作台', exact=True).click()
                page.wait_for_url('**/#/opportunities*')
            def sign_out() -> None:
                page.locator('.sidebar-user').click()
                page.wait_for_url('**/#/login*')
            sign_in(username, password)
            stage = '创建普通用户'
            page.get_by_role('link', name='用户管理', exact=True).click()
            page.get_by_role('button', name='新增普通用户', exact=False).click()
            dialog = page.get_by_role('dialog')
            dialog.get_by_placeholder('3～20 位，以字母开头').fill('e2e_reader')
            dialog.get_by_placeholder('请输入用户名称').fill('隔离测试读者')
            dialog.locator('input[type=password]').nth(0).fill('readerpass123')
            dialog.locator('input[type=password]').nth(1).fill('readerpass123')
            dialog.get_by_role('button', name='创建用户', exact=True).click()
            expect(dialog).not_to_be_visible()
            expect(page.locator('.data-table tbody')).to_contain_text('e2e_reader')
            sign_out()
            stage = '普通用户权限'
            sign_in('e2e_reader', 'readerpass123')
            assert page.get_by_role('link', name='用户管理', exact=True).count() == 0
            page.goto(base.rstrip('/') + '/#/admin/data', wait_until='networkidle')
            page.wait_for_url('**/#/opportunities*')
            status = page.evaluate("""async () => {
                const token = JSON.parse(sessionStorage.getItem('stocksieve.auth.v1')).accessToken;
                const response = await fetch('/api/v1/admin/users', {headers:{Authorization:'Bearer '+token}});
                return response.status;
            }""")
            assert status == 403
            sign_out()
            stage = '修改密码及旧密码失效'
            sign_in(username, password)
            page.get_by_role('link', name='修改密码', exact=True).click()
            page.get_by_label('当前密码', exact=True).fill(password)
            page.get_by_label('新密码', exact=True).fill('changedpass123')
            page.get_by_label('确认新密码', exact=True).fill('changedpass123')
            page.get_by_role('button', name='保存并重新登录', exact=True).click()
            page.wait_for_url('**/#/login*')
            page.get_by_placeholder('请输入账号', exact=True).fill(username)
            page.get_by_placeholder('请输入密码', exact=True).fill(password)
            page.get_by_role('button', name='登录工作台', exact=True).click()
            expect(page.get_by_text('账号或密码错误。', exact=True)).to_be_visible()
            page.get_by_placeholder('请输入密码', exact=True).fill('changedpass123')
            page.get_by_role('button', name='登录工作台', exact=True).click()
            page.wait_for_url('**/#/opportunities*')
            sign_out()
    except Exception:
        raise RuntimeError(f'隔离账号浏览器验收未通过，阶段：{stage}。') from None
