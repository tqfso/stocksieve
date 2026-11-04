"""本文件通过巨潮公开检索发现当前证券公告，复用项目缓存和供应商频控。"""

import html
import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urljoin

from stocksieve.modules.analysis.application.ports import SearchHit
from stocksieve.modules.ingestion.application.ports import PublicHTTPClient
from stocksieve.shared.errors import DomainError
from stocksieve.shared.time import SHANGHAI


def matches_title(title: str, topic: str, year: str | None) -> bool:
    if year and year not in title:
        return False
    for keyword in ('三季度报告', '一季度报告', '半年度报告', '年度报告', '减持', '诉讼', '处罚'):
        if keyword in topic.replace('第', ''):
            if keyword not in title.replace('第', ''):
                return False
            if keyword == '年度报告' and '半年度报告' in title:
                return False
            break
    if '减持' in topic and any(word in topic for word in ('进展', '结果', '完成', '终止')):
        if not any(word in title for word in ('进展', '结果', '完成', '终止', '实施', '届满', '期限届满')):
            return False
    return not ('报告' in topic and any(word in title for word in ('摘要', '英文', '审议', '董事会', '提示性公告', '延期披露', '更正公告', '预约披露')))


class CninfoSearch:
    """只返回匹配证券代码的公告原文，检索结果本身不充当事实证据。"""

    endpoint = 'https://www.cninfo.com.cn/new/fulltextSearch/full'

    def __init__(self, http: PublicHTTPClient) -> None:
        self.http = http

    async def search(self, query: str, exchange: str | None = None) -> list[SearchHit]:
        match = re.search(r'(?<!\d)\d{6}(?!\d)', query)
        if not match:
            return []
        code = match.group()
        topic = query.replace(code, '').strip()
        year = re.search(r'(20\d{2})年?', topic)

        async def fetch(searchkey: str) -> tuple[list[dict[str, Any]], datetime, str | None]:
            # 先读证券近期公告再匹配主题，兼容不同发行人“三季度/第三季度”等标题写法。
            params = {'searchkey': searchkey, 'sdate': '', 'edate': '', 'isfulltext': 'false',
                      'sortName': 'pubdate', 'sortType': 'desc', 'pageNum': '1'}
            raw, fetched = await self.http.document('cninfo', self.endpoint + '?' + urlencode(params), cache_seconds=3600, stale_seconds=86400)
            payload = json.loads(raw)
            warning = None
            if (datetime.now(UTC) - fetched).total_seconds() > 3600:
                warning = '公告资料更新至' + fetched.astimezone(SHANGHAI).strftime('%m月%d日%H:%M') + '，之后的新披露尚未覆盖'
            return sorted(payload['announcements'] or [], key=lambda row: row.get('announcementTime') or 0, reverse=True), fetched, warning

        try:
            result: list[SearchHit] = []
            queries = [code, query]
            if re.search(r'第?[一三]季度报告', query):
                alternate = re.sub(r'第?([一三])季度报告', lambda match: ('' if '第' in match.group() else '第') + match.group(1) + '季度报告', query)
                queries.append(alternate)
            for searchkey in dict.fromkeys(queries):
                rows, fetched, warning = await fetch(searchkey)
                for row in rows:
                    if row.get('secCode') != code:
                        continue
                    path = row.get('adjunctUrl') or ''
                    if not re.fullmatch(r'finalpage/[0-9-]+/[a-zA-Z0-9_.-]+\.pdf', path, re.IGNORECASE):
                        continue
                    title = html.unescape(re.sub('<[^>]+>', '', row.get('announcementTitle') or '公告原文'))
                    if not matches_title(title, topic, year.group(1) if year else None):
                        continue
                    stamp = row.get('announcementTime')
                    published = datetime.fromtimestamp(stamp / 1000, SHANGHAI).isoformat() if isinstance(stamp, (int, float)) else None
                    result.append(SearchHit(title[:200], urljoin('https://static.cninfo.com.cn/', path), published, fetched.isoformat(), warning))
                if result:
                    break
            return result[:8]
        except (ValueError, TypeError, KeyError, OverflowError, OSError) as error:
            raise DomainError('SOURCE_UNAVAILABLE', '巨潮检索未返回可核验的公告列表。', 503) from error
