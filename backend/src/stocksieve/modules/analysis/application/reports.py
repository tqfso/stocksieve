"""本文件限制智能体报告结构与摘要长度，数值和来源由后端计算、校验并补齐。"""

from typing import Annotated, Literal

from pydantic import Field, field_validator

from stocksieve.shared.contracts import Input

Short = Annotated[str, Field(min_length=1, max_length=80)]


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


class Section(Input):
    id: Literal['company', 'advantages', 'risks', 'holding', 'buy', 'avoid', 'sell']
    headline: Short
    sourceKeys: list[str] = Field(default_factory=list, max_length=32)
    title: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=3000)


class ReportDraft(Input):
    overview: Overview
    summary: str = Field(min_length=1, max_length=3000)
    sections: list[Section] = Field(min_length=7, max_length=7)
    sourceKeys: list[str] = Field(min_length=1, max_length=32)
    missingInformation: list[Short] = Field(default_factory=list, max_length=12)
    reductions: list['ReductionEvidence'] = Field(default_factory=list, max_length=20)

    @field_validator('sections')
    @classmethod
    def complete_sections(cls, value: list[Section]) -> list[Section]:
        """七个问题必须完整且顺序固定，不能遗漏或重复章节。"""
        if [section.id for section in value] != ['company', 'advantages', 'risks', 'holding', 'buy', 'avoid', 'sell']:
            raise ValueError('报告需要按顺序完整回答七个问题。')
        return value


class ReductionEvidence(Input):
    sourceKey: str = Field(min_length=1, max_length=128)
    shareholder: str = Field(min_length=1, max_length=100)
    status: Literal['planned', 'in_progress', 'completed', 'terminated']
    evidence: str = Field(min_length=20, max_length=2000)
    planText: str | None = Field(default=None, max_length=500)
    actualText: str | None = Field(default=None, max_length=500)
    periodText: str | None = Field(default=None, max_length=500)
