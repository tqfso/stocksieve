"""本文件核对模型摘录的页码、原文、数值、单位及报告期，阻止未验证事实进入报告。"""

import re
from datetime import date
from decimal import Decimal
from typing import Any

from stocksieve.modules.analysis.application.reports import FactEvidence
from stocksieve.shared.errors import DomainError

LABELS = {'revenue': ('营业收入', '营业总收入'), 'net_profit': ('归属于上市公司股东的净利润', '归属于上市公司普通股股东的净利润', '归属于母公司', '归母净利润'),
          'adjusted_profit': ('扣除非经常性损益', '扣非'),
          'operating_cashflow': ('经营活动产生的现金流量净额', '经营活动的现金流量净额', '经营活动现金流量净额'),
          'shareholder_count': ('普通股股东总数', '股东户数')}
SCALES = {'元': Decimal(1), '万元': Decimal(10000), '亿元': Decimal(100000000), '户': Decimal(1)}
NAMES = {'revenue': '营业收入', 'net_profit': '归母净利润', 'adjusted_profit': '扣非净利润',
         'operating_cashflow': '经营现金流', 'shareholder_count': '股东户数'}


def normalized(value: str) -> str:
    return re.sub(r'\s+', '', value).replace('，', ',').replace('（', '(').replace('）', ')').replace('−', '-').replace('％', '%')


def numeric_text(value: str) -> str:
    """数字列保留空格边界，防止金额与下一列同比拼接成新数字。"""
    text = re.sub(r'\s+', ' ', value).replace('，', ',').replace('（', '(').replace('）', ')').replace('−', '-').replace('％', '%')
    text = re.sub(r'\(\s*', '(', text)
    return re.sub(r'\s*\)', ')', text)


def period_end(title: str) -> date | None:
    """从正式报告标题识别标准累计期间，不能把披露日期当统计日期。"""
    year = re.search(r'(20\d{2})年?', title)
    if not year:
        return None
    if re.search(r'第?[三3]季度报告', title):
        month, day = 9, 30
    elif re.search(r'第?[一1]季度报告', title):
        month, day = 3, 31
    elif '半年度报告' in title:
        month, day = 6, 30
    elif '年度报告' in title:
        month, day = 12, 31
    else:
        return None
    return date(int(year.group(1)), month, day)


def numbers(text: str) -> set[Decimal]:
    result = set()
    for match in re.finditer(r'\(?-?\d[\d,]*(?:\.\d+)?\)?', text):
        token = match.group().replace(',', '')
        negative = token.startswith('(') and token.endswith(')')
        result.add(Decimal(token.strip('()')) * (-1 if negative else 1))
    return result


def amounts(text: str) -> list[Decimal]:
    """保留原文顺序并排除百分数，用于区分单季、累计和上年金额。"""
    result = []
    for match in re.finditer(r'\(?-?\d[\d,]*(?:\.\d+)?\)?', text):
        if text[match.end():].lstrip().startswith('%'):
            continue
        token = match.group().replace(',', '')
        result.append(Decimal(token.strip('()')) * (-1 if token.startswith('(') and token.endswith(')') else 1))
    return result


def verify_facts(claims: list[FactEvidence], materials: dict[str, Any], as_of: date) -> list[dict[str, Any]]:
    """只接受已读原文中的原始数值；同期间同口径事实冲突时拒绝发布。"""
    verified: dict[tuple[str, date, str], dict[str, Any]] = {}
    for claim in claims:
        source = materials.get(claim.sourceKey)
        data = (source or {}).get('content_json') or {}
        page = next((item for item in data.get('pages', []) if item['page'] == claim.page), None)
        quote = normalized(claim.evidence)
        numeric_quote = numeric_text(claim.evidence)
        text = normalized(page['text']) if page else ''
        valid = source and source['status'] == 'available' and source['material_kind'] == 'research' and page
        valid = valid and quote in text and claim.value in numbers(numeric_quote)
        valid = valid and any(normalized(label) in quote for label in LABELS[claim.metric])
        valid = valid and claim.periodEnd == period_end(source['source_name']) and claim.periodEnd <= as_of
        if claim.metric == 'shareholder_count':
            valid = valid and claim.unit == '户' and claim.basis in {'A', 'all'} and claim.value > 0 and claim.value == claim.value.to_integral_value()
            valid = valid and '报告期末' in quote and '披露日前' not in quote
            if claim.basis == 'A':
                a_share = re.search(r'A股[:：]?(\d[\d,]*)', quote)
                valid = valid and a_share and claim.value == Decimal(a_share.group(1).replace(',', ''))
            else:
                values = amounts(numeric_quote)
                valid = valid and values and claim.value == values[0]
        else:
            # 使用该行之前最近出现的货币单位，防止把元、万元和亿元混算。
            prefix = text[:text.find(quote)] + quote
            units = re.findall(r'单位[:：]?(?:人民币)?(亿元|万元|元)', prefix)
            row_units = re.findall(r'\((?:人民币)?(亿元|万元|元)\)', quote)
            unit = row_units[-1] if row_units else units[-1] if units else None
            valid = valid and claim.basis == 'consolidated' and claim.unit != '户' and claim.unit == unit
            valid = valid and not any(label in text for label in ('母公司利润表', '母公司现金流量表'))
            valid = valid and not any(any(normalized(label) in quote for label in labels)
                                      for metric, labels in LABELS.items() if metric != claim.metric)
            values = amounts(numeric_quote)
            quarterly_columns = '本报告期' in text and '年初至报告期末' in text and claim.periodEnd.month != 12
            expected = values[-1] if quarterly_columns and len(values) == 2 else values[0] if values else None
            valid = valid and claim.value == expected
        if claim.yoy is not None:
            percentages = [Decimal(value.replace(',', '')) for value in re.findall(r'(-?\d[\d,]*(?:\.\d+)?)\s*%', numeric_quote)]
            valid = valid and percentages and claim.yoy == percentages[-1]
        if not valid:
            raise DomainError('ANALYSIS_INVALID_REPORT', f'报告中的{NAMES[claim.metric]}未通过原文、单位或期间核验。', 503)
        item = {'metric': claim.metric, 'value': float(claim.value * SCALES[claim.unit]),
                'unit': '户' if claim.unit == '户' else '元', 'periodEnd': claim.periodEnd.isoformat(),
                'basis': claim.basis, 'sourceKey': claim.sourceKey, 'page': claim.page,
                'evidence': claim.evidence, 'yoy': float(claim.yoy) if claim.yoy is not None else None,
                'sourceUrl': source['source_url'], 'publishedAt': source['published_at'].isoformat() if source['published_at'] else None}
        key = claim.metric, claim.periodEnd, claim.basis
        if key in verified and verified[key]['value'] != item['value']:
            raise DomainError('ANALYSIS_INVALID_REPORT', '同一报告期的事实数值存在冲突，未发布报告。', 503)
        verified[key] = item
    return list(verified.values())
