# PostgreSQL 单机部署

数据库与后端统一由[backend/compose.yaml](../../backend/compose.yaml)管理，配置统一放在[backend/.env](../../backend/.env)。完整构建、旧部署迁移与历史参数说明见[统一部署](backend.md)。

## 首次准备

使用已有postgres:15-alpine镜像，数据卷为stocksieve-postgres-data。若只初始化数据库，在项目根执行：

```bash
bash backend/deploy/postgres/setup.sh
```

该兼容入口使用uv调用统一部署脚本的`--database-only`模式，生成权限600的私有配置、创建external卷并等待PG健康。只有目标库完全无业务表时才执行初始化SQL；已有17张表不会重新建库，其他非空结构会停止供人工核对。

已有两个独立Compose的服务器使用完整部署命令合并。脚本先核验容器、镜像、口令和数据卷，再备份并迁移管理权，保持原数据库、账户、端口和数据卷。备份及旧env存于`backend/.state/backups/`，均不入Git。PG数据卷和后端状态卷均标为external。

## 连接与管理

| 项目 | 默认值 |
| --- | --- |
| 服务器地址 | 127.0.0.1:5432 |
| 数据库 | stocksieve |
| 数据库管理账户 | stocksieve_admin |
| 口令 | backend/.env中的STOCKSIEVE_POSTGRES_PASSWORD |
| 容器 | stocksieve-postgres |
| 数据卷 | stocksieve-postgres-data |

后端使用主机网络连接本机PG端口；数据库端口保持原来的映射。变更端口需同步统一env并重建服务。修改env中的密码不会自动修改已初始化数据库角色密码。

所有命令在项目根执行：

```bash
docker compose -f backend/compose.yaml ps
docker compose -f backend/compose.yaml exec postgres sh -eu -c 'exec psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose -f backend/compose.yaml stop
docker compose -f backend/compose.yaml up -d
```

进入psql后可用`\dt public.*`查看17张业务表。后续结构修改沿用[数据库约定](../../AGENT.md#数据库约束)，直接更新现有库，不生成增量脚本。

## 手工备份

```bash
umask 077
mkdir -p backend/.state/backups
docker compose -f backend/compose.yaml exec -T postgres sh -eu -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > backend/.state/backups/manual.dump
```

恢复备份应另建空库，保留当前业务库以供核对。
