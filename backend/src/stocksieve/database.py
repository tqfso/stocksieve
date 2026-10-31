"""本文件维护数据库连接与短事务，生产只连接既有 PostgreSQL，不自动建表或修改结构。"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import StaticPool

from .tables import metadata


def utc(value: datetime) -> datetime:
    """SQLite 离线测试返回无时区时间时，按数据库统一的 UTC 解释。"""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class Database:
    """事务结束后立即释放连接，网络取数和模型调用在事务外执行。"""

    def __init__(self, url: str, *, testing: bool = False) -> None:
        if not url.startswith('postgresql') and not testing:
            raise ValueError('生产服务必须使用 PostgreSQL')
        options: dict[str, Any] = {'pool_pre_ping': True, 'hide_parameters': True}
        if url.startswith('sqlite'):
            options.update(poolclass=StaticPool, connect_args={'check_same_thread': False})
        self.engine: Engine = create_engine(url, **options)
        if url.startswith('sqlite'):
            @event.listens_for(self.engine, 'connect')
            def enable_foreign_keys(connection: Any, record: Any) -> None:
                connection.execute('PRAGMA foreign_keys=ON')
        self.testing = testing

    def create_test_schema(self) -> None:
        if not self.testing:
            raise RuntimeError('仅离线测试可自动建表')
        metadata.create_all(self.engine)

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
