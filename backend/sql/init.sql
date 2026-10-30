-- 本文件负责在空 PostgreSQL 15 数据库中初始化 StockSieve 的17张业务表、约束、索引及中文注释。
-- 默认目标数据库：stocksieve；PostgreSQL 在连接时通过 -d 选库，本文件不创建或切换数据库。
-- 在项目根目录执行（数据库须已创建）：
-- psql -X -h 127.0.0.1 -p 5432 -U stocksieve_admin -d stocksieve -v ON_ERROR_STOP=1 -f backend/sql/init.sql
-- 图形客户端请先连接 stocksieve 数据库，再执行整个文件；其他库或临时验证库须显式更换连接。
-- 每张表的 COMMENT ON TABLE / COMMENT ON COLUMN 紧随建表语句；错误时整个事务回滚。
-- 权限校验、资料冻结、发布事务及任务租约校验由后端实现，本脚本不插入演示账号或行情。

BEGIN;
SET LOCAL TIME ZONE 'UTC';

-- 拒绝在已有业务表、视图的库中混入初始结构，避免误操作其他数据库。
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_class AS relation
        JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public' AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
    ) THEN
        RAISE EXCEPTION 'public 已有表或视图，请使用空数据库执行初始化。';
    END IF;
END;
$$;

-- 1. 创建账户、证券和分类基础表。
CREATE TABLE public.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(20) NOT NULL UNIQUE,
    name VARCHAR(20) NOT NULL,
    password_hash TEXT NOT NULL,
    role VARCHAR(16) NOT NULL DEFAULT 'user',
    password_changed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT users_username_check CHECK (username = lower(btrim(username)) AND username ~ '^[a-z][a-z0-9_]{2,19}$'),
    CONSTRAINT users_name_check CHECK (char_length(btrim(name)) BETWEEN 1 AND 20),
    CONSTRAINT users_password_hash_check CHECK (btrim(password_hash) <> ''),
    CONSTRAINT users_role_check CHECK (role IN ('admin', 'user'))
);
COMMENT ON TABLE public.users IS '用户';
COMMENT ON COLUMN public.users.id IS '用户 ID';
COMMENT ON COLUMN public.users.username IS '去空白、转小写后的账号';
COMMENT ON COLUMN public.users.name IS '用户名称';
COMMENT ON COLUMN public.users.password_hash IS '密码哈希，不保存明文';
COMMENT ON COLUMN public.users.role IS 'admin / user';
COMMENT ON COLUMN public.users.password_changed_at IS '最近设置密码时间';
COMMENT ON COLUMN public.users.created_at IS '创建时间';
COMMENT ON COLUMN public.users.updated_at IS '更新时间';
CREATE INDEX users_created_idx ON public.users (created_at DESC, id DESC);

CREATE TABLE public.user_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
    token_hash CHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    CONSTRAINT user_sessions_hash_check CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT user_sessions_expiry_check CHECK (expires_at > created_at),
    CONSTRAINT user_sessions_revocation_check CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);
COMMENT ON TABLE public.user_sessions IS '登录会话';
COMMENT ON COLUMN public.user_sessions.id IS '会话 ID';
COMMENT ON COLUMN public.user_sessions.user_id IS '所属用户';
COMMENT ON COLUMN public.user_sessions.token_hash IS '随机访问令牌的摘要';
COMMENT ON COLUMN public.user_sessions.created_at IS '创建时间';
COMMENT ON COLUMN public.user_sessions.expires_at IS '到期时间，按接口有效期设置';
COMMENT ON COLUMN public.user_sessions.revoked_at IS '退出或改密后的撤销时间';
CREATE INDEX user_sessions_user_idx ON public.user_sessions (user_id);

CREATE TABLE public.stocks (
    stock_id VARCHAR(16) PRIMARY KEY,
    code VARCHAR(6) NOT NULL,
    name VARCHAR(64) NOT NULL,
    exchange VARCHAR(4) NOT NULL,
    board VARCHAR(16),
    listed_on DATE,
    delisted_on DATE,
    source_codes JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT stocks_exchange_code_uq UNIQUE (exchange, code),
    CONSTRAINT stocks_id_check CHECK (btrim(stock_id) <> ''),
    CONSTRAINT stocks_code_check CHECK (code ~ '^[0-9]{6}$'),
    CONSTRAINT stocks_name_check CHECK (btrim(name) <> ''),
    CONSTRAINT stocks_exchange_check CHECK (exchange IN ('SSE', 'SZSE', 'BSE')),
    CONSTRAINT stocks_board_check CHECK (
        board IS NULL OR (exchange = 'SSE' AND board IN ('SSE_MAIN', 'STAR'))
        OR (exchange = 'SZSE' AND board IN ('SZSE_MAIN', 'CHINEXT'))
        OR (exchange = 'BSE' AND board = 'BSE')
    ),
    CONSTRAINT stocks_listing_dates_check CHECK (listed_on IS NULL OR delisted_on IS NULL OR delisted_on >= listed_on),
    CONSTRAINT stocks_source_codes_check CHECK (jsonb_typeof(source_codes) = 'object')
);
COMMENT ON TABLE public.stocks IS '股票基础信息';
COMMENT ON COLUMN public.stocks.stock_id IS '稳定 ID，如 SZSE.300750';
COMMENT ON COLUMN public.stocks.code IS '股票代码，保留前导零';
COMMENT ON COLUMN public.stocks.name IS '当前股票名称';
COMMENT ON COLUMN public.stocks.exchange IS 'SSE / SZSE / BSE';
COMMENT ON COLUMN public.stocks.board IS '当前上市板块，取值见[筛选选项](../../api/market/filters.md)，未确认时为 NULL';
COMMENT ON COLUMN public.stocks.listed_on IS '上市、退市日期';
COMMENT ON COLUMN public.stocks.delisted_on IS '上市、退市日期';
COMMENT ON COLUMN public.stocks.source_codes IS '各来源的股票代码映射';
COMMENT ON COLUMN public.stocks.created_at IS '创建时间';
COMMENT ON COLUMN public.stocks.updated_at IS '更新时间';

