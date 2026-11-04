"""本文件从共用事实数据生成指标、股东信息和覆盖说明，页面与文件共用结果。"""

from datetime import date
from decimal import Decimal
from typing import Any

from stocksieve.modules.analysis.domain.calculations import financial_metrics
from stocksieve.shared.errors import MissingData

FINANCIAL_FIELDS = {'revenue': 'revenue_ytd', 'net_profit': 'net_profit_parent_ytd',
                    'operating_cashflow': 'operating_cashflow_ytd'}


def complete_financial_facts(record: dict[str, Any], facts: dict[str, Any]) -> bool:
    """用已核对的累计期间计算TTM；仅有前三季度数据时不能冒充全年或TTM。"""
    rows: dict[str, dict[str, Any]] = {}
    for fact in facts.get('verifiedFacts', []):
        if fact['metric'] not in FINANCIAL_FIELDS:
            continue
        end = date.fromisoformat(fact['periodEnd'])
        row = rows.setdefault(fact['periodEnd'], {'id': fact['periodEnd'], 'period_start': date(end.year, 1, 1),
            'period_end': end, 'statement_scope': 'consolidated', 'extra_json': {'currency': 'CNY'}})
        row[FINANCIAL_FIELDS[fact['metric']]] = fact['value']
    if not rows:
        return False
    try:
        cap = (record.get('quote') or {}).get('totalMarketCap')
        result = financial_metrics(list(rows.values()), Decimal(str(cap)) if cap is not None else None)
    except MissingData:
        return False
    if result['revenueTtm'] is None or result['netProfitParentTtm'] is None:
        return False
    facts['financial'] = result
    return True


def shareholder_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """已有本期户数就展示，只有同口径上次数据齐备时才比较户数变化。"""
    counts = [item for item in facts.get('verifiedFacts', []) if item['metric'] == 'shareholder_count']
    counts.sort(key=lambda item: (item['periodEnd'], item['basis'] == 'A'), reverse=True)
    result: dict[str, Any] = {'status': 'unavailable', 'latestCount': None, 'previousCount': None,
        'changePercent': None, 'asOf': None, 'previousAsOf': None, 'publishedAt': None, 'sourceUrl': None,
        'basis': None, 'trend': 'unknown', 'trendLabel': '暂不能比较户数变化',
        'explanation': '尚未取得可核对的股东户数。'}
    if not counts:
        return result
    latest = counts[0]
    previous = next((item for item in counts[1:] if item['basis'] == latest['basis'] and item['periodEnd'] < latest['periodEnd']), None)
    result.update(status='partial', latestCount=int(latest['value']), asOf=latest['periodEnd'],
        publishedAt=latest['publishedAt'], sourceUrl=latest['sourceUrl'], basis=latest['basis'],
        explanation='已核对本期户数；缺少同口径对比期，暂不比较变化。')
    if previous:
        change = (latest['value'] / previous['value'] - 1) * 100
        result.update(status='available', previousCount=int(previous['value']), previousAsOf=previous['periodEnd'],
            changePercent=round(change, 2), trend='decreasing' if change < 0 else 'increasing' if change > 0 else 'unchanged',
            trendLabel=f'较对比期{"减少" if change < 0 else "增加" if change > 0 else "持平"}' + (f'{abs(change):.2f}%' if change else ''),
            explanation='户数变化不能直接证明主力增减仓；比较日期和普通股/A股口径须一致。')
    return result


def financial_gap(facts: dict[str, Any]) -> str:
    """准确列出计算最近12个月指标还缺的期间和字段，不否定已取得的当期业绩。"""
    observed = [item for item in facts.get('verifiedFacts', []) if item['metric'] in {'revenue', 'net_profit'}]
    if not observed:
        return '尚未取得可核对的当期财务指标。'
    latest = date.fromisoformat(max(item['periodEnd'] for item in observed))
    periods = [latest] if latest.month == 12 else [latest, date(latest.year - 1, 12, 31), date(latest.year - 1, latest.month, latest.day)]
    missing = []
    for end in periods:
        known = {item['metric'] for item in observed if item['periodEnd'] == end.isoformat()}
        fields = [label for key, label in (('revenue', '收入'), ('net_profit', '归母净利润')) if key not in known]
        if fields:
            period = f'{end.year}年全年' if end.month == 12 else f'{end.year}年1—{end.month}月'
            missing.append(period + '及'.join(fields))
    return '最近12个月指标尚缺：' + '；'.join(missing) + '。' if missing else '最近12个月指标暂未完成核对。'


