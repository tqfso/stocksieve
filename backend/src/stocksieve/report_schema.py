"""本文件限制智能体报告结构与摘要长度，数值和来源由后端计算、校验并补齐。"""

from typing import Annotated, Literal

from pydantic import Field

from .validation import Input

Short = Annotated[str, Field(min_length=1, max_length=80)]


class Decision(Input):
    action: Literal['consider', 'wait', 'avoid']
    label: str = Field(min_length=1, max_length=20)
    reason: Short


class Overview(Input):
    decision: Decision
    opportunities: list[Short] = Field(max_length=2)
    risks: list[Short] = Field(max_length=2)


class Section(Input):
    title: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=3000)


class ReportDraft(Input):
    overview: Overview
    summary: str = Field(min_length=1, max_length=3000)
    sections: list[Section] = Field(min_length=1, max_length=8)
    sourceKeys: list[str] = Field(min_length=1, max_length=32)
    missingInformation: list[Short] = Field(default_factory=list, max_length=12)


class ReductionEvidence(Input):
    sourceKey: str = Field(min_length=1, max_length=128)
    shareholder: str = Field(min_length=1, max_length=100)
    status: Literal['planned', 'in_progress', 'completed', 'terminated']
    evidence: str = Field(min_length=20, max_length=2000)
    planText: str | None = Field(default=None, max_length=500)
    actualText: str | None = Field(default=None, max_length=500)
    periodText: str | None = Field(default=None, max_length=500)
