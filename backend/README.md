# StockSieve 后端

Python 3.12、aiohttp、SQLAlchemy、PostgreSQL；依赖和虚拟环境统一由 uv 管理。采用模块化单体与分层架构，数据源通过能力接口扩展，见[后端概要设计](../docs/design/backend.md)。

## 运行

前端、PostgreSQL、API、worker、scheduler 的统一部署与配置见[线上部署](../docs/deployment/production.md)。当前访问地址为 `http://172.21.21.98:24048`。

- 新浪提供沪深四板块的证券基础、日历、行情、复权和市值；百度提供行业与题材。
- 默认先发布150个交易日策略，再回补至2500个交易日，约十年；历史范围由 env 控制。
- 相同日期、渠道、配置和分类的成功结果直接复用；失败同步从原任务检查点继续。
- 任意已覆盖股票均可分析。AI支持真实模型与离线模拟；真实模式检索巨潮公告、读取HTML/PDF并使用固定金融公式，缺资料如实展示；任务、报告和权限走真实服务；统一七章报告支持Word/PDF导出，筹码为独立查询，股东户数资料待接入。

`backend/.env` 是唯一配置文件，环境变量优先。参考[配置示例](.env.example)，不要提交真实凭据。

真实分析设置`STOCKSIEVE_AI_MODE=live`并配置模型地址、模型、密钥及协议。模型仅可调用检索下载、文档阅读、金融计算三个工具，资料入口和查询配方集中在[股票分析技能](skills/stock-analysis/SKILL.md)。任务限制工具次数、输出长度和总耗时，记录实际Token用量；模型失败明确报错，不自动退回模拟。当前公告研究不保证穷尽事件，行业PE和结构化股东户数仍待接入。

## 命令入口

在仓库根目录运行 `uv run --frozen --project backend python backend/run.py <命令>`：

| 命令 | 用途 |
| --- | --- |
| `api --host 127.0.0.1 --port 8000` | HTTP接口与前端静态文件 |
| `worker` | 执行同步和分析任务 |
| `scheduler` | 登记到期的定时任务 |
| `create-admin` | 创建管理员，支持私有口令文件 |
| `check-config` | 查看非敏感运行配置 |
| `check-db [--counts]` | 检查连接、表字段及可选业务行数 |

探测、数据修复和历史部署兼容入口已移除；日常数据操作从管理页面进行。

## 测试

```bash
uv sync --project backend --frozen
uv run --project backend --frozen pytest backend/tests -q
uv run --project backend --frozen playwright install chromium
uv run --project backend --frozen pytest backend/tests --browser -q
```

显式真实模型验收：`uv run --project backend --frozen pytest backend/tests/test_live_analysis.py -k real_provider --live-model -q -s`。此项使用私有模型配置及共享渠道频控，产生模型用量；行情为隔离schema内受控输入，正式行情另行部署复验。普通回归使用受控供应商和模型响应。

接口测试始终使用真实 PostgreSQL 和随机端口 HTTP 服务，在随机 `stocksieve_test_` schema 中执行完整初始化 SQL，结束自动清理，不向 public 写测试数据。供应商返回值使用受控输入，测试不会消耗真实渠道预算。

保留的少量单元测试用于策略、复权、筹码等核心计算及限频保护。浏览器测试需先构建前端，复用同一隔离数据库；`tests/e2e/browser_check.py` 是浏览器辅助代码。

VS Code 使用[工作区配置](../.vscode/settings.json)中的 uv 解释器和 `backend/src` 导入路径。
