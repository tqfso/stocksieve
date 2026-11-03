"""本文件通过巨潮公开检索发现当前证券公告，复用项目缓存和供应商频控。"""

import html
import json
import re
from datetime import datetime
from urllib.parse import urlencode, urljoin

from stocksieve.modules.analysis.application.ports import SearchHit
from stocksieve.modules.ingestion.application.ports import PublicHTTPClient
from stocksieve.shared.errors import DomainError
from stocksieve.shared.time import SHANGHAI


class CninfoSearch:
    """只返回匹配证券代码的公告原文，检索结果本身不充当事实证据。"""

    endpoint = 'https://www.cninfo.com.cn/new/fulltextSearch/full'

    def __init__(self, http: PublicHTTPClient) -> None:
        self.http = http

    async def search(self, query: str) -> list[SearchHit]:
        match = re.search(r'(?<!\d)\d{6}(?!\d)', query)
        if not match:
            return []
        code = match.group()
        topic = query.replace(code, '').strip()
        year = re.search(r'(20\d{2})年?', topic)

        def matches(title: str) -> bool:
            if year and year.group(1) not in title:
                return False
            for keyword in ('三季度报告', '一季度报告', '半年度报告', '年度报告', '减持', '诉讼', '处罚'):
                if keyword in topic.replace('第', ''):
                    if keyword not in title.replace('第', ''):
                        return False
                    if keyword == '年度报告' and '半年度报告' in title:
                        return False
                    break
            return not ('报告' in topic and any(word in title for word in ('摘要', '英文', '审议', '董事会')))

        async def fetch(searchkey: str) -> list[dict[str, object]]:
            # 先读证券近期公告再匹配主题，兼容不同发行人“三季度/第三季度”等标题写法。
            params = {'searchkey': searchkey, 'sdate': '', 'edate': '', 'isfulltext': 'false',
                      'sortName': 'pubdate', 'sortType': 'desc', 'pageNum': '1'}
            raw, _ = await self.http.document('cninfo', self.endpoint + '?' + urlencode(params), cache_seconds=3600)
            payload = json.loads(raw)
            return sorted(payload['announcements'] or [], key=lambda row: row.get('announcementTime') or 0, reverse=True)

        try:
            result: list[SearchHit] = []
            for searchkey in dict.fromkeys((code, query)):
                rows = await fetch(searchkey)
                for row in rows:
                    if row.get('secCode') != code:
                        continue
                    path = row.get('adjunctUrl') or ''
                    if not re.fullmatch(r'finalpage/[0-9-]+/[a-zA-Z0-9_.-]+\.pdf', path, re.IGNORECASE):
                        continue
                    title = html.unescape(re.sub('<[^>]+>', '', row.get('announcementTitle') or '公告原文'))
                    if not matches(title):
                        continue
                    stamp = row.get('announcementTime')
                    published = datetime.fromtimestamp(stamp / 1000, SHANGHAI).isoformat() if isinstance(stamp, (int, float)) else None
                    result.append(SearchHit(title[:200], urljoin('https://static.cninfo.com.cn/', path), published))
                if result:
                    break
            return result[:8]
        except (ValueError, TypeError, KeyError, OverflowError, OSError) as error:
            raise DomainError('SOURCE_UNAVAILABLE', '巨潮检索未返回可核验的公告列表。', 503) from error
