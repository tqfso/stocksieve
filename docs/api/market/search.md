# 股票搜索 `GET /stocks`

权限：登录用户。搜索完整股票目录，不受策略、行业或题材筛选限制；当前返回已开放板块内、未标记退市的股票。

| 参数 | 说明 |
| --- | --- |
| search | 名称或代码，最长50字；支持部分匹配，完整代码优先，也可用股票稳定ID |
| page、pageSize | 标准分页，默认1/20，pageSize最大100 |

成功200，返回`{items, total, page, pageSize}`。每项包含`stockId`、`code`、`name`、`exchange`、`board`、`dataStatus`和`dataDate`。

`dataStatus=ready`表示已有收盘报价，`pending`表示行情尚未发布或该股票尚未覆盖；准备状态的`dataDate=null`。可用报价不保证每类分析资料都充足，各分析独立判断。未初始化时返回空列表，搜索无匹配也返回空列表；不存在或不支持的市场不补造股票身份。
