"""本文件确定性计算累计财报 TTM、PE 和换手衰减筹码估算，不由模型补造数值。"""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from .domain import Bar, adjusted_history
from .errors import MissingData

D = Decimal


def financial_metrics(reports: list[dict[str, Any]], market_cap: Decimal | None) -> dict[str, Any]:
    """只接受合并、人民币、年初累计口径；季度 TTM 使用本年累计+上年全年-上年同期。"""
    eligible = [row for row in reports if row['statement_scope'] == 'consolidated'
                and row['period_start'] == date(row['period_end'].year, 1, 1)
                and (row.get('extra_json') or {}).get('currency') == 'CNY']
    def publication(row: dict[str, Any]) -> datetime:
        value = row.get('published_at') or row.get('fetched_at')
        if value is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    eligible.sort(key=lambda row: (row['period_end'], row.get('published_at') is not None, publication(row)), reverse=True)
    periods: dict[date, dict[str, Any]] = {}
    for row in eligible:
        periods.setdefault(row['period_end'], row)
    if not periods:
        raise MissingData('缺少可核验的合并、人民币累计财报。')
    latest = periods[max(periods)]
    end = latest['period_end']
    if (end.month, end.day) not in {(3, 31), (6, 30), (9, 30), (12, 31)}:
        raise MissingData('报告期不是标准季度累计口径。')
    components = [latest]
    previous_annual = previous_same = None
    if end.month != 12:
        previous_annual = periods.get(date(end.year - 1, 12, 31))
        previous_same = periods.get(date(end.year - 1, end.month, end.day))
        if previous_annual is None or previous_same is None:
            raise MissingData('缺少上年全年或上年同期累计财报。')
        components.extend([previous_annual, previous_same])
    def ttm(field: str) -> Decimal | None:
        if any(row.get(field) is None for row in components):
            return None
        current = D(str(latest[field]))
        return current if previous_annual is None else current + D(str(previous_annual[field])) - D(str(previous_same[field]))
    revenue, profit, cashflow = (ttm(field) for field in ('revenue_ytd', 'net_profit_parent_ytd', 'operating_cashflow_ytd'))
    pe = market_cap / profit if market_cap is not None and market_cap > 0 and profit is not None and profit > 0 else None
    return {'periodEnd': end.isoformat(), 'revenueTtm': float(revenue) if revenue is not None else None,
            'netProfitParentTtm': float(profit) if profit is not None else None,
            'operatingCashflowTtm': float(cashflow) if cashflow is not None else None,
            'peTtm': float(pe) if pe is not None else None, 'reportIds': [row['id'] for row in components]}


def chip_distribution(bars: list[Bar], as_of: date, *, bin_count: int = 40) -> dict[str, Any]:
    """以换手率衰减旧成本、在当日价格区间均匀加入新成本；这是估算模型而非持股清单。"""
    if not 10 <= bin_count <= 100:
        raise ValueError('筹码档位数量超限')
    selected = sorted((bar for bar in bars if bar.day <= as_of), key=lambda bar: bar.day)[-120:]
    if len(selected) < 60 or any(bar.turnover is None for bar in selected):
        raise MissingData('筹码估算至少需要60个交易日及对应换手率。')
    adjusted = adjusted_history(selected, as_of)
    low, high = min(bar.low for bar in adjusted), max(bar.high for bar in adjusted)
    padding = max((high - low) / 20, adjusted[-1].close / 100)
    low, high = max(D('.000001'), low - padding), high + padding
    width = (high - low) / bin_count
    weights = [D(0)] * bin_count
    # 首日成本分布同样是假设，随之后换手逐步衰减，报告须保留方法版本。
    for position, bar in enumerate(adjusted):
        fraction = D(1) if position == 0 else min(D(1), bar.turnover / 100)
        weights = [weight * (1 - fraction) for weight in weights]
        if bar.high == bar.low:
            index = max(0, min(bin_count - 1, int((bar.close - low) / width)))
            weights[index] += fraction
        else:
            for index in range(bin_count):
                bottom, top = low + width * index, low + width * (index + 1)
                overlap = max(D(0), min(bar.high, top) - max(bar.low, bottom))
                weights[index] += fraction * overlap / (bar.high - bar.low)
    total = sum(weights)
    if total <= 0:
        raise MissingData('无法形成有效成本分布。')
    weights = [weight / total for weight in weights]
    bins = [{'lower': float(low + width * index), 'upper': float(low + width * (index + 1)),
             'price': float(low + width * (D(index) + D('.5'))), 'ratio': float(weight * 100)} for index, weight in enumerate(weights)]
    def quantile(percent: Decimal) -> float:
        cumulative = D(0)
        for index, weight in enumerate(weights):
            if cumulative + weight >= percent and weight > 0:
                return float(low + width * index + width * (percent - cumulative) / weight)
            cumulative += weight
        return float(high)
    current = adjusted[-1].close
    profitable = sum(weight * min(D(1), max(D(0), (current - (low + width * index)) / width)) for index, weight in enumerate(weights))
    below = [item for item in bins if item['upper'] <= float(current) and item['ratio'] > 0]
    above = [item for item in bins if item['lower'] > float(current) and item['ratio'] > 0]
    return {'currentPrice': float(current), 'bins': bins,
            'averageCost': sum(item['price'] * item['ratio'] / 100 for item in bins),
            'profitableRatio': float(profitable * 100), 'cost70': [quantile(D('.15')), quantile(D('.85'))],
            'cost90': [quantile(D('.05')), quantile(D('.95'))],
            'support': max(below, key=lambda item: item['ratio']) if below else None,
            'resistance': max(above, key=lambda item: item['ratio']) if above else None}
