# 导出报告 `GET /analyses/{analysisId}/export`

[模块目录](README.md) · [接口总览](../README.md)

权限：成功报告所属用户，需Bearer令牌。管理员不能导出他人报告，权限与详情一致。

查询必填`format=docx`或`format=pdf`。成功`200`直接返回文件，不使用JSON响应包：

- Word类型为`application/vnd.openxmlformats-officedocument.wordprocessingml.document`，PDF为`application/pdf`。
- `Content-Disposition: attachment`包含UTF-8文件名；客户端使用鉴权请求读取文件，不把令牌放到下载URL中。
- 包含摘要、七章、关键指标、引用与日期；股东户数归入公司基本资料，行业PE归入关键指标，不单列估值对比或资料缺失清单；原文链接可点击，历史报告原标识保留。
- 导出使用已保存版本，不重新分析或联网，不包含独立筹码展示页；PDF内嵌中文字体。

格式无效返回`400 INVALID_ARGUMENT`，报告尚未成功或不是新版完整报告返回`409 ANALYSIS_EXPORT_UNAVAILABLE`；未登录返回401，无权访问和不存在返回404。错误仍使用统一JSON响应，可重试且不影响原报告。