CREATE TABLE public.categories (
    id VARCHAR(64) PRIMARY KEY,
    kind VARCHAR(16) NOT NULL,
    name VARCHAR(80) NOT NULL,
    source_codes JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT categories_id_check CHECK (btrim(id) <> ''),
    CONSTRAINT categories_kind_check CHECK (kind IN ('industry', 'concept')),
    CONSTRAINT categories_name_check CHECK (btrim(name) <> ''),
    CONSTRAINT categories_source_codes_check CHECK (jsonb_typeof(source_codes) = 'object')
);
COMMENT ON TABLE public.categories IS '行业与概念字典';
COMMENT ON COLUMN public.categories.id IS '稳定分类 ID';
COMMENT ON COLUMN public.categories.kind IS 'industry / concept';
COMMENT ON COLUMN public.categories.name IS '当前名称';
COMMENT ON COLUMN public.categories.source_codes IS '各来源的分类代码映射';
COMMENT ON COLUMN public.categories.created_at IS '创建时间';
COMMENT ON COLUMN public.categories.updated_at IS '更新时间';

CREATE TABLE public.trading_calendar (
    exchange VARCHAR(4) NOT NULL,
    trade_date DATE NOT NULL,
    is_open BOOLEAN,
    close_at TIMESTAMPTZ,
    source VARCHAR(32) NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (exchange, trade_date),
    CONSTRAINT trading_calendar_exchange_check CHECK (exchange IN ('SSE', 'SZSE', 'BSE')),
    CONSTRAINT trading_calendar_source_check CHECK (btrim(source) <> ''),
    CONSTRAINT trading_calendar_closed_check CHECK (is_open IS DISTINCT FROM FALSE OR close_at IS NULL),
    CONSTRAINT trading_calendar_close_date_check CHECK (close_at IS NULL OR (close_at AT TIME ZONE 'Asia/Shanghai')::DATE = trade_date)
);
COMMENT ON TABLE public.trading_calendar IS '当前交易日历';
COMMENT ON COLUMN public.trading_calendar.exchange IS '所属交易所';
COMMENT ON COLUMN public.trading_calendar.trade_date IS '北京时间自然日期';
COMMENT ON COLUMN public.trading_calendar.is_open IS 'true 开市、false 确认休市、NULL 未确认';
COMMENT ON COLUMN public.trading_calendar.close_at IS '该日收盘时点';
COMMENT ON COLUMN public.trading_calendar.source IS '日历来源';
COMMENT ON COLUMN public.trading_calendar.fetched_at IS '最近取得时间';

