"""本文件对照当前初始化定义校验并直接更新既有库约束及索引，不重建业务表或生成增量SQL。"""

import re
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .database import Database


def identifier(value: str) -> str:
    if not re.fullmatch(r'[a-z][a-z0-9_]*', value):
        raise RuntimeError('数据库标识不合法。')
    return '"' + value + '"'


def definitions(database: Database, schema: str) -> dict[str, Any]:
    with database.transaction() as connection:
        constraints = connection.execute(text("""SELECT relation.relname AS table_name, item.conname AS name,
            item.contype AS kind, pg_get_constraintdef(item.oid, true) AS definition
            FROM pg_catalog.pg_constraint item JOIN pg_catalog.pg_class relation ON relation.oid=item.conrelid
            JOIN pg_catalog.pg_namespace namespace ON namespace.oid=relation.relnamespace
            WHERE namespace.nspname=:schema AND item.contype IN ('c','f','u')"""), {'schema': schema}).mappings().all()
        indexes = connection.execute(text("""SELECT relation.relname AS table_name, index_relation.relname AS name,
            pg_get_indexdef(index_relation.oid) AS definition FROM pg_catalog.pg_index item
            JOIN pg_catalog.pg_class relation ON relation.oid=item.indrelid
            JOIN pg_catalog.pg_class index_relation ON index_relation.oid=item.indexrelid
            JOIN pg_catalog.pg_namespace namespace ON namespace.oid=relation.relnamespace
            WHERE namespace.nspname=:schema AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint c WHERE c.conindid=item.indexrelid)"""), {'schema': schema}).mappings().all()
        columns = connection.execute(text("""SELECT relation.relname AS table_name, item.attname AS name,
            format_type(item.atttypid,item.atttypmod) AS type FROM pg_catalog.pg_attribute item
            JOIN pg_catalog.pg_class relation ON relation.oid=item.attrelid
            JOIN pg_catalog.pg_namespace namespace ON namespace.oid=relation.relnamespace
            WHERE namespace.nspname=:schema AND relation.relkind='r' AND item.attnum>0 AND NOT item.attisdropped"""), {'schema': schema}).mappings().all()
    def public(row: Any) -> dict[str, Any]:
        return {key: value.replace('"' + schema + '".', 'public.').replace(schema + '.', 'public.') if isinstance(value, str) and key == 'definition' else value for key, value in row.items()}
    return {'constraints': {(row['table_name'], row['name']): public(row) for row in constraints},
            'indexes': {(row['table_name'], row['name']): public(row) for row in indexes},
            'columns': {(row['table_name'], row['name']): row['type'] for row in columns}}


def normalized(value: str) -> str:
    value = value.replace('"public".', '').replace('public.', '')
    # PostgreSQL会把varchar数组整体转text重写为逐元素转换；只消除无长度的文本常量冗余转换。
    value = re.sub(r"('(?:[^']|'')*')(?:::(?:character varying|text))+", r'\1', value)
    value = re.sub(r'(ARRAY\[[^\]]*\])::text\[\]', r'\1', value)
    return value


def sync(database: Database, *, apply: bool = False, details: bool = False) -> dict[str, Any]:
    """先在独立临时schema解析标准SQL；更新在单事务内验证，失败整次回滚，不删除业务记录。"""
    target = database.schema or 'public'
    prefix = identifier(target) + '.'
    def ddl(value: str) -> str:
        return value.replace('public.', prefix)
    temporary = Database(database.engine.url, testing=True, schema='stocksieve_test_' + uuid4().hex[:16])
    try:
        temporary.create_test_schema()
        expected, actual = definitions(database, temporary.schema), definitions(database, target)
        type_changes = [{'table': key[0], 'column': key[1], 'actual': actual['columns'].get(key), 'expected': value}
                        for key, value in expected['columns'].items() if actual['columns'].get(key) != value]
        changed = [row for key, row in expected['constraints'].items() if key not in actual['constraints'] or normalized(row['definition']) != normalized(actual['constraints'][key]['definition'])]
        obsolete = [row for key, row in actual['constraints'].items() if row['kind'] == 'c' and key not in expected['constraints'] and row['name'].startswith(row['table_name'] + '_')]
        indexes = [row for key, row in expected['indexes'].items() if key not in actual['indexes'] or normalized(row['definition']) != normalized(actual['indexes'][key]['definition'])]
        report = {'applied': False, 'columnTypeDifferences': type_changes,
                  'constraintChanges': [{'table': row['table_name'], 'name': row['name'], 'kind': row['kind']} for row in changed],
                  'obsoleteProjectChecks': [{'table': row['table_name'], 'name': row['name']} for row in obsolete],
                  'indexChanges': [{'table': row['table_name'], 'name': row['name']} for row in indexes]}
        if details:
            report['constraintDefinitions'] = [{'name': row['name'], 'expected': row['definition'],
                'actual': actual['constraints'].get((row['table_name'], row['name']), {}).get('definition')} for row in changed]
        if not apply:
            return report
        if type_changes:
            raise RuntimeError('存在字段类型差异，停止自动更新以避免数据精度损失；请先核对审计结果。')
        if any(row['kind'] == 'u' and (row['table_name'], row['name']) in actual['constraints'] for row in changed):
            raise RuntimeError('存在已被外键引用的唯一约束差异，需单独核对，未修改。')
        try:
            with database.transaction(serialized=True) as connection:
                connection.execute(text("SET LOCAL lock_timeout='5s'"))
                # 1. 先补齐新的唯一键，保留旧唯一键，避免破坏既有外键依赖。
                for row in changed:
                    if row['kind'] == 'u':
                        connection.exec_driver_sql(f"ALTER TABLE {prefix}{identifier(row['table_name'])} ADD CONSTRAINT {identifier(row['name'])} {ddl(row['definition'])}")
                # 2. 替换项目拥有的旧CHECK和需要变化的外键，验证所有已有记录。
                for row in [*obsolete, *(row for row in changed if row['kind'] != 'u' and (row['table_name'], row['name']) in actual['constraints'])]:
                    connection.exec_driver_sql(f"ALTER TABLE {prefix}{identifier(row['table_name'])} DROP CONSTRAINT {identifier(row['name'])}")
                for row in changed:
                    if row['kind'] != 'u':
                        connection.exec_driver_sql(f"ALTER TABLE {prefix}{identifier(row['table_name'])} ADD CONSTRAINT {identifier(row['name'])} {ddl(row['definition'])}")
                # 3. 对齐非约束索引；版本序列只在缺失时建立，并从既有最大版本继续。
                for row in indexes:
                    if (row['table_name'], row['name']) in actual['indexes']:
                        connection.exec_driver_sql(f"DROP INDEX {prefix}{identifier(row['name'])}")
                    connection.exec_driver_sql(ddl(row['definition']))
                if connection.scalar(text('SELECT to_regclass(:name)'), {'name': target + '.snapshot_version_seq'}) is None:
                    maximum = connection.scalar(text(f'SELECT COALESCE(max(version),0) FROM {prefix}data_snapshots'))
                    connection.exec_driver_sql(f'CREATE SEQUENCE {prefix}snapshot_version_seq AS BIGINT START WITH {int(maximum) + 1}')
            report['applied'] = True
            return report
        except SQLAlchemyError:
            raise RuntimeError('现有数据或依赖不满足目标约束，更新已整次回滚，业务数据保留。') from None
    finally:
        try:
            temporary.drop_test_schema()
        finally:
            temporary.close()
