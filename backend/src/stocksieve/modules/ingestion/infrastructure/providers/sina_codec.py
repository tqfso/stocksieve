"""本文件安全解析新浪的JS数据赋值，并用随项目保存的解码器解压日线和日历。"""

import json
import re
from pathlib import Path
from typing import Any

from py_mini_racer import py_mini_racer

from stocksieve.shared.errors import DomainError


def assignment(content: bytes) -> Any:
    """只读取赋值右侧的JSON值，允许随后的分号和注释，不执行远端JavaScript。"""
    text = content.decode('utf-8-sig').strip()
    match = re.search(r'\bvar\s+[A-Za-z_$][\w$]*\s*=\s*', text)
    if match:
        text = text[match.end():].lstrip()
    if text.startswith('('):
        text = text[1:].lstrip()
    def reject(value: str) -> Any:
        raise ValueError('不允许非有限数值')
    return json.JSONDecoder(parse_constant=reject).raw_decode(text)[0]


class SinaDecoder:
    """执行固定的本地解码算法，设置执行时限；行情内容仅作为字符串参数传入。"""

    def __init__(self) -> None:
        self.context = py_mini_racer.MiniRacer()
        self.context.eval(Path(__file__).with_name('resources').joinpath('sina_decode.js').read_text(), timeout=1000)

    def decode(self, content: bytes) -> list[Any]:
        try:
            value = assignment(content)
            if not isinstance(value, str):
                raise TypeError('压缩内容应为字符串')
            rows = self.context.call('d', value, timeout=3000)
            if not isinstance(rows, list):
                raise TypeError('解码结果应为列表')
            return rows
        except (ValueError, TypeError, KeyError, py_mini_racer.JSEvalException) as error:
            raise DomainError('DATA_INCOMPLETE', '新浪压缩数据格式未通过校验。', 503) from error
