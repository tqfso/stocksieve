"""本文件整理报告使用的真实指标和估值对比，缺失资料不补造。"""

from typing import Any


def report_facts(record: dict[str, Any], facts: dict[str, Any], data_date: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """统一页面与导出所用的指标展示值；行业估值待真实资料接入。"""
    quote, financial = record.get('quote') or {}, facts.get('financial') or {}
    metrics: list[dict[str, str]] = []
    def add(label: str, value: Any, unit: str, detail: str = '同一行情快照') -> None:
        metrics.append({'label': label, 'value': '待核验' if value is None else f'{value:.2f}{unit}', 'detail': detail})
    add('收盘价', quote.get('price'), ' 元')
    for period in (5, 10, 20, 60):
        add(f'MA{period}', facts['averages'].get(f'ma{period}'), ' 元', '前复权；不足完整窗口时为空')
    scenario = (facts.get('generalTrade') or {}).get('riskReward') or {}
    ratio = scenario.get('ratio')
    if ratio is not None:
        detail = (f"快照收盘价{scenario['currentPrice']:.2f}、上方参考{scenario['upsideReference']:.2f}、风险参考{scenario['riskReference']:.2f}元；"
            '以前20日高低点计算，未计交易费用，不代表胜率或实际成交结果。')
    else:
        detail = ('（前20日高点－现价）÷（现价－前20日低点）；'
            '行情、复权或交易状态不足，或现价不在两个参考位之间时不计算。')
    metrics.append({'label': '技术盈亏比（赔率）', 'value': '暂无可靠参考' if ratio is None else f'{ratio:.2f} : 1', 'detail': detail})
    for label, field in [('营业收入（TTM）', 'revenueTtm'), ('归母净利润（TTM）', 'netProfitParentTtm')]:
        value = financial.get(field)
        add(label, value / 100000000 if value is not None else None, ' 亿元', financial.get('periodEnd', '缺少可核验财报'))
    add('公司 PE（TTM）', financial.get('peTtm'), ' 倍', '总市值 / 归母净利润TTM；非正盈利无有效倍数')
    add('行业 PE（TTM）', None, ' 倍', '尚未取得同口径、可比日期的行业估值')
    add('公司静态 PE', None, ' 倍', '尚未取得可核验的静态口径')
    add('公司动态 PE', None, ' 倍', '尚未取得可核验的动态盈利口径')
    chips = facts.get('chips')
    if chips:
        add('筹码平均成本', chips['averageCost'], ' 元', '换手衰减模型估算')
        add('估算获利比例', chips['profitableRatio'], '%', '不等同真实股东持仓')
    industry = record['stock'].get('industry')
    valuation = {'basis': 'TTM', 'companyPe': financial.get('peTtm'), 'industryPe': None,
        'industry': industry['name'] if industry else None, 'industryClassification': '已同步行业分类',
        'companyDataDate': data_date, 'financialPeriodEnd': financial.get('periodEnd'), 'industryDataDate': None,
        'industryStatistic': None, 'status': 'unavailable',
        'explanation': '尚未取得同口径、可比日期的行业市盈率，不能据此判断公司估值高低。'}
    return metrics, valuation


def shareholder_facts() -> dict[str, Any]:
    """股东户数尚未采集时保持未知，不能从成交成本估算反推持有人数量。"""
    return {'status': 'unavailable', 'latestCount': None, 'previousCount': None, 'changePercent': None,
        'asOf': None, 'previousAsOf': None, 'publishedAt': None, 'sourceUrl': None,
        'trend': 'unknown', 'trendLabel': '暂不能判断集中或分散',
        'explanation': '尚未接入已核验的股东户数资料，不能判断持股是否趋于集中或分散；日线成本分布不等同股东人数变化。'}