-- 2. 先创建任务表；对行情快照的循环外键在所有表创建后补齐。
CREATE TABLE public.tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category VARCHAR(16) NOT NULL,
    kind VARCHAR(32) NOT NULL,
    created_by UUID REFERENCES public.users(id) ON DELETE RESTRICT,
    trigger VARCHAR(16) NOT NULL DEFAULT 'manual',
    data_source VARCHAR(32),
    target_date DATE,
    stock_id VARCHAR(16) REFERENCES public.stocks(stock_id) ON DELETE RESTRICT,
    input_snapshot_id UUID,
    strategy_code VARCHAR(16),
    status VARCHAR(16) NOT NULL DEFAULT 'queued',
    progress SMALLINT NOT NULL DEFAULT 0,
    dedupe_key VARCHAR(255) NOT NULL,
    scheduled_for TIMESTAMPTZ,
    run_after TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    wait_reason VARCHAR(64),
    retry_deadline_at TIMESTAMPTZ,
    retry_count INTEGER NOT NULL DEFAULT 0,
    precheck_json JSONB,
    options_json JSONB NOT NULL DEFAULT '{}'::JSONB,
    attempt_no INTEGER NOT NULL DEFAULT 0,
    lease_owner VARCHAR(64),
    lease_expires_at TIMESTAMPTZ,
    result_json JSONB,
    result_snapshot_id UUID,
    error_code VARCHAR(64),
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    CONSTRAINT tasks_category_kind_check CHECK (
        (category = 'sync' AND kind IN ('initialize', 'daily')
            AND data_source IS NOT NULL AND btrim(data_source) <> ''
            AND stock_id IS NULL AND input_snapshot_id IS NULL AND strategy_code IS NULL
            AND ((kind = 'initialize' AND target_date IS NULL) OR (kind = 'daily' AND target_date IS NOT NULL)))
        OR (category = 'analysis' AND kind IN ('fundamental', 'chips', 'trade')
            AND stock_id IS NOT NULL AND input_snapshot_id IS NOT NULL AND target_date IS NOT NULL
            AND data_source IS NULL AND result_snapshot_id IS NULL AND result_json IS NULL
            AND ((kind = 'trade' AND strategy_code IS NOT NULL AND strategy_code IN ('short', 'swing', 'long', 'trend_wait', 'base_wait'))
                OR (kind <> 'trade' AND strategy_code IS NULL)))
    ),
    CONSTRAINT tasks_trigger_check CHECK (
        (trigger = 'manual' AND created_by IS NOT NULL AND scheduled_for IS NULL)
        OR (trigger = 'scheduled' AND category = 'sync' AND kind = 'daily' AND created_by IS NULL AND scheduled_for IS NOT NULL)
    ),
    CONSTRAINT tasks_scheduled_date_check CHECK (trigger <> 'scheduled' OR (scheduled_for AT TIME ZONE 'Asia/Shanghai')::DATE = target_date),
    CONSTRAINT tasks_status_check CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    CONSTRAINT tasks_progress_check CHECK (progress BETWEEN 0 AND 100),
    CONSTRAINT tasks_dedupe_key_check CHECK (btrim(dedupe_key) <> ''),
    CONSTRAINT tasks_attempts_check CHECK (attempt_no >= 0 AND retry_count >= 0),
    CONSTRAINT tasks_state_check CHECK (
        (status IN ('queued', 'running') AND finished_at IS NULL AND error_code IS NULL AND error_message IS NULL)
        OR (status = 'succeeded' AND progress = 100 AND finished_at IS NOT NULL AND error_code IS NULL AND error_message IS NULL)
        OR (status = 'failed' AND finished_at IS NOT NULL AND error_code IS NOT NULL AND btrim(error_code) <> '' AND error_message IS NOT NULL AND btrim(error_message) <> '')
    ),
    CONSTRAINT tasks_time_check CHECK ((started_at IS NULL OR started_at >= created_at) AND (finished_at IS NULL OR finished_at >= created_at) AND (started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at)),
    CONSTRAINT tasks_lease_check CHECK (
        (status = 'running' AND lease_owner IS NOT NULL AND btrim(lease_owner) <> '' AND lease_expires_at IS NOT NULL)
        OR (status <> 'running' AND lease_owner IS NULL AND lease_expires_at IS NULL)
    ),
    CONSTRAINT tasks_wait_check CHECK (
        wait_reason IS NULL OR (category = 'sync' AND kind = 'daily' AND status = 'queued'
            AND wait_reason = 'BOARD_DATA_NOT_READY' AND precheck_json IS NOT NULL
            AND retry_deadline_at IS NOT NULL AND run_after <= retry_deadline_at)
    ),
    CONSTRAINT tasks_readiness_scope_check CHECK (
        (category = 'sync' AND kind = 'daily') OR
        (wait_reason IS NULL AND retry_deadline_at IS NULL AND precheck_json IS NULL AND retry_count = 0)
    ),
    CONSTRAINT tasks_options_check CHECK (jsonb_typeof(options_json) = 'object'),
    CONSTRAINT tasks_precheck_check CHECK (precheck_json IS NULL OR jsonb_typeof(precheck_json) = 'object'),
    CONSTRAINT tasks_result_check CHECK (result_json IS NULL OR jsonb_typeof(result_json) = 'object'),
    CONSTRAINT tasks_result_snapshot_scope_check CHECK (result_snapshot_id IS NULL OR (category = 'sync' AND kind = 'daily' AND status = 'succeeded'))
);
COMMENT ON TABLE public.tasks IS '后台任务';
COMMENT ON COLUMN public.tasks.id IS '同步任务或分析任务 ID';
COMMENT ON COLUMN public.tasks.category IS 'sync / analysis';
COMMENT ON COLUMN public.tasks.kind IS '同步为 initialize / daily；分析为 fundamental / chips / trade';
COMMENT ON COLUMN public.tasks.created_by IS '手动创建者；系统定时同步为 NULL';
COMMENT ON COLUMN public.tasks.trigger IS 'manual / scheduled';
COMMENT ON COLUMN public.tasks.data_source IS '同步来源，映射 API 的 source';
COMMENT ON COLUMN public.tasks.target_date IS '固定目标日期；初始化为 NULL';
COMMENT ON COLUMN public.tasks.stock_id IS '分析股票';
COMMENT ON COLUMN public.tasks.input_snapshot_id IS '分析输入快照';
COMMENT ON COLUMN public.tasks.strategy_code IS '买卖条件分析使用的策略';
COMMENT ON COLUMN public.tasks.status IS 'queued / running / succeeded / failed，默认queued';
COMMENT ON COLUMN public.tasks.progress IS '0～100，默认0';
COMMENT ON COLUMN public.tasks.dedupe_key IS '非终态任务去重键';
COMMENT ON COLUMN public.tasks.scheduled_for IS '定时任务原计划时间';
COMMENT ON COLUMN public.tasks.run_after IS '最早可领取时间；等待就绪时映射 nextRetryAt';
COMMENT ON COLUMN public.tasks.wait_reason IS '就绪等待原因';
COMMENT ON COLUMN public.tasks.retry_deadline_at IS '就绪等待截止时间，终态保留';
COMMENT ON COLUMN public.tasks.retry_count IS '已完成的重试预检次数，默认0';
COMMENT ON COLUMN public.tasks.precheck_json IS '最近一轮五板块检查结果';
COMMENT ON COLUMN public.tasks.options_json IS '受理时固定的执行设置，如重试间隔和最长等待时长';
COMMENT ON COLUMN public.tasks.attempt_no IS '执行领取次数，默认0；用于阻止旧执行者回写';
COMMENT ON COLUMN public.tasks.lease_owner IS '当前执行者标识';
COMMENT ON COLUMN public.tasks.lease_expires_at IS '执行租约截止时间';
COMMENT ON COLUMN public.tasks.result_json IS '同步 outcome 和股票计数；分析正文另存';
COMMENT ON COLUMN public.tasks.result_snapshot_id IS '新发布或复用的快照，映射 result.publishedSnapshotId';
COMMENT ON COLUMN public.tasks.error_code IS '失败原因';
COMMENT ON COLUMN public.tasks.error_message IS '失败原因';
COMMENT ON COLUMN public.tasks.created_at IS '创建时间';
COMMENT ON COLUMN public.tasks.started_at IS '首次执行、终态时间';
COMMENT ON COLUMN public.tasks.finished_at IS '首次执行、终态时间';
CREATE UNIQUE INDEX tasks_active_dedupe_uq ON public.tasks (dedupe_key) WHERE status IN ('queued', 'running');
CREATE UNIQUE INDEX tasks_active_sync_scope_uq ON public.tasks (kind, (COALESCE(target_date, DATE '0001-01-01'))) WHERE category = 'sync' AND status IN ('queued', 'running');
CREATE UNIQUE INDEX tasks_active_analysis_scope_uq ON public.tasks (created_by, stock_id, input_snapshot_id, kind, (COALESCE(strategy_code, ''))) WHERE category = 'analysis' AND status IN ('queued', 'running');
CREATE UNIQUE INDEX tasks_one_running_sync_uq ON public.tasks (category) WHERE category = 'sync' AND status = 'running';
CREATE INDEX tasks_ready_idx ON public.tasks (status, run_after);
CREATE INDEX tasks_category_history_idx ON public.tasks (category, created_at DESC, id DESC);
CREATE INDEX tasks_owner_history_idx ON public.tasks (created_by, category, stock_id, kind, created_at DESC, id DESC);

-- 3. 行情版本、原始日线和当日展示视图。
CREATE SEQUENCE public.snapshot_version_seq AS BIGINT START WITH 1;

