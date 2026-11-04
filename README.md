# stocksieve

面向股票入门用户的收盘后选股项目。管理员初始化和更新股票数据，用户可浏览五类策略机会，也可从“个股分析”按名称或代码搜索任意已覆盖股票，查看行情并发起AI分析。当前提供真实模型生成的六章报告、独立筹码展示及Word/PDF导出，详见[产品设计](docs/product/ai-analysis-design.md)。模型通过三个受控工具检索公告、阅读原文并计算金融指标。

前后端已通过真实HTTP接口对接，并使用本机ubuntu:24.04通过Docker Compose部署到当前服务器。当前上线沪深四板块，北交所后续补充；分析模式由服务端配置，业务数据由服务器管理。真实数据准备与验收状态见[后端指南](backend/README.md)，访问方法见[线上部署说明](docs/deployment/production.md)。

## 代码目录

- `frontend/`：真实接口页面、交互、图表及前端测试。
- `backend/`：后端接口、数据同步、选股策略、AI 分析和账号权限。

## 项目文档

- [项目需求说明书（V1）](docs/requirements.md)
- [后端概要设计](docs/design/backend.md)
- [选股策略设计](docs/product/strategies/README.md)
- [AI 分析设计](docs/product/ai-analysis-design.md)
- [后端 API 接口文档（V1）](docs/api/README.md)
- [数据库初始化 SQL](backend/sql/init.sql)
- [线上环境部署（前端、后端与数据库）](docs/deployment/production.md)
- [前端启动与联调指南](frontend/README.md)
- [后端运行与测试指南](backend/README.md)
