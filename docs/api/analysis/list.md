# 本人报告历史 `GET /analyses`

[模块目录](README.md) · [接口总览](../README.md)

权限：登录用户，仅返回本人统一报告记录。

查询可选`stockId`、`snapshotId`、`status`、`page`、`pageSize`，不接受`kind`或`strategy`。按创建时间倒序、相同时间按ID倒序。

成功`200`，标准分页`items/total/page/pageSize`；每项同[生成报告](create.md)的任务结构，省略`report`正文。使用[详情](detail.md)读取报告。

恢复任务时查询该股票所有状态，不传`status`；成功历史可传`status=succeeded`。不传快照时允许跨快照查看本人历史，每份报告保留其原行情日期。刷新或重新登录只查询，不自动提交任务。