CREATE TABLE public.data_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sync_task_id UUID NOT NULL UNIQUE REFERENCES public.tasks(id) ON DELETE RESTRICT,
    base_snapshot_id UUID REFERENCES public.data_snapshots(id) ON DELETE RESTRICT,
    version BIGINT UNIQUE,
    data_date DATE NOT NULL,
    source VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'building',
    adjustment VARCHAR(8) NOT NULL DEFAULT 'qfq',
    strategy_version VARCHAR(32) NOT NULL,
    calculation_version VARCHAR(32) NOT NULL,
    strategy_config_json JSONB NOT NULL,
    history_start_date DATE,
    calendar_json JSONB,
    coverage_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMPTZ,
    CONSTRAINT data_snapshots_status_check CHECK (status IN ('building', 'published', 'failed')),
    CONSTRAINT data_snapshots_adjustment_check CHECK (adjustment = 'qfq'),
    CONSTRAINT data_snapshots_source_check CHECK (btrim(source) <> ''),
    CONSTRAINT data_snapshots_versions_check CHECK (btrim(strategy_version) <> '' AND btrim(calculation_version) <> ''),
    CONSTRAINT data_snapshots_publication_check CHECK (
        (status = 'published' AND version IS NOT NULL AND version > 0 AND published_at IS NOT NULL
            AND history_start_date IS NOT NULL AND calendar_json IS NOT NULL AND coverage_json IS NOT NULL)
        OR (status IN ('building', 'failed') AND version IS NULL AND published_at IS NULL)
    ),
    CONSTRAINT data_snapshots_base_check CHECK (base_snapshot_id IS NULL OR base_snapshot_id <> id),
    CONSTRAINT data_snapshots_dates_check CHECK (history_start_date IS NULL OR history_start_date <= data_date),
    CONSTRAINT data_snapshots_publish_time_check CHECK (published_at IS NULL OR published_at >= created_at),
    CONSTRAINT data_snapshots_strategy_config_check CHECK (jsonb_typeof(strategy_config_json) = 'object'),
    CONSTRAINT data_snapshots_calendar_check CHECK (calendar_json IS NULL OR jsonb_typeof(calendar_json) = 'object'),
    CONSTRAINT data_snapshots_coverage_check CHECK (coverage_json IS NULL OR jsonb_typeof(coverage_json) = 'object')
);
COMMENT ON TABLE public.data_snapshots IS '行情发布快照';
COMMENT ON COLUMN public.data_snapshots.id IS 'API 的 snapshotId';
COMMENT ON COLUMN public.data_snapshots.sync_task_id IS '创建本快照的收盘同步任务';
COMMENT ON COLUMN public.data_snapshots.base_snapshot_id IS '开始构建时 version 最大的已发布快照';
COMMENT ON COLUMN public.data_snapshots.version IS '发布顺序号，发布事务内分配';
COMMENT ON COLUMN public.data_snapshots.data_date IS '本次数据日期';
COMMENT ON COLUMN public.data_snapshots.source IS '本次主数据源';
COMMENT ON COLUMN public.data_snapshots.status IS 'building / published / failed';
COMMENT ON COLUMN public.data_snapshots.adjustment IS 'V1 固定 qfq';
COMMENT ON COLUMN public.data_snapshots.strategy_version IS '策略计算版本';
COMMENT ON COLUMN public.data_snapshots.calculation_version IS '指标与复权计算实现版本';
COMMENT ON COLUMN public.data_snapshots.strategy_config_json IS '当次策略参数和说明快照';
COMMENT ON COLUMN public.data_snapshots.history_start_date IS '本快照支持的最早历史日期';
COMMENT ON COLUMN public.data_snapshots.calendar_json IS '历史范围内所用日历及周、年边界视图';
COMMENT ON COLUMN public.data_snapshots.coverage_json IS '总股票数、各市场与指标覆盖情况';
COMMENT ON COLUMN public.data_snapshots.created_at IS '创建时间';
COMMENT ON COLUMN public.data_snapshots.published_at IS '发布时间';
CREATE INDEX data_snapshots_latest_idx ON public.data_snapshots (status, data_date DESC, published_at DESC);

CREATE TABLE public.daily_bars (
    snapshot_id UUID NOT NULL REFERENCES public.data_snapshots(id) ON DELETE RESTRICT,
    stock_id VARCHAR(16) NOT NULL REFERENCES public.stocks(stock_id) ON DELETE RESTRICT,
    trade_date DATE NOT NULL,
    open NUMERIC(18,6) NOT NULL,
    high NUMERIC(18,6) NOT NULL,
    low NUMERIC(18,6) NOT NULL,
    close NUMERIC(18,6) NOT NULL,
    volume BIGINT,
    amount NUMERIC(24,4),
    turnover NUMERIC(12,6),
    total_market_cap NUMERIC(24,4),
    total_shares BIGINT,
    float_shares BIGINT,
    adj_factor NUMERIC(24,12),
    source VARCHAR(32) NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_id, stock_id, trade_date),
    CONSTRAINT daily_bars_prices_check CHECK (low > 0 AND high >= low AND open BETWEEN low AND high AND close BETWEEN low AND high),
    CONSTRAINT daily_bars_amounts_check CHECK ((volume IS NULL OR volume >= 0) AND (amount IS NULL OR amount >= 0) AND (turnover IS NULL OR turnover >= 0) AND (total_market_cap IS NULL OR total_market_cap >= 0)),
    CONSTRAINT daily_bars_shares_check CHECK ((total_shares IS NULL OR total_shares >= 0) AND (float_shares IS NULL OR float_shares >= 0) AND (total_shares IS NULL OR float_shares IS NULL OR float_shares <= total_shares)),
    CONSTRAINT daily_bars_factor_check CHECK (adj_factor IS NULL OR adj_factor > 0),
    CONSTRAINT daily_bars_finite_check CHECK (NOT ('NaN'::NUMERIC = ANY (ARRAY[open, high, low, close, amount, turnover, total_market_cap, adj_factor]))),
    CONSTRAINT daily_bars_source_check CHECK (btrim(source) <> '')
);
COMMENT ON TABLE public.daily_bars IS '日线修订记录';
COMMENT ON COLUMN public.daily_bars.snapshot_id IS '写入该版本的快照';
COMMENT ON COLUMN public.daily_bars.stock_id IS '股票';
COMMENT ON COLUMN public.daily_bars.trade_date IS '行情日期';
COMMENT ON COLUMN public.daily_bars.open IS '未复权 OHLC';
COMMENT ON COLUMN public.daily_bars.high IS '未复权 OHLC';
COMMENT ON COLUMN public.daily_bars.low IS '未复权 OHLC';
COMMENT ON COLUMN public.daily_bars.close IS '未复权 OHLC';
COMMENT ON COLUMN public.daily_bars.volume IS '原始成交股数';
COMMENT ON COLUMN public.daily_bars.amount IS '原始成交额，元';
COMMENT ON COLUMN public.daily_bars.turnover IS '换手率，百分数';
COMMENT ON COLUMN public.daily_bars.total_market_cap IS '当日总市值，元';
COMMENT ON COLUMN public.daily_bars.total_shares IS '同期总股本、流通股本';
COMMENT ON COLUMN public.daily_bars.float_shares IS '同期总股本、流通股本';
COMMENT ON COLUMN public.daily_bars.adj_factor IS '统一口径的累计复权因子';
COMMENT ON COLUMN public.daily_bars.source IS '实际采用的数据来源';
COMMENT ON COLUMN public.daily_bars.fetched_at IS '取得时间';
CREATE INDEX daily_bars_history_idx ON public.daily_bars (stock_id, trade_date DESC, snapshot_id);

