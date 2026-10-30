# PostgreSQL 单机部署

使用本机 `postgres:15-alpine` 镜像和 Docker Compose。结构定义以 [init.sql](../../backend/sql/init.sql) 为准；以下命令在项目根目录执行。

## 1. 首次部署与初始化

```bash
docker image inspect postgres:15-alpine --format '{{.Id}}'
bash backend/deploy/postgres/setup.sh
```

脚本依次检查现有资源、生成密码、启动服务，先在临时库验证 SQL 和约束，再初始化目标空库并检查 TCP 认证和表数量。仅清理本次创建的临时验证库；不会下载镜像，遇到其他项目的同名容器、孤立的已有数据卷或非空数据库会停止，不覆盖原数据。

默认连接信息：

| 项目 | 值 |
| --- | --- |
| 地址 | `127.0.0.1:5432` |
| 数据库 | `stocksieve` |
| 数据库管理账号 | `stocksieve_admin` |
| 密码 | 首次随机生成，保存在 `backend/deploy/postgres/.env` |
| 容器 | `stocksieve-postgres` |
| 持久化卷 | `stocksieve-postgres-data` |

`.env` 权限为600且不入 Git，已有文件不会覆盖。端口冲突时，部署前在该文件修改 `STOCKSIEVE_POSTGRES_PORT`。数据卷首次建库后，修改文件中的密码不会自动修改数据库密码。

数据库管理账号用于建库；网站管理员账号由后端接入时创建。本次不插入演示用户或行情，也不执行股票同步任务。

## 2. 各步骤对应命令

先执行首次部署脚本以生成 `.env`。之后可进入配置目录管理服务：

```bash
cd backend/deploy/postgres
docker compose config --quiet
docker compose up -d --wait
docker compose ps
```

若手动初始化，**仅对空库执行一次**：

PostgreSQL 在连接时选择数据库，下方 `-d "$POSTGRES_DB"` 默认指定 `stocksieve`；图形客户端也需先连接该库。`init.sql` 在每张建表语句后通过 `COMMENT ON TABLE` 和 `COMMENT ON COLUMN` 保存中文表、字段说明。

```bash
docker compose exec -T postgres sh -eu -c 'exec psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < ../../sql/init.sql
docker compose exec -T postgres sh -eu -c 'exec psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < ../../sql/tests/constraints.sql
```

建表脚本使用事务，失败即回滚；约束测试中的业务数据也会回滚。首次部署脚本已执行这两步时，不需再次初始化。

## 3. 连接与检查

在配置目录进入数据库：

```bash
docker compose exec postgres sh -eu -c 'exec psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

在 psql 中执行：

```sql
\dt public.*
SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public';
\q
```

预期为17张业务表。服务器上的程序连接 `127.0.0.1`，同一 Compose 网络中的容器连接 `postgres:5432`；主机无需安装 PostgreSQL 客户端。

## 4. 停止、重启与备份

在配置目录执行，停止不会删除持久化卷：

```bash
docker compose stop
docker compose up -d --wait
```

备份到不入 Git 的本地目录：

```bash
umask 077
mkdir -p backups
postgres_backup_file="backups/stocksieve-$(date +%Y%m%d-%H%M%S).dump"
docker compose exec -T postgres sh -eu -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$postgres_backup_file"
```

日常启动不会重复执行建表脚本。后续结构调整遵循[项目数据库约定](../../AGENT.md#数据库约束)；恢复备份应另建空库，避免覆盖当前库。
