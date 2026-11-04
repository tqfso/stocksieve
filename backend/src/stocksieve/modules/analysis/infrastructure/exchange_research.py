"""本文件检索深交所官方公告原文，证券身份、日期和附件地址均以接口返回值确认。"""

import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from stocksieve.modules.analysis.application.ports import SearchHit
from stocksieve.modules.analysis.infrastructure.research import matches_title
from stocksieve.modules.ingestion.application.ports import AnnouncementHTTPClient
from stocksieve.shared.errors import DomainError
from stocksieve.shared.time import SHANGHAI


class SzseSearch:
    """独立官方来源，共享szse供应商预算，不修改其他供应商的冷却。"""

    endpoint = 'https://www.szse.cn/api/disc/announcement/annList'

    def __init__(self, http: AnnouncementHTTPClient, clock: Callable[[], datetime] | None = None) -> None:
        self.http = http
        self.clock = clock or (lambda: datetime.now(SHANGHAI))

    def window(self, topic: str, year: str | None) -> tuple[date, date]:
        """用主题缩小检索窗口，实际披露日始终读取接口，不能由窗口推定。"""
        today = self.clock().astimezone(SHANGHAI).date()
        topic = topic.replace('第', '')
        if '年度报告' in topic and '半年度报告' not in topic:
            fiscal = int(year) if year else today.year - 1
            return date(fiscal + 1, 1, 1), min(today, date(fiscal + 1, 6, 30))
        for keyword, month, end_month in (('三季度报告', 10, 12), ('半年度报告', 7, 9), ('一季度报告', 4, 6)):
            if keyword in topic:
                fiscal = int(year) if year else today.year if today.month >= month else today.year - 1
                end_day = 31 if end_month == 12 else 30
                return date(fiscal, month, 1), min(today, date(fiscal, end_month, end_day))
        return today - timedelta(days=730), today

    async def search(self, query: str, exchange: str | None = None) -> list[SearchHit]:
        match = re.search(r'(?<!\d)\d{6}(?!\d)', query)
        if not match or exchange not in {None, 'SZSE'}:
            return []
        code = match.group()
        topic = query.replace(code, '').strip()
        year_match = re.search(r'(20\d{2})年?', topic)
        year = year_match.group(1) if year_match else None
        start, end = self.window(topic, year)
        if start > end:
            return []
        result = []
        try:
            for page in range(1, 4):
                data = {'seDate': [start.isoformat(), end.isoformat()], 'stock': [code], 'pageSize': 30,
                        'pageNum': page, 'channelCode': ['listedNotice_disc']}
                raw, fetched = await self.http.post_document('szse', self.endpoint, data, cache_seconds=3600, stale_seconds=86400)
                payload = json.loads(raw)
                rows = payload.get('data') or []
                if not isinstance(rows, list):
                    raise ValueError('公告列表结构无效')
                warning = None
                if (datetime.now(UTC) - fetched).total_seconds() > 3600:
                    warning = '交易所公告资料更新至' + fetched.astimezone(SHANGHAI).strftime('%m月%d日%H:%M') + '，之后的新披露尚未覆盖'
                for row in rows:
                    codes = row.get('secCode') or []
                    if isinstance(codes, str):
                        codes = [codes]
                    if code not in codes:
                        continue
                    title = row.get('title') or ''
                    path = row.get('attachPath') or ''
                    if not matches_title(title, topic, year) or not re.fullmatch(r'/disc/disk\d+/finalpage/[0-9-]+/[a-zA-Z0-9_.-]+\.pdf', path, re.IGNORECASE):
                        continue
                    published = None
                    if row.get('publishTime'):
                        stamp = datetime.fromisoformat(row['publishTime'])
                        published = (stamp.replace(tzinfo=SHANGHAI) if stamp.tzinfo is None else stamp).isoformat()
                    url = 'https://disc.static.szse.cn/download' + path
                    result.append(SearchHit(title[:200], url, published, fetched.isoformat(), warning))
                if result or page * 30 >= int(payload.get('announceCount') or len(rows)) or not rows:
                    break
        except (ValueError, KeyError, TypeError, OverflowError) as error:
            raise DomainError('SOURCE_UNAVAILABLE', '交易所公告资料暂未就绪。', 503) from error
        result.sort(key=lambda item: item.published_at or '', reverse=True)
        return result[:8]
