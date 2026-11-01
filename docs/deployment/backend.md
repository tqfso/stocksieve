# 前后端 Docker 部署

使用本机ubuntu:24.04构建应用镜像、PostgreSQL15保存数据，Python依赖由uv锁定。PostgreSQL、API、worker、scheduler统一在`backend/compose.yaml`中，前端由API同源提供。当前覆盖沪深四板块，AI保持模拟。

当前部署已完成首次真实数据发布和有数据的浏览器验收，数量与测试结果统一记录在[后端指南](../../backend/README.md)。

现有数据库已迁移至统一Compose，原数据卷与口令沿用，迁移前后业务行数核对一致。历史配置当前保持150/0，临时容器250/500覆盖检查及迁移后的正式浏览器验收均通过。

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

API默认监听服务器`127.0.0.1:8000`，使用主机网络连接现有PG。客户端可通过已有反代或SSH转发访问：

```bash
ssh -N -L 8000:127.0.0.1:8000 root@172.21.21.98
```

随后浏览器访问`http://127.0.0.1:8000`。

## 配置与凭据

所有配置统一在`backend/.env`，参考[示例](../../backend/.env.example)，文件权限600且不提交Git。首次迁移自动合并旧数据库env，保留原口令；成功后旧env转入`backend/.state/backups/`私有备份，后续仅修改统一文件。当前`STOCKSIEVE_AI_MODE=mock`无需模型密钥。

初始管理员`admin`已创建，随机口令位于服务器`backend/.state/initial-admin.json`，权限600，不在终端或聊天输出。已有管理员和口令文件不会覆盖。新增服务器可执行：

```bash
uv run --project backend --frozen python backend/run.py create-admin --username admin --name 管理员 --generate-password-file backend/.state/initial-admin.json
```

镜像不包含凭据、宿主虚拟环境或业务数据库。公开源账本及成功响应缓存单独挂载，保留已有次数、间隔、冷却和原保存时间；管理员凭据不在这些挂载目录内。应用以非root用户运行，根文件系统只读。

## 历史范围配置

在`backend/.env`设置，单位均为交易日：

```dotenv
STOCKSIEVE_HISTORY_TRADING_DAYS=150
STOCKSIEVE_HISTORY_MAX_TRADING_DAYS=0
```

补齐天数至少140，约一年可设250；上限0表示不限制最新快照窗口，非0时须不小于补齐天数。例如250/500表示补齐250天，随后累积到最新窗口最多500天。上限作用于新快照的计算、K线和新写入范围；旧快照及报告引用的日线保留，数据库物理行数不按此参数删除。

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
```

结构对齐只调整项目约束和索引，不重建表；遇到字段类型/精度差异停止，避免自动损失数据。更新在同一事务校验，失败整次回滚；临时比较schema自动清理，不生成增量脚本。

首轮通过正式HTTP提交初始化、分类和最近收盘任务，可跨中断恢复：

```bash
uv run --project backend --frozen python backend/run.py bootstrap-data --credentials-file backend/.state/initial-admin.json --source baostock --date 2026-10-30
```

日期需改为已确认收盘的交易日。该流程不写模拟行情，任务受理后由服务器执行；网络失败、冷却和预算等待按原任务恢复，完整校验后发布。首轮按配置补齐历史比后续增量慢；进度、等待时间和日志可在数据管理查看。

正式服务已有快照后，可加`--only-kind initialize`、`catalog`或`daily`只执行单种任务。相同状态文件恢复原任务；需要新一轮更新时指定新的`--state-file`。

题材更新在管理页选择“百度股市通题材”；该渠道只修改题材并保留原行业，每轮最多400只，继续更新会补齐其他股票。分类成功后再更新收盘行情，新分类才进入最新筛选。行情渠道继续选择Baostock组合。

正式发布后的浏览器验收：

```bash
uv run --project backend --frozen pytest backend/tests/test_browser.py --deployed-base http://127.0.0.1:8000 --deployed-credentials backend/.state/initial-admin.json --deployed-data -q
```

它使用隔离浏览器会话验证真实列表、详情、K线与后端模拟筹码报告；会产生一份明确标记的模拟报告，不更改行情或账号口令。隔离PostgreSQL回归命令见[后端说明](../../backend/README.md)。

题材渠道验收后，可加`--deployed-concepts -k deployed_concepts -s`单独检查真实下拉选择、成员筛选和刷新恢复；该检查只读，并保存页面截图到`backend/.state/concept-filter.png`。访问历史链接仍沿用原快照，查看新分类请从“机会筛选”菜单重新进入。

## 日常维护

```bash
docker compose -f backend/compose.yaml ps
docker compose -f backend/compose.yaml logs --tail=100 api worker scheduler
```

数据库卷和后端状态卷均为external，统一Compose停止或移除容器仍保留数据。首次迁移备份位于`backend/.state/backups/compose-*.dump`。重启不取消已受理任务，不删除频控持久账本或成功缓存。默认正式预算为400次/日、全渠道至少间隔10秒，实际可执行时间由已消耗次数及上游冷却共同确定。
