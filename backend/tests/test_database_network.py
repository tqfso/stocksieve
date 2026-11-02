"""本文件验证宿主机与Compose内部的数据库连接配置，确保端口映射不改变数据库身份。"""

from typing import Any

from sqlalchemy.engine import make_url

from stocksieve.shared.settings import Settings


def test_compose_service_host_and_internal_port_override_host_database_url(monkeypatch: Any) -> None:
    monkeypatch.setenv('STOCKSIEVE_DATABASE_URL', '')
    monkeypatch.setenv('STOCKSIEVE_POSTGRES_USER', 'test_user')
    monkeypatch.setenv('STOCKSIEVE_POSTGRES_PASSWORD', 'test-only-@:/?#')
    monkeypatch.setenv('STOCKSIEVE_POSTGRES_DB', 'test_db')
    monkeypatch.setenv('STOCKSIEVE_POSTGRES_HOST', 'postgres')
    monkeypatch.setenv('STOCKSIEVE_POSTGRES_PORT', '5432')
    url = make_url(Settings.from_env().database_url)
    assert (url.host, url.port, url.database, url.username) == ('postgres', 5432, 'test_db', 'test_user')
    assert url.password == 'test-only-@:/?#'


def test_host_cli_keeps_explicit_database_url(monkeypatch: Any) -> None:
    monkeypatch.setenv('STOCKSIEVE_DATABASE_URL', 'postgresql+psycopg://test_user:test-only@127.0.0.1:15432/test_db')
    monkeypatch.setenv('STOCKSIEVE_POSTGRES_HOST', 'postgres')
    url = make_url(Settings.from_env().database_url)
    assert (url.host, url.port, url.database) == ('127.0.0.1', 15432, 'test_db')
