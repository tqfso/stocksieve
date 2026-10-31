"""本文件映射现有17张 PostgreSQL 业务表；SQLite 类型变体只供离线事务与接口测试。"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, Column as C, Date, DateTime, ForeignKeyConstraint, Index, Integer, JSON, MetaData, Numeric, String, Table, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
UUID = Uuid(as_uuid=False)
J = JSON().with_variant(JSONB(), 'postgresql')
T = DateTime(timezone=True)
P = Numeric(24, 12)


def now() -> datetime:
    return datetime.now(timezone.utc)


def identifier() -> str:
    return str(uuid4())


users = Table('users', metadata, C('id', UUID, primary_key=True, default=identifier),
              C('username', String(20), nullable=False, unique=True), C('name', String(20), nullable=False),
              C('password_hash', Text, nullable=False), C('role', String(16), nullable=False),
              C('password_changed_at', T, default=now), C('created_at', T, default=now), C('updated_at', T, default=now))
sessions = Table('user_sessions', metadata, C('id', UUID, primary_key=True, default=identifier),
                 C('user_id', UUID, nullable=False), C('token_hash', String(64), unique=True, nullable=False),
                 C('created_at', T, default=now), C('expires_at', T, nullable=False), C('revoked_at', T))
stocks = Table('stocks', metadata, C('stock_id', String(16), primary_key=True), C('code', String(6), nullable=False),
               C('name', String(64), nullable=False), C('exchange', String(4), nullable=False), C('board', String(16)),
               C('listed_on', Date), C('delisted_on', Date), C('source_codes', J, default=dict),
               C('created_at', T, default=now), C('updated_at', T, default=now))
categories = Table('categories', metadata, C('id', String(64), primary_key=True), C('kind', String(16)),
                   C('name', String(80)), C('source_codes', J, default=dict), C('created_at', T, default=now), C('updated_at', T, default=now))
calendar = Table('trading_calendar', metadata, C('exchange', String(4), primary_key=True), C('trade_date', Date, primary_key=True),
                 C('is_open', Boolean), C('close_at', T), C('source', String(32)), C('fetched_at', T, default=now))
tasks = Table('tasks', metadata, C('id', UUID, primary_key=True, default=identifier),
              C('category', String(16), nullable=False), C('kind', String(32), nullable=False), C('created_by', UUID),
              C('trigger', String(16), nullable=False), C('data_source', String(32)), C('target_date', Date),
              C('stock_id', String(16)), C('input_snapshot_id', UUID), C('strategy_code', String(32)),
              C('status', String(16), nullable=False), C('progress', Integer, default=0), C('dedupe_key', String(255), nullable=False),
              C('scheduled_for', T), C('run_after', T, default=now), C('wait_reason', String(64)),
              C('retry_deadline_at', T), C('retry_count', Integer, default=0), C('precheck_json', J),
              C('options_json', J, default=dict), C('attempt_no', Integer, default=0),
              C('lease_owner', String(64)), C('lease_expires_at', T), C('result_json', J), C('result_snapshot_id', UUID),
              C('error_code', String(64)), C('error_message', Text), C('created_at', T, default=now), C('started_at', T), C('finished_at', T))
active = tasks.c.status.in_(['queued', 'running'])
Index('tasks_active_dedupe_uq', tasks.c.dedupe_key, unique=True, postgresql_where=active, sqlite_where=active)
snapshots = Table('data_snapshots', metadata, C('id', UUID, primary_key=True, default=identifier),
                  C('sync_task_id', UUID, unique=True), C('base_snapshot_id', UUID), C('version', BigInteger, unique=True),
                  C('data_date', Date), C('source', String(32)), C('status', String(16)), C('adjustment', String(8)),
                  C('strategy_version', String(32)), C('calculation_version', String(32)), C('strategy_config_json', J),
                  C('history_start_date', Date), C('calendar_json', J), C('coverage_json', J),
                  C('created_at', T, default=now), C('published_at', T))
bars = Table('daily_bars', metadata, C('snapshot_id', UUID, primary_key=True), C('stock_id', String(16), primary_key=True),
             C('trade_date', Date, primary_key=True), C('open', P), C('high', P), C('low', P), C('close', P),
             C('volume', BigInteger), C('amount', P), C('turnover', P), C('total_market_cap', P),
             C('total_shares', BigInteger), C('float_shares', BigInteger), C('adj_factor', P), C('source', String(32)), C('fetched_at', T, default=now))
snapshot_stocks = Table('snapshot_stocks', metadata, C('snapshot_id', UUID, primary_key=True), C('stock_id', String(16), primary_key=True),
                        C('code', String(6)), C('name', String(64)), C('exchange', String(4)), C('board', String(16)),
                        C('industry_id', String(64)), C('industry_name', String(80)), C('listed_on', Date),
                        C('trade_status', String(16)), C('is_st', Boolean), C('is_delisting', Boolean),
                        C('quote_price', P), C('change_pct', P), C('turnover', P), C('amount', P), C('total_market_cap', P),
                        *(C(name, P) for name in ('ma5', 'ma10', 'ma20', 'ma60', 'ma120')), C('excluded_reason', String(64)))
concepts = Table('snapshot_stock_concepts', metadata, C('snapshot_id', UUID, primary_key=True),
                 C('stock_id', String(16), primary_key=True), C('concept_id', String(64), primary_key=True), C('concept_name', String(80)))
signals = Table('strategy_signals', metadata, C('id', UUID, primary_key=True, default=identifier),
                C('snapshot_id', UUID), C('stock_id', String(16)), C('strategy_code', String(16)), C('reason', Text),
                C('metrics_json', J), C('condition_results_json', J), C('invalidation_json', J),
                C('sort_value', P), C('created_at', T, default=now))
logs = Table('task_logs', metadata, C('id', BigInteger().with_variant(Integer(), 'sqlite'), primary_key=True, autoincrement=True),
             C('task_id', UUID), C('time', T, default=now), C('level', String(16)), C('message', Text), C('details_json', J))
triggers = Table('sync_triggers', metadata, C('scheduled_date', Date, primary_key=True), C('scheduled_for', T),
                 C('task_id', UUID), C('created_at', T, default=now), C('linked_at', T), C('last_error', Text))
idempotency = Table('idempotency_requests', metadata, C('user_id', UUID, primary_key=True), C('http_method', String(8), primary_key=True),
                    C('request_path', String(256), primary_key=True), C('idempotency_key', String(128), primary_key=True),
                    C('request_hash', String(64)), C('task_id', UUID), C('response_status', Integer),
                    C('response_data', J), C('response_headers', J), C('created_at', T, default=now), C('expires_at', T))
financials = Table('financial_reports', metadata, C('id', UUID, primary_key=True, default=identifier), C('stock_id', String(16)),
                   C('period_start', Date), C('period_end', Date), C('statement_scope', String(16)),
                   C('revenue_ytd', P), C('net_profit_parent_ytd', P), C('operating_cashflow_ytd', P), C('equity_parent_end', P),
                   C('extra_json', J), C('source', String(32)), C('source_url', Text), C('published_at', T),
                   C('fetched_at', T, default=now), C('content_hash', String(64)), C('raw_payload_json', J))
materials = Table('analysis_materials', metadata, C('id', UUID, primary_key=True, default=identifier), C('task_id', UUID),
                  C('material_key', String(128)), C('material_kind', String(32)), C('financial_report_id', UUID), C('source_name', Text), C('source_url', Text), C('data_date', Date),
                  C('published_at', T), C('fetched_at', T), C('status', String(16)), C('content_json', J), C('content_text', Text),
                  C('content_hash', String(64)), C('missing_reason', Text))
results = Table('analysis_results', metadata, C('task_id', UUID, primary_key=True), C('payload_json', J),
                C('schema_version', Integer), C('provider', String(32)), C('model_name', String(128)),
                C('prompt_version', String(32)), C('calculation_version', String(32)), C('usage_json', J),
                C('materials_as_of_at', T), C('generated_at', T))

# 离线库同样校验资源归属关系；生产使用既有 init.sql 中更完整的约束。
signals.append_constraint(UniqueConstraint('snapshot_id', 'stock_id', 'strategy_code'))
for child, columns, parent, referenced in [
    (sessions, ['user_id'], users, ['id']), (tasks, ['created_by'], users, ['id']),
    (tasks, ['stock_id'], stocks, ['stock_id']), (tasks, ['result_snapshot_id'], snapshots, ['id']),
    (tasks, ['input_snapshot_id', 'stock_id'], snapshot_stocks, ['snapshot_id', 'stock_id']),
    (tasks, ['input_snapshot_id', 'stock_id', 'strategy_code'], signals, ['snapshot_id', 'stock_id', 'strategy_code']),
    (snapshots, ['sync_task_id'], tasks, ['id']), (snapshots, ['base_snapshot_id'], snapshots, ['id']),
    (bars, ['snapshot_id'], snapshots, ['id']), (bars, ['stock_id'], stocks, ['stock_id']),
    (snapshot_stocks, ['snapshot_id'], snapshots, ['id']), (snapshot_stocks, ['stock_id'], stocks, ['stock_id']),
    (snapshot_stocks, ['industry_id'], categories, ['id']), (concepts, ['concept_id'], categories, ['id']),
    (concepts, ['snapshot_id', 'stock_id'], snapshot_stocks, ['snapshot_id', 'stock_id']),
    (signals, ['snapshot_id', 'stock_id'], snapshot_stocks, ['snapshot_id', 'stock_id']),
    (logs, ['task_id'], tasks, ['id']), (triggers, ['task_id'], tasks, ['id']),
    (idempotency, ['user_id'], users, ['id']), (idempotency, ['task_id'], tasks, ['id']),
    (financials, ['stock_id'], stocks, ['stock_id']), (materials, ['task_id'], tasks, ['id']),
    (materials, ['financial_report_id'], financials, ['id']), (results, ['task_id'], tasks, ['id']),
]:
    child.append_constraint(ForeignKeyConstraint(columns, [f'{parent.name}.{name}' for name in referenced]))
