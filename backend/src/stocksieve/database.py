"""本文件维护数据库连接与短事务，生产只连接既有 PostgreSQL，不自动建表或修改结构。"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import re
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine, URL, make_url
from sqlalchemy.pool import StaticPool

from .tables import metadata


def utc(value: datetime) -> datetime:
    """SQLite 离线测试返回无时区时间时，按数据库统一的 UTC 解释。"""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class Database:
    """事务结束后立即释放连接，网络取数和模型调用在事务外执行。"""

    def __init__(self, url: str | URL, *, testing: bool = False, schema: str | None = None) -> None:
        parsed = make_url(url)
        backend = parsed.get_backend_name()
        if backend != 'postgresql' and not testing:
            raise ValueError('生产服务必须使用 PostgreSQL')
        if schema is not None and (not testing or backend != 'postgresql' or not re.fullmatch(r'stocksieve_test_[a-f0-9]{16}', schema)):
            raise ValueError('只允许测试模式使用自动生成的隔离 schema。')
        options: dict[str, Any] = {'pool_pre_ping': True, 'hide_parameters': True}
        if backend == 'postgresql':
            options['connect_args'] = {'connect_timeout': 5}
            if schema:
                options['connect_args']['options'] = f'-c search_path={schema} -c statement_timeout=10000 -c lock_timeout=5000'
        if backend == 'sqlite':
            options.update(poolclass=StaticPool, connect_args={'check_same_thread': False})
        self.engine: Engine = create_engine(parsed, **options)
        if backend == 'sqlite':
            @event.listens_for(self.engine, 'connect')
            def enable_foreign_keys(connection: Any, record: Any) -> None:
                connection.execute('PRAGMA foreign_keys=ON')
        self.testing = testing
        self.schema = schema
        self._test_schema_created = False
        self._created_schema: str | None = None

    def create_test_schema(self) -> None:
        if not self.testing:
            raise RuntimeError('仅测试模式可初始化测试结构')
        if self.engine.dialect.name == 'sqlite':
            metadata.create_all(self.engine)
            return
        if self.schema is None or not re.fullmatch(r'stocksieve_test_[a-f0-9]{16}', self.schema):
            raise RuntimeError('PostgreSQL 测试必须指定隔离 schema，禁止初始化 public。')
        # 使用与业务库相同的 SQL 及全部约束，只替换受控的 schema 名。
        source = Path(__file__).resolve().parents[2] / 'sql/init.sql'
        script = source.read_text().replace('public.', f'"{self.schema}".').replace("namespace.nspname = 'public'", f"namespace.nspname = '{self.schema}'")
        with self.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{self.schema}"')
            self._test_schema_created = True
            self._created_schema = self.schema
            cursor = connection.connection.driver_connection.cursor()
            try:
                cursor.execute('SELECT current_schema()')
                if cursor.fetchone()[0] != self.schema:
                    raise RuntimeError('测试连接未绑定隔离 schema，停止写入。')
                cursor.execute(script, prepare=False)
            except BaseException:
                cursor.execute('ROLLBACK')
                raise
            finally:
                cursor.close()

    def drop_test_schema(self) -> None:
        """只清理由本实例创建的临时 schema，不能清理 public 或其他已有 schema。"""
        if not self.testing or not self.schema or not self._test_schema_created:
            return
        if self.schema != self._created_schema or not re.fullmatch(r'stocksieve_test_[a-f0-9]{16}', self.schema):
            raise RuntimeError('测试 schema 归属不匹配，禁止清理。')
        with self.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{self.schema}" CASCADE')
        self._test_schema_created = False
        self._created_schema = None

    @contextmanager
    def transaction(self, *, serialized: bool = False) -> Iterator[Connection]:
        with self.engine.connect() as connection:
            try:
                if serialized and self.engine.dialect.name == 'sqlite':
                    connection.exec_driver_sql('BEGIN IMMEDIATE')
                else:
                    connection.begin()
                    if serialized:
                        connection.execute(text('SELECT pg_advisory_xact_lock(831448510)'))
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def close(self) -> None:
        self.engine.dispose()
