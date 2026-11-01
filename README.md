# stocksieve

面向股票入门用户的收盘后选股项目。管理员初始化和更新股票数据，用户登录后浏览短线、波段、长线、趋势埋伏、低位筑底五类机会，查看行情图表并按需发起基本面、筹码及买卖建议分析。

前后端已通过真实HTTP接口对接，并使用本机ubuntu:24.04通过Docker Compose部署到当前服务器。当前上线沪深四板块，北交所后续补充；AI暂用后端模拟，其他业务数据由服务器管理。真实数据准备与验收状态见[后端指南](backend/README.md)，访问方法见[部署说明](docs/deployment/backend.md)。

## 代码目录

- `frontend/`：真实接口页面、交互、图表及前端测试。
- `backend/`：后端接口、数据同步、选股策略、AI 分析和账号权限。

## 项目文档

- [项目需求说明书（V1）](docs/requirements.md)
- [AI 分析专项评审](docs/product/ai-analysis.md)
- [后端概要设计](docs/design/backend.md)
- [免费数据源验证](docs/research/data-sources.md)
- [选股策略设计](docs/product/strategies/README.md)
- [AI 分析设计](docs/product/ai-analysis-design.md)
- [后端 API 接口文档（V1）](docs/api/README.md)
- [数据库初始化 SQL](backend/sql/init.sql)
- [PostgreSQL 单机部署](docs/deployment/postgresql.md)
- [前后端 Docker 部署](docs/deployment/backend.md)
- [前端启动与联调指南](frontend/README.md)
- [后端运行与测试指南](backend/README.md)
