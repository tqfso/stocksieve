# 报告详情 `GET /analyses/{analysisId}`

[模块目录](README.md) · [接口总览](../README.md)

权限：任务所属用户，管理员也不能查看他人报告；无权访问和不存在均返回`404 ANALYSIS_NOT_FOUND`。

成功`200`返回任务。`status`为queued/running/succeeded/failed；`stage`为queued、preparing、researching、analyzing、writing、completed。模拟阶段researching只准备本地资料，不表示已联网。

失败保留最后阶段，`report=null`且`error`包含原因；不删除其他成功报告。成功时`report`如下：

| 字段 | 含义 |
| --- | --- |
| title、stock、snapshotId、dataDate | 标题、股票身份与引用行情版本；stock含code/name/industry等 |
| simulated、researchStatus | 模拟标识；`not_searched`明确未联网，`researched`表示已执行资料研究 |
| overview | decision含action（consider/wait/avoid）、label、reason；holding含持有判断label/reason；opportunities/risks为短句数组；pricePlan为技术观察条件和价位 |
| summary | 报告摘要 |
| sections | 七章，依次为company、advantages、risks、holding、buy、avoid、sell；每项含id/title/headline/text/sourceIds |
| metrics | 实际指标的label/value/detail，缺失展示待核验，不填零代替 |
| valuation | 公司与行业PE对比，见下文 |
| shareholders | 本期/上期股东户数、变化比例、统计与披露时间、来源及集中趋势；未采集保持null/unknown，较早报告可省略 |
| sources | 实际引用资料，每项含id/title/publisher/url/publishedAt/fetchedAt，章节sourceIds关联此处 |
| dataQuality、missingInformation | complete/partial与资料缺失列表；部分结果明确判断范围 |
| materialsAsOfAt、generatedAt | 资料集合截至时间与报告生成时间；与行情日期分别展示 |

七章分别回答公司基本资料（含公司类型、股东户数与集中趋势）、优势壁垒与市场关注、风险、是否持有、买入条件与区间、不买条件、卖出条件。正文为纯文本，不能当HTML执行。筹码分布图不再附于报告，改用[独立筹码接口](../market/chips.md)。

`pricePlan`含waiting、entryLabel、entryPrice、entryCondition、exitPrice、exitCondition、considerIf、avoidIf。数值及条件来自冻结的计算结果；资料不足时显示暂无可靠参考。模拟区间只是技术观察，不构成已核验的公司投资建议。

`valuation`含basis（当前TTM）、companyPe、industryPe、industry、industryClassification、companyDataDate、financialPeriodEnd、industryDataDate、industryStatistic、status、explanation。未取得同口径行业估值时industryPe与行业日期为null，status=unavailable，禁止直接判断相对高低；动态与静态PE不能替代TTM。

资料URL只采用实际保存的原文，未知可为null；发布日未知与未检索到资料均不意味着没有风险。当前模拟模式不联网，不虚构公告、券商目标价、行业PE或公司分类；sources可包含无外部链接的本地计算资料。报告不包含筹码图，但可引用筹码指标。

支持[Word/PDF导出](export.md)，导出与所选报告版本一致。

`shareholders`只用于公司基本资料，包含status、latestCount、previousCount、changePercent、asOf、previousAsOf、publishedAt、sourceUrl、trend（当前unknown）、trendLabel、explanation。新报告与导出明确提示未核验，不能从成本图推断实际股东人数或主力吸筹；旧报告缺少此字段时页面提示可重新生成。

关键指标含“技术盈亏比（赔率）”，value形如`2.00 : 1`；detail给出当前价、上方/风险参考及计算口径。行情不足或无法建立有效上下方情景时显示“暂无可靠参考”。该值不代表胜率，不单独改变持有建议；生成后随报告版本保存，Word/PDF沿用相同值。
