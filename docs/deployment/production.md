# 线上环境部署（前端、后端与数据库）

使用本机ubuntu:24.04构建应用镜像、PostgreSQL15保存数据，Python依赖由uv锁定。PostgreSQL、API、worker、scheduler统一在`backend/compose.yaml`中，前端由API同源提供。当前覆盖沪深四板块，AI保持模拟。

当前部署已完成空库重建、管理员登录和空库浏览器验收，正在重新补齐十年历史行情；同步与测试状态统一记录在[后端指南](../../backend/README.md)。

PostgreSQL、API、worker和scheduler统一由Compose管理。历史统一按交易日配置；先发布近期策略，再补齐较早历史，遇到访问间隔、预算或冷却会保留进度后续跑。完整回补状态以数据管理中的任务结果为准。

## 构建与启动

在仓库根目录执行：

```bash
cd frontend
npm ci
npm run build
cd ..
uv run --project backend --frozen python backend/deploy/docker/deploy.py
```

部署脚本核实本机镜像、构建应用、校验统一配置，按依赖顺序等待PG和API健康。首次合并旧部署会先停止应用写入并保存权限600的数据库备份，再将旧PG容器停止、改名并用原卷启动统一服务；业务行数一致且全部检查通过后才移除旧容器。失败时可恢复原容器，数据卷不删除。已有数据库仅审计和直接对齐约束，完全空库才执行初始化SQL。

明确需要清空项目数据并重建时使用：

```bash
uv run --project backend --frozen python backend/deploy/docker/deploy.py --reset-data
```

此入口先核对项目独占资源、保存并校验数据库备份，然后移除本项目容器及两只数据卷，归档旧缓存和任务状态，创建空库并复用`backend/.state/initial-admin.json`中的管理员账号。恢复资料在`backend/.state/backups/`；私有env与源频控账本保留。日常部署不加此参数。

所有服务使用Compose默认的bridge网络，应用容器通过`postgres:5432`访问数据库。API在容器内监听`0.0.0.0:8000`，映射到服务器`0.0.0.0:24048`，同时提供前端页面与`/api/v1`接口。

浏览器直接访问`http://172.21.21.98:24048`。服务器映射端口由`backend/.env`中的`STOCKSIEVE_API_PORT=24048`控制，容器内部端口和健康检查固定为8000。API、worker、scheduler在容器中自动使用服务名连接PG；宿主机CLI仍使用本机数据库端口，连接参数无需改成容器服务名。

## 配置与凭据

所有配置统一在`backend/.env`，参考[示例](../../backend/.env.example)，文件权限600且不提交Git。首次迁移自动合并旧数据库env，保留原口令；成功后旧env转入`backend/.state/backups/`私有备份，后续仅修改统一文件。当前`STOCKSIEVE_AI_MODE=mock`无需模型密钥。

初始管理员`admin`已创建，随机口令位于服务器`backend/.state/initial-admin.json`，权限600，不在终端或聊天输出。已有管理员和口令文件不会覆盖。新增服务器可执行：

```bash
uv run --project backend --frozen python backend/run.py create-admin --username admin --name 管理员 --generate-password-file backend/.state/initial-admin.json
```

镜像不包含凭据、宿主虚拟环境或业务数据库。公开源账本及成功响应缓存单独挂载，保留已有次数、间隔、冷却和原保存时间；管理员凭据不在这些挂载目录内。应用以非root用户运行，根文件系统只读。

应用账户为`stocksieve`（UID/GID 10001）。运行容器的只读根目录不能用`apt`安装工具；系统工具应在镜像构建时准备。需要独立排障环境时可用本机Ubuntu镜像启动临时容器，共享API网络但不挂载业务数据：`docker run --rm -it --network container:stocksieve-backend-api-1 ubuntu:24.04 bash`。该临时容器内可按需安装网络工具，退出后移除。

## 数据库管理与备份

完整部署会自动准备数据库；如仅准备数据库，可在项目根执行`uv run --project backend --frozen python backend/deploy/docker/deploy.py --database-only`。连接配置见`backend/.env`；修改其中的密码不会自动修改已初始化的数据库角色密码。

进入数据库控制台后，可用`\dt public.*`查看业务表：

