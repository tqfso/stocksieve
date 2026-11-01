"""本文件用隔离浏览器上下文验证真实前端和HTTP接口，不输出口令、令牌或登录响应。"""

from __future__ import annotations

from contextlib import contextmanager
import re
from typing import Any, Iterator

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
