"""本文件核对数据库映射与已有初始化SQL，避免离线测试依赖生产库不存在的字段。"""

import re
from pathlib import Path
import pytest

from stocksieve.database import Database
from stocksieve.tables import metadata


def test_mapping_columns_exist_in_initial_sql():
    sql = (Path(__file__).resolve().parents[1] / 'sql/init.sql').read_text()
    tables = dict(re.findall(r'CREATE TABLE public\.(\w+) \((.*?)\n\);', sql, re.S))
    assert len(tables) == len(metadata.tables) == 17
    for name, table in metadata.tables.items():
        columns = set(re.findall(r'^    ([a-z][a-z0-9_]*)\s+[A-Z]', tables[name], re.M))
        assert set(table.c.keys()) <= columns, (name, set(table.c.keys()) - columns)


@pytest.mark.parametrize('url,testing,schema', [
    ('postgresql+psycopg://localhost/stocksieve', True, 'public'),
    ('postgresql+psycopg://localhost/stocksieve', False, 'stocksieve_test_0123456789abcdef'),
    ('sqlite+pysqlite:///:memory:', True, 'stocksieve_test_0123456789abcdef'),
])
def test_isolation_namespace_rejected_before_connecting(url, testing, schema):
    with pytest.raises(ValueError):
        Database(url, testing=testing, schema=schema)


def test_cleanup_never_accepts_public_or_unowned_namespace():
    database = Database('sqlite+pysqlite:///:memory:', testing=True)
    try:
        database.schema = 'public'
        database._test_schema_created = True
        database._created_schema = 'stocksieve_test_0123456789abcdef'
        with pytest.raises(RuntimeError):
            database.drop_test_schema()
    finally:
        database.close()
