"""本文件计算不依赖策略入选的通用技术观察参考，供模拟报告使用，不生成交易指令。"""

from datetime import date
from decimal import Decimal
from typing import Any

from stocksieve.modules.market.domain.models import Bar, StockState, adjusted_history
from stocksieve.modules.market.domain.strategies import Context
from stocksieve.shared.errors import MissingData


def general_trade(bars: list[Bar], days: list[date], target: date, state: StockState) -> dict[str, Any]:
    """仅在状态和连续窗口可核验时给观察价；弱势、缺失及无效区间均不提供买入价。"""
    plan: dict[str, Any] = {
        'waiting': True, 'entryLabel': '买入参考', 'entryPrice': '暂无可靠参考',
        'entryCondition': '先等待行情与交易状态资料补齐。', 'exitPrice': '暂无可靠参考',
        'exitCondition': '资料不足，暂不能提供风险复查价。',
        'considerIf': '趋势、量价及事件风险核验后再评估。', 'avoidIf': '核心资料不足时先不参与。',
    }
    result: dict[str, Any] = {'method': 'general-trade-v1', 'action': 'wait', 'label': '先等待',
        'reason': '行情或交易状态资料不足，先等待。', 'plan': plan, 'metrics': {}, 'missing': []}
    if state.is_st or state.is_delisting:
        result.update(action='avoid', label='暂不买', reason='股票存在特别处理或退市风险标记，先不参与。')
        plan.update(entryCondition='先核验特别处理或退市风险。', avoidIf='存在特别处理或退市风险标记。')
        return result
    if state.trade_status != 'trading' or state.is_st is None or state.is_delisting is None:
        result['missing'] = ['当日正常交易状态未确认或股票已停牌，暂不提供技术价位。']
        return result
    try:
        original = Context(bars, days, target).window(60)
        context = Context(adjusted_history(original, target), days, target)
        price, ma20, ma60 = context.bar().close, context.ma(20), context.ma(60)
        previous = context.window(20, 1)
        support, resistance = min(bar.low for bar in previous), max(bar.high for bar in previous)
    except MissingData:
        result['missing'] = ['通用技术参考需要至少60个连续交易日及完整复权资料。']
        return result
    result['metrics'] = {'ma20': float(ma20), 'ma60': float(ma60), 'support20': float(support), 'resistance20': float(resistance)}
    plan.update(exitPrice=f'{support:.2f} 元', exitCondition='后续收盘跌破此前20日低点时，重新评估风险。',
        entryCondition='尚未满足趋势观察条件，先等待。', avoidIf='趋势继续转弱、跌破风险参考位或事件风险尚未核验。')
    if price < support or ma20 < ma60 and price < ma20:
        result.update(action='avoid', label='暂不买', reason='当前走势偏弱，先等待趋势修复。')
    elif ma20 >= ma60 and price >= ma20 and ma20 * Decimal('.99') > support:
        plan.update(entryLabel='回踩观察区间', entryPrice=f"{ma20 * Decimal('.99'):.2f}～{ma20 * Decimal('1.01'):.2f} 元",
            entryCondition='回到20日均价附近后重新走稳，且MA20不低于MA60；仅触及价位不代表可以买入。',
            considerIf='看好趋势延续，并愿意等待回踩走稳及风险核验后再考虑。')
        result.update(reason='均线趋势尚可，等待回踩走稳及风险核验。')
    else:
        result.update(reason='趋势或观察区间尚未确认，继续等待。')
    return result