CREATE TABLE public.snapshot_stocks (
    snapshot_id UUID NOT NULL REFERENCES public.data_snapshots(id) ON DELETE RESTRICT,
    stock_id VARCHAR(16) NOT NULL REFERENCES public.stocks(stock_id) ON DELETE RESTRICT,
    code VARCHAR(6) NOT NULL,
    name VARCHAR(64) NOT NULL,
    exchange VARCHAR(4) NOT NULL,
    board VARCHAR(16) NOT NULL,
    industry_id VARCHAR(64) REFERENCES public.categories(id) ON DELETE RESTRICT,
    industry_name VARCHAR(80),
    listed_on DATE,
    trade_status VARCHAR(16),
    is_st BOOLEAN,
    is_delisting BOOLEAN,
    quote_price NUMERIC(18,6),
    change_pct NUMERIC(12,6),
    turnover NUMERIC(12,6),
    amount NUMERIC(24,4),
    total_market_cap NUMERIC(24,4),
    ma5 NUMERIC(18,6),
    ma10 NUMERIC(18,6),
    ma20 NUMERIC(18,6),
    ma60 NUMERIC(18,6),
    ma120 NUMERIC(18,6),
    excluded_reason VARCHAR(64),
    PRIMARY KEY (snapshot_id, stock_id),
    CONSTRAINT snapshot_stocks_code_check CHECK (code ~ '^[0-9]{6}$'),
    CONSTRAINT snapshot_stocks_name_check CHECK (btrim(name) <> ''),
    CONSTRAINT snapshot_stocks_exchange_check CHECK (exchange IN ('SSE', 'SZSE', 'BSE')),
    CONSTRAINT snapshot_stocks_board_check CHECK (
        (exchange = 'SSE' AND board IN ('SSE_MAIN', 'STAR'))
        OR (exchange = 'SZSE' AND board IN ('SZSE_MAIN', 'CHINEXT'))
        OR (exchange = 'BSE' AND board = 'BSE')
    ),
    CONSTRAINT snapshot_stocks_industry_check CHECK ((industry_id IS NULL) = (industry_name IS NULL)),
    CONSTRAINT snapshot_stocks_trade_status_check CHECK (trade_status IS NULL OR trade_status IN ('trading', 'suspended', 'delisted')),
    CONSTRAINT snapshot_stocks_amounts_check CHECK ((quote_price IS NULL OR quote_price > 0) AND (turnover IS NULL OR turnover >= 0) AND (amount IS NULL OR amount >= 0) AND (total_market_cap IS NULL OR total_market_cap >= 0)),
    CONSTRAINT snapshot_stocks_averages_check CHECK ((ma5 IS NULL OR ma5 > 0) AND (ma10 IS NULL OR ma10 > 0) AND (ma20 IS NULL OR ma20 > 0) AND (ma60 IS NULL OR ma60 > 0) AND (ma120 IS NULL OR ma120 > 0)),
    CONSTRAINT snapshot_stocks_finite_check CHECK (NOT ('NaN'::NUMERIC = ANY (ARRAY[quote_price, change_pct, turnover, amount, total_market_cap, ma5, ma10, ma20, ma60, ma120])))
);
COMMENT ON TABLE public.snapshot_stocks IS '快照股票与指标';
COMMENT ON COLUMN public.snapshot_stocks.snapshot_id IS '数据快照';
COMMENT ON COLUMN public.snapshot_stocks.stock_id IS '股票';
COMMENT ON COLUMN public.snapshot_stocks.code IS '冻结的代码、名称';
COMMENT ON COLUMN public.snapshot_stocks.name IS '冻结的代码、名称';
COMMENT ON COLUMN public.snapshot_stocks.exchange IS '冻结的交易所、板块';
COMMENT ON COLUMN public.snapshot_stocks.board IS '冻结的交易所、板块';
COMMENT ON COLUMN public.snapshot_stocks.industry_id IS '行业 ID';
COMMENT ON COLUMN public.snapshot_stocks.industry_name IS '当时的行业名称';
COMMENT ON COLUMN public.snapshot_stocks.listed_on IS '上市日期';
COMMENT ON COLUMN public.snapshot_stocks.trade_status IS 'trading / suspended / delisted，NULL 为未确认';
COMMENT ON COLUMN public.snapshot_stocks.is_st IS '当日 ST、退市整理状态';
COMMENT ON COLUMN public.snapshot_stocks.is_delisting IS '当日 ST、退市整理状态';
COMMENT ON COLUMN public.snapshot_stocks.quote_price IS '当日同口径收盘价，映射 API 的 price';
COMMENT ON COLUMN public.snapshot_stocks.change_pct IS '涨跌幅、换手率，百分数';
COMMENT ON COLUMN public.snapshot_stocks.turnover IS '涨跌幅、换手率，百分数';
COMMENT ON COLUMN public.snapshot_stocks.amount IS '原始成交额、总市值，元';
COMMENT ON COLUMN public.snapshot_stocks.total_market_cap IS '原始成交额、总市值，元';
COMMENT ON COLUMN public.snapshot_stocks.ma5 IS '当日收盘均线';
COMMENT ON COLUMN public.snapshot_stocks.ma10 IS '当日收盘均线';
COMMENT ON COLUMN public.snapshot_stocks.ma20 IS '当日收盘均线';
COMMENT ON COLUMN public.snapshot_stocks.ma60 IS '当日收盘均线';
COMMENT ON COLUMN public.snapshot_stocks.ma120 IS '当日收盘均线';
COMMENT ON COLUMN public.snapshot_stocks.excluded_reason IS '停牌、ST、历史不足、必要数据缺失等剔除原因';
CREATE INDEX snapshot_stocks_board_industry_idx ON public.snapshot_stocks (snapshot_id, board, industry_id);
CREATE INDEX snapshot_stocks_turnover_idx ON public.snapshot_stocks (snapshot_id, turnover);
CREATE INDEX snapshot_stocks_market_cap_idx ON public.snapshot_stocks (snapshot_id, total_market_cap);

CREATE TABLE public.snapshot_stock_concepts (
    snapshot_id UUID NOT NULL,
    stock_id VARCHAR(16) NOT NULL,
    concept_id VARCHAR(64) NOT NULL REFERENCES public.categories(id) ON DELETE RESTRICT,
    concept_name VARCHAR(80) NOT NULL,
    PRIMARY KEY (snapshot_id, stock_id, concept_id),
    FOREIGN KEY (snapshot_id, stock_id) REFERENCES public.snapshot_stocks(snapshot_id, stock_id) ON DELETE RESTRICT,
    CONSTRAINT snapshot_stock_concepts_name_check CHECK (btrim(concept_name) <> '')
);
COMMENT ON TABLE public.snapshot_stock_concepts IS '快照概念归属';
COMMENT ON COLUMN public.snapshot_stock_concepts.snapshot_id IS '快照';
COMMENT ON COLUMN public.snapshot_stock_concepts.stock_id IS '股票';
COMMENT ON COLUMN public.snapshot_stock_concepts.concept_id IS '概念 ID';
COMMENT ON COLUMN public.snapshot_stock_concepts.concept_name IS '冻结的概念名称';
CREATE INDEX snapshot_stock_concepts_filter_idx ON public.snapshot_stock_concepts (snapshot_id, concept_id, stock_id);

