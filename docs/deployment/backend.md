# 前后端 Docker 部署

使用当前服务器本机ubuntu:24.04镜像、既有PostgreSQL和uv锁文件。API、worker、scheduler共用应用镜像；编译前端由API同源提供。当前覆盖沪深四板块，AI保持后端模拟。

当前部署已完成首次真实数据发布和有数据的浏览器验收，数量与测试结果统一记录在[后端指南](../../backend/README.md)。

## 构建与启动

在仓库根目录执行：

```bash
cd frontend
npm ci
npm run build
cd ..
uv run --project backend --frozen python backend/deploy/docker/deploy.py
```

部署脚本核实本机基础镜像和uv/Python兼容性，准备前端构建及公开CA/时区；对照初始化SQL直接校验并对齐既有约束，保留业务数据；按锁文件构建应用，再检查数据库和API健康、后台进程与基础HTTP行为。不拉取新的基础镜像，不重建数据库。

API默认监听服务器`127.0.0.1:8000`，使用主机网络连接现有PG。客户端可通过已有反代或SSH转发访问：

```bash
ssh -N -L 8000:127.0.0.1:8000 root@172.21.21.98
```

随后浏览器访问`http://127.0.0.1:8000`。

## 配置与凭据

数据库由既有`backend/deploy/postgres/.env`注入；应用可在`backend/.env`配置，参考[示例](../../backend/.env.example)。当前`STOCKSIEVE_AI_MODE=mock`，无需模型密钥；未来切换真实模式再填写访问信息。

初始管理员`admin`已创建，随机口令位于服务器`backend/.state/initial-admin.json`，权限600，不在终端或聊天输出。已有管理员和口令文件不会覆盖。新增服务器可执行：

```bash
uv run --project backend --frozen python backend/run.py create-admin --username admin --name 管理员 --generate-password-file backend/.state/initial-admin.json
```

镜像不包含凭据、宿主虚拟环境或业务数据库。公开源账本及成功响应缓存单独挂载，保留已有次数、间隔、冷却和原保存时间；管理员凭据不在这些挂载目录内。应用以非root用户运行，根文件系统只读。

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

日期需改为已确认收盘的交易日。该流程不写模拟行情，任务受理后由服务器执行；网络失败、冷却和预算等待按原任务恢复，完整校验后发布。首轮补齐150个交易日比后续增量慢；进度、等待时间和日志可在数据管理查看。

正式服务已有快照后，可加`--only-kind initialize`、`catalog`或`daily`只执行单种任务。相同状态文件恢复原任务；需要新一轮更新时指定新的`--state-file`。

正式发布后的浏览器验收：

```bash
uv run --project backend --frozen pytest backend/tests/test_browser.py --deployed-base http://127.0.0.1:8000 --deployed-credentials backend/.state/initial-admin.json --deployed-data -q
```

它使用隔离浏览器会话验证真实列表、详情、K线与后端模拟筹码报告；会产生一份明确标记的模拟报告，不更改行情或账号口令。隔离PostgreSQL回归命令见[后端说明](../../backend/README.md)。

## 日常维护

```bash
docker compose -f backend/deploy/docker/compose.yaml ps
docker compose -f backend/deploy/docker/compose.yaml logs --tail=100 api worker scheduler
```

数据库备份沿用现有流程。重启不取消已受理任务，不删除频控持久账本或成功缓存。默认正式预算为400次/日、全渠道至少间隔10秒，实际可执行时间由已消耗次数及上游冷却共同确定。
