"""本文件核对数据库映射与已有初始化SQL，避免离线测试依赖生产库不存在的字段。"""

import re
from pathlib import Path

from stocksieve.tables import metadata


def test_mapping_columns_exist_in_initial_sql():
    sql = (Path(__file__).resolve().parents[1] / 'sql/init.sql').read_text()
    tables = dict(re.findall(r'CREATE TABLE public\.(\w+) \((.*?)\n\);', sql, re.S))
    assert len(tables) == len(metadata.tables) == 17
    for name, table in metadata.tables.items():
        columns = set(re.findall(r'^    ([a-z][a-z0-9_]*)\s+[A-Z]', tables[name], re.M))
        assert set(table.c.keys()) <= columns, (name, set(table.c.keys()) - columns)
