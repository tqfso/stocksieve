"""本文件用固定公式计算盈亏比、估值和情景结果，不执行表达式或推断胜率。"""

from decimal import Decimal
from typing import Any

from stocksieve.modules.market.domain.models import decimal_value


def calculate(operation: str, values: dict[str, Any]) -> dict[str, Any]:
    """所有金额使用相同单位；保留输入与假设，拒绝缺失、非有限值及无效区间。"""
    def number(name: str, default: Any = None) -> Decimal:
        value = values.get(name, default)
        if value is None:
            raise ValueError(f'缺少参数：{name}')
        result = decimal_value(value)
        if abs(result) > Decimal('1e24'):
            raise ValueError('计算数值超出范围')
        return result

    if operation == 'risk_reward':
        entry, stop, target = number('entry'), number('stop'), number('target')
        cost = number('costRatePercent', 0)
        if not 0 < stop < entry < target or not 0 <= cost <= 10:
            raise ValueError('需要0<止损价<买入价<目标价，往返成本率为0～10%')
        expense = entry * cost / 100
        profit, loss = target - entry - expense, entry - stop + expense
        result = {'profitPerShare': profit, 'lossPerShare': loss, 'riskRewardRatio': profit / loss,
                  'upsidePercent': profit / entry * 100, 'downsidePercent': loss / entry * 100,
                  'breakEvenWinRatePercent': loss / (profit + loss) * 100 if profit > 0 else None}
        formula = '净收益=(target-entry)-entry×costRatePercent/100；净风险=(entry-stop)+同额成本；盈亏比=净收益/净风险'
        limitation = '价格为条件假设；盈亏比和盈亏平衡胜率均不代表实际胜率，跳空可能导致实际损失超过止损情景。'
    elif operation == 'growth':
        current, previous = number('current'), number('previous')
        if previous <= 0:
            raise ValueError('基期必须为正；负基期或零基期不输出常规增长率')
        result = {'change': current - previous, 'growthPercent': (current / previous - 1) * 100}
        formula, limitation = '(current/previous-1)×100%', '输入必须同单位、同口径、可比期间。'
    elif operation == 'valuation':
        cap, profit = number('marketCap'), number('netProfitTtm')
        if cap <= 0 or profit <= 0:
            raise ValueError('总市值和归母净利润TTM必须为正，亏损时不提供有效PE倍数')
        result = {'companyPeTtm': cap / profit}
        if 'industryPeTtm' in values:
            industry = number('industryPeTtm')
            if industry <= 0 or not values.get('comparability'):
                raise ValueError('行业PE需为正，并说明相同口径、可比日期及行业分类')
            result['premiumPercent'] = (cap / profit / industry - 1) * 100
        formula, limitation = 'PE(TTM)=总市值/归母净利润TTM', '低PE不能单独证明低估；行业对比须核对日期和口径。'
    elif operation == 'expected_return':
        probability, profit, loss, cost = number('winProbability'), number('profit'), number('loss'), number('cost', 0)
        if not 0 <= probability <= 1 or min(profit, loss, cost) < 0 or not values.get('assumption'):
            raise ValueError('需要0～1的假设胜率、非负盈亏与成本，并明确假设依据')
        result = {'expectedReturn': probability * profit - (1 - probability) * loss - cost}
        formula, limitation = 'p×盈利-(1-p)×亏损-成本', '输入概率仅为情景假设，不是经过回测的历史胜率。'
    elif operation == 'scenarios':
        entry = number('entry')
        targets = values.get('targets')
        if entry <= 0 or not isinstance(targets, list) or not 1 <= len(targets) <= 5:
            raise ValueError('需要正买入价及1～5个情景目标价')
        results = []
        for value in targets:
            target = decimal_value(value)
            if not 0 < target <= Decimal('1e24'):
                raise ValueError('情景目标价必须为有限正数')
            results.append({'target': target, 'returnPercent': (target / entry - 1) * 100})
        result = {'scenarios': results}
        formula, limitation = '(情景目标价/买入价-1)×100%', '各情景仅为假设，不赋予未经验证的概率，结果未扣交易成本。'
    else:
        raise ValueError('只支持risk_reward、growth、valuation、expected_return、scenarios')

    def serialize(value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, dict):
            return {key: serialize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [serialize(item) for item in value]
        return value

    return {'operation': operation, 'inputs': values, 'result': serialize(result), 'formula': formula, 'limitation': limitation}