def report_facts(record: dict[str, Any], facts: dict[str, Any], data_date: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """只列已取得的指标，缺项集中说明，避免空白估值行淹没实际信息。"""
    quote, financial = record.get('quote') or {}, facts.get('financial') or {}
    metrics: list[dict[str, str]] = []

    def add(label: str, value: Any, unit: str, detail: str = '同一收盘数据') -> None:
        if value is not None:
            metrics.append({'label': label, 'value': f'{value:.2f}{unit}', 'detail': detail})

    add('收盘价', quote.get('price'), ' 元')
    for period in (5, 10, 20, 60):
        add(f'{period}日平均价', facts['averages'].get(f'ma{period}'), ' 元', '最近交易日的平均价格；前复权')
    available = [item for item in facts.get('verifiedFacts', []) if item['metric'] != 'shareholder_count']
    newest = max((item['periodEnd'] for item in available), default=None)
    labels = {'revenue': '营业收入', 'net_profit': '归母净利润', 'adjusted_profit': '扣除非经常性损益后的净利润',
              'operating_cashflow': '经营现金流'}
    for item in available:
        if item['periodEnd'] != newest:
            continue
        end = date.fromisoformat(item['periodEnd'])
        period = f'{end.year}年全年' if end.month == 12 else f'{end.year}年1—{end.month}月'
        detail = period
        if item['yoy'] is not None:
            change = item['yoy']
            detail += f'；较上年同期{"增长" if change >= 0 else "下降"}{abs(change):.2f}%'
        scale, unit = (100000000, ' 亿元') if abs(item['value']) >= 100000000 else (10000, ' 万元')
        add(labels[item['metric']], item['value'] / scale, unit, detail)
    for label, key in [('最近12个月营业收入', 'revenueTtm'), ('最近12个月归母净利润', 'netProfitParentTtm')]:
        value = financial.get(key)
        add(label, value / 100000000 if value is not None else None, ' 亿元', financial.get('periodEnd', ''))
    add('市盈率（最近12个月）', financial.get('peTtm'), ' 倍', '公司总市值/最近12个月归母净利润；亏损时不适用')
    scenario = (facts.get('generalTrade') or {}).get('riskReward') or {}
    ratio = scenario.get('ratio')
    detail = '前20日高低价与现价形成的条件估算，未计费用，不代表实际胜率。'
    if ratio is not None:
        detail = f"现价{scenario['currentPrice']:.2f}，上方参考{scenario['upsideReference']:.2f}，风险参考{scenario['riskReference']:.2f}元；" + detail
    calculations = [item for item in facts.get('calculations', []) if item['operation'] == 'risk_reward']
    if calculations:
        calc = calculations[-1]
        inputs = calc['inputs']
        ratio = calc['result']['riskRewardRatio']
        cost = inputs.get('costRatePercent', 0)
        detail = f"假设价格{inputs['entry']}，上方参考{inputs['target']}，风险参考{inputs['stop']}元；" + (f'计入往返成本率{cost}%。' if float(cost) else '未计交易费用。') + '仅为条件情景，不代表实际胜率。'
    metrics.append({'label': '技术情景盈亏比', 'value': '暂无可靠参考' if ratio is None else f'{ratio:.2f} : 1', 'detail': detail})
    chips = facts.get('chips')
    if chips:
        add('筹码平均成本', chips['averageCost'], ' 元', '历史成交成本估算')
        add('估算获利比例', chips['profitableRatio'], '%', '不等同真实股东持仓')
    industry = record['stock'].get('industry')
    valuation = {'basis': 'TTM', 'companyPe': financial.get('peTtm'), 'industryPe': None,
        'industry': industry['name'] if industry else None, 'industryClassification': '已同步行业分类',
        'companyDataDate': data_date, 'financialPeriodEnd': financial.get('periodEnd'), 'industryDataDate': None,
        'industryStatistic': None, 'status': 'unavailable', 'explanation': '行业估值资料尚未取得，暂不作行业估值比较。'}
    return metrics, valuation
