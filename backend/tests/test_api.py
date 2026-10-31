"""本文件通过真实路由和中间件离线测试鉴权、参数校验、追踪、幂等与账号隔离。"""

import pytest


def test_login_roles_and_session_revocation(context):
    status, response, headers = context.request('GET', '/api/v1/auth/me', token=None)
    assert status == 401 and response['error']['code'] == 'UNAUTHENTICATED'
    assert response['requestId'] == headers['X-Request-ID'] == 'test-request'
    status, response, _ = context.request('POST', '/api/v1/auth/login', {'username': ' ADMIN ', 'password': 'testpass123'}, token=None)
    assert status == 200
    token = response['data']['accessToken']
    assert 'password' not in response['data']['user']
    status, response, _ = context.request('POST', '/api/v1/admin/users', {'username': ' VIEWER ', 'name': '新用户', 'password': 'viewerpass123'})
    assert status == 201 and response['data']['role'] == 'user' and response['data']['username'] == 'viewer'
    assert context.request('POST', '/api/v1/admin/users', {'username': 'viewer', 'name': '重复', 'password': 'viewerpass123'})[0] == 409
    viewer = context.api.auth.login('viewer', 'viewerpass123')['accessToken']
    assert context.request('GET', '/api/v1/admin/users', token=viewer)[0] == 403
    assert context.request('PUT', '/api/v1/auth/password', {'oldPassword': 'viewerpass123', 'newPassword': 'anotherpass'}, token=viewer)[0] == 403
    status, response, _ = context.request('PUT', '/api/v1/auth/password', {'oldPassword': 'testpass123', 'newPassword': 'newpass123'})
    assert status == 204 and response is None
    assert context.request('GET', '/api/v1/auth/me', token=token)[0] == 401
    assert context.request('GET', '/api/v1/auth/me')[0] == 401
    assert context.request('GET', '/api/v1/auth/me', token=viewer)[0] == 200


@pytest.mark.parametrize('path', ['/api/v1/auth/me?unknown=1', '/api/v1/admin/users?page=1&page=2', '/api/v1/admin/users?pageSize=101', '/api/v1/admin/users?role=owner'])
def test_bad_query_rejected(context, path):
    assert context.request('GET', path)[0] == 400


def test_json_and_body_strictness(context):
    assert context.request('POST', '/api/v1/auth/login', {'username': 'admin', 'password': 'testpass123', 'admin': True})[0] == 400
    assert context.request('POST', '/api/v1/auth/login', raw=b'{broken')[0] == 400
    assert context.request('POST', '/api/v1/auth/login', ['admin'])[0] == 400
    assert context.request('POST', '/api/v1/admin/users', {'username': 'admin2', 'name': 'Admin', 'password': 'testpass123', 'role': 'admin'})[0] == 400
    assert context.request('POST', '/api/v1/admin/sync-jobs', {'kind': 'daily', 'source': 'fixture', 'dataDate': '20261030'}, key='invalid-date')[0] == 400


def test_empty_database_is_preparation_not_empty_results(context):
    status, response, _ = context.request('GET', '/api/v1/market/status')
    assert status == 200 and response['data']['publishedSnapshot'] is None
    assert response['data']['initialized'] is False
    assert context.request('GET', '/api/v1/strategies')[0] == 200
    assert context.request('GET', '/api/v1/opportunities?strategy=short')[0] == 409
    assert context.request('POST', '/api/v1/admin/sync-jobs', {'kind': 'daily', 'source': 'fixture'}, key='not-initialized')[0] == 409


def test_sync_idempotency_returns_original_acceptance_and_conflict(context):
    path = '/api/v1/admin/sync-jobs'
    payload = {'kind': 'initialize', 'source': 'fixture'}
    status, first, headers = context.request('POST', path, payload, key='same-request')
    assert status == 202 and first['data']['status'] == 'queued'
    assert headers['Location'].endswith(first['data']['id'])
    context.run()
    status, replay, repeated_headers = context.request('POST', path, payload, key='same-request')
    assert status == 202 and replay['data'] == first['data'] and repeated_headers['Location'] == headers['Location']
    assert context.request('POST', path, {'kind': 'catalog', 'source': 'fixture'}, key='same-request')[0] == 409
    assert context.api.tasks.detail(first['data']['id'], 'sync')['status'] == 'succeeded'


def test_analysis_conflict_and_owner_privacy(context):
    snapshot = context.published()
    stock_id = context.source.universe_stocks[0].stock_id
    path = f'/api/v1/stocks/{stock_id}/analyses'
    status, first, _ = context.request('POST', path, {'kind': 'trade', 'strategy': 'short', 'snapshotId': snapshot}, key='analysis-first')
    assert status == 202, first
    assert first['data']['stage'] == 'queued'
    status, conflict, _ = context.request('POST', path, {'kind': 'trade', 'strategy': 'short', 'snapshotId': snapshot}, key='analysis-second')
    assert status == 409 and conflict['error']['details']['analysisId'] == first['data']['id']
    context.api.auth.create_user('reader', '测试读者', 'readerpass123')
    viewer = context.api.auth.login('reader', 'readerpass123')['accessToken']
    assert context.request('GET', f"/api/v1/analyses/{first['data']['id']}", token=viewer)[0] == 404
    assert context.request('GET', '/api/v1/analyses', token=viewer)[1]['data']['items'] == []
    assert context.request('GET', '/api/v1/analyses?strategy=short')[0] == 400


def test_logout_revokes_only_current_session_and_expiry_is_enforced(context):
    other = context.api.auth.login('admin', 'testpass123')['accessToken']
    assert context.request('POST', '/api/v1/auth/logout')[0] == 204
    assert context.request('GET', '/api/v1/auth/me')[0] == 401
    assert context.request('GET', '/api/v1/auth/me', token=other)[0] == 200
    context.clock.advance(8 * 3600)
    status, error, _ = context.request('GET', '/api/v1/auth/me', token=other)
    assert status == 401 and error['error']['code'] == 'TOKEN_EXPIRED'


def test_all_documented_api_routes_are_registered(context):
    from pathlib import Path
    import re
    document = Path(__file__).resolve().parents[2] / 'docs/api/README.md'
    expected = {(method, '/api/v1' + path) for method, path in re.findall(r'\| (GET|POST|PUT) \| `([^`]+)`', document.read_text())}
    actual = {(route.method, route.resource.canonical) for route in context.api.app.router.routes()}
    assert expected and expected <= actual
