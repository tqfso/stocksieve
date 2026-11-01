"""本文件在测试专属schema验证约束对齐，禁止测试修改public业务结构或记录。"""

from typing import Any

import pytest

from stocksieve.schema_sync import normalized, sync


def test_existing_schema_constraints_are_reconciled_without_deleting_data(context: Any) -> None:
    if context.api.db.engine.dialect.name != 'postgresql':
        pytest.skip('数据库结构对齐仅在--postgres隔离schema验收。')
    context.submit('initialize'); context.run()
    database = context.api.db
    with database.transaction() as connection:
        connection.exec_driver_sql(f'ALTER TABLE "{database.schema}".tasks ADD CONSTRAINT tasks_legacy_catalog_check CHECK (kind <> \'catalog\')')
        connection.exec_driver_sql(f'ALTER TABLE "{database.schema}".tasks DROP CONSTRAINT tasks_category_kind_check')
        connection.exec_driver_sql(f'ALTER TABLE "{database.schema}".tasks ADD CONSTRAINT tasks_category_kind_check CHECK (category <> \'sync\' OR kind IN (\'initialize\',\'daily\'))')
    audit = sync(database)
    assert audit['obsoleteProjectChecks'] == [{'table': 'tasks', 'name': 'tasks_legacy_catalog_check'}]
    result = sync(database, apply=True)
    assert result['applied'] is True
    assert not sync(database)['obsoleteProjectChecks']
    assert not sync(database)['constraintChanges']
    assert context.request('GET', '/api/v1/admin/users')[1]['data']['total'] == 1
    job = context.submit('catalog'); context.run()
    assert context.api.tasks.detail(job, 'sync')['status'] == 'succeeded'


def test_schema_normalization_does_not_erase_logical_grouping() -> None:
    assert normalized('CHECK (a AND (b OR c))') != normalized('CHECK ((a AND b) OR c)')
