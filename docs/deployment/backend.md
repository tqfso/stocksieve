# 后端单机部署

状态：配置已准备，尚未在当前服务器启动。先完成[后端指南](../../backend/README.md)中的真实依赖、数据库与供应商验证，再启用服务。

## 1. 依赖与配置

使用 Python 3.12 和 uv。核心依赖以 `backend/pyproject.toml`、`backend/uv.lock` 为准。PostgreSQL 驱动和智能体扩展需在联网恢复后完成具体版本验证并加入同一 uv 项目，当前不宣称已安装或兼容。

数据库默认读取既有 `backend/deploy/postgres/.env`，也可通过 `STOCKSIEVE_DATABASE_URL` 覆盖。其他配置参考 [示例](../../backend/.env.example)，真实配置写入不入 Git 的 `backend/.env`；手动运行时用 uv 的 `--env-file` 加载，systemd 使用 EnvironmentFile。

| 配置 | 用途 |
| --- | --- |
| STOCKSIEVE_ENABLED_SOURCES | 只允许启用已实现并验证的渠道；当前留空 |
| STOCKSIEVE_DEFAULT_SOURCE | 自动收盘同步所选的单个渠道 |
| STOCKSIEVE_SOURCE_INTERVAL_SECONDS / DAILY_LIMIT | 同供应商共享的请求间隔与日预算，间隔至少10秒 |
| STOCKSIEVE_AI_BASE_URL / MODEL / API_KEY | 模型服务地址、模型和密钥，仅保存在服务器 |
| STOCKSIEVE_AI_PROTOCOL | chat_completions 或 responses，按服务实际支持选择 |
| STOCKSIEVE_AI_TOOLS_SUPPORTED | 支持工具调用时用技能智能体，否则使用预备资料的图工作流；两条SDK路径均待实测 |

免费检索与行情适配器必须复用统一频控，不能通过新增渠道别名绕过供应商预算。没有足够数据或服务未配置时返回可读错误，不能回退成虚构报告。

## 2. 数据库检查与管理员

以下从项目根目录执行，均不重建数据库：

```bash
uv run --project backend --frozen python backend/run.py check-db
uv run --project backend --frozen python backend/run.py create-admin --username admin --name 管理员
```

管理员命令交互读取密码，不使用演示凭据；已有管理员时停止，保持原账号不变。密码也可从指定服务器环境变量读取，使用 `--password-env` 指定变量名称，避免把密码写进命令行。

## 3. 三类进程

```bash
uv run --project backend --env-file backend/.env --frozen python backend/run.py api --host 127.0.0.1 --port 8000
uv run --project backend --env-file backend/.env --frozen python backend/run.py worker
uv run --project backend --env-file backend/.env --frozen python backend/run.py scheduler
```

`/health/live` 检查进程，`/health/ready` 检查数据库；数据是否已准备由已鉴权的 `/api/v1/market/status` 表示。

systemd文件位于 [backend/deploy/systemd](../../backend/deploy/systemd/stocksieve-api.service)，使用当前服务器的实际路径。完成依赖与联调后再安装、启动这三个单元；本轮未修改 `/etc/systemd` 或启动生产进程。数据库连接使用本机地址，访问范围沿用现有 PostgreSQL 部署配置。

## 4. 日志与恢复

进程日志由 systemd/journald 保存，同步业务日志通过管理员任务接口查询。重启后回收过期租约，等待和已完成的批次仍保留；发布事务保证失败构建不替换有效快照。

持久数据使用现有 PostgreSQL 备份流程；`.state` 中包含跨进程供应商预算及公开资料缓存，应保留频控状态，避免重启绕过冷却。缓存不是行情和报告的唯一持久来源。
