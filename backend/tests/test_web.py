"""本文件通过真实HTTP验证编译前端的同源托管、静态文件边界和接口鉴权。"""

from dataclasses import replace
from typing import Any
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

import pytest

from conftest import HTTPServer
from stocksieve.api import API


def test_compiled_frontend_is_served_without_exposing_server_files(context: Any, tmp_path: Any) -> None:
    if context.server is None:
        pytest.skip('静态文件验收需--http模式。')
    web = tmp_path / 'web'
    (web / 'assets').mkdir(parents=True)
    (web / 'index.html').write_text('<html><title>test compiled app</title></html>')
    (web / 'assets/app.js').write_text('/* compiled-test */')
    (tmp_path / '.env').write_text('private-test-marker')
    api = API(context.api.db, replace(context.api.settings, web_dir=web), context.api.registry, clock=context.clock.now)
    api.app.freeze()
    server = HTTPServer(api.app)
    opener = build_opener(ProxyHandler({}))
    try:
        for path, expected in [('/', b'test compiled app'), ('/assets/app.js', b'compiled-test')]:
            with opener.open(server.base_url + path, timeout=10) as response:
                assert response.status == 200 and expected in response.read()
        for path, status in [('/.env', 404), ('/api/v1/auth/me', 401), ('/..%2F.env', 404)]:
            try:
                response = opener.open(server.base_url + path, timeout=10)
            except HTTPError as error:
                response = error
            with response:
                assert response.status == status
                assert b'private-test-marker' not in response.read()
    finally:
        server.close()
