"""本文件统一序列化日期和有限数值，供HTTP与后台业务共用。"""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal) and value.is_finite():
        return float(value)
    raise TypeError('结果包含不可序列化的内容')


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=json_default)