CREATE TABLE public.strategy_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id UUID NOT NULL,
    stock_id VARCHAR(16) NOT NULL,
    strategy_code VARCHAR(16) NOT NULL,
    reason TEXT NOT NULL,
    metrics_json JSONB NOT NULL,
    condition_results_json JSONB NOT NULL,
    invalidation_json JSONB NOT NULL,
    sort_value NUMERIC(24,8) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (snapshot_id, stock_id) REFERENCES public.snapshot_stocks(snapshot_id, stock_id) ON DELETE RESTRICT,
    CONSTRAINT strategy_signals_scope_uq UNIQUE (snapshot_id, stock_id, strategy_code),
    CONSTRAINT strategy_signals_code_check CHECK (strategy_code IN ('short', 'swing', 'long', 'trend_wait', 'base_wait')),
    CONSTRAINT strategy_signals_sort_value_check CHECK (sort_value <> 'NaN'::NUMERIC),
    CONSTRAINT strategy_signals_reason_check CHECK (btrim(reason) <> ''),
    CONSTRAINT strategy_signals_metrics_check CHECK (jsonb_typeof(metrics_json) = 'object'),
    CONSTRAINT strategy_signals_conditions_check CHECK (jsonb_typeof(condition_results_json) = 'array'),
    CONSTRAINT strategy_signals_invalidation_check CHECK (jsonb_typeof(invalidation_json) = 'object')
);
COMMENT ON TABLE public.strategy_signals IS '策略入选信号';
COMMENT ON COLUMN public.strategy_signals.id IS 'API 的 signal.id';
COMMENT ON COLUMN public.strategy_signals.snapshot_id IS '数据快照';
COMMENT ON COLUMN public.strategy_signals.stock_id IS '入选股票';
COMMENT ON COLUMN public.strategy_signals.strategy_code IS 'short / swing / long / trend_wait（趋势埋伏）/ base_wait（低位筑底）';
COMMENT ON COLUMN public.strategy_signals.reason IS '入选理由';
COMMENT ON COLUMN public.strategy_signals.metrics_json IS '该策略的具体指标';
COMMENT ON COLUMN public.strategy_signals.condition_results_json IS '条件 ID 及判断结果';
COMMENT ON COLUMN public.strategy_signals.invalidation_json IS '失效说明和固定参考价';
COMMENT ON COLUMN public.strategy_signals.sort_value IS '策略默认排序指标';
COMMENT ON COLUMN public.strategy_signals.created_at IS '创建时间';
CREATE INDEX strategy_signals_sort_idx ON public.strategy_signals (snapshot_id, strategy_code, sort_value, stock_id);

-- 4. 补齐任务的快照引用，以及分析股票、策略与输入快照的组合外键。
ALTER TABLE public.tasks
    ADD CONSTRAINT tasks_input_snapshot_fk FOREIGN KEY (input_snapshot_id) REFERENCES public.data_snapshots(id) ON DELETE RESTRICT,
    ADD CONSTRAINT tasks_result_snapshot_fk FOREIGN KEY (result_snapshot_id) REFERENCES public.data_snapshots(id) ON DELETE RESTRICT,
    ADD CONSTRAINT tasks_input_stock_fk FOREIGN KEY (input_snapshot_id, stock_id) REFERENCES public.snapshot_stocks(snapshot_id, stock_id) ON DELETE RESTRICT,
    ADD CONSTRAINT tasks_input_signal_fk FOREIGN KEY (input_snapshot_id, stock_id, strategy_code) REFERENCES public.strategy_signals(snapshot_id, stock_id, strategy_code) ON DELETE RESTRICT;

-- 5. 创建调度、日志与请求幂等表。
CREATE TABLE public.task_logs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES public.tasks(id) ON DELETE RESTRICT,
    time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    level VARCHAR(16) NOT NULL,
    message TEXT NOT NULL,
    details_json JSONB,
    CONSTRAINT task_logs_level_check CHECK (level IN ('info', 'warning', 'error')),
    CONSTRAINT task_logs_message_check CHECK (btrim(message) <> ''),
    CONSTRAINT task_logs_details_check CHECK (details_json IS NULL OR jsonb_typeof(details_json) = 'object')
);
COMMENT ON TABLE public.task_logs IS '任务日志';
COMMENT ON COLUMN public.task_logs.id IS '日志游标；接口转为字符串';
COMMENT ON COLUMN public.task_logs.task_id IS '所属任务';
COMMENT ON COLUMN public.task_logs.time IS '记录时间';
COMMENT ON COLUMN public.task_logs.level IS 'info / warning / error';
COMMENT ON COLUMN public.task_logs.message IS '可读过程、等待或失败说明';
COMMENT ON COLUMN public.task_logs.details_json IS '分板块预检历史、阶段统计等诊断内容';
CREATE INDEX task_logs_cursor_idx ON public.task_logs (task_id, id);

