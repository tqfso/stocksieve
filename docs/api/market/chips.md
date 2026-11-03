# 筹码分布 `GET /stocks/{stockId}/chips`

[接口总览](../README.md)

权限：登录用户。可选`snapshotId`，省略使用最新已发布快照。

成功`200`，`data`含stockId、snapshotId、dataDate、method、distribution、missingInformation和shareholders。不创建AI任务，不调用模型或联网。

`distribution`为成本分布估算：currentPrice（元）、bins（lower/upper/price/ratio，比例为%）、averageCost、profitableRatio、cost70、cost90、support、resistance。占比合计100%，价格使用同一快照前复权口径；现价某侧没有正占比档位时该侧参考区为null。

资料不足时distribution=null，missingInformation说明原因，仍返回200。股票未覆盖、尚无行情、无权访问分别沿用个股接口约定。

shareholders是独立的股东户数观察区：status、latestCount、previousCount、changePercent、asOf、previousAsOf、publishedAt、sourceUrl、trend、trendLabel、explanation。当前未接入已核验户数，status=unavailable、数值和日期为null，不作集中或分散判断。后续接入时需明确相同股本口径、统计日、披露日与原文来源。

成本分布不等同真实持股清单；股东户数减少也不直接证明主力吸筹，两项指标分别解释。
