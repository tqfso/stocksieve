"""本文件用隔离浏览器上下文验证真实前端和HTTP接口，不输出口令、令牌或登录响应。"""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, urlencode, urlsplit

from playwright.sync_api import expect, sync_playwright


@contextmanager
def browser_page() -> Iterator[Any]:
    """优先启动配套Chromium；回退时仅新建隔离上下文，不读取或关闭其他页面。"""
    with sync_playwright() as playwright:
        owned = True
        try:
            options: dict[str, Any] = {'headless': True, 'args': ['--no-sandbox', '--disable-dev-shm-usage'], 'timeout': 15000}
            if not Path(playwright.chromium.executable_path).is_file():
                options['executable_path'] = '/snap/bin/chromium'
            browser = playwright.chromium.launch(**options)
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
                stage = '独立筹码与统一模拟报告'
                page.get_by_role('button', name='筹码分布', exact=True).click()
                expect(page.locator('.chip-distribution')).to_be_visible()
                page.get_by_role('button', name='AI 分析报告').click()
                page.get_by_role('button', name=re.compile(r'^(生成报告|重新生成)$')).click()
                expect(page.locator('.report-heading h2')).to_contain_text('模拟', timeout=30000)
                expect(page.locator('.report-chapters .chapter')).to_have_count(7)
                assert page.locator('.analysis-panel .chip-distribution').count() == 0
                page.reload(wait_until='networkidle')
                expect(page.locator('.report-heading h2')).to_contain_text('模拟')
                checked.append('独立筹码、统一报告及刷新恢复')
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
                page.screenshot(path=str(screenshot), full_page=True, animations='disabled')
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
            crypto_capabilities = page.evaluate("""() => ({secureContext:window.isSecureContext,
                randomUUID:typeof globalThis.crypto?.randomUUID === 'function',
                getRandomValues:typeof globalThis.crypto?.getRandomValues === 'function'})""")
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
                    results[strategy] = {total:result.total, counts:result.summary.strategyCounts, stockId:result.items[0]?.stock.stockId};
                }
                return {snapshotId:id, strategies:results};
            }""")
            counts = {key: result['total'] for key, result in results['strategies'].items()}
            assert all(result['counts'] == counts for result in results['strategies'].values())
            stage = '已清理的快照链接恢复'
            page.goto(base.rstrip('/') + '/#/opportunities?strategy=short&snapshotId=11111111-1111-4111-8111-111111111111', wait_until='networkidle')
            expect(page.locator('.strategy-tabs b')).to_have_text([str(value) for value in counts.values()])
            expect(page).to_have_url(re.compile(re.escape(results['snapshotId'])))
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
            stage = '共用K线、收起和页签切换'
            stock_id = next((result.get('stockId') for result in results['strategies'].values() if result.get('stockId')), None)
            if stock_id:
                page.goto(base.rstrip('/') + '/#/stocks/' + stock_id + '?' + urlencode({'entry': 'analysis', 'tab': 'ai', 'snapshotId': results['snapshotId']}), wait_until='networkidle')
                chart = page.locator('.candlestick-chart')
                expect(chart).to_be_visible()
                expect(page.locator('.chart-panel')).to_have_count(1)
                expect(page.locator('.chart-heading')).to_contain_text('行情截至')
                page.get_by_role('button', name='周 K', exact=True).click()
                expect(chart).to_have_attribute('aria-label', re.compile('周K线'))
                average = page.locator('.ma-legend input').nth(2)
                average.uncheck()
                # 用当前SVG节点确认切页签和收起均没有卸载或重建图表。
                chart.evaluate("element => element.dataset.browserCheck = 'shared-chart'")
                for name in ('规则筛选结果', 'AI 分析报告'):
                    page.get_by_role('button', name=name, exact=name == '规则筛选结果').click()
                    expect(chart).to_be_visible()
                    expect(chart).to_have_attribute('data-browser-check', 'shared-chart')
                    expect(page.get_by_role('button', name='周 K', exact=True)).to_have_class(re.compile('active'))
                    expect(average).not_to_be_checked()
                page.get_by_role('button', name='收起 K 线', exact=True).click()
                expect(chart).not_to_be_visible()
                expect(page.get_by_role('button', name='展开 K 线', exact=True)).to_have_attribute('aria-expanded', 'false')
                page.get_by_role('button', name='规则筛选结果', exact=True).click()
                expect(chart).not_to_be_visible()
                page.get_by_role('button', name='展开 K 线', exact=True).click()
                expect(chart).to_be_visible()
                expect(chart).to_have_attribute('data-browser-check', 'shared-chart')
                expect(average).not_to_be_checked()
                page.set_viewport_size({'width': 390, 'height': 844})
                page.get_by_role('button', name='AI 分析报告', exact=False).click()
                expect(chart).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                page.set_viewport_size({'width': 1440, 'height': 1000})
            stage = '历史回补期间日常更新入口'
            page.get_by_role('link', name='数据管理', exact=True).click()
            expect(page.get_by_role('heading', name='让研究从完整数据开始')).to_be_visible()
            default_source = page.evaluate("""async () => {
                const token = JSON.parse(sessionStorage.getItem('stocksieve.auth.v1')).accessToken;
                const response = await fetch('/api/v1/market/status', {headers:{Authorization:'Bearer '+token}});
                if (!response.ok) throw new Error('行情状态查询失败');
                const status = (await response.json()).data;
                return status.sources.find(source => source.id === status.defaultSource && source.available && source.supportedSyncKinds.includes('daily')) ?? null;
            }""")
            if default_source:
                expect(page.locator('.summary-card').filter(has=page.get_by_role('heading', name='收盘行情与策略', exact=True)).locator('.el-select')).to_contain_text(default_source['name'])
            only_backfill = page.evaluate("""async () => {
                const token = JSON.parse(sessionStorage.getItem('stocksieve.auth.v1')).accessToken;
                const response = await fetch('/api/v1/admin/sync-jobs', {headers:{Authorization:'Bearer '+token}});
                if (!response.ok) throw new Error('任务接口未成功');
                const active = (await response.json()).data.items.filter(job => ['queued','running'].includes(job.status));
                return active.length > 0 && active.every(job => job.historyBackfill);
            }""")
            if only_backfill:
                expect(page.locator('.data-table')).to_contain_text('历史行情回补')
                controls = page.get_by_role('button', name='开始更新', exact=True)
                expect(controls).to_have_count(3)
                for index in range(3):
                    expect(controls.nth(index)).to_be_enabled()
            page.locator('.sidebar-user').click()
            page.wait_for_url('**/#/login*')
            return {'snapshotId': results['snapshotId'], 'strategyCounts': counts, 'defaultDailySource': default_source['id'] if default_source else None, 'filterResetChecked': True, 'regularUpdateAvailableDuringBackfill': only_backfill, 'sharedChartChecked': bool(stock_id), 'browserCrypto': crypto_capabilities}
    except Exception:
        raise RuntimeError('策略浏览器验收未通过，阶段：' + stage + '。') from None


def run_readiness_check(base: str, username: str, password: str) -> dict[str, Any]:
    """只读核对管理页的就绪或访问等待提示，以及等待时仍保留已发布行情。"""
    __tracebackhide__ = True
    with browser_page() as page:
        errors: list[str] = []
        page.on('pageerror', lambda error: errors.append(str(error)[:300]))
        page.on('console', lambda message: errors.append(message.text[:500]) if message.type == 'error' and ('import' in message.text or 'Error' in message.text or 'Failed' in message.text) else None)
        page.goto(base.rstrip('/') + '/#/login', wait_until='networkidle')
        page.get_by_placeholder('请输入账号', exact=True).fill(username)
        page.get_by_placeholder('请输入密码', exact=True).fill(password)
        page.get_by_role('button', name='登录工作台', exact=True).click()
        page.wait_for_url('**/#/opportunities*')
        expect(page.get_by_role('heading', name='发现值得研究的机会')).to_be_visible(timeout=15000)
        expect(page.locator('.stock-name').first).to_be_visible(timeout=15000)
        page.get_by_role('link', name='数据管理', exact=True).click(modifiers=[])
        try:
            expect(page.get_by_role('heading', name='让研究从完整数据开始')).to_be_visible(timeout=15000)
        except Exception:
            diagnostic = page.evaluate("""async () => {
                const session = JSON.parse(sessionStorage.getItem('stocksieve.auth.v1') ?? 'null');
                const response = await fetch('/api/v1/auth/me', {headers:{Authorization:'Bearer '+session?.accessToken}});
                const body = await response.json();
                return {storedRole:session?.user?.role, status:response.status, serverRole:body.data?.role};
            }""")
            raise RuntimeError('数据管理未能打开：' + page.url + '；' + str(diagnostic) + '；' + '; '.join(errors)) from None
        job = page.evaluate("""async () => {
            const token = JSON.parse(sessionStorage.getItem('stocksieve.auth.v1')).accessToken;
            const response = await fetch('/api/v1/admin/sync-jobs?kind=daily', {headers:{Authorization:'Bearer '+token}});
            if (!response.ok) throw new Error('任务查询未成功');
            return (await response.json()).data.items.find(job => !job.historyBackfill && ['queued','running'].includes(job.status));
        }""")
        assert job
        expect(page.locator('.active-sync')).not_to_be_visible()
        page.locator(f'tr[data-task-id="{job["id"]}"]').get_by_role('button', name='查看详情', exact=True).click()
        panel = page.locator('.active-sync')
        expect(panel).to_contain_text(job['dataDate'])
        if job['waitReason'] == 'BOARD_DATA_NOT_READY':
            expect(panel.locator('.section-caption')).to_contain_text('数据暂未就绪')
            expect(panel).to_contain_text('目标日期收盘数据暂未就绪')
            expect(panel.locator('.board-readiness article')).to_have_count(len(job['precheck']['boards']))
        elif job['waitReason'] == 'SOURCE_ACCESS_WAIT':
            expect(panel.locator('.section-caption')).to_contain_text('等待数据源访问')
        else:
            raise RuntimeError('本次验收需要处于数据或渠道等待中的收盘任务。')
        page.set_viewport_size({'width': 390, 'height': 844})
        expect(panel).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.locator('.el-drawer__close-btn').click()
        page.get_by_role('link', name='机会筛选', exact=True).click()
        expect(page.locator('.stock-name').first).to_be_visible()
        page.locator('.sidebar-user').click()
        page.wait_for_url('**/#/login*')
        return {'waitReason': job['waitReason'], 'dataDate': job['dataDate'], 'nextRetryAt': job['nextRetryAt'], 'waitingStateChecked': True}


def run_sync_progress_check(base: str, username: str, password: str, *, screenshot: Path | None = None) -> dict[str, Any]:
    """只读核对单一进度入口、按需详情与10秒实际处理速度，不提交同步任务。"""
    __tracebackhide__ = True
    with browser_page() as page:
        page.goto(base.rstrip('/') + '/#/login', wait_until='networkidle')
        page.get_by_placeholder('请输入账号', exact=True).fill(username)
        page.get_by_placeholder('请输入密码', exact=True).fill(password)
        page.get_by_role('button', name='登录工作台', exact=True).click()
        page.wait_for_url('**/#/opportunities*')
        expect(page.locator('.stock-name').first).to_be_visible()
        page.get_by_role('link', name='数据管理', exact=True).click()
        expect(page.locator('.data-table')).to_be_visible()
        page.wait_for_load_state('networkidle')
        expect(page.locator('.active-sync')).not_to_be_visible()
        query = """async () => {
            const token = JSON.parse(sessionStorage.getItem('stocksieve.auth.v1')).accessToken;
            const response = await fetch('/api/v1/admin/sync-jobs?kind=daily', {headers:{Authorization:'Bearer '+token}});
            if (!response.ok) throw new Error('任务查询失败');
            return (await response.json()).data.items.find(job => !job.historyBackfill && job.stockProgress && ['queued','running'].includes(job.status));
        }"""
        first = page.evaluate(query)
        if not first:
            return {'singleProgressEntryChecked': True, 'activeTaskPresent': False}
        market_requests = []
        page.on('request', lambda request: market_requests.append(request.url) if urlsplit(request.url).path == '/api/v1/market/status' else None)
        started = time.monotonic()
        page.wait_for_timeout(10000)
        after = page.evaluate(query)
        elapsed = time.monotonic() - started
        assert after and after['id'] == first['id']
        saved = after['stockProgress']['processedCount']
        previous = first['stockProgress']['processedCount']
        assert saved > previous, '采样期间已保存股票数未增长。'
        expected = min(99.9, round(saved / after['stockProgress']['totalCount'] * 100, 1))
        assert after['progress'] == expected
        assert len(market_requests) == 0, '进度轮询不应重新加载上方行情状态。'
        page.locator(f'tr[data-task-id="{after["id"]}"]').get_by_role('button', name='查看详情', exact=True).click()
        panel = page.locator('.active-sync')
        expect(panel).to_be_visible()
        expect(panel.locator('.el-progress__text')).to_be_visible()
        displayed = float(panel.locator('.el-progress__text').inner_text().strip('%'))
        assert abs(displayed - expected) <= .2
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.locator('.el-drawer__close-btn').click()
        expect(panel).not_to_be_visible()
        if screenshot:
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True, animations='disabled')
        page.locator('.sidebar-user').click()
        page.wait_for_url('**/#/login*')
        return {'singleProgressEntryChecked': True, 'detailsChecked': True, 'marketReloadsDuringPolling': len(market_requests),
            'processedBefore': previous, 'processedAfter': saved, 'elapsedSeconds': round(elapsed, 2),
            'stocksPerSecond': round((saved - previous) / elapsed, 2), 'actualProgress': after['progress']}


def run_sync_dedup_check(base: str, username: str, password: str) -> dict[str, Any]:
    """实际点击已发布日期的更新，确认提示完成、返回原任务且任务列表未新增。"""
    __tracebackhide__ = True
    with browser_page() as page:
        page.goto(base.rstrip('/') + '/#/login', wait_until='networkidle')
        page.get_by_placeholder('请输入账号', exact=True).fill(username)
        page.get_by_placeholder('请输入密码', exact=True).fill(password)
        page.get_by_role('button', name='登录工作台', exact=True).click()
        page.wait_for_url('**/#/opportunities*')
        expect(page.locator('.stock-name').first).to_be_visible()
        page.get_by_role('link', name='数据管理', exact=True).click()
        expect(page.locator('.data-table')).to_be_visible()
        query = """async () => {
            const token = JSON.parse(sessionStorage.getItem('stocksieve.auth.v1')).accessToken;
            const headers = {Authorization:'Bearer '+token};
            const [state, jobs] = await Promise.all([fetch('/api/v1/market/status',{headers}), fetch('/api/v1/admin/sync-jobs?pageSize=100',{headers})]);
            return {state:(await state.json()).data, jobs:(await jobs.json()).data.items};
        }"""
        before = page.evaluate(query)
        assert before['state']['publishedSnapshot']
        day = before['state']['publishedSnapshot']['dataDate']
        panel = page.locator('.summary-card').filter(has=page.get_by_role('heading', name='收盘行情与策略', exact=True))
        entry = panel.get_by_placeholder('目标收盘日期', exact=True)
        entry.fill(day); entry.press('Enter')
        with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/v1/admin/sync-jobs') as captured:
            panel.get_by_role('button', name='开始更新', exact=True).click()
        response = captured.value
        assert response.status == 202
        job = response.json()['data']
        assert job['status'] == 'succeeded' and job['id'] in {item['id'] for item in before['jobs']}
        expect(page.get_by_text(day + '已成功更新，无需重复执行。', exact=True)).to_be_visible()
        after = page.evaluate(query)
        assert {item['id'] for item in after['jobs']} == {item['id'] for item in before['jobs']}
        assert after['state']['publishedSnapshot']['id'] == before['state']['publishedSnapshot']['id']
        page.locator('.sidebar-user').click(); page.wait_for_url('**/#/login*')
        return {'date': day, 'reusedJobId': job['id'], 'noNewTask': True, 'completedMessageChecked': True}


def run_sync_retry_check(base: str, username: str, password: str, job_id: str) -> dict[str, Any]:
    """通过页面继续指定失败任务，确认返回原ID和原进度，并越过原失败位置。"""
    __tracebackhide__ = True
    with browser_page() as page:
        page.goto(base.rstrip('/') + '/#/login', wait_until='networkidle')
        page.get_by_placeholder('请输入账号', exact=True).fill(username)
        page.get_by_placeholder('请输入密码', exact=True).fill(password)
        page.get_by_role('button', name='登录工作台', exact=True).click()
        page.wait_for_url('**/#/opportunities*')
        expect(page.locator('.stock-name').first).to_be_visible()
        page.get_by_role('link', name='数据管理', exact=True).click()
        expect(page.locator('.data-table')).to_be_visible()
        query = """async id => {
            const token = JSON.parse(sessionStorage.getItem('stocksieve.auth.v1')).accessToken;
            const response = await fetch('/api/v1/admin/sync-jobs/'+id, {headers:{Authorization:'Bearer '+token}});
            if (!response.ok) throw new Error('任务查询失败');
            return (await response.json()).data;
        }"""
        before = page.evaluate(query, job_id)
        assert before['status'] == 'failed' and before['stockProgress']
        row = page.locator(f'tr[data-task-id="{job_id}"]')
        with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path.endswith('/' + job_id + '/retry')) as response_info:
            row.get_by_role('button', name='从断点继续', exact=True).click()
        response = response_info.value
        assert response.status == 202
        accepted = response.json()['data']
        assert accepted['id'] == job_id and accepted['stockProgress'] == before['stockProgress']
        # 显式等待已落库股票增加；不能仅把异步predicate返回的Promise当成完成。
        deadline = time.monotonic() + 45
        after = page.evaluate(query, job_id)
        while after['status'] != 'failed' and after['stockProgress']['processedCount'] <= before['stockProgress']['processedCount'] and time.monotonic() < deadline:
            page.wait_for_timeout(1000)
            after = page.evaluate(query, job_id)
        assert after['status'] != 'failed', after.get('error')
        assert after['stockProgress']['processedCount'] > before['stockProgress']['processedCount'], '未越过原失败位置。'
        assert after['source'] == before['source'] and after['dataDate'] == before['dataDate']
        expect(row).to_have_count(1)
        expect(row.locator('.tag').first).not_to_contain_text('失败')
        page.locator('.sidebar-user').click()
        page.wait_for_url('**/#/login*')
        return {'jobId': job_id, 'startedFrom': accepted['stockProgress']['processedCount'],
            'processedAfter': after['stockProgress']['processedCount'], 'total': after['stockProgress']['totalCount'],
            'sameJobResumed': True, 'passedFailurePosition': True}


def run_research_check(base: str, username: str, password: str, *, screenshot: Path | None = None) -> dict[str, Any]:
    """验证任意个股、独立筹码、完整报告及两种真实文件下载，并检查移动布局。"""
    from io import BytesIO
    from tempfile import TemporaryDirectory
    from zipfile import ZipFile

    from pypdf import PdfReader

    stage = '登录和选择未入选个股'
    try:
        with browser_page() as page:
            failures: list[str] = []
            analysis_posts: list[str] = []
            page.on('pageerror', lambda error: failures.append(type(error).__name__))
            page.on('request', lambda request: analysis_posts.append(request.url) if request.method == 'POST' and request.url.endswith('/analyses') else None)
            page.goto(base.rstrip('/') + '/#/login', wait_until='networkidle')
            page.get_by_placeholder('请输入账号', exact=True).fill(username)
            page.get_by_placeholder('请输入密码', exact=True).fill(password)
            page.get_by_role('button', name='登录工作台', exact=True).click()
            page.wait_for_url('**/#/opportunities*')
            choice = page.evaluate("""async () => {
                const token = JSON.parse(sessionStorage.getItem('stocksieve.auth.v1')).accessToken;
                const get = async path => {const r=await fetch('/api/v1'+path,{headers:{Authorization:'Bearer '+token}});if(!r.ok)throw new Error('接口未成功');return(await r.json()).data;};
                const directory=await get('/stocks?pageSize=100');
                for(const stock of directory.items.filter(row=>row.dataStatus==='ready')){
                    const detail=await get('/stocks/'+stock.stockId);
                    if(!detail.signals.length && detail.quote?.price != null)return stock;
                }
                throw new Error('需要未入选的已同步个股');
            }""")
            page.get_by_role('link', name='个股分析', exact=True).click()
            search = page.get_by_placeholder('输入股票名称或代码，如浦发银行、600000', exact=True)
            search.fill('__no_stock_matches__')
            expect(page.get_by_text('没有找到这只股票', exact=True)).to_be_visible()
            search.fill(choice['name'])
            expect(page.locator('.research-match').filter(has_text=choice['code'])).to_be_visible()
            search.fill(choice['code'])
            expect(page.locator('.research-match')).to_have_count(1)
            page.reload(wait_until='networkidle')
            expect(search).to_have_value(choice['code'])
            page.locator('.research-match').get_by_role('button', name='查看分析', exact=True).click()
            expect(page.locator('.stock-heading h1')).to_have_text(choice['name'])
            stage = '无需AI任务的独立筹码'
            before = len(analysis_posts)
            page.get_by_role('button', name='筹码分布', exact=True).click()
            expect(page.locator('.chip-distribution')).to_be_visible(timeout=15000)
            expect(page.locator('.chips-panel')).not_to_contain_text('股东户数')
            assert len(analysis_posts) == before
            stage = '统一七章模拟报告'
            page.get_by_role('button', name='AI 分析报告').click()
            assert page.locator('.analysis-types').count() == 0
            with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/analyses')) as received:
                page.get_by_role('button', name=re.compile(r'^(生成报告|重新生成)$')).click()
            result = received.value.json()['data']
            assert result['kind'] == 'report' and result['simulated'] and result['stockId'] == choice['stockId']
            expect(page.get_by_role('button', name='重新生成', exact=True)).to_be_enabled(timeout=30000)
            expect(page.locator('.report-heading h2')).to_contain_text('模拟', timeout=30000)
            expect(page.locator('.report-chapters .chapter')).to_have_count(7)
            expect(page.locator('.brief-grid')).to_contain_text('持有建议')
            page.locator('.chapter').first.locator('summary').click()
            expect(page.locator('.chapter').first).to_contain_text('公司基本资料')
            expect(page.locator('.report-ownership')).to_contain_text('本期股东户数')
            expect(page.locator('.report-ownership')).to_contain_text('暂不能判断集中或分散')
            stage = '切换页签保留阅读位置'
            page.set_viewport_size({'width': 1440, 'height': 800})
            page.locator('.detail-tabs').evaluate("element => element.scrollIntoView({block:'center'})")
            assert page.evaluate('window.scrollY') > 50
            page.get_by_role('button', name='筹码分布', exact=True).click()
            expect(page.locator('.chip-distribution')).to_be_visible()
            page.get_by_role('button', name='AI 分析报告').click()
            expect(page.locator('.report-heading h2')).to_contain_text('模拟')
            page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            assert page.evaluate('window.scrollY') > 50, '切换AI报告不应滚动到页面顶部'
            expect(page.locator('.valuation-details')).to_have_count(0)
            page.get_by_text('查看关键指标', exact=True).click()
            expect(page.locator('.report-metrics')).to_contain_text('技术盈亏比（赔率）')
            expect(page.locator('.analysis-panel .chip-distribution')).to_have_count(0)
            page.locator('.chapter').nth(1).locator('summary').click()
            previous_url = page.url
            page.locator('.chapter').nth(1).locator('a').first.click()
            expect(page).to_have_url(previous_url)
            stage = 'Word与PDF下载'
            downloads = []
            for format, label in [('docx', '导出 Word'), ('pdf', '导出 PDF')]:
                stage = label + '下载'
                with page.expect_download(timeout=30000) as download_info:
                    page.get_by_role('button', name=label, exact=True).click()
                download = download_info.value
                assert download.suggested_filename.endswith('.' + format)
                with TemporaryDirectory(prefix='stocksieve-download-') as directory:
                    file = Path(directory) / ('report.' + format)
                    download.save_as(str(file))
                    payload = file.read_bytes()
                if format == 'docx':
                    assert payload.startswith(b'PK'), '下载文件不是有效Word文档'
                    with ZipFile(BytesIO(payload)) as archive:
                        assert '【模拟】' in archive.read('word/document.xml').decode()
                else:
                    reader = PdfReader(BytesIO(payload))
                    assert '行业 PE（TTM）' in ''.join(p.extract_text() for p in reader.pages)
                if screenshot:
                    destination = screenshot.parent / ('unified-report.' + format)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    download.save_as(str(destination))
                downloads.append(format)
            stage = '刷新和移动视口'
            page.reload(wait_until='networkidle')
            expect(page.locator('.report-heading h2')).to_contain_text('模拟')
            page.set_viewport_size({'width': 390, 'height': 844})
            expect(page.locator('.report-decision')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            if screenshot:
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshot), full_page=True, animations='disabled')
            page.get_by_role('link', name='返回个股搜索', exact=True).click()
            expect(search).to_have_value(choice['code'])
            assert not failures
            return {'stockId': choice['stockId'], 'name': choice['name'], 'unselectedStockAnalyzed': True,
                'reportId': result['id'], 'downloads': downloads, 'refreshRestored': True, 'mobileChecked': True, 'pageErrors': len(failures)}
    except Exception as error:
        diagnostic = (type(error).__name__ + ': ' + str(error)[:300]) if stage.startswith('导出 ') else type(error).__name__
        raise RuntimeError('个股分析浏览器验收未通过，阶段：' + stage + '，' + diagnostic) from None


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
