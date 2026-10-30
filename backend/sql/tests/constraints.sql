-- 本文件在事务内验证初始化结构及关键约束，所有测试业务数据最终回滚。
-- 使用固定测试ID，不消耗行情版本序列或日志自增序列。
BEGIN;

-- 捕获预期SQLSTATE，任何未预期的成功或错误都会使测试失败并回滚。
CREATE FUNCTION pg_temp.expect_sqlstate(statement_text TEXT, expected_state TEXT)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    BEGIN
        EXECUTE statement_text;
    EXCEPTION WHEN OTHERS THEN
        IF SQLSTATE = expected_state THEN
            RETURN;
        END IF;
        RAISE;
    END;
    RAISE EXCEPTION '预期SQLSTATE %，但语句执行成功', expected_state;
END;
$$;

DO $checks$
BEGIN
    -- 1. 确认结构完整，并检查账号、外键和板块边界。
    IF (SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public') <> 17 THEN
        RAISE EXCEPTION '预期public下恰好17张业务表';
    END IF;
    INSERT INTO public.users (id, username, name, password_hash, role)
    VALUES ('00000000-0000-4000-8000-000000000001', 'schema_test_admin', '约束测试', 'test-only-hash', 'admin');
    PERFORM pg_temp.expect_sqlstate($sql$
        INSERT INTO public.users (username, name, password_hash) VALUES ('InvalidCase', '测试', 'test-only-hash')
    $sql$, '23514');
    PERFORM pg_temp.expect_sqlstate($sql$
        INSERT INTO public.users (username, name, password_hash) VALUES ('schema_test_admin', '重复', 'test-only-hash')
    $sql$, '23505');
    PERFORM pg_temp.expect_sqlstate($sql$
        INSERT INTO public.user_sessions (user_id, token_hash, expires_at)
        VALUES ('00000000-0000-4000-8000-000000000999', repeat('a', 64), CURRENT_TIMESTAMP + INTERVAL '8 hours')
    $sql$, '23503');
    INSERT INTO public.stocks (stock_id, code, name, exchange, board)
    VALUES ('SZSE.300750', '300750', '约束测试股票', 'SZSE', 'CHINEXT');
    PERFORM pg_temp.expect_sqlstate($sql$
        INSERT INTO public.stocks (stock_id, code, name, exchange, board)
        VALUES ('SSE.600001', '600001', '错误板块', 'SSE', 'CHINEXT')
    $sql$, '23514');

    -- 2. 不同日期可排队，但只能同时执行一个同步任务。
    INSERT INTO public.tasks (id, category, kind, created_by, data_source, target_date, dedupe_key)
    VALUES ('00000000-0000-4000-8000-000000000010', 'sync', 'daily', '00000000-0000-4000-8000-000000000001', 'test', DATE '2026-10-30', 'schema:sync:primary');
    INSERT INTO public.tasks (id, category, kind, created_by, data_source, target_date, dedupe_key)
    VALUES ('00000000-0000-4000-8000-000000000011', 'sync', 'daily', '00000000-0000-4000-8000-000000000001', 'test', DATE '2026-10-29', 'schema:sync:secondary');
    PERFORM pg_temp.expect_sqlstate($sql$
        INSERT INTO public.tasks (category, kind, created_by, data_source, target_date, dedupe_key)
        VALUES ('sync', 'daily', '00000000-0000-4000-8000-000000000001', 'test', DATE '2026-10-30', 'different-key-same-date')
    $sql$, '23505');
    UPDATE public.tasks SET status = 'running', started_at = CURRENT_TIMESTAMP,
        lease_owner = 'schema-test', lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '1 minute'
    WHERE id = '00000000-0000-4000-8000-000000000010';
    PERFORM pg_temp.expect_sqlstate($sql$
        UPDATE public.tasks SET status = 'running', started_at = CURRENT_TIMESTAMP,
            lease_owner = 'schema-test', lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '1 minute'
        WHERE id = '00000000-0000-4000-8000-000000000011'
    $sql$, '23505');

    -- 3. 快照、原始日线、股票视图和策略信号能正确关联。
    INSERT INTO public.data_snapshots (id, sync_task_id, data_date, source, strategy_version, calculation_version, strategy_config_json)
    VALUES ('00000000-0000-4000-8000-000000000020', '00000000-0000-4000-8000-000000000010', DATE '2026-10-30', 'test', 'v1', 'v1', '{}'::JSONB);
    INSERT INTO public.daily_bars (snapshot_id, stock_id, trade_date, open, high, low, close, volume, amount, source)
    VALUES ('00000000-0000-4000-8000-000000000020', 'SZSE.300750', DATE '2026-10-30', 100, 110, 95, 105, 100, 10500, 'test');
    PERFORM pg_temp.expect_sqlstate($sql$
        INSERT INTO public.daily_bars (snapshot_id, stock_id, trade_date, open, high, low, close, source)
        VALUES ('00000000-0000-4000-8000-000000000020', 'SZSE.300750', DATE '2026-10-29', 105, 100, 110, 105, 'test')
    $sql$, '23514');
    PERFORM pg_temp.expect_sqlstate($sql$
        INSERT INTO public.daily_bars (snapshot_id, stock_id, trade_date, open, high, low, close, source)
        VALUES ('00000000-0000-4000-8000-000000000020', 'SZSE.300750', DATE '2026-10-29', 'NaN', 'NaN', 'NaN', 'NaN', 'test')
    $sql$, '23514');
    INSERT INTO public.snapshot_stocks (snapshot_id, stock_id, code, name, exchange, board, quote_price, trade_status, is_st, is_delisting)
    VALUES ('00000000-0000-4000-8000-000000000020', 'SZSE.300750', '300750', '约束测试股票', 'SZSE', 'CHINEXT', 105, 'trading', FALSE, FALSE);
    INSERT INTO public.strategy_signals (id, snapshot_id, stock_id, strategy_code, reason, metrics_json, condition_results_json, invalidation_json, sort_value)
    VALUES ('00000000-0000-4000-8000-000000000030', '00000000-0000-4000-8000-000000000020', 'SZSE.300750', 'short', '仅用于约束测试', '{}'::JSONB, '[]'::JSONB, '{}'::JSONB, 2);
    UPDATE public.data_snapshots SET status = 'published', version = 1, history_start_date = DATE '2026-10-30',
        calendar_json = '{}'::JSONB, coverage_json = '{}'::JSONB, published_at = CURRENT_TIMESTAMP
    WHERE id = '00000000-0000-4000-8000-000000000020';
    UPDATE public.tasks SET status = 'succeeded', progress = 100, finished_at = CURRENT_TIMESTAMP,
        lease_owner = NULL, lease_expires_at = NULL, result_snapshot_id = '00000000-0000-4000-8000-000000000020'
    WHERE id = '00000000-0000-4000-8000-000000000010';

    -- 4. 分析不能引用未入选策略；无策略分析同样受自然范围去重保护。
    PERFORM pg_temp.expect_sqlstate($sql$
        INSERT INTO public.tasks (category, kind, created_by, stock_id, input_snapshot_id, strategy_code, target_date, dedupe_key)
        VALUES ('analysis', 'trade', '00000000-0000-4000-8000-000000000001', 'SZSE.300750', '00000000-0000-4000-8000-000000000020', 'long', DATE '2026-10-30', 'schema:invalid:strategy')
    $sql$, '23503');
    INSERT INTO public.tasks (id, category, kind, created_by, stock_id, input_snapshot_id, target_date, dedupe_key)
    VALUES ('00000000-0000-4000-8000-000000000040', 'analysis', 'fundamental', '00000000-0000-4000-8000-000000000001', 'SZSE.300750', '00000000-0000-4000-8000-000000000020', DATE '2026-10-30', 'schema:analysis:primary');
    PERFORM pg_temp.expect_sqlstate($sql$
        INSERT INTO public.tasks (category, kind, created_by, stock_id, input_snapshot_id, target_date, dedupe_key)
        VALUES ('analysis', 'fundamental', '00000000-0000-4000-8000-000000000001', 'SZSE.300750', '00000000-0000-4000-8000-000000000020', DATE '2026-10-30', 'different-key-same-analysis')
    $sql$, '23505');

    -- 5. 允许真实财务亏损值，拒绝没有内容却标记可用的资料。
    INSERT INTO public.financial_reports (id, stock_id, period_start, period_end, statement_scope, revenue_ytd, net_profit_parent_ytd, source, content_hash, raw_payload_json)
    VALUES ('00000000-0000-4000-8000-000000000050', 'SZSE.300750', DATE '2026-01-01', DATE '2026-09-30', 'consolidated', 100, -5, 'test', repeat('b', 64), '{}'::JSONB);
    PERFORM pg_temp.expect_sqlstate($sql$
        INSERT INTO public.analysis_materials (task_id, material_key, material_kind, source_name, status)
        VALUES ('00000000-0000-4000-8000-000000000040', 'invalid', 'financial', '约束测试', 'available')
    $sql$, '23514');
    INSERT INTO public.analysis_materials (id, task_id, material_key, material_kind, financial_report_id, source_name, status, content_json, content_hash)
    VALUES ('00000000-0000-4000-8000-000000000060', '00000000-0000-4000-8000-000000000040', 'financial:test', 'financial', '00000000-0000-4000-8000-000000000050', '约束测试', 'available', '{"profit": -5}'::JSONB, repeat('c', 64));
    INSERT INTO public.analysis_results (task_id, payload_json, provider, model_name, prompt_version, calculation_version, materials_as_of_at)
    VALUES ('00000000-0000-4000-8000-000000000040', '{"title": "仅用于约束测试"}'::JSONB, 'test', 'schema-test', 'v1', 'v1', CURRENT_TIMESTAMP);
    UPDATE public.tasks SET status = 'succeeded', progress = 100, finished_at = CURRENT_TIMESTAMP
    WHERE id = '00000000-0000-4000-8000-000000000040';

    -- 6. 定时计划使用北京时间18:00；幂等响应只保存data。
    -- 分类采用独立的手动任务，无目标行情日期；这里不发布快照。
    INSERT INTO public.tasks (id, category, kind, created_by, data_source, dedupe_key)
    VALUES ('00000000-0000-4000-8000-000000000090', 'sync', 'catalog', '00000000-0000-4000-8000-000000000001', 'catalog_test', 'schema:catalog');
    PERFORM pg_temp.expect_sqlstate($sql$
        UPDATE public.tasks SET target_date = DATE '2026-10-30'
        WHERE id = '00000000-0000-4000-8000-000000000090'
    $sql$, '23514');
    PERFORM pg_temp.expect_sqlstate($sql$
        UPDATE public.tasks SET trigger = 'scheduled', created_by = NULL, scheduled_for = TIMESTAMPTZ '2026-10-30 18:00:00+08'
        WHERE id = '00000000-0000-4000-8000-000000000090'
    $sql$, '23514');
    PERFORM pg_temp.expect_sqlstate($sql$
        INSERT INTO public.sync_triggers (scheduled_date, scheduled_for)
        VALUES (DATE '2026-10-30', TIMESTAMPTZ '2026-10-30 17:00:00+08')
    $sql$, '23514');
    INSERT INTO public.sync_triggers (scheduled_date, scheduled_for)
    VALUES (DATE '2026-10-30', TIMESTAMPTZ '2026-10-30 18:00:00+08');
    PERFORM pg_temp.expect_sqlstate($sql$
        INSERT INTO public.idempotency_requests (user_id, http_method, request_path, idempotency_key, request_hash, task_id, response_data)
        VALUES ('00000000-0000-4000-8000-000000000001', 'POST', '/api/v1/stocks/SZSE.300750/analyses', 'schema-key', repeat('d', 64), '00000000-0000-4000-8000-000000000040', '{"requestId": "must-not-cache"}'::JSONB)
    $sql$, '23514');
    RAISE NOTICE '结构与关键约束验证通过；测试数据即将回滚。';
END;
$checks$;

ROLLBACK;
