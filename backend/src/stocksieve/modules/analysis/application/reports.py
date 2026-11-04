"""本文件限制智能体报告结构与摘要长度，数值和来源由后端计算、校验并补齐。"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, field_validator

from stocksieve.shared.contracts import Input

Short = Annotated[str, Field(min_length=1, max_length=60)]
CHAPTERS = {'company': '公司做什么', 'advantages': '有什么优势', 'risks': '主要风险',
            'holding': '已有持仓怎么办', 'entry': '现在适合买入吗', 'sell': '何时考虑卖出'}


class Decision(Input):
    action: Literal['consider', 'wait', 'avoid']
    label: str = Field(min_length=1, max_length=20)
    reason: Short


class Holding(Input):
    label: Short
    reason: Short


class Overview(Input):
    decision: Decision
    holding: Holding
    opportunities: list[Short] = Field(max_length=2)
    risks: list[Short] = Field(max_length=2)
    watch: list[Short] = Field(default_factory=list, max_length=2)


class Section(Input):
    id: Literal['company', 'advantages', 'risks', 'holding', 'entry', 'sell']
    headline: Short
    sourceKeys: list[str] = Field(default_factory=list, max_length=32)
    title: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=240)


class FactEvidence(Input):
    """模型摘录原始值和原文证据，后端复核后统一用于正文、指标与导出。"""

    metric: Literal['revenue', 'net_profit', 'adjusted_profit', 'operating_cashflow', 'shareholder_count']
    value: Decimal = Field(allow_inf_nan=False)
    unit: Literal['元', '万元', '亿元', '户']
    periodEnd: date
    basis: Literal['consolidated', 'A', 'all']
    sourceKey: str = Field(min_length=1, max_length=128)
    page: int = Field(ge=1, le=160)
    evidence: str = Field(min_length=8, max_length=1200)
    yoy: Decimal | None = Field(default=None, allow_inf_nan=False)


class ReportDraft(Input):
    overview: Overview
    summary: str = Field(min_length=1, max_length=200)
    sections: list[Section] = Field(min_length=6, max_length=6)
    sourceKeys: list[str] = Field(min_length=1, max_length=32)
    missingInformation: list[Short] = Field(default_factory=list, max_length=12)
    reductions: list['ReductionEvidence'] = Field(default_factory=list, max_length=20)
    facts: list[FactEvidence] = Field(default_factory=list, max_length=24)

    @field_validator('sections')
    @classmethod
    def complete_sections(cls, value: list[Section]) -> list[Section]:
        """合并买与不买为一个判断，固定六章，标题由后端统一。"""
        if [section.id for section in value] != list(CHAPTERS):
            raise ValueError('报告需要按顺序完整回答六个问题。')
        for section in value:
            section.title = CHAPTERS[section.id]
        return value


class ReductionEvidence(Input):
    sourceKey: str = Field(min_length=1, max_length=128)
    shareholder: str = Field(min_length=1, max_length=100)
    status: Literal['planned', 'in_progress', 'completed', 'terminated']
    evidence: str = Field(min_length=20, max_length=2000)
    planText: str | None = Field(default=None, max_length=500)
    actualText: str | None = Field(default=None, max_length=500)
    periodText: str | None = Field(default=None, max_length=500)