CREATE TABLE public.sync_triggers (
    scheduled_date DATE PRIMARY KEY,
    scheduled_for TIMESTAMPTZ NOT NULL,
    task_id UUID REFERENCES public.tasks(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    linked_at TIMESTAMPTZ,
    last_error TEXT,
    CONSTRAINT sync_triggers_link_check CHECK ((task_id IS NULL) = (linked_at IS NULL)),
    CONSTRAINT sync_triggers_date_check CHECK ((scheduled_for AT TIME ZONE 'Asia/Shanghai')::DATE = scheduled_date),
    CONSTRAINT sync_triggers_time_check CHECK ((scheduled_for AT TIME ZONE 'Asia/Shanghai')::TIME = TIME '18:00:00')
);
COMMENT ON TABLE public.sync_triggers IS '每日计划触发';
COMMENT ON COLUMN public.sync_triggers.scheduled_date IS '北京时间计划日期，每日一条';
COMMENT ON COLUMN public.sync_triggers.scheduled_for IS '当日北京时间18:00对应的时点';
COMMENT ON COLUMN public.sync_triggers.task_id IS '新建或复用的日同步任务';
COMMENT ON COLUMN public.sync_triggers.created_at IS '创建时间';
COMMENT ON COLUMN public.sync_triggers.linked_at IS '完成任务关联的时间';
COMMENT ON COLUMN public.sync_triggers.last_error IS '尚未关联成功的原因';
CREATE INDEX sync_triggers_task_idx ON public.sync_triggers (task_id);

CREATE TABLE public.idempotency_requests (
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
    http_method VARCHAR(8) NOT NULL,
    request_path VARCHAR(256) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    request_hash CHAR(64) NOT NULL,
    task_id UUID NOT NULL REFERENCES public.tasks(id) ON DELETE RESTRICT,
    response_status SMALLINT NOT NULL DEFAULT 202,
    response_data JSONB NOT NULL,
    response_headers JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (CURRENT_TIMESTAMP + INTERVAL '24 hours'),
    PRIMARY KEY (user_id, http_method, request_path, idempotency_key),
    CONSTRAINT idempotency_requests_method_check CHECK (http_method = 'POST'),
    CONSTRAINT idempotency_requests_path_check CHECK (request_path LIKE '/api/v1/%'),
    CONSTRAINT idempotency_requests_key_check CHECK (char_length(idempotency_key) BETWEEN 8 AND 128),
    CONSTRAINT idempotency_requests_hash_check CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT idempotency_requests_status_check CHECK (response_status = 202),
    CONSTRAINT idempotency_requests_expiry_check CHECK (expires_at > created_at),
    CONSTRAINT idempotency_requests_data_check CHECK (jsonb_typeof(response_data) = 'object' AND NOT (response_data ? 'requestId')),
    CONSTRAINT idempotency_requests_headers_check CHECK (jsonb_typeof(response_headers) = 'object')
);
COMMENT ON TABLE public.idempotency_requests IS '请求幂等';
COMMENT ON COLUMN public.idempotency_requests.user_id IS '请求用户';
COMMENT ON COLUMN public.idempotency_requests.http_method IS 'HTTP 方法';
COMMENT ON COLUMN public.idempotency_requests.request_path IS '实际请求路径，包含股票 ID 等路径值';
COMMENT ON COLUMN public.idempotency_requests.idempotency_key IS '客户端幂等键';
COMMENT ON COLUMN public.idempotency_requests.request_hash IS '规范化请求体摘要';
COMMENT ON COLUMN public.idempotency_requests.task_id IS '已受理任务';
COMMENT ON COLUMN public.idempotency_requests.response_status IS '原受理状态，当前为202';
COMMENT ON COLUMN public.idempotency_requests.response_data IS '原受理响应的 data，不包含 requestId';
COMMENT ON COLUMN public.idempotency_requests.response_headers IS '需重放的 Location 等响应头，不保存令牌和追踪 ID';
COMMENT ON COLUMN public.idempotency_requests.created_at IS '创建时间';
COMMENT ON COLUMN public.idempotency_requests.expires_at IS '创建、过期时间，当前有效期24小时';
CREATE INDEX idempotency_requests_expiry_idx ON public.idempotency_requests (expires_at);

-- 6. 创建财务资料、分析输入和页面结果表。
CREATE TABLE public.financial_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stock_id VARCHAR(16) NOT NULL REFERENCES public.stocks(stock_id) ON DELETE RESTRICT,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    statement_scope VARCHAR(16) NOT NULL,
    revenue_ytd NUMERIC(24,4),
    net_profit_parent_ytd NUMERIC(24,4),
    operating_cashflow_ytd NUMERIC(24,4),
    equity_parent_end NUMERIC(24,4),
    extra_json JSONB,
    source VARCHAR(32) NOT NULL,
    source_url TEXT,
    published_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    content_hash CHAR(64) NOT NULL,
    raw_payload_json JSONB NOT NULL,
    CONSTRAINT financial_reports_content_uq UNIQUE (stock_id, period_start, period_end, statement_scope, source, content_hash),
    CONSTRAINT financial_reports_period_check CHECK (period_start <= period_end),
    CONSTRAINT financial_reports_scope_check CHECK (statement_scope IN ('consolidated', 'parent')),
    CONSTRAINT financial_reports_source_check CHECK (btrim(source) <> '' AND (source_url IS NULL OR source_url ~* '^https?://')),
    CONSTRAINT financial_reports_hash_check CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT financial_reports_finite_check CHECK (NOT ('NaN'::NUMERIC = ANY (ARRAY[revenue_ytd, net_profit_parent_ytd, operating_cashflow_ytd, equity_parent_end]))),
    CONSTRAINT financial_reports_extra_check CHECK (extra_json IS NULL OR jsonb_typeof(extra_json) = 'object'),
    CONSTRAINT financial_reports_payload_check CHECK (jsonb_typeof(raw_payload_json) = 'object')
);
COMMENT ON TABLE public.financial_reports IS '财务报表版本';
COMMENT ON COLUMN public.financial_reports.id IS '财务记录版本 ID';
COMMENT ON COLUMN public.financial_reports.stock_id IS '股票';
COMMENT ON COLUMN public.financial_reports.period_start IS '累计报告期起止日期';
COMMENT ON COLUMN public.financial_reports.period_end IS '累计报告期起止日期';
COMMENT ON COLUMN public.financial_reports.statement_scope IS 'consolidated 合并、parent 母公司口径';
COMMENT ON COLUMN public.financial_reports.revenue_ytd IS '年初累计营业收入，元';
COMMENT ON COLUMN public.financial_reports.net_profit_parent_ytd IS '年初累计归母净利润，元';
COMMENT ON COLUMN public.financial_reports.operating_cashflow_ytd IS '年初累计经营现金流，元';
COMMENT ON COLUMN public.financial_reports.equity_parent_end IS '期末归母权益，元';
COMMENT ON COLUMN public.financial_reports.extra_json IS '来源提供的其他指标及口径';
COMMENT ON COLUMN public.financial_reports.source IS '来源标识、公开地址，不含认证信息';
COMMENT ON COLUMN public.financial_reports.source_url IS '来源标识、公开地址，不含认证信息';
COMMENT ON COLUMN public.financial_reports.published_at IS '对外披露时间，未知不补造';
COMMENT ON COLUMN public.financial_reports.fetched_at IS '本次取得时间';
COMMENT ON COLUMN public.financial_reports.content_hash IS '标准化内容摘要';
COMMENT ON COLUMN public.financial_reports.raw_payload_json IS '来源原值与单位，供核对';
CREATE INDEX financial_reports_lookup_idx ON public.financial_reports (stock_id, period_end DESC, published_at DESC, fetched_at DESC);

