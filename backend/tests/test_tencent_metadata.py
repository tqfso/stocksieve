"""本文件验证明确板块标签、总市值口径及日期/价格不匹配时保留未知，不请求网络。"""

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest
from stocksieve.modules.ingestion.infrastructure.providers.baostock import security
from stocksieve.modules.ingestion.infrastructure.providers.tencent import (
    TencentMetadata,
)
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy


@pytest.mark.parametrize('cap,expected', [('1680.21', D('168021000000')), ('0', None), ('--', None)])
def test_quote_metadata_requires_matching_price_date(tmp_path: Path, monkeypatch: Any, cap: str, expected: D | None) -> None:
    import stocksieve.modules.ingestion.infrastructure.providers.tencent as module
    fields = [''] * 90
    fields[2], fields[3], fields[30], fields[45], fields[61] = '302132', '62.88', '20261030150000', cap, 'GP-A-CYB'
    payload = ('v_sz302132="' + '~'.join(fields) + '";').encode('gb18030')
    class HTTP:
        def __init__(self, *args: Any) -> None:
            pass
        async def document(self, *args: Any, **kwargs: Any) -> Any:
            return payload, datetime.now(timezone.utc)
        async def close(self) -> None:
            pass
    monkeypatch.setattr(module, 'SourceHTTP', HTTP)
    metadata = TencentMetadata(SourcePolicy(tmp_path / 'rate'), tmp_path / 'cache')
    asyncio.run(metadata.fetch(['sz302132']))
    assert metadata.board('sz.302132') == 'CHINEXT'
    stock = security('sz.302132', '测试股票', board_override=metadata.board('sz.302132'))
    assert metadata.cap(stock, date(2026, 10, 30), D('62.88')) == expected
    assert metadata.cap(stock, date(2026, 10, 29), D('62.88')) is None
    assert metadata.cap(stock, date(2026, 10, 30), D('70')) is None
