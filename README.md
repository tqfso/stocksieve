# stocksieve

面向全 A 股（含北交所）的收盘后选股项目。管理员初始化和更新股票数据，用户登录后按行业浏览短线、波段、长线机会，并按需发起基本面、筹码及买卖建议的 AI 分析。

项目当前处于前端模拟版开发与产品评审阶段，全部页面使用模拟数据，后端接口对接后续再开展。

## 代码目录

- `frontend/`：前端页面、交互和组件，交互原型在此开发并逐步完善为正式前端。
- `backend/`：后端接口、数据同步、选股策略、AI 分析和账号权限。

## 项目文档

- [项目需求说明书（V1）](docs/requirements.md)
- [AI 分析专项评审](docs/product/ai-analysis.md)
- [后端概要设计](docs/design/backend.md)
- [免费数据源验证](docs/research/data-sources.md)
- [选股策略设计](docs/product/strategies/README.md)
- [AI 分析设计](docs/product/ai-analysis-design.md)
- [后端 API 接口文档（V1 设计稿）](docs/api/README.md)
- [数据库初始化 SQL](backend/sql/init.sql)
- [PostgreSQL 单机部署](docs/deployment/postgresql.md)
- [前端启动与评审指南](frontend/README.md)
