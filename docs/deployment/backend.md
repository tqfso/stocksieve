# 后端 Docker 部署

状态：配置与脚本已准备，Compose 配置校验通过；按用户安排暂缓构建和启动。已通过 Docker 列表确认本机存在 `ubuntu:24.04`（镜像ID `f794f40ddfff`），默认采用该引用；运行兼容性仍需验证。

## 1. 部署结构

API、worker、scheduler 共用一个 `stocksieve-backend:local` 镜像，由 [Compose](../../backend/deploy/docker/compose.yaml) 管理。使用当前 Linux 服务器的主机网络，连接已有 PostgreSQL；API 默认只监听 `127.0.0.1:8000`。不会创建或重建 PostgreSQL 容器。

业务库连接参数从既有 `backend/deploy/postgres/.env` 注入，不进入镜像。AI 当前默认模拟模式，无需模型配置；后续可在 `backend/.env` 设置 `STOCKSIEVE_AI_MODE=live` 并填写模型访问参数，参考 [示例](../../backend/.env.example)。供应商频控、公开资料缓存和临时分析工作区使用 `stocksieve-backend-state` 持久卷。

## 2. 构建与启动

从项目根目录执行：

```bash
uv run --project backend --frozen python backend/deploy/docker/deploy.py
```

脚本依次执行：

1. 准备本机已经验证的 uv、可重定位 Python 3.12、公开CA和时区数据，不复制宿主机虚拟环境或凭据。
2. 检查指定镜像在本机存在，并以禁止拉取、断网的临时容器验证 Ubuntu、架构及运行材料兼容性。
3. 检查 PostgreSQL 驱动已声明且进入 uv 锁文件，再按 [Dockerfile](../../backend/deploy/docker/Dockerfile) 构建；基础镜像不主动更新，依赖由 `uv sync --frozen` 安装。
4. 在临时应用容器只读检查现有数据库表结构，然后启动三个后端容器。
5. 通过容器内真实 HTTP 请求检查进程、数据库健康、未登录权限和参数校验。登录及数据验收需要另提供账号配置。

辅助模式：

```bash
uv run --project backend --frozen python backend/deploy/docker/deploy.py --prepare-only
uv run --project backend --frozen python backend/deploy/docker/deploy.py --build-only
```

未指定标签时只查询本机该仓库的镜像，优先验证 `latest`，其次验证最近创建的标签；也可用 `--base-image` 固定本机标签。构建仍可能需要联网下载锁定的 Python 依赖；本机已有基础镜像不代表应用依赖已经齐全。

PostgreSQL 驱动与智能体SDK已通过 uv 安装并锁定；真实数据库连接及隔离接口回归已通过。免费供应商适配和实际模型兼容性仍待完成，状态见[后端指南](../../backend/README.md)。不能把配置解析或临时HTTP测试通过当作 Docker 部署成功。

## 3. HTTP 验收

真实 PostgreSQL 的隔离回归测试可使用 [后端指南](../../backend/README.md#真实数据库与http自测) 中的 `--postgres --http` 模式；临时测试服务只用于验收，不替代正式 Docker 部署。

容器启动后，在宿主机执行基础 HTTP 检查：

```bash
uv run --project backend --frozen python backend/deploy/docker/http_check.py
```

提供现有账号时，从服务器环境变量读取口令；不将密码放在命令行。要求验证已发布的真实行情时加 `--require-data`：

```bash
uv run --project backend --frozen python backend/deploy/docker/http_check.py --username admin --password-env STOCKSIEVE_HTTP_PASSWORD --require-data
```

检查脚本通过真实 HTTP 请求验证统一响应、追踪标识、登录与退出、只读查询，并明确输出是否存在已发布数据。空库时仅验证空库行为，不能宣称初始化、同步和策略流水线已经通过；脚本不会写入模拟行情或调用模型。

## 4. 管理与日志

```bash
docker compose -f backend/deploy/docker/compose.yaml ps
docker compose -f backend/deploy/docker/compose.yaml logs --tail=100 api worker scheduler
docker compose -f backend/deploy/docker/compose.yaml run --rm api create-admin --username admin --name 管理员
```

管理员初始化交互读取密码，已有管理员时停止，不覆盖账号。应用容器以固定非root用户运行，根文件系统只读；配置注入、业务库和频控状态独立于镜像保存。不要删除频控持久卷以绕过冷却。

数据库备份沿用既有流程；更新应用只构建并替换后端容器。此前构建和启动命令被环境自动审批服务503故障阻断，未执行；现按用户安排暂缓容器启动。未变更现有业务表或已有容器。
