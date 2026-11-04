# 报告详情 `GET /analyses/{analysisId}`

[模块目录](README.md) · [接口总览](../README.md)

权限：任务所属用户，管理员也不能查看他人报告；无权访问和不存在均返回`404 ANALYSIS_NOT_FOUND`。

成功`200`返回任务。`status`为queued/running/succeeded/failed；`stage`为queued、preparing、researching、analyzing、writing、completed。researching表示准备资料，实际引用范围以sources为准。

失败保留最后阶段，`report=null`且`error`包含原因；不删除其他成功报告。成功时`report`如下：

| 字段 | 含义 |
| --- | --- |
| title、stock、snapshotId、dataDate | 标题、股票身份与引用行情版本；stock含code/name/industry等 |
| simulated、researchStatus | 新报告simulated=false；历史标识保留。`not_searched`表示旧报告未联网，`researched`表示已取得公开原文，`unavailable`表示公告暂缺但仍依据已有资料完成分析 |
| overview | decision含action（consider/wait/avoid）、label、reason；holding含持有判断label/reason；opportunities/risks为短句数组；pricePlan为技术观察条件和价位 |
| summary | 精简摘要，新报告最多200字 |
| sections | 六章，依次为company、advantages、risks、holding、entry、sell；每项含id/title/headline/text/sourceIds |
| reportScope | company表示已取得公开原文；available_data表示依据已有行情、技术指标及已同步财务，公告暂缺不阻止生成 |
| schemaVersion | 新报告为4；旧报告按原结构读取 |
| verifiedFacts | 已核对的数值、单位、统计期、口径、页码、原文和sourceId引用 |
| criticalNotes、coverageNotes | 重要事项核验缺口；其他资料范围与缺项 |
| calculationDetails | 已保存的计算输入、结果、公式和假设，供页面按需展开 |
| metrics | 实际指标的label/value/detail，缺失展示待核验；情景计算保留输入假设与费用口径，理论盈亏平衡胜率不代表实际胜率 |
| valuation | 公司与行业PE对比，见下文 |
| shareholders | 本期/上期股东户数、变化比例、统计与披露时间、来源及集中趋势；已知本期即展示；缺同口径上次时不比较变化，较早报告可省略 |
| sources | 实际引用资料，每项含id/title/publisher/url/publishedAt/fetchedAt，章节sourceIds关联此处 |
| dataQuality、missingInformation | complete/partial与资料缺失列表；部分结果明确判断范围 |
| materialsAsOfAt、generatedAt | 资料集合截至时间与报告生成时间；与行情日期分别展示 |

六章分别回答公司资料、优势、风险、已有持仓、合并的买入与不买条件、卖出条件。无公告时前两章按已有身份、行情和财务解读，不编造经营或事件事实。正文为纯文本，不能当HTML执行。筹码分布图不再附于报告，改用[独立筹码接口](../market/chips.md)。

`pricePlan`含waiting、entryLabel、entryPrice、entryCondition、exitPrice、exitCondition、considerIf、avoidIf。数值及条件来自冻结的计算结果；资料不足时显示暂无可靠参考。技术情景区间只是观察参考，不构成已核验的公司投资建议。

`valuation`含basis（当前TTM）、companyPe、industryPe、industry、industryClassification、companyDataDate、financialPeriodEnd、industryDataDate、industryStatistic、status、explanation。未取得同口径行业估值时industryPe与行业日期为null，status=unavailable，禁止直接判断相对高低；动态与静态PE不能替代TTM。

资料URL只采用实际保存的原文，未知可为null；发布日未知与未检索到资料均不意味着没有风险。未取得证据时不虚构公告、券商目标价、行业PE或公司分类；sources可包含无外部链接的本地计算资料。报告不包含筹码图，但可引用筹码指标。

支持[Word/PDF导出](export.md)，导出与所选报告版本一致。

`shareholders`只用于公司基本资料，包含status、latestCount、previousCount、changePercent、asOf、previousAsOf、publishedAt、sourceUrl、trend（当前unknown）、trendLabel、explanation。新报告与导出明确提示未核验，不能从成本图推断实际股东人数或主力吸筹；旧报告缺少此字段时页面提示可重新生成。

关键指标含“技术盈亏比（赔率）”，value形如`2.00 : 1`；detail给出当前价、上方/风险参考及计算口径。行情不足或无法建立有效上下方情景时显示“暂无可靠参考”。该值不代表胜率，不单独改变持有建议；生成后随报告版本保存，Word/PDF沿用相同值。
