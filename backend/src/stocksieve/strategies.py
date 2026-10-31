"""本文件按交易日历和统一前复权口径计算五类策略，拒绝未来数据和不完整窗口。"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

from .domain import Bar, StockState, Strategy, adjusted_history
from .errors import MissingData

D = Decimal
# 对外策略标识、条件ID及默认排序遵循 V1 契约，数值条件在计算函数中判断。
NAMES = {'short': '短线', 'swing': '波段', 'long': '长线', 'trend_wait': '趋势埋伏', 'base_wait': '低位筑底'}
CONDITIONS = {
    'short': ('price_breakout', 'amount_expansion', 'ma20_distance'),
    'swing': ('uptrend', 'pullback_near_ma20', 'pullback_amount', 'stabilization'),
    'long': ('ma_alignment', 'ma60_rising', 'ma120_rising', 'ma60_distance'),
    'trend_wait': ('trend_holding', 'range_contraction', 'amount_contraction', 'near_range_upper'),
    'base_wait': ('trend_unrepaired', 'low_position', 'range_contraction', 'no_new_low', 'amount_contraction', 'inside_range'),
}
INVALIDATION = {
    'short': '后续收盘价低于入选时的突破参考价B。',
    'swing': '后续收盘价低于入选时的回调参考低点P。',
    'long': '连续5个交易日收盘低于各自MA60，或MA60不再高于MA120。',
    'trend_wait': '收盘低于本次区间下沿P，或趋势条件不再满足。',
    'base_wait': '收盘低于本次区间下沿P。',
}
SORTS = {'short': ('amountMultiple', 'desc'), 'swing': ('ma20Distance', 'asc'),
         'long': ('ma60Distance', 'asc'), 'trend_wait': ('distanceToUpper', 'asc'),
         'base_wait': ('position120', 'asc')}


@dataclass(frozen=True)
class Evaluation:
    signals: tuple[dict[str, Any], ...]
    excluded_reason: str | None = None


class Context:
    """窗口按交易日索引读取；缺失交易日不能用更早记录凑足数量。"""

    def __init__(self, bars: list[Bar], days: list[date], as_of: date) -> None:
        self.days = sorted(day for day in days if day <= as_of)
        if len(set(self.days)) != len(self.days) or not self.days or self.days[-1] != as_of:
            raise MissingData('计算日交易日历缺失或重复')
        self.bars = {bar.day: bar for bar in bars if bar.day <= as_of}

    def window(self, count: int, ago: int = 0) -> list[Bar]:
        end = len(self.days) - ago
        if count < 1 or end < count:
            raise MissingData('历史长度不足')
        try:
            return [self.bars[day] for day in self.days[end - count:end]]
        except KeyError as error:
            raise MissingData('交易日窗口有缺失') from error

    def bar(self, ago: int = 0) -> Bar:
        return self.window(1, ago)[0]

    def ma(self, count: int, ago: int = 0) -> Decimal:
        return sum((bar.close for bar in self.window(count, ago)), D(0)) / count

    def amount(self, count: int, ago: int = 0) -> Decimal:
        amounts = [bar.amount for bar in self.window(count, ago)]
        if any(value is None for value in amounts):
            raise MissingData('成交额缺失')
        return sum((value for value in amounts if value is not None), D(0)) / count


def ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator <= 0:
        raise MissingData('计算分母为零')
    return numerator / denominator


def pct(current: Decimal, previous: Decimal) -> Decimal:
    return (ratio(current, previous) - 1) * 100


def evaluate_one(code: Strategy, context: Context) -> dict[str, Any] | None:
    """条件使用未舍入的 Decimal 判断，入选后才转换为展示数值。"""
    c = context
    price = c.bar().close
    ma20 = c.ma(20)
    reference: Decimal | None = None
    if code == 'short':
        reference = max(bar.high for bar in c.window(20, 1))
        multiple = ratio(c.amount(1), c.amount(5, 1))
        distance = pct(price, ma20)
        passed = [price > reference, multiple >= D('1.5'), 0 < distance <= 10]
        metrics = {'breakoutReference': reference, 'amountMultiple': multiple, 'ma20': ma20, 'ma20Distance': distance}
        reason = f'突破此前20日高点，成交额为此前5日均值的{multiple:.2f}倍，高于MA20 {distance:.2f}%。'
    elif code == 'swing':
        ma60, old60 = c.ma(60), c.ma(60, 5)
        pullback = any(c.bar(ago).close < c.bar(ago + 1).close
                       and c.bar(ago + 1).close > c.ma(20, ago + 1)
                       and abs(pct(c.bar(ago).close, c.ma(20, ago))) <= 3
                       for ago in range(1, 6))
        amount_ratio = ratio(c.amount(5, 1), c.amount(20, 1))
        reference = min(bar.low for bar in c.window(5, 1))
        passed = [ma20 > ma60 and ma60 > old60, pullback,
                  amount_ratio <= D('.8'), price > c.bar(1).close and price > ma20]
        metrics = {'ma20': ma20, 'ma60': ma60, 'ma60Change5': pct(ma60, old60),
                   'ma20Distance': pct(price, ma20), 'pullbackAmountRatio': amount_ratio, 'pullbackLow': reference}
        reason = '上升趋势中缩量回调至MA20附近，当日收涨并站上MA20。'
    elif code == 'long':
        ma60, ma120 = c.ma(60), c.ma(120)
        change60, change120 = pct(ma60, c.ma(60, 20)), pct(ma120, c.ma(120, 20))
        distance = pct(price, ma60)
        passed = [ma20 > ma60 > ma120, change60 > 0, change120 > 0, 0 < distance <= 20]
        metrics = {'ma20': ma20, 'ma60': ma60, 'ma120': ma120,
                   'ma60Change20': change60, 'ma120Change20': change120, 'ma60Distance': distance}
        reason = f'中长期均线多头排列并上行，收盘价高于MA60 {distance:.2f}%。'
    else:
        previous = c.window(20, 1)
        upper, lower = max(bar.high for bar in previous), min(bar.low for bar in previous)
        width = pct(upper, lower)
        amount_ratio = ratio(c.amount(5), c.amount(20))
        ma60 = c.ma(60)
        reference = lower
        metrics = {'rangeLower': lower, 'rangeUpper': upper, 'rangeWidth': width,
                   'amountRatio': amount_ratio, 'ma20': ma20, 'ma60': ma60}
        if code == 'trend_wait':
            distance = (upper - price) / upper * 100
            change = pct(ma60, c.ma(60, 5))
            passed = [ma20 >= ma60 and change >= 0, width <= 12,
                      amount_ratio <= D('.8'), lower <= price <= upper and 0 <= distance <= 3]
            metrics.update(distanceToUpper=distance, ma60Change5=change)
            reason = f'上升趋势中缩量整理，距区间上沿{distance:.2f}%，等待突破确认。'
        else:
            history = c.window(120, 1)
            high120, low120 = max(bar.high for bar in history), min(bar.low for bar in history)
            position = ratio(price - low120, high120 - low120) * 100
            low5, previous_low15 = min(bar.low for bar in c.window(5)), min(bar.low for bar in c.window(15, 5))
            passed = [ma20 < ma60, 0 <= position <= 35, width <= 12,
                      low5 >= previous_low15, amount_ratio <= D('.8'), lower <= price <= upper]
            metrics.update(position120=position, low5=low5, previousLow15=previous_low15)
            reason = f'处于此前120日价格区间的{position:.2f}%，近期未创新低，等待筑底确认。'
    if not all(passed):
        return None
    return {'id': str(uuid4()), 'strategy': code, 'reason': reason,
            'metrics': {key: float(value) for key, value in metrics.items()},
            'conditionResults': [{'conditionId': key, 'passed': value} for key, value in zip(CONDITIONS[code], passed, strict=True)],
            'invalidation': {'description': INVALIDATION[code], 'referencePrice': float(reference) if reference is not None else None}}


def evaluate(bars: list[Bar], open_dates: list[date], as_of: date, state: StockState) -> Evaluation:
    """先执行共同过滤，再独立计算各策略，长窗口不足不影响已具备短窗口的策略。"""
    if state.trade_status != 'trading':
        return Evaluation((), 'NOT_TRADING')
    if state.is_st is not False or state.is_delisting is not False:
        return Evaluation((), 'ST_OR_DELISTING_OR_UNKNOWN')
    try:
        context = Context(bars, open_dates, as_of)
        if context.amount(20) < 30_000_000:
            return Evaluation((), 'LOW_LIQUIDITY')
    except MissingData:
        return Evaluation((), 'DATA_INCOMPLETE')
    signals = []
    for code in NAMES:
        try:
            length = {'short': 21, 'swing': 65, 'long': 140, 'trend_wait': 65, 'base_wait': 121}[code]
            adjusted = Context(adjusted_history(context.window(length), as_of), open_dates, as_of)
            signal = evaluate_one(code, adjusted)  # type: ignore[arg-type]
            if signal is not None:
                signals.append(signal)
        except MissingData:
            continue
    return Evaluation(tuple(signals))
