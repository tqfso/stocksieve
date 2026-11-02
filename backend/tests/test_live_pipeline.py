"""本文件验证长时间历史同步可通过HTTP提交后退出，后台任务仍可恢复且不重复创建。"""

import asyncio
import json
from pathlib import Path
from typing import Any

from conftest import HTTPServer
from sqlalchemy import func, select

from stocksieve.infrastructure.persistence import schema as t
from stocksieve.operations.live_pipeline import bootstrap


def test_submit_only_keeps_durable_task_and_reuses_existing_state(context: Any, tmp_path: Path, capsys: Any) -> None:
    server = context.server or HTTPServer(context.api.app)
    credentials = tmp_path / 'credentials.json'
    credentials.write_text(json.dumps({'username': 'admin', 'password': 'testpass123'}))
    credentials.chmod(0o600)
    state = tmp_path / 'pipeline.json'
    try:
        for _ in range(2):
            asyncio.run(bootstrap(server.base_url, credentials, state, 'fixture', only_kind='initialize', submit_only=True))
        task_id = json.loads(state.read_text())['jobs']['initialize']
        assert context.api.tasks.detail(task_id, 'sync')['status'] == 'queued'
        with context.api.db.transaction() as connection:
            assert connection.scalar(select(func.count()).select_from(t.tasks)) == 1
        assert context.run()
        assert context.api.tasks.detail(task_id, 'sync')['status'] == 'succeeded'
        asyncio.run(bootstrap(server.base_url, credentials, state, 'fixture', only_kind='initialize'))
        output = capsys.readouterr().out
        assert 'jobSubmitted' in output and 'taskCompleted' in output
        assert 'testpass123' not in output and 'accessToken' not in output
    finally:
        if context.server is None:
            server.close()