CREATE TABLE public.analysis_materials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES public.tasks(id) ON DELETE RESTRICT,
    material_key VARCHAR(128) NOT NULL,
    material_kind VARCHAR(32) NOT NULL,
    financial_report_id UUID REFERENCES public.financial_reports(id) ON DELETE RESTRICT,
    source_name TEXT NOT NULL,
    source_url TEXT,
    data_date DATE,
    published_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(16) NOT NULL,
    content_json JSONB,
    content_text TEXT,
    content_hash CHAR(64),
    missing_reason TEXT,
    CONSTRAINT analysis_materials_key_uq UNIQUE (task_id, material_key),
    CONSTRAINT analysis_materials_key_check CHECK (btrim(material_key) <> '' AND btrim(material_kind) <> ''),
    CONSTRAINT analysis_materials_source_check CHECK (btrim(source_name) <> '' AND (source_url IS NULL OR source_url ~* '^https?://')),
    CONSTRAINT analysis_materials_status_check CHECK (status IN ('available', 'missing', 'failed')),
    CONSTRAINT analysis_materials_content_check CHECK (
        (status = 'available' AND content_hash IS NOT NULL
            AND ((content_json IS NOT NULL AND jsonb_typeof(content_json) IN ('object', 'array')) OR (content_text IS NOT NULL AND btrim(content_text) <> '')))
        OR (status IN ('missing', 'failed') AND missing_reason IS NOT NULL AND btrim(missing_reason) <> '')
    ),
    CONSTRAINT analysis_materials_hash_check CHECK (content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$')
);
COMMENT ON TABLE public.analysis_materials IS '分析输入资料';
COMMENT ON COLUMN public.analysis_materials.id IS '资料 ID';
COMMENT ON COLUMN public.analysis_materials.task_id IS '所属分析任务';
COMMENT ON COLUMN public.analysis_materials.material_key IS '任务内稳定资料键';
COMMENT ON COLUMN public.analysis_materials.material_kind IS 'profile / financial / market / strategy / chips / news 等';
COMMENT ON COLUMN public.analysis_materials.financial_report_id IS '引用的具体财务版本';
COMMENT ON COLUMN public.analysis_materials.source_name IS '来源名称、公开HTTP(S)地址，不含认证参数';
COMMENT ON COLUMN public.analysis_materials.source_url IS '来源名称、公开HTTP(S)地址，不含认证参数';
COMMENT ON COLUMN public.analysis_materials.data_date IS '资料统计日期或报告期截止日';
COMMENT ON COLUMN public.analysis_materials.published_at IS '资料公开时间';
COMMENT ON COLUMN public.analysis_materials.fetched_at IS '取得或确认缺失的时间';
COMMENT ON COLUMN public.analysis_materials.status IS 'available / missing / failed';
COMMENT ON COLUMN public.analysis_materials.content_json IS '实际取得的结构化数据或正文';
COMMENT ON COLUMN public.analysis_materials.content_text IS '实际取得的结构化数据或正文';
COMMENT ON COLUMN public.analysis_materials.content_hash IS '已取得内容摘要';
COMMENT ON COLUMN public.analysis_materials.missing_reason IS '缺失或失败原因';
CREATE INDEX analysis_materials_task_kind_idx ON public.analysis_materials (task_id, material_kind);

CREATE TABLE public.analysis_results (
    task_id UUID PRIMARY KEY REFERENCES public.tasks(id) ON DELETE RESTRICT,
    payload_json JSONB NOT NULL,
    schema_version SMALLINT NOT NULL DEFAULT 1,
    provider VARCHAR(32) NOT NULL,
    model_name VARCHAR(128) NOT NULL,
    prompt_version VARCHAR(32) NOT NULL,
    calculation_version VARCHAR(32) NOT NULL,
    usage_json JSONB,
    materials_as_of_at TIMESTAMPTZ NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT analysis_results_payload_check CHECK (jsonb_typeof(payload_json) = 'object' AND NOT (payload_json ? 'generatedAt')),
    CONSTRAINT analysis_results_schema_check CHECK (schema_version > 0),
    CONSTRAINT analysis_results_metadata_check CHECK (btrim(provider) <> '' AND btrim(model_name) <> '' AND btrim(prompt_version) <> '' AND btrim(calculation_version) <> ''),
    CONSTRAINT analysis_results_usage_check CHECK (usage_json IS NULL OR jsonb_typeof(usage_json) = 'object'),
    CONSTRAINT analysis_results_time_check CHECK (generated_at >= materials_as_of_at)
);
COMMENT ON TABLE public.analysis_results IS '页面分析结果';
COMMENT ON COLUMN public.analysis_results.task_id IS '每个分析任务至多一份成功结果';
COMMENT ON COLUMN public.analysis_results.payload_json IS '标题、摘要、指标、正文、来源、资料质量、缺失说明及可选筹码图';
COMMENT ON COLUMN public.analysis_results.schema_version IS '结果结构版本';
COMMENT ON COLUMN public.analysis_results.provider IS '模型来源与名称';
COMMENT ON COLUMN public.analysis_results.model_name IS '模型来源与名称';
COMMENT ON COLUMN public.analysis_results.prompt_version IS '提示词版本';
COMMENT ON COLUMN public.analysis_results.calculation_version IS '指标、筹码和参考位的计算版本';
COMMENT ON COLUMN public.analysis_results.usage_json IS '来源实际返回的用量统计';
COMMENT ON COLUMN public.analysis_results.materials_as_of_at IS '本次资料集合冻结时间';
COMMENT ON COLUMN public.analysis_results.generated_at IS '分析完成时间';

-- 7. 统一维护可更新基础表的更新时间；不可变数据的事务保护由后端负责。
CREATE FUNCTION public.stocksieve_set_updated_at() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;
CREATE TRIGGER users_updated_at BEFORE UPDATE ON public.users
    FOR EACH ROW EXECUTE FUNCTION public.stocksieve_set_updated_at();
CREATE TRIGGER stocks_updated_at BEFORE UPDATE ON public.stocks
    FOR EACH ROW EXECUTE FUNCTION public.stocksieve_set_updated_at();
CREATE TRIGGER categories_updated_at BEFORE UPDATE ON public.categories
    FOR EACH ROW EXECUTE FUNCTION public.stocksieve_set_updated_at();

COMMENT ON SEQUENCE public.snapshot_version_seq IS '仅在串行发布事务内分配行情版本，不在构建时分配。';
COMMENT ON FUNCTION public.stocksieve_set_updated_at() IS '统一更新基础表的更新时间。';

COMMIT;