```bash
docker compose -f backend/compose.yaml exec postgres sh -eu -c 'exec psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

手工备份使用带时间戳的私有文件：

```bash
umask 077
mkdir -p backend/.state/backups
docker compose -f backend/compose.yaml exec -T postgres sh -eu -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "backend/.state/backups/manual-$(date +%Y%m%d-%H%M%S).dump"
```

恢复时另建空库并核对结果，保留当前业务库。

## 历史范围配置

在`backend/.env`设置：

```dotenv
STOCKSIEVE_HISTORY_TRADING_DAYS=150
STOCKSIEVE_HISTORY_MAX_TRADING_DAYS=2500
```

`TRADING_DAYS`是首次发布策略需要补齐的近期窗口，至少140；`MAX_TRADING_DAYS`是后续回补及最新窗口上限，0表示不限制，非0须不小于前者。150/2500表示先用最近150个交易日发布策略，再由独立后台任务补齐最多2500个交易日，约十年；后续日常更新继续使用已取得的历史。

日期使用真实交易日历，上市较晚的股票从实际可得行情开始，不补造数据。日线按快照差异保存，策略只计算各自所需窗口；旧快照及报告引用的原始日线保留。

修改后运行部署命令重建应用容器，再提交新的收盘更新。任务受理时固定配置，进行中的任务及旧快照不受之后改动影响。扩大范围会补真实日历和缺失行情，继续遵守原频控。可只读核对生效值：

```bash
uv run --project backend --frozen python backend/run.py check-config
docker compose -f backend/compose.yaml exec api /app/.venv/bin/python run.py check-config
```

## 数据库与真实数据验收

结构审计和直接对齐命令：

```bash
uv run --project backend --frozen python backend/run.py sync-schema
uv run --project backend --frozen python backend/run.py sync-schema --apply
uv run --project backend --frozen python backend/run.py check-db --counts
uv run --project backend --frozen python backend/run.py check-db --sync-jobs
```

结构对齐只调整项目约束和索引，不重建表；遇到字段类型/精度差异停止，避免自动损失数据。更新在同一事务校验，失败整次回滚；临时比较schema自动清理，不生成增量脚本。

首轮通过正式HTTP提交初始化、分类和最近收盘任务，可跨中断恢复：

```bash
uv run --project backend --frozen python backend/run.py bootstrap-data --credentials-file backend/.state/initial-admin.json --source baostock --date 2026-10-30
```

日期需改为已确认收盘的交易日。该流程不写模拟行情，任务受理后由服务器执行；网络失败、冷却和预算等待按原任务恢复，完整校验后发布。首轮按配置补齐历史比后续增量慢；进度、等待时间和日志可在数据管理查看。

长期回补允许分多日完成。基础初始化和行业同步成功后，可用`--only-kind daily --submit-only`仅提交收盘任务并返回；任务由worker跨日执行，不需要终端持续连接。近期策略发布后页面即可使用，日常更新优先于较早历史回补；停止或重建应用容器不会清空已保存进度。

正式服务已有快照后，可加`--only-kind initialize`、`catalog`或`daily`只执行单种任务。相同状态文件恢复原任务；需要新一轮更新时指定新的`--state-file`。

题材更新在管理页选择“百度股市通题材”；该渠道只修改题材并保留原行业，每轮最多400只，继续更新会补齐其他股票。分类成功后再更新收盘行情，新分类才进入最新筛选。行情渠道继续选择Baostock组合。

正式发布后的浏览器验收：

```bash
uv run --project backend --frozen pytest backend/tests/test_browser.py --deployed-base http://172.21.21.98:24048 --deployed-credentials backend/.state/initial-admin.json --deployed-data -q
```

它使用隔离浏览器会话验证真实列表、详情、K线与后端模拟筹码报告；会产生一份明确标记的模拟报告，不更改行情或账号口令。隔离PostgreSQL回归命令见[后端说明](../../backend/README.md)。

题材渠道验收后，可加`--deployed-concepts -k deployed_concepts -s`单独检查真实下拉选择、成员筛选和刷新恢复；该检查只读，并保存页面截图到`backend/.state/concept-filter.png`。访问历史链接仍沿用原快照，查看新分类请从“机会筛选”菜单重新进入。

## 日常维护

```bash
docker compose -f backend/compose.yaml ps
docker compose -f backend/compose.yaml logs --tail=100 api worker scheduler
```

数据库卷和后端状态卷均为external，统一Compose停止或移除容器仍保留数据。首次迁移备份位于`backend/.state/backups/compose-*.dump`。重启不取消已受理任务，不删除频控账本或成功缓存。

`STOCKSIEVE_SOURCE_INTERVAL_SECONDS=1`和`STOCKSIEVE_SOURCE_DAILY_LIMIT=1000`控制共享请求间隔与本项目日预算，不是上游公布的限额。回补预留最多100次给日常更新；更小的预算预留约四分之一。日常更新可在历史回补期间提交并优先执行；403/429及其他真实冷却仍会暂停对应渠道。
