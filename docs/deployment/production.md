# 线上部署：前端、后端与数据库

四个服务由 `backend/compose.yaml` 管理：PostgreSQL 保存数据；API 提供前端和接口；worker 执行任务；scheduler 登记定时任务。应用使用本机 `ubuntu:24.04` 构建，Python 依赖由 uv 锁定。

## 构建与启动

在项目根目录执行：

```bash
cd frontend
npm ci
npm run build
cd ..
uv run --project backend --frozen python backend/deploy/docker/deploy.py
```

脚本准备 uv 管理的 Python、前端构建、CA 和时区，核实本机基础镜像后构建，按顺序等待 PG 和 API 健康。支持 `--base-image`、`--image`、`--prepare-only` 和 `--build-only`；不包含清库、旧部署迁移或数据修复选项。

浏览器访问 **http://172.21.21.98:24048**。所有服务使用 Compose bridge 网络，应用通过 `postgres:5432` 连接数据库；API 内部8000映射到服务器 `STOCKSIEVE_API_PORT=24048`，同源提供页面与 `/api/v1`。

## 配置与账号

配置统一放在 `backend/.env`，环境变量优先。首次部署按[示例](../../backend/.env.example)生成私有配置和数据库口令；已有配置保持原值。修改 env 后重新部署使容器生效。修改 env 中的数据库密码不会自动修改已有 PG 角色密码。

当前管理员为 `admin`，随机口令保存在服务器 `backend/.state/initial-admin.json`，权限600。新服务器创建管理员：

```bash
uv run --project backend --frozen python backend/run.py create-admin --username admin --name 管理员 --generate-password-file backend/.state/initial-admin.json
```

普通用户从管理页面创建。当前 `STOCKSIEVE_AI_MODE=mock`，无需模型配置。后续真实模型配置键见 env 示例，当前不启用。

## 数据与历史窗口

在数据管理页面依次执行初始化、百度行业与题材更新、收盘行情更新。行情选择新浪。分类成功后通过新的收盘快照生效；任务由后台执行，关闭页面不取消。

```dotenv
STOCKSIEVE_HISTORY_TRADING_DAYS=150
STOCKSIEVE_HISTORY_MAX_TRADING_DAYS=2500
STOCKSIEVE_SOURCE_INTERVAL_SECONDS=0.2
STOCKSIEVE_SOURCE_DAILY_LIMIT=20000
```

首次策略窗口至少140个交易日；最大窗口0表示不限制，非0须不小于首次窗口。150/2500表示先发布近期策略，再由独立任务补齐约十年历史。新股从实际可得数据开始；已保存的快照和报告资料保留。

0.2秒间隔和20000次日预算是项目配置；80%预算预留给日常更新，回补让位并可跨日继续。百度分类至少间隔1秒、每批20只。所有进程共用 `backend/.state/rate/`，重启不重置次数或冷却；缓存命中不发请求。渠道拒绝访问时暂停，不通过新建任务绕过限制。

同日同渠道、配置和分类已成功时直接复用结果。失败任务从页面“从断点继续”恢复原任务与进度。

## 持久化与数据库

- `stocksieve-postgres-data`：PG 数据卷。
- `stocksieve-backend-state`：应用状态卷。
- `backend/.state/rate/`：共享访问账本。
- `backend/.state/source-cache/`：公开数据响应和同步检查点。

两个命名卷均为 external，部署不会删除。`backend/sql/init.sql` 仅在空 PG 卷首次启动时执行；修改结构时同时更新完整 SQL 并直接更新现有数据库，不重建业务库、不提供增量脚本。

应用以 `stocksieve`（UID/GID10001）运行，根目录只读。镜像不包含私有 env、口令、宿主机虚拟环境或业务数据。

```bash
uv run --project backend --frozen python backend/run.py check-config
uv run --project backend --frozen python backend/run.py check-db --counts
docker compose -f backend/compose.yaml ps
docker compose -f backend/compose.yaml logs --tail=100 api worker scheduler
```

进入数据库：

```bash
docker compose -f backend/compose.yaml exec postgres sh -eu -c 'exec psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

备份到私有文件：

```bash
umask 077
mkdir -p backend/.state/backups
docker compose -f backend/compose.yaml exec -T postgres sh -eu -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "backend/.state/backups/manual-$(date +%Y%m%d-%H%M%S).dump"
```

恢复先在新建空库验证，保留当前业务库。HTTP 与浏览器测试见[后端说明](../../backend/README.md)。
